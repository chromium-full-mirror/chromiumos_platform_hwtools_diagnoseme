# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""gRPC Servicer for Maui functional testing (SMT Step 2 and FATP Step 4).

Covers the Host-only checks (host USB/console, 180° host re-plug, RESET and BSL
buttons) and the Host+DUT stages (with charger, 180° SBU flip, charger
disconnected).
"""

import dataclasses
import logging
import os
import re
import subprocess
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import grpc
import serial

from server import maui_common
from server.generated import maui_manufacturing_pb2
from server.generated import maui_manufacturing_pb2_grpc
from server.maui_common import LogStatus
from server.maui_common import write_device_log
from server.maui_console_tester import BSL_PRESSED_THRESHOLD_MV
from server.maui_console_tester import BSL_SAMPLE_DURATION_SEC
from server.maui_console_tester import BSL_SAMPLE_INTERVAL_SEC
from server.maui_console_tester import ConsolePortBusyError
from server.maui_console_tester import ConsoleTestResult
from server.maui_console_tester import EXPECTED_HOST_GPIOS
from server.maui_console_tester import EXPECTED_HWREV
from server.maui_console_tester import MauiConsoleTester
from server.maui_console_tester import MIN_RESET_BASELINE_UPTIME_MS
from server.maui_console_tester import open_serial_port
from server.maui_console_tester import PdcInfoTestCase
from server.maui_console_tester import port_lock
from server.maui_console_tester import TEMP_MAX_C
from server.maui_console_tester import TEMP_MIN_C
from server.maui_console_tester import verify_host_adc_voltages


# Generated protobuf messages are not visible to pylint.
# pylint: disable=no-member

DEFAULT_TTY = "/dev/ttyUSB0"
CONSOLE_TIMEOUT_SEC = 0.5

# RESET button test actions (the UI sends the first of each; the rest are legacy aliases).
RESET_BASELINE_ACTIONS = ("check_initial_uptime", "baseline", "capture_baseline")
RESET_VERIFY_ACTIONS = ("verify_post_reset_uptime", "verify_reboot", "verify")
RESET_SETTLE_DELAY_SEC = 1.0
RESET_POLL_INTERVAL_SEC = 0.25
RESET_POLL_ATTEMPTS = 10

# Host+DUT stages (the UI sends the first of each; the rest are legacy aliases).
STAGE_WITH_CHARGER = ("stage1_with_charger",)
STAGE_SBU_FLIP = ("stage2_sbu_flip", "stage3_sbu_flip")
STAGE_NO_CHARGER = ("stage3_no_charger", "stage1_no_charger")
STAGE_ALL = "all"
STAGE1_LOG = "host_dut_stage1_with_charger.log"
HOST_ONLY_LOG = "host_only_tests.log"

# Host-only charger port detection (5 V source present).
HOST_CHARGER_MIN_MV = 4500
HOST_CHARGER_MIN_V = 4.5
# Host+DUT: an active PD charger (above a 5 V back-feed, e.g. 20 V contract).
PD_CHARGER_MIN_MV = 12000
PD_CHARGER_MIN_V = 9.0
# DUT 20 V PD contract.
DUT_20V_MIN_V = 18.5
DUT_20V_MIN_MV = 18000

# USB link speeds (sysfs `speed`, Mbps).
SUPERSPEED_MBPS = 5000
HIGH_SPEED_MBPS = 480

ADB_TIMEOUT_SEC = 1.0
ADB_SHELL_TIMEOUT_SEC = 1.5
CCD_QUERY_TIMEOUT_SEC = 0.5
CCD_FIRST_BYTE_TIMEOUT_SEC = 0.25
# CCD (Cr50/Ti50) USB interface number -> console name.
CCD_INTERFACE_CONSOLES = {0: "gsc", 1: "ap", 2: "ec"}

_HOST_ORIENTATION_RE = re.compile(
    r"(?:Host Orientation|Host CC Orientation|Orientation):\s*([^\r\n,|]+)",
    re.IGNORECASE,
)
_SBU_ORIENTATION_RE = re.compile(
    r"(?:Orientation|DUT Orientation|SBU Orientation):\s*([^\r\n,|]+)", re.IGNORECASE
)
_DUT_USB_LINE_RE = re.compile(
    r"^\s*([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s+speed=(\d+)", re.MULTILINE
)


def is_sbu_flipped(orig: str, curr: str) -> bool:
    """Checks if SBU cable orientation flipped (SBU1 <-> SBU2)."""
    o_l = orig.lower()
    c_l = curr.lower()
    if not orig or not curr or "unknown" in o_l or "unknown" in c_l:
        return False
    if "sbu1" in o_l:
        return "sbu2" in c_l
    if "sbu2" in o_l:
        return "sbu1" in c_l
    if "cc1" in o_l:
        return "cc2" in c_l or "sbu2" in c_l
    if "cc2" in o_l:
        return "cc1" in c_l or "sbu1" in c_l
    return o_l != c_l


def is_host_cc_flipped(orig: str, curr: str) -> bool:
    """Checks if Host CC orientation flipped (CC1 <-> CC2) via USB_HOST_CC1/CC2 ADC readings."""
    o_l = orig.lower()
    c_l = curr.lower()
    if not orig or not curr or "unknown" in o_l or "unknown" in c_l:
        return False
    if "cc1" in o_l:
        return "cc2" in c_l
    if "cc2" in o_l:
        return "cc1" in c_l
    return o_l != c_l


def _pass_fail(ok: bool) -> str:
    """'PASS' / 'FAIL' text of a check."""
    return "PASS" if ok else "FAIL"


def _status(ok: bool) -> str:
    """Machine-readable log STATUS of a check."""
    return LogStatus.PASSED if ok else LogStatus.FAILED


def _no_contract(contract: str) -> bool:
    """True when the PDC reports no active power contract."""
    return (contract or "").lower() in ("none", "unknown", "")


def _has_active_pd_charger(pdc_chg: Dict[str, Any], chg_adc_vbus_mv: int) -> bool:
    """True when the charger port has an active PD source (above the ~5 V DUT back-feed)."""
    return bool(
        chg_adc_vbus_mv >= PD_CHARGER_MIN_MV
        or pdc_chg.get("measured_vbus_mv", 0) >= PD_CHARGER_MIN_MV
        or pdc_chg.get("voltage_v", 0.0) >= PD_CHARGER_MIN_V
        or (
            pdc_chg.get("plug_present", False)
            and not _no_contract(pdc_chg.get("contract_type", "None"))
        )
    )


def _format_speed(speed_mbps: int) -> str:
    """Human-readable USB link speed (sysfs Mbps value)."""
    if speed_mbps >= SUPERSPEED_MBPS:
        return f"{speed_mbps}M"
    if speed_mbps >= HIGH_SPEED_MBPS:
        return "480M (High-Speed)"
    return f"{speed_mbps}M"


def _resolve_serial(serial_no: str) -> str:
    """Returns serial_no, or the serial of the first Maui ADB/H2H or FTDI endpoint found."""
    if serial_no:
        return serial_no
    for dev in maui_common.scan_usb_devices():
        if not dev.serial:
            continue
        if dev.vid == maui_common.GOOGLE_VID and dev.pid in maui_common.GOOGLE_ADB_PIDS:
            return dev.serial
        if dev.vid == maui_common.FTDI_VID:
            return dev.serial
    return serial_no


def _resolve_console_port(requested: str, default: str = DEFAULT_TTY) -> str:
    """Resolves the Maui MCU console: requested port, FTDI sysfs node, then numerically first ttyUSB."""
    if requested and os.path.exists(requested):
        return requested
    for dev in maui_common.scan_usb_devices():
        if (
            dev.vid == maui_common.FTDI_VID
            and dev.dev_node
            and os.path.exists(dev.dev_node)
        ):
            return dev.dev_node
    tty_nodes = maui_common.sorted_tty_nodes()
    return tty_nodes[0] if tty_nodes else default


def _read_previous_orientation(
    serial_no: str, log_name: str, pattern: re.Pattern
) -> str:
    """Reads the baseline orientation recorded by an earlier Step log of this exact unit."""
    if maui_common.is_unknown_serial(serial_no):
        return ""
    log_path = os.path.join(maui_common.device_log_dir_path(serial_no), log_name)
    if not os.path.exists(log_path):
        return ""
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            match = pattern.search(f.read())
    except (OSError, UnicodeDecodeError) as e:
        logging.warning("Could not read baseline orientation from %s: %s", log_path, e)
        return ""
    return match.group(1).strip() if match else ""


@dataclasses.dataclass
class _HostConsoleChecks:  # pylint: disable=too-many-instance-attributes
    """Results of the shared Host console session (Host-only and Host re-plug)."""

    console_results: Dict[str, ConsoleTestResult] = dataclasses.field(
        default_factory=dict
    )
    session_error: str = ""
    # The console port was held by another RPC (session_error has the message).
    port_busy: bool = False
    pdc_chg: Dict[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "success": False,
            "plug_present": False,
            "voltage_v": 0.0,
            "measured_vbus_mv": 0,
            "raw_output": "",
        }
    )
    adc_status: Dict[str, Any] = dataclasses.field(default_factory=dict)
    hwrev_ok: bool = False
    detected_hwrev: int = -1
    hwrev_raw: str = ""
    temp_ok: bool = False
    temp_c: float = -1.0
    temp_raw: str = ""
    gpios_ok: bool = False
    gpio_mismatches: List[str] = dataclasses.field(
        default_factory=lambda: ["Serial port not accessible"]
    )
    gpios_raw: str = ""
    voltages_ok: bool = False
    voltage_mismatches: List[str] = dataclasses.field(
        default_factory=lambda: ["Serial port not accessible"]
    )

    def adc(self, key: str, default: Any = 0) -> Any:
        """Value of one parsed `adc status` field."""
        return self.adc_status.get(key, default)

    def test_result(self, name: str) -> Tuple[bool, str]:
        """(success, message) of a registered console test, (False, 'Not run') if absent."""
        result = self.console_results.get(name)
        return (result.success, result.message) if result else (False, "Not run")

    @property
    def pdc_versions(self) -> Tuple[str, str]:
        """(app_config_version, fw_version) actually read from `pdc info`."""
        result = self.console_results.get(MauiConsoleTester.PDC_INFO_TEST)
        return PdcInfoTestCase.parse_versions(result.output if result else "")

    @property
    def gpio_details(self) -> str:
        """GPIO mismatches, or the verified count."""
        if self.gpio_mismatches:
            return "; ".join(self.gpio_mismatches)
        return f"All {len(EXPECTED_HOST_GPIOS)} GPIOs verified"

    @property
    def voltage_details(self) -> str:
        """Voltage mismatches, or the measured rails."""
        if self.voltage_mismatches:
            return "; ".join(self.voltage_mismatches)
        return f"PP5000={self.adc('pp5000_mv')}mV, Host={self.adc('host_vbus_mv')}mV, CHG={self.adc('chg_vbus_mv')}mV"

    def summary_fields(self) -> str:
        """Summary fragment shared by the Host-only and Host re-plug results."""
        return (
            f"HWRev={self.detected_hwrev} ({_pass_fail(self.hwrev_ok)}), "
            f"Temp={self.temp_c:.1f}°C ({_pass_fail(self.temp_ok)}), "
            f"GPIOs={'PASS' if self.gpios_ok else 'FAIL (' + self.gpio_details + ')'}, "
            f"Voltages={'PASS' if self.voltages_ok else 'FAIL (' + self.voltage_details + ')'}"
        )

    def log_details(self) -> str:
        """Log section shared by the Host-only and Host re-plug logs."""
        return (
            f"HWRev: {self.detected_hwrev} (Expected: {EXPECTED_HWREV})\n"
            f"Temperature: {self.temp_c:.2f} 'C (Limits: {TEMP_MIN_C:g}-{TEMP_MAX_C:g} 'C)\n"
            f"GPIO Status: {_pass_fail(self.gpios_ok)}\n"
            f"GPIO Details: {self.gpio_details}\n"
            f"Voltages: PP5000={self.adc('pp5000_mv')} mV, Host VBUS={self.adc('host_vbus_mv')} mV, "
            f"CHG VBUS={self.adc('chg_vbus_mv')} mV\n"
            f"Voltage Details: {self.voltage_details}\n\n"
        )

    def log_raw_outputs(self) -> str:
        """Raw console outputs section of the Host logs."""
        return (
            f"GPIO Get:\n{self.gpios_raw}\n"
            f"HWRev Output:\n{self.hwrev_raw}\n"
            f"Temp Output:\n{self.temp_raw}"
        )


def _run_host_console_checks(port_node: str, read_charger: bool) -> _HostConsoleChecks:
    """Runs the Host console checks (tests, [charger PDC], ADC, hwrev, temp, GPIOs) in one session."""
    checks = _HostConsoleChecks()
    if not os.path.exists(port_node):
        checks.session_error = f"Port {port_node} does not exist"
        return checks

    tester = MauiConsoleTester(port_node=port_node, timeout=CONSOLE_TIMEOUT_SEC)
    try:
        with port_lock(port_node), open_serial_port(
            port_node, tester.baudrate, timeout=tester.timeout
        ) as ser:
            checks.console_results = tester.run_all_tests(ser=ser)
            if read_charger:
                checks.pdc_chg = tester.read_pdc_port_status(port="charger", ser=ser)
            checks.adc_status = tester.read_adc_status(ser=ser)
            checks.hwrev_ok, checks.detected_hwrev, checks.hwrev_raw = (
                tester.read_hwrev(ser=ser)
            )
            checks.temp_ok, checks.temp_c, checks.temp_raw = tester.read_temperature(
                ser=ser
            )
            checks.gpios_ok, _, checks.gpio_mismatches, checks.gpios_raw = (
                tester.read_and_verify_gpios(ser=ser)
            )
            checks.voltages_ok, checks.voltage_mismatches = verify_host_adc_voltages(
                checks.adc_status
            )
    except ConsolePortBusyError as e:
        checks.session_error = str(e)
        checks.port_busy = True
    except (serial.SerialException, OSError, ValueError) as e:
        logging.warning("Console session failed on %s: %s", port_node, e)
        checks.session_error = str(e)
    return checks


@dataclasses.dataclass
class _HostDutReadings:  # pylint: disable=too-many-instance-attributes
    """Everything measured during one Host+DUT stage run."""

    pdc_dut: Dict[str, Any]
    pdc_chg: Dict[str, Any]
    adc_status: Dict[str, Any]
    adb_ok: bool = False
    h2h_5gbps: bool = False
    dut_speed_str: str = "Unknown"
    adb_logs: str = ""
    ccd_nodes: Dict[str, str] = dataclasses.field(default_factory=dict)
    ec_uart_ok: bool = False
    ec_version_str: str = "No CCD device detected"
    gsc_uart_ok: bool = False
    gsc_version_str: str = "No CCD device detected"

    @property
    def dut_orientation(self) -> str:
        """DUT SBU orientation from `adc status`."""
        return self.adc_status.get("orientation", "Unknown")

    @property
    def voltage_v(self) -> float:
        """Negotiated DUT PD voltage."""
        return self.pdc_dut.get("voltage_v", 0.0)

    @property
    def current_a(self) -> float:
        """Negotiated DUT PD current (operating, else max)."""
        return self.pdc_dut.get("op_current_a", 0.0) or self.pdc_dut.get(
            "max_current_a", 0.0
        )

    @property
    def measured_vbus_mv(self) -> int:
        """Measured DUT VBUS."""
        return self.pdc_dut.get("measured_vbus_mv", 0)

    @property
    def calculated_power_w(self) -> float:
        """Negotiated DUT power."""
        return round(self.voltage_v * self.current_a, 2)

    @property
    def ccd_summary(self) -> str:
        """CCD console summary text."""
        return f"CCD Consoles: EC={_pass_fail(self.ec_uart_ok)}, GSC={_pass_fail(self.gsc_uart_ok)}"


def _empty_pdc_reading() -> Dict[str, Any]:
    """Default PDC status used when the console cannot be read."""
    return {
        "success": False,
        "plug_present": False,
        "voltage_v": 0.0,
        "max_current_a": 0.0,
        "op_current_a": 0.0,
        "measured_vbus_mv": 0,
        "orientation": "Unknown",
        "raw_output": "",
    }


class MauiStation2Servicer(
    maui_manufacturing_pb2_grpc.MauiStation2FunctionalTestingServicer
):
    """Implementation of the Maui functional testing gRPC service (Steps 2 and 4)."""

    def __init__(self):
        super().__init__()
        logging.info("MauiStation2Servicer initialized.")

    @staticmethod
    def _check_host_usb_topology() -> Tuple[bool, bool, bool, bool]:
        """Checks presence of Hub2, Hub3, FTDI (0x6002), and H2H (18d1:506d/5084) using sysfs scanning."""
        hub_usb2_ok = False
        hub_usb3_ok = False
        ftdi_ok = False
        h2h_ok = False

        for dev in maui_common.scan_usb_devices():
            vid_pid = (dev.vid, dev.pid)
            if vid_pid in maui_common.HUB_USB2_VID_PIDS:
                hub_usb2_ok = True
            elif vid_pid in maui_common.HUB_USB3_VID_PIDS:
                hub_usb3_ok = True
            elif vid_pid == (maui_common.FTDI_VID, maui_common.FTDI_PID_PROGRAMMED):
                ftdi_ok = True
            elif (
                dev.vid == maui_common.GOOGLE_VID
                and dev.pid in maui_common.GOOGLE_ADB_PIDS
            ):
                h2h_ok = True

        return hub_usb2_ok, hub_usb3_ok, ftdi_ok, h2h_ok

    def test_host_replug(
        self,
        request: maui_manufacturing_pb2.MauiHostReplugTestRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiHostReplugTestResponse:
        """Verifies that the Maui board re-enumerates and flips CC polarity after a 180° cable flip."""
        serial_no = _resolve_serial(request.serial_number)
        dev_port = _resolve_console_port(request.device_port or "")
        logging.info(
            "Running Host Re-plug & 180° Flip Verification for serial: %s (port: %s)",
            serial_no,
            dev_port,
        )

        hub_usb2_ok, hub_usb3_ok, ftdi_ok, h2h_ok = self._check_host_usb_topology()
        checks = _run_host_console_checks(dev_port, read_charger=False)
        if checks.session_error:
            mcu_version_ok, mcu_msg = False, checks.session_error
        else:
            mcu_version_ok, mcu_msg = checks.test_result(
                MauiConsoleTester.MCU_VERSION_TEST
            )

        curr_orientation = checks.adc("host_orientation", "Unknown")
        cc1_mv = checks.adc("host_cc1_mv")
        cc2_mv = checks.adc("host_cc2_mv")

        # Verify Host Cable 180° Flip Polarity via USB_HOST_CC1 / USB_HOST_CC2 ADC voltages
        prev_orientation = request.previous_orientation or _read_previous_orientation(
            serial_no, HOST_ONLY_LOG, _HOST_ORIENTATION_RE
        )
        host_flip_ok = bool(
            prev_orientation
            and not curr_orientation.startswith("Unknown")
            and is_host_cc_flipped(prev_orientation, curr_orientation)
        )

        if not prev_orientation:
            flip_status_str = (
                f"FAIL (No baseline host orientation from the Host-Only test; cannot verify 180° flip, "
                f"current: {curr_orientation})"
            )
        elif not host_flip_ok:
            flip_status_str = (
                f"FAIL (Host cable polarity not inverted: still {curr_orientation}, "
                f"expected flip from {prev_orientation})"
            )
        else:
            flip_status_str = f"PASS ({prev_orientation} -> {curr_orientation})"

        all_passed = (
            hub_usb2_ok
            and hub_usb3_ok
            and ftdi_ok
            and h2h_ok
            and mcu_version_ok
            and host_flip_ok
            and checks.hwrev_ok
            and checks.temp_ok
            and checks.gpios_ok
            and checks.voltages_ok
        )

        status_msg = (
            f"Host 180° Flip: {flip_status_str} | Hub2={hub_usb2_ok}, Hub3={hub_usb3_ok}, "
            f"FTDI={ftdi_ok}, H2H={h2h_ok}, Console={mcu_msg}, "
            + checks.summary_fields()
        )

        write_device_log(
            serial_no,
            "host_replug_test",
            f"ALL_PASSED={all_passed}\n{status_msg}\nHost CC1: {cc1_mv} mV, Host CC2: {cc2_mv} mV\n"
            f"Orientation: {curr_orientation}\n"
            + checks.log_details()
            + f"ADC Raw:\n{checks.adc('raw_output', '')}\n"
            + checks.log_raw_outputs(),
            status=_status(all_passed),
        )

        return maui_manufacturing_pb2.MauiHostReplugTestResponse(
            all_passed=all_passed,
            hub_usb2_detected=hub_usb2_ok,
            hub_usb3_detected=hub_usb3_ok,
            ftdi_detected=ftdi_ok,
            h2h_detected=h2h_ok,
            message=status_msg,
            host_flip_ok=host_flip_ok,
            host_orientation=curr_orientation,
            cc1_mv=cc1_mv,
            cc2_mv=cc2_mv,
            gpios_ok=checks.gpios_ok,
            hwrev_ok=checks.hwrev_ok,
            detected_hwrev=checks.detected_hwrev,
            temp_ok=checks.temp_ok,
            temperature_c=checks.temp_c,
            voltages_ok=checks.voltages_ok,
            pp5000_mv=checks.adc("pp5000_mv"),
            host_vbus_mv=checks.adc("host_vbus_mv"),
            chg_vbus_mv=checks.adc("chg_vbus_mv"),
            gpio_details=checks.gpio_details,
            voltage_details=checks.voltage_details,
        )

    def test_reset_button(
        self,
        request: maui_manufacturing_pb2.MauiResetButtonTestRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiResetButtonTestResponse:
        """Two-step RESET button verification with tight timeouts."""
        action = request.action or "check_initial_uptime"
        dev_port = _resolve_console_port(request.device_port or "")

        if action in RESET_BASELINE_ACTIONS:
            return self._capture_reset_baseline(dev_port)
        if action in RESET_VERIFY_ACTIONS:
            return self._verify_reset(
                request.serial_number, dev_port, int(request.initial_uptime_ms or 0)
            )
        return maui_manufacturing_pb2.MauiResetButtonTestResponse(
            success=False,
            message=f"Unknown reset action '{action}'",
        )

    @staticmethod
    def _capture_reset_baseline(
        dev_port: str,
    ) -> maui_manufacturing_pb2.MauiResetButtonTestResponse:
        """Step A: reads the baseline kernel uptime before the operator presses RESET."""
        logging.info("Step A: Querying baseline kernel uptime on %s...", dev_port)
        try:
            with port_lock(dev_port):
                uptime_ms, raw_out = MauiConsoleTester(
                    port_node=dev_port, timeout=CONSOLE_TIMEOUT_SEC
                ).read_kernel_uptime()
        except ConsolePortBusyError as e:
            return maui_manufacturing_pb2.MauiResetButtonTestResponse(
                success=False,
                current_uptime_ms=0,
                uptime_sufficient=False,
                reset_verified=False,
                message=str(e),
                raw_output="",
            )

        if uptime_ms < 0:
            msg = f"Failed to read kernel uptime from MCU console on {dev_port}. Output: {raw_out}"
            return maui_manufacturing_pb2.MauiResetButtonTestResponse(
                success=False,
                current_uptime_ms=0,
                uptime_sufficient=False,
                reset_verified=False,
                message=msg,
                raw_output=raw_out,
            )

        min_ms = MIN_RESET_BASELINE_UPTIME_MS
        uptime_sufficient = uptime_ms >= min_ms
        if uptime_sufficient:
            msg = (
                f"Baseline uptime captured: {uptime_ms} ms (>= {min_ms} ms). "
                "Operator: Please press the RESET button on the Maui board now."
            )
        else:
            msg = (
                f"Initial uptime was only {uptime_ms} ms (< {min_ms} ms). "
                f"Please wait for board to be powered on >= {min_ms // 1000}s."
            )

        return maui_manufacturing_pb2.MauiResetButtonTestResponse(
            success=uptime_sufficient,
            current_uptime_ms=uptime_ms,
            uptime_sufficient=uptime_sufficient,
            reset_verified=False,
            message=msg,
            raw_output=raw_out,
        )

    @staticmethod
    def _verify_reset(
        serial_no: str, dev_port: str, initial_uptime_ms: int
    ) -> maui_manufacturing_pb2.MauiResetButtonTestResponse:
        """Step B: verifies the board rebooted (uptime smaller than the baseline)."""
        logging.info(
            "Step B: Verifying post-reset reboot (baseline was %d ms)...",
            initial_uptime_ms,
        )
        time.sleep(RESET_SETTLE_DELAY_SEC)

        post_uptime_ms = -1
        raw_out = ""
        busy_error = ""
        for attempt in range(1, RESET_POLL_ATTEMPTS + 1):
            if not os.path.exists(dev_port):
                dev_port = _resolve_console_port("", default=dev_port)

            try:
                with port_lock(dev_port):
                    read_uptime, raw_out = MauiConsoleTester(
                        port_node=dev_port, timeout=CONSOLE_TIMEOUT_SEC
                    ).read_kernel_uptime()
            except ConsolePortBusyError as e:
                busy_error = str(e)
                break
            logging.info(
                "[RESET Poll %d/%d] Read kernel uptime: %d ms (Initial was %d ms)",
                attempt,
                RESET_POLL_ATTEMPTS,
                read_uptime,
                initial_uptime_ms,
            )

            # Polling is only to ensure we successfully read at least once (read_uptime >= 0).
            # Once successfully polled, if the read uptime is longer than expected (>= initial),
            # fail immediately without polling further.
            if read_uptime >= 0:
                post_uptime_ms = read_uptime
                break

            time.sleep(RESET_POLL_INTERVAL_SEC)

        reset_verified = 0 <= post_uptime_ms < initial_uptime_ms
        uptime_line = (
            f"Initial Uptime: {initial_uptime_ms} ms, Post Uptime: {post_uptime_ms} ms"
        )
        if reset_verified:
            msg = (
                f"RESET Button Test PASSED! Uptime successfully reset from {initial_uptime_ms} ms "
                f"to {post_uptime_ms} ms."
            )
            write_device_log(
                serial_no,
                "reset_button_test",
                f"ALL_PASSED=true\nRESULT: PASSED\n{msg}\n{uptime_line}\n\nRaw Output:\n{raw_out}",
                status=LogStatus.PASSED,
            )
            return maui_manufacturing_pb2.MauiResetButtonTestResponse(
                success=True,
                current_uptime_ms=post_uptime_ms,
                uptime_sufficient=True,
                reset_verified=True,
                message=msg,
                raw_output=raw_out,
            )

        if busy_error:
            err_msg = f"RESET Button Test FAILED: {busy_error}"
        elif post_uptime_ms >= 0:
            err_msg = (
                f"RESET Button Test FAILED: Post-reset uptime ({post_uptime_ms} ms) was not smaller than "
                f"initial uptime ({initial_uptime_ms} ms). Board did not reboot."
            )
        else:
            err_msg = (
                "RESET Button Test FAILED: Failed to read kernel uptime from MCU console after "
                f"{RESET_POLL_ATTEMPTS} poll attempts."
            )
        write_device_log(
            serial_no,
            "reset_button_test",
            f"ALL_PASSED=false\nRESULT: FAILED\n{err_msg}\n{uptime_line}\n\nRaw Output:\n{raw_out}",
            status=LogStatus.FAILED,
        )
        return maui_manufacturing_pb2.MauiResetButtonTestResponse(
            success=False,
            current_uptime_ms=max(post_uptime_ms, 0),
            uptime_sufficient=False,
            reset_verified=False,
            message=err_msg,
            raw_output=raw_out,
        )

    def test_bsl_button(
        self,
        request: maui_manufacturing_pb2.MauiBslButtonTestRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiBslButtonTestResponse:
        """Automated BSL button test sampling ADC status over console (expected BSL >= 2500mV)."""
        dev_port = _resolve_console_port(request.device_port or "")
        tester = MauiConsoleTester(port_node=dev_port, timeout=CONSOLE_TIMEOUT_SEC)
        duration = request.max_sample_duration_sec or BSL_SAMPLE_DURATION_SEC
        threshold = BSL_PRESSED_THRESHOLD_MV
        logging.info(
            "Sampling BSL button ADC status on %s for %.1fs...", dev_port, duration
        )

        busy_error = ""
        try:
            with port_lock(dev_port):
                passed, peak_mv, raw_adc = tester.sample_bsl_adc(
                    max_duration_sec=duration,
                    interval_sec=BSL_SAMPLE_INTERVAL_SEC,
                    threshold_mv=threshold,
                )
        except ConsolePortBusyError as e:
            busy_error = str(e)
            passed, peak_mv, raw_adc = False, 0, busy_error
        serial_no = request.serial_number or ""
        if not serial_no:
            for dev in maui_common.scan_usb_devices():
                if dev.serial:
                    serial_no = dev.serial
                    break

        if busy_error:
            msg = f"BSL Button Test FAILED: {busy_error}"
        elif passed:
            msg = f"BSL Button Test PASSED! Peak ADC reading: {peak_mv} mV (>= {threshold} mV threshold)."
        else:
            msg = f"BSL Button Test FAILED. Peak ADC reading was {peak_mv} mV (expected >= {threshold} mV)."

        write_device_log(
            serial_no,
            "bsl_button_test",
            f"ALL_PASSED={passed}\nRESULT: {'PASSED' if passed else 'FAILED'}\n{msg}\n"
            f"Peak ADC: {peak_mv} mV (Threshold >= {threshold} mV)\n\nRaw ADC Samples:\n{raw_adc}",
            status=_status(passed),
        )

        return maui_manufacturing_pb2.MauiBslButtonTestResponse(
            success=passed,
            peak_bsl_mv=peak_mv,
            threshold_mv=threshold,
            message=msg,
            raw_adc_table=raw_adc,
        )

    def run_host_only_tests(
        self,
        request: maui_manufacturing_pb2.MauiHostOnlyTestRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiHostOnlyTestResponse:
        """Runs all verification tests requiring ONLY the host connected."""
        serial_no = _resolve_serial(request.serial_number)
        dev_port = _resolve_console_port(request.device_port or "")
        logging.info(
            "Running Host-Only Verification for serial %s (port: %s)...",
            serial_no,
            dev_port,
        )

        # 1. Host USB Enumeration Checks
        hub_usb2_ok, hub_usb3_ok, ftdi_ok, h2h_ok = self._check_host_usb_topology()
        logging.info(
            "Host USB checks -> Hub2: %s, Hub3: %s, FTDI (0x6002): %s, H2H: %s",
            hub_usb2_ok,
            hub_usb3_ok,
            ftdi_ok,
            h2h_ok,
        )

        # 2. Maui Console Tester Commands in a single serial session
        checks = _run_host_console_checks(dev_port, read_charger=True)
        mcu_version_ok, mcu_version_msg = checks.test_result(
            MauiConsoleTester.MCU_VERSION_TEST
        )
        if checks.port_busy:
            mcu_version_msg = checks.session_error
        pdc_info_ok, pdc_info_msg = checks.test_result(MauiConsoleTester.PDC_INFO_TEST)
        pdc_appconfig_ver, pdc_fw_ver = checks.pdc_versions

        chg_adc_vbus_mv = checks.adc("chg_vbus_mv")
        pdc_chg = checks.pdc_chg
        chg_plug = pdc_chg.get("plug_present", False)
        chg_v = pdc_chg.get("voltage_v", 0.0)
        chg_mv = pdc_chg.get("measured_vbus_mv", 0)
        chg_contract = pdc_chg.get("contract_type", "None")

        # Charger port check uses only the parsed PDC / ADC fields.
        pdc_charger_ok = bool(
            chg_adc_vbus_mv >= HOST_CHARGER_MIN_MV
            or chg_plug
            or chg_v >= HOST_CHARGER_MIN_V
            or chg_mv >= HOST_CHARGER_MIN_MV
            or chg_contract not in ("None", "Unknown", "")
        )
        logging.info(
            "PDC Charger Check -> ADC VBUS: %dmV, Plug: %s, Voltage: %.2fV, Measured: %dmV, Contract: %s, OK: %s",
            chg_adc_vbus_mv,
            chg_plug,
            chg_v,
            chg_mv,
            chg_contract,
            pdc_charger_ok,
        )

        host_orientation = checks.adc("host_orientation", "Unknown")
        cc1_mv = checks.adc("host_cc1_mv")
        cc2_mv = checks.adc("host_cc2_mv")

        all_passed = (
            hub_usb2_ok
            and hub_usb3_ok
            and ftdi_ok
            and h2h_ok
            and mcu_version_ok
            and pdc_info_ok
            and pdc_charger_ok
            and checks.hwrev_ok
            and checks.temp_ok
            and checks.gpios_ok
            and checks.voltages_ok
        )
        charger_str = (
            f"PASS ({chg_v or (chg_adc_vbus_mv / 1000.0)}V)"
            if pdc_charger_ok
            else "FAIL (No Charger)"
        )
        summary = (
            f"USB: Hub2={hub_usb2_ok}, Hub3={hub_usb3_ok}, FTDI={ftdi_ok}, H2H={h2h_ok} | "
            f"Console: Version={mcu_version_msg}, PDC={pdc_info_msg}, "
            + checks.summary_fields()
            + f", Host CC={host_orientation}, Charger Port={charger_str}"
        )

        write_device_log(
            serial_no,
            "host_only_tests",
            f"ALL_PASSED={all_passed}\n{summary}\n"
            f"Host Orientation: {host_orientation}\nHost CC1: {cc1_mv} mV, Host CC2: {cc2_mv} mV\n"
            + checks.log_details()
            + f"PDC Charger:\n{pdc_chg.get('raw_output', '')}\n"
            + f"ADC Status:\n{checks.adc('raw_output', '')}\n"
            + checks.log_raw_outputs(),
            status=_status(all_passed),
        )

        return maui_manufacturing_pb2.MauiHostOnlyTestResponse(
            all_passed=all_passed,
            hub_usb2_detected=hub_usb2_ok,
            hub_usb3_detected=hub_usb3_ok,
            ftdi_detected=ftdi_ok,
            h2h_detected=h2h_ok,
            mcu_version_ok=mcu_version_ok,
            pdc_info_ok=pdc_info_ok,
            mcu_version_str=mcu_version_msg,
            pdc_appconfig_ver=pdc_appconfig_ver,
            pdc_fw_ver=pdc_fw_ver,
            summary=summary,
            pdc_charger_ok=pdc_charger_ok,
            charger_voltage_v=chg_v,
            charger_measured_vbus_mv=chg_mv,
            host_orientation=host_orientation,
            cc1_mv=cc1_mv,
            cc2_mv=cc2_mv,
            gpios_ok=checks.gpios_ok,
            hwrev_ok=checks.hwrev_ok,
            detected_hwrev=checks.detected_hwrev,
            temp_ok=checks.temp_ok,
            temperature_c=checks.temp_c,
            voltages_ok=checks.voltages_ok,
            pp5000_mv=checks.adc("pp5000_mv"),
            host_vbus_mv=checks.adc("host_vbus_mv"),
            chg_vbus_mv=chg_adc_vbus_mv,
            gpio_details=checks.gpio_details,
            voltage_details=checks.voltage_details,
        )

    def _find_ccd_serial_nodes(self) -> Dict[str, str]:
        """Scans for Google CCD device (Cr50 / Ti50) and maps ap, ec, gsc serial ports."""
        ccd_nodes: Dict[str, str] = {}
        candidate_ttys = maui_common.sorted_tty_nodes()
        ftdi_port = ""

        # 1. Check if a CCD device (18d1:5014, 18d1:504a, 18d1:5066, 18d1:520d) exists on the USB bus
        ccd_present = False
        for dev in maui_common.scan_usb_devices():
            if dev.vid == maui_common.FTDI_VID and dev.dev_node:
                ftdi_port = dev.dev_node
            if dev.vid == maui_common.GOOGLE_VID and dev.pid in maui_common.CCD_PIDS:
                ccd_present = True
                logging.info(
                    "Found Google CCD device on USB bus: VID=18d1, PID=%04x (%s)",
                    dev.pid,
                    dev.product,
                )
            elif dev.product and any(
                c in dev.product.lower()
                for c in ("cr50", "ti50", "ccd", "google converter")
            ):
                ccd_present = True
                logging.info(
                    "Found Google CCD device on USB bus by product: %s", dev.product
                )

        # 2. Precise sysfs mapping: /sys/class/tty/ttyUSBn/device -> <usb dev>/<interface>/ttyUSBn
        for tty_dir in maui_common.sorted_tty_nodes("/sys/class/tty/ttyUSB*"):
            dev_link = os.path.join(tty_dir, "device")
            if not os.path.exists(dev_link):
                continue
            iface_dir = os.path.dirname(os.path.realpath(dev_link))
            usb_dir = (
                iface_dir
                if os.path.exists(os.path.join(iface_dir, "idVendor"))
                else os.path.dirname(iface_dir)
            )
            vid = maui_common.read_sysfs_file(os.path.join(usb_dir, "idVendor")).lower()
            pid = maui_common.read_sysfs_file(
                os.path.join(usb_dir, "idProduct")
            ).lower()
            if vid != f"{maui_common.GOOGLE_VID:04x}" or pid not in (
                f"{p:04x}" for p in maui_common.CCD_PIDS
            ):
                continue
            ccd_present = True
            m_iface = re.search(r":\d+\.(\d+)$", os.path.basename(iface_dir))
            console = (
                CCD_INTERFACE_CONSOLES.get(int(m_iface.group(1))) if m_iface else None
            )
            if console:
                ccd_nodes[console] = f"/dev/{os.path.basename(tty_dir)}"

        # 3. Fallback: If CCD device is present on USB bus, map remaining non-FTDI ttyUSB ports
        # Standard order: 1st = GSC (Cr50), 2nd = AP (Android shell), 3rd = EC
        if ccd_present and len(ccd_nodes) < 3:
            if ftdi_port:
                non_ftdi = [t for t in candidate_ttys if t != ftdi_port]
            else:
                # Default ttyUSB0 is FTDI MCU console
                non_ftdi = candidate_ttys[1:]
            for console, node in zip(("gsc", "ap", "ec"), non_ftdi):
                ccd_nodes[console] = ccd_nodes.get(console) or node

        return ccd_nodes

    @staticmethod
    def _query_adb_device(serial_number: str, logs: List[str]) -> Tuple[bool, str]:
        """Checks ADB communication with the DUT. Returns (adb_ok, target_serial)."""
        try:
            devices_found = maui_common.list_adb_devices(
                accepted_states=("device", "recovery", "sideload"),
                timeout=ADB_TIMEOUT_SEC,
            )
            target_serial = serial_number or (devices_found[0] if devices_found else "")
            if target_serial and target_serial in devices_found:
                state_proc = subprocess.run(
                    ["adb", "-s", target_serial, "get-state"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=ADB_TIMEOUT_SEC,
                    check=False,
                )
                if state_proc.returncode == 0 and "device" in state_proc.stdout:
                    logs.append(
                        f"ADB device '{target_serial}' state: {state_proc.stdout.strip()}"
                    )
                    return True, target_serial
                return False, target_serial
            if devices_found:
                logs.append(f"ADB device '{devices_found[0]}' found")
                return True, devices_found[0]
            return False, target_serial
        except (OSError, subprocess.SubprocessError) as e:
            logs.append(f"ADB query error/absent: {e}")
            return False, ""

    @staticmethod
    def _query_dut_h2h_speed(target_serial: str, logs: List[str]) -> Optional[int]:
        """Link speed (Mbps) of the Maui H2H endpoint as seen by the DUT over ADB, None if unknown."""
        dut_cmd = (
            "for d in /sys/bus/usb/devices/*; do "
            "if [ -f $d/idVendor ]; then "
            "echo $(cat $d/idVendor 2>/dev/null):$(cat $d/idProduct 2>/dev/null) speed=$(cat $d/speed 2>/dev/null); "
            "fi; done"
        )
        try:
            shell_proc = subprocess.run(
                ["adb", "-s", target_serial, "shell", dut_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=ADB_SHELL_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logs.append(f"DUT ADB shell query error: {e}")
            return None
        dut_out = shell_proc.stdout or ""
        logs.append(f"DUT USB enumeration: {dut_out.strip()}")

        speeds = [
            int(m.group(3))
            for m in _DUT_USB_LINE_RE.finditer(dut_out)
            if (int(m.group(1), 16), int(m.group(2), 16))
            in maui_common.MAUI_ADB_VID_PIDS
        ]
        return max(speeds) if speeds else None

    @staticmethod
    def _host_h2h_speed() -> Optional[int]:
        """Link speed (Mbps) of the Maui H2H endpoint on the host side, None if not found."""
        speeds = []
        for dev in maui_common.scan_usb_devices():
            if (dev.vid, dev.pid) in maui_common.MAUI_ADB_VID_PIDS and str(
                dev.speed
            ).strip().isdigit():
                speeds.append(int(str(dev.speed).strip()))
        return max(speeds) if speeds else None

    def _check_adb_and_dut_speed(
        self, serial_number: str = ""
    ) -> Tuple[bool, bool, str, str]:
        """Verifies ADB communication with DUT and the H2H SuperSpeed link with fast timeouts.

        DUT-side and host-side speeds are evaluated separately, matching only the
        exact Maui H2H/ADB VID:PIDs. The DUT-side speed decides when it is known;
        the host-side speed is only used when the DUT side cannot be queried.

        Returns (adb_ok, h2h_5gbps, dut_speed_str, log_summary).
        """
        logs: List[str] = []
        adb_ok, target_serial = self._query_adb_device(serial_number, logs)
        dut_speed = (
            self._query_dut_h2h_speed(target_serial, logs)
            if adb_ok and target_serial
            else None
        )
        host_speed = self._host_h2h_speed()
        logs.append(
            f"H2H link speed: DUT side={_format_speed(dut_speed) if dut_speed is not None else 'Unknown'}, "
            f"Host side={_format_speed(host_speed) if host_speed is not None else 'Unknown'}"
        )

        if dut_speed is not None:
            h2h_5gbps = dut_speed >= SUPERSPEED_MBPS
            dut_speed_str = _format_speed(dut_speed)
        else:
            h2h_5gbps = host_speed is not None and host_speed >= SUPERSPEED_MBPS
            dut_speed_str = "5000M (SuperSpeed)" if h2h_5gbps else "Unknown"
        return adb_ok, h2h_5gbps, dut_speed_str, "\n".join(logs)

    @staticmethod
    def _find_h2h_serial() -> str:
        """Serial of the first Maui H2H/ADB or FTDI endpoint on the host (never the DUT's CCD)."""
        for dev in maui_common.scan_usb_devices():
            is_maui = (
                dev.vid,
                dev.pid,
            ) in maui_common.MAUI_ADB_VID_PIDS or maui_common.is_maui_ftdi_endpoint(
                dev.vid, dev.pid
            )
            if is_maui and dev.serial:
                return dev.serial
        return ""

    @staticmethod
    def _read_dut_console(
        dev_port: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """Reads PDC DUT/charger status and ADC in one fast MCU console session."""
        pdc_dut = _empty_pdc_reading()
        pdc_chg = _empty_pdc_reading()
        adc_status: Dict[str, Any] = {
            "chg_vbus_mv": 0,
            "dut_vbus_mv": 0,
            "sbu1_mv": 0,
            "sbu2_mv": 0,
            "bsl_mv": 0,
            "orientation": "Unknown",
            "raw_output": "",
        }
        if not os.path.exists(dev_port):
            pdc_dut["error"] = pdc_chg["error"] = (
                f"Device port {dev_port} does not exist"
            )
            return pdc_dut, pdc_chg, adc_status

        tester = MauiConsoleTester(port_node=dev_port, timeout=CONSOLE_TIMEOUT_SEC)
        try:
            with port_lock(dev_port), open_serial_port(
                dev_port, tester.baudrate, timeout=tester.timeout
            ) as ser:
                pdc_dut = tester.read_pdc_port_status(port="dut", ser=ser)
                pdc_chg = tester.read_pdc_port_status(port="charger", ser=ser)
                adc_status = tester.read_adc_status(ser=ser)
        except ConsolePortBusyError as e:
            pdc_dut["error"] = pdc_chg["error"] = str(e)
            pdc_dut["port_busy"] = True
        except (serial.SerialException, OSError, ValueError) as e:
            logging.warning("MCU Serial port %s inaccessible: %s", dev_port, e)
            pdc_dut["error"] = pdc_chg["error"] = str(e)
        return pdc_dut, pdc_chg, adc_status

    def _check_ccd_consoles(self, readings: _HostDutReadings) -> None:
        """CCD consoles check (EC & GSC query version; AP query response disabled)."""
        readings.ccd_nodes = self._find_ccd_serial_nodes()
        logging.info("Discovered CCD Serial Nodes: %s", readings.ccd_nodes)
        for console in ("ec", "gsc"):
            node = readings.ccd_nodes.get(console)
            if not node:
                continue
            ok, version = MauiConsoleTester.query_ccd_console(
                node,
                "version",
                timeout=CCD_QUERY_TIMEOUT_SEC,
                first_byte_timeout=CCD_FIRST_BYTE_TIMEOUT_SEC,
            )
            setattr(readings, f"{console}_uart_ok", ok)
            setattr(readings, f"{console}_version_str", version)

    def run_host_dut_tests(  # pylint: disable=too-many-locals
        self,
        request: maui_manufacturing_pb2.MauiHostDutTestRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiHostDutTestResponse:
        """Runs the Host+DUT verification stages with guaranteed fast non-blocking return."""
        expected_serial = request.expected_serial or request.serial_number
        stage = request.stage or STAGE_ALL
        real_h2h_serial = self._find_h2h_serial()

        expected_serial_norm = (expected_serial or "").strip().upper()
        real_serial_norm = (real_h2h_serial or "").strip().upper()
        detected_serial = real_h2h_serial or expected_serial or "UNKNOWN"
        # An empty expected serial accepts any real Maui H2H serial.
        serial_matched = bool(
            real_serial_norm
            and (not expected_serial_norm or real_serial_norm == expected_serial_norm)
        )

        # 1. Open a single fast serial session for MCU console operations
        dev_port = _resolve_console_port("")
        pdc_dut, pdc_chg, adc_status = self._read_dut_console(dev_port)
        readings = _HostDutReadings(
            pdc_dut=pdc_dut, pdc_chg=pdc_chg, adc_status=adc_status
        )

        # 2. Fast H2H SuperSpeed & ADB verification (<1.0s)
        (
            readings.adb_ok,
            readings.h2h_5gbps,
            readings.dut_speed_str,
            readings.adb_logs,
        ) = self._check_adb_and_dut_speed(detected_serial)
        logging.info(
            "ADB check -> ADB_OK: %s, 5Gbps SuperSpeed: %s (Speed: %s)",
            readings.adb_ok,
            readings.h2h_5gbps,
            readings.dut_speed_str,
        )

        # 3. CCD Consoles Check (EC & GSC query version; AP query response disabled)
        self._check_ccd_consoles(readings)
        ap_node = readings.ccd_nodes.get("ap")
        ap_version_str = (
            f"Node: {ap_node} (response check disabled)"
            if ap_node
            else "No AP node detected"
        )

        # 4. Charger / DUT power evaluation (parsed PDC and ADC fields)
        chg_adc_vbus_mv = adc_status.get("chg_vbus_mv", 0)
        dut_plug_present = pdc_dut.get("plug_present", False)
        chg_raw_lower = pdc_chg.get("raw_output", "").lower()
        has_active_charger = _has_active_pd_charger(pdc_chg, chg_adc_vbus_mv)

        # Charger Port Detection: Must be connected for Stage 1 & 2 (20V PD charger or active contract)
        charger_connected_ok = has_active_charger or (
            "plug present: yes" in chg_raw_lower
            and "active contract:  none" not in chg_raw_lower
            and "active contract: none" not in chg_raw_lower
        )
        # DUT PD Contract Verification: Must negotiate 20V with charger connected
        dut_20v_contract_ok = bool(
            dut_plug_present
            and (
                readings.voltage_v >= DUT_20V_MIN_V
                or readings.measured_vbus_mv >= DUT_20V_MIN_MV
                or adc_status.get("dut_vbus_mv", 0) >= DUT_20V_MIN_MV
            )
        )
        # Charger Disconnection Check: Must be completely unplugged for Stage 3
        # Note: When external charger is disconnected, CHG rail may show ~4.3V (4331 mV) due to
        # internal diode drop/back-feed from DUT 4.8V battery sink rail, but PDC reports
        # 'Plug Present: No' & 'Active Contract: None', and voltage is not a 20V PD contract (<12V).
        is_chg_unplugged = not pdc_chg.get("plug_present", False) or _no_contract(
            pdc_chg.get("contract_type", "None")
        )
        charger_disconnected_ok = bool(is_chg_unplugged and not has_active_charger)

        # 5. SBU 180° Cable Flip Polarity Verification via 'adc status' (SBU1_DET vs SBU2_DET)
        prev_orientation = request.previous_orientation
        if not prev_orientation and stage in STAGE_SBU_FLIP:
            prev_orientation = _read_previous_orientation(
                detected_serial, STAGE1_LOG, _SBU_ORIENTATION_RE
            )
        dut_orientation = readings.dut_orientation
        polarity_inverted = bool(
            prev_orientation
            and not dut_orientation.startswith("Unknown")
            and is_sbu_flipped(prev_orientation, dut_orientation)
        )
        sbu_flip_ok = bool(dut_plug_present and polarity_inverted)

        ccd_ok = readings.ec_uart_ok and readings.gsc_uart_ok
        serial_str = f"PASS ({detected_serial})" if serial_matched else "FAIL"
        h2h_str = f"PASS ({readings.dut_speed_str})" if readings.h2h_5gbps else "FAIL"
        v, a, w = readings.voltage_v, readings.current_a, readings.calculated_power_w

        # Evaluate stage specific pass/fail criteria
        # User flow: 1st & 2nd with charger connected (20V contract), 3rd with charger disconnected
        if stage in STAGE_WITH_CHARGER:
            all_passed = (
                charger_connected_ok
                and dut_20v_contract_ok
                and readings.h2h_5gbps
                and serial_matched
                and ccd_ok
            )
            summary = (
                f"[Stage 1: With Charger] Charger Port: "
                f"{'PASS (Connected)' if charger_connected_ok else 'FAIL (No Charger)'} | "
                f"DUT 20V Contract: {v}V @ {a}A ({w}W, VBUS={readings.measured_vbus_mv}mV, "
                f"{'PASS' if dut_20v_contract_ok else 'FAIL (Expected 20V)'}) | "
                f"DUT Orientation: {dut_orientation} | "
                f"Serial Match: {serial_str} | "
                f"H2H 5Gbps: {h2h_str} | {readings.ccd_summary}"
            )
        elif stage in STAGE_SBU_FLIP:
            all_passed = (
                sbu_flip_ok
                and charger_connected_ok
                and dut_20v_contract_ok
                and readings.h2h_5gbps
                and ccd_ok
            )
            if not prev_orientation:
                sbu_status_str = (
                    f"FAIL (No baseline SBU orientation from Stage 1; cannot verify 180° flip, "
                    f"current: {dut_orientation})"
                )
            elif not polarity_inverted:
                sbu_status_str = f"FAIL (Polarity not inverted: still {dut_orientation}, expected flip from {prev_orientation})"
            else:
                sbu_status_str = f"PASS ({prev_orientation} -> {dut_orientation})"
            summary = (
                f"[Stage 2: 180° SBU Flip] SBU Orientation: {sbu_status_str} | "
                f"Charger Port: {_pass_fail(charger_connected_ok)} | "
                f"DUT 20V Contract: {'PASS (' + str(v) + 'V)' if dut_20v_contract_ok else 'FAIL'} | "
                f"H2H Re-connect: {h2h_str} | {readings.ccd_summary}"
            )
        elif stage in STAGE_NO_CHARGER:
            all_passed = (
                charger_disconnected_ok
                and readings.h2h_5gbps
                and serial_matched
                and ccd_ok
            )
            summary = (
                f"[Stage 3: Charger Disconnected] Charger Disconnected: "
                f"{'PASS' if charger_disconnected_ok else 'FAIL (Plug Detected)'} | "
                f"H2H 5Gbps (Battery Sink): {h2h_str} | "
                f"Serial Match: {serial_str} | {readings.ccd_summary}"
            )
        else:
            all_passed = (
                charger_connected_ok
                and dut_20v_contract_ok
                and readings.h2h_5gbps
                and serial_matched
                and ccd_ok
                and sbu_flip_ok
            )
            summary = (
                f"H2H 5Gbps: {h2h_str} | "
                f"Serial Match: {serial_str} | "
                f"Charger Port: {_pass_fail(charger_connected_ok)} | "
                f"DUT 20V Contract: {v}V @ {a}A ({w}W, {_pass_fail(dut_20v_contract_ok)}) | "
                f"{readings.ccd_summary} | "
                f"SBU Orientation: {dut_orientation} ({_pass_fail(sbu_flip_ok)})"
            )

        if pdc_dut.get("port_busy"):
            all_passed = False
            summary = f"{pdc_dut['error']} | {summary}"

        safe_stage = re.sub(r"[^a-zA-Z0-9_-]", "", stage)
        write_device_log(
            detected_serial,
            f"host_dut_{safe_stage}" if stage != STAGE_ALL else "host_dut_tests",
            f"ALL_PASSED={all_passed}\n{summary}\n\nCCD Nodes: {readings.ccd_nodes}\n"
            f"EC: {readings.ec_version_str}\nGSC: {readings.gsc_version_str}\n"
            f"ADB Output:\n{readings.adb_logs}\n"
            f"PDC DUT:\n{pdc_dut.get('raw_output', '')}\nPDC CHG:\n{pdc_chg.get('raw_output', '')}\n"
            f"ADC Status:\n{adc_status.get('raw_output', '')}",
            status=_status(all_passed),
        )

        return maui_manufacturing_pb2.MauiHostDutTestResponse(
            all_passed=all_passed,
            h2h_speed_5gbps=readings.h2h_5gbps,
            h2h_serial=detected_serial,
            h2h_serial_matched=serial_matched,
            negotiated_voltage_v=v,
            negotiated_current_a=a,
            pd_contract_ok=dut_20v_contract_ok,
            ap_uart_ok=bool(ap_node),
            ec_uart_ok=readings.ec_uart_ok,
            gsc_uart_ok=readings.gsc_uart_ok,
            sbu_flip_ok=sbu_flip_ok,
            summary=summary,
            measured_vbus_mv=readings.measured_vbus_mv,
            ap_version_str=ap_version_str,
            ec_version_str=readings.ec_version_str,
            gsc_version_str=readings.gsc_version_str,
            dut_orientation=dut_orientation,
            adb_connected=readings.adb_ok,
            adb_dut_speed=readings.dut_speed_str,
            charger_disconnected_ok=charger_disconnected_ok,
            charger_connected_ok=charger_connected_ok,
            calculated_power_w=w,
        )
