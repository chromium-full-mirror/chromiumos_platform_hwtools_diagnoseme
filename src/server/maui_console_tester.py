#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Extensible Modular Console Testing Framework for Maui devices."""

import contextlib
import dataclasses
import logging
import os
import re
import threading
import time
from typing import Any
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple

import serial


DEFAULT_BAUDRATE = 115200
DEFAULT_READ_TIMEOUT_SEC = 0.5
DEFAULT_WRITE_TIMEOUT_SEC = 0.3

# Acceptance limits of the console checks (shared by checks and log text).
EXPECTED_HWREV = 2
TEMP_MIN_C = 20.0
TEMP_MAX_C = 65.0
# Minimum kernel uptime before the RESET button test can verify a reboot.
MIN_RESET_BASELINE_UPTIME_MS = 7000
# BSL button pressed threshold on the BSL ADC channel.
BSL_PRESSED_THRESHOLD_MV = 2500
BSL_SAMPLE_DURATION_SEC = 3.0
BSL_SAMPLE_INTERVAL_SEC = 0.15
# Host CC orientation threshold matching Maui firmware CONFIG_HOST_MUX_HIGH_THRESH.
HOST_CC_HIGH_THRESHOLD_MV = 200
HOST_CC_MIN_DELTA_MV = 300
SBU_HIGH_THRESHOLD_MV = 1500
SBU_LOW_THRESHOLD_MV = 1000
SBU_MIN_DELTA_MV = 800

# How long an RPC waits for another RPC using the same serial port before it
# fails with a 'console port busy' error.
PORT_LOCK_TIMEOUT_SEC = 10.0

_MAUI_PROMPT_SUFFIXES = (b"maui$ ", b"\nmaui$ ", b"\r\nmaui$ ", b"\n$ ", b"\r\n$ ")
_CCD_PROMPT_SUFFIXES = (b"> ", b"$ ", b"# ")

_port_locks: Dict[str, threading.RLock] = {}
_port_locks_guard = threading.Lock()

# One console lock for every MCU console user: port_lock (tests and cards,
# serialized per port) and the exclusive claims of long operations (Maui v1.1:
# MCU and PDC flashing through the FTDI console; Maui v1.2:
# maui_v12_device.claim_consoles for MCU App / full image / H2H / PDC
# flashing, serial provisioning, ...). A claimed port fails port_lock at once
# (a short claim, the unit status query, is waited for), and a port in
# port_lock cannot be claimed. Claims are not bound to a thread (async RPCs
# release them from other threads), so they are kept apart from the
# per-thread RLocks of port_lock.
ALL_PORTS = "*"
# realpath of a port (or ALL_PORTS) -> (what holds it, short claim).
_port_claims: Dict[str, Tuple[str, bool]] = {}
# realpath of a port -> number of port_lock holders.
_port_users: Dict[str, int] = {}
_port_claims_cond = threading.Condition()


class ConsolePortBusyError(RuntimeError):
    """Another RPC held the serial console port for longer than the lock timeout."""

    def __init__(self, port_node: str, timeout: float, holder: str = ""):
        if holder:
            message = (
                f"Console port {port_node} busy: {holder} is running. Retry when "
                "it has finished."
            )
        else:
            message = (
                f"Console port {port_node} busy: another test is still using it "
                f"(waited {timeout:.0f}s). Retry when the other test has finished."
            )
        super().__init__(message)
        self.port_node = port_node


def _port_key(port_node: str) -> str:
    return port_node if port_node == ALL_PORTS else os.path.realpath(port_node or "")


def claim_ports(port_nodes: List[str], what: str, short: bool = False) -> str:
    """Claims |port_nodes| (ALL_PORTS: every port) exclusively for |what|.

    Returns '' when claimed, else what uses one of the ports (port_lock
    holders: 'a console test on <port>'; other claims: their |what|). Never
    blocks. A |short| claim makes port_lock wait instead of failing. Release
    with release_ports().
    """
    keys = [_port_key(p) for p in port_nodes if p]
    with _port_claims_cond:
        for key in keys:
            if ALL_PORTS in _port_claims:
                return _port_claims[ALL_PORTS][0]
            if key == ALL_PORTS:
                if _port_claims:
                    return next(iter(_port_claims.values()))[0]
                users = [k for k, n in _port_users.items() if n]
                if users:
                    return f"a console test on {users[0]}"
            elif key in _port_claims:
                return _port_claims[key][0]
            elif _port_users.get(key):
                return f"a console test on {key}"
        for key in keys:
            _port_claims[key] = (what, short)
    return ""


def release_ports(port_nodes: List[str]) -> None:
    """Ends a claim of claim_ports()."""
    with _port_claims_cond:
        for port_node in port_nodes:
            if port_node:
                _port_claims.pop(_port_key(port_node), None)
        _port_claims_cond.notify_all()


def port_claim(port_node: str) -> str:
    """Returns what claims |port_node| exclusively, '' if nothing does."""
    with _port_claims_cond:
        claim = _port_claims.get(ALL_PORTS) or _port_claims.get(_port_key(port_node))
        return claim[0] if claim else ""


def _enter_port(key: str, port_node: str, deadline: float, timeout: float) -> None:
    """Registers a port_lock holder of |key|; waits for short claims only."""
    with _port_claims_cond:
        while True:
            claim = _port_claims.get(ALL_PORTS) or _port_claims.get(key)
            if not claim:
                _port_users[key] = _port_users.get(key, 0) + 1
                return
            what, short = claim
            remaining = deadline - time.monotonic()
            if not short or remaining <= 0:
                logging.error("Console port %s is used by %s.", port_node, what)
                raise ConsolePortBusyError(port_node, timeout, holder=what)
            _port_claims_cond.wait(remaining)


def _leave_port(key: str) -> None:
    with _port_claims_cond:
        count = _port_users.get(key, 0) - 1
        if count > 0:
            _port_users[key] = count
        else:
            _port_users.pop(key, None)
        _port_claims_cond.notify_all()


def open_serial_port(
    port_node: str,
    baudrate: int = DEFAULT_BAUDRATE,
    timeout: float = DEFAULT_READ_TIMEOUT_SEC,
    write_timeout: float = DEFAULT_WRITE_TIMEOUT_SEC,
) -> serial.Serial:
    """Opens a serial port with strict read/write timeouts and flow control disabled to prevent blocking."""
    return serial.Serial(
        port=port_node,
        baudrate=baudrate,
        timeout=timeout,
        write_timeout=write_timeout,
        rtscts=False,
        dsrdtr=False,
        xonxoff=False,
    )


@contextlib.contextmanager
def port_lock(port_node: str, timeout: float = PORT_LOCK_TIMEOUT_SEC) -> Iterator[None]:
    """Serializes access to one serial port across overlapping RPCs (re-entrant per thread).

    Also respects the exclusive claims of claim_ports(): a claimed port fails
    at once (a short claim is waited for), and claim_ports() refuses a port
    held here.

    Raises:
      ConsolePortBusyError: the port stayed busy for longer than `timeout`, or
        an exclusive operation holds it; the caller fails its RPC/card instead
        of using the port unlocked.
    """
    key = os.path.realpath(port_node or "")
    deadline = time.monotonic() + timeout
    with _port_locks_guard:
        lock = _port_locks.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=timeout):
        logging.error(
            "Console port %s still busy after %.0fs; failing the request.",
            port_node,
            timeout,
        )
        raise ConsolePortBusyError(port_node, timeout)
    try:
        # Exclusive claims (see claim_ports) win over port_lock.
        _enter_port(key, port_node, deadline, timeout)
        try:
            yield
        finally:
            _leave_port(key)
    finally:
        lock.release()


# Expected GPIO values for Host-Only Verification (both basic check and after 180 flip)
EXPECTED_HOST_GPIOS: Dict[str, int] = {
    "h2h_reset_l": 0,
    "usb2_mux_hub_oe_l": 1,
    "usb2_mux_hub_sel": 0,
    "usb3_mux_dut_oe_r_l": 1,
    "usb3_mux_dut_sel_r": 0,
    "usb2_mux_dut_oe_r_l": 1,
    "usb2_mux_dut_sel_r": 0,
    "pp5000_cutoff": 0,
    "usb3_mux_hub_oe_l": 1,
    "usb3_mux_hub_sel": 0,
    "usb3_mux_host_oe_r_l": 1,
    "hub_reset_l": 0,
    "hub_ss_up_status": 1,
    "hub_ss_suspend": 1,
    "pdc_hi_priority_irq_l": 0,
    "pp1200_pg_od": 1,
    "pp3300_pg_od": 1,
    "pp1100_pg_od": 1,
    "hub_hs_suspend": 1,
}

# Host ADC rail limits: (name, adc key, nominal mV); accepted range is nominal +-10%.
_HOST_ADC_RAILS = (
    ("PP5000", "pp5000_mv", 2500),
    ("PP5000_USB_HOST_VBUS", "host_vbus_mv", 2500),
    ("PPVAR_USB_CHG_VBUS", "chg_vbus_mv", 5000),
)
_HOST_ADC_TOLERANCE = 0.10


def verify_host_adc_voltages(adc_status: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Verifies PP5000 (+-10% from 2500mV), PP5000_USB_HOST_VBUS (+-10% from 2500mV),
    and PPVAR_USB_CHG_VBUS (+-10% from 5000mV).
    """
    mismatches: List[str] = []
    for rail_name, key, nominal_mv in _HOST_ADC_RAILS:
        value = adc_status.get(key, 0)
        low = int(nominal_mv * (1 - _HOST_ADC_TOLERANCE))
        high = int(nominal_mv * (1 + _HOST_ADC_TOLERANCE))
        if not low <= value <= high:
            mismatches.append(
                f"{rail_name}: {value} mV outside [{low}, {high}] mV ({nominal_mv} mV +-10%)"
            )
    return len(mismatches) == 0, mismatches


class ConsoleTestCase:
    """Base class for an extensible console test case."""

    def __init__(self, name: str, command: str):
        self.name = name
        self.command = command

    def evaluate(self, output: str) -> Tuple[bool, str]:
        """Evaluates the console output; returns (passed, message)."""
        raise NotImplementedError("Subclasses must implement evaluate()")


class RegexMatchTestCase(ConsoleTestCase):
    """Test case validating output against a regex pattern."""

    def __init__(self, name: str, command: str, pattern: str, description: str):
        super().__init__(name, command)
        self.pattern = re.compile(pattern)
        self.description = description

    def evaluate(self, output: str) -> Tuple[bool, str]:
        if self.pattern.search(output):
            return True, f"Passed: Found expected pattern for {self.name}"
        return (
            False,
            f"Failed: Pattern '{self.pattern.pattern}' not found in output of '{self.command}'",
        )


UNKNOWN_VERSION = "UNKNOWN"


class PdcInfoTestCase(ConsoleTestCase):
    """Test case validating PDC info fields (App Config Version & FW Version)."""

    def __init__(
        self,
        name: str,
        expected_app_config_ver: Optional[str] = None,
        expected_fw_ver: Optional[str] = None,
    ):
        super().__init__(name, "pdc info")
        self.expected_app_config_ver = expected_app_config_ver
        self.expected_fw_ver = expected_fw_ver

    @staticmethod
    def parse_versions(output: str) -> Tuple[str, str]:
        """Returns (app_config_version, fw_version) read from `pdc info`, UNKNOWN if absent."""
        app_config_match = re.search(
            r"App Config Version:\s*([0-9a-fA-F]+)", output or ""
        )
        fw_ver_match = re.search(r"FW Version:\s*([0-9a-fA-F]+)", output or "")
        return (
            app_config_match.group(1) if app_config_match else UNKNOWN_VERSION,
            fw_ver_match.group(1) if fw_ver_match else UNKNOWN_VERSION,
        )

    def evaluate(self, output: str) -> Tuple[bool, str]:
        app_config_ver, fw_ver = self.parse_versions(output)

        errors = []
        if UNKNOWN_VERSION in (app_config_ver, fw_ver):
            errors.append(
                f"PDC versions could not be read (AppConfig: {app_config_ver}, FW: {fw_ver})"
            )
        if (
            self.expected_app_config_ver
            and app_config_ver.lower() != self.expected_app_config_ver.lower()
        ):
            errors.append(
                f"AppConfig ver mismatch: expected {self.expected_app_config_ver}, got {app_config_ver}"
            )
        if self.expected_fw_ver and fw_ver.lower() != self.expected_fw_ver.lower():
            errors.append(
                f"PDC FW ver mismatch: expected {self.expected_fw_ver}, got {fw_ver}"
            )

        if errors:
            return False, "; ".join(errors)
        return True, f"PDC Info verified (AppConfig: {app_config_ver}, FW: {fw_ver})"


@dataclasses.dataclass
class ConsoleTestResult:
    """Result of one registered console test case."""

    success: bool
    message: str
    output: str = ""


def _parse_mv(output: str, channel: str) -> Optional[int]:
    """Parses `[ <channel> ] = <n> mV` from `adc status` output."""
    match = re.search(
        r"\[\s*" + re.escape(channel) + r"\s*\]\s*=\s*(\d+)\s*mV", output, re.IGNORECASE
    )
    return int(match.group(1)) if match else None


@dataclasses.dataclass(frozen=True)
class _OrientationLimits:
    """Thresholds deciding which of two lines (e.g. SBU1/SBU2, CC1/CC2) is active."""

    labels: Tuple[str, str]
    high_mv: int
    low_mv: int
    min_delta_mv: int


_SBU_LIMITS = _OrientationLimits(
    ("SBU1", "SBU2"), SBU_HIGH_THRESHOLD_MV, SBU_LOW_THRESHOLD_MV, SBU_MIN_DELTA_MV
)
# Host CC orientation check matching Maui firmware CONFIG_HOST_MUX_HIGH_THRESH (200 mV)
_HOST_CC_LIMITS = _OrientationLimits(
    ("CC1", "CC2"),
    HOST_CC_HIGH_THRESHOLD_MV,
    HOST_CC_HIGH_THRESHOLD_MV,
    HOST_CC_MIN_DELTA_MV,
)


def _classify_orientation(a_mv: int, b_mv: int, limits: _OrientationLimits) -> str:
    """Classifies which of two lines (e.g. SBU1/SBU2, CC1/CC2) is active."""
    first, second = limits.labels
    if a_mv >= limits.high_mv and b_mv < limits.low_mv:
        name = first
    elif b_mv >= limits.high_mv and a_mv < limits.low_mv:
        name = second
    elif a_mv > b_mv + limits.min_delta_mv:
        name = first
    elif b_mv > a_mv + limits.min_delta_mv:
        name = second
    else:
        name = "Unknown"
    return f"{name} ({a_mv}mV / {b_mv}mV)"


class MauiConsoleTester:
    """Extensible framework for running console commands and validating responses.

    Configuration constants can easily be updated for future test suites.
    """

    EXPECTED_MCU_VERSION_PATTERN = r"Maui version\s+[\w.-]+"
    # Expected PDC versions; None means "any version, as long as it can be read".
    EXPECTED_PDC_APP_CONFIG_VER: Optional[str] = None
    EXPECTED_PDC_FW_VER: Optional[str] = None

    MCU_VERSION_TEST = "MCU Version Check"
    PDC_INFO_TEST = "PDC Info Check"

    def __init__(
        self,
        port_node: str,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_READ_TIMEOUT_SEC,
    ):
        self.port_node = port_node
        self.baudrate = baudrate
        self.timeout = timeout
        self.test_cases: List[ConsoleTestCase] = []
        self._register_default_tests()

    def _register_default_tests(self):
        """Registers default test cases for the Step 1 / Step 2 quick test."""
        self.test_cases.append(
            RegexMatchTestCase(
                name=self.MCU_VERSION_TEST,
                command="version",
                pattern=self.EXPECTED_MCU_VERSION_PATTERN,
                description="Validates MCU version string format from 'version' command",
            )
        )
        self.test_cases.append(
            PdcInfoTestCase(
                name=self.PDC_INFO_TEST,
                expected_app_config_ver=self.EXPECTED_PDC_APP_CONFIG_VER,
                expected_fw_ver=self.EXPECTED_PDC_FW_VER,
            )
        )

    def add_test_case(self, test_case: ConsoleTestCase):
        """Allows adding new test cases dynamically for future test suites."""
        self.test_cases.append(test_case)

    @contextlib.contextmanager
    def _session(self, ser: Optional[serial.Serial]) -> Iterator[serial.Serial]:
        """Yields `ser` if given, otherwise a freshly opened port that is closed afterwards."""
        if ser is not None:
            yield ser
        else:
            with open_serial_port(
                self.port_node, self.baudrate, timeout=self.timeout
            ) as s:
                yield s

    def send_command(
        self,
        ser: serial.Serial,
        command: str,
        timeout: float = 1.0,
        first_byte_timeout: float = 0.8,
        inter_byte_timeout: float = 0.08,
    ) -> str:
        """Sends a single command to the MCU console and reads response with minimal latency.

        Returns the raw response, or "" (logged as a warning) when the command
        could not be written.
        """
        try:
            # Drain any stale characters left over in the line
            while ser.in_waiting > 0:
                ser.read(ser.in_waiting)
                time.sleep(0.005)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            ser.write(f"{command.strip()}\r\n".encode("utf-8"))
            ser.flush()
        except (serial.SerialException, OSError) as e:
            logging.warning(
                "Failed to write command '%s' to serial port: %s", command, e
            )
            return ""

        response = bytearray()
        start_time = time.time()
        last_data_time = start_time
        cmd_bytes = command.strip().encode("utf-8")
        saw_echo = False

        while (time.time() - start_time) < timeout:
            try:
                in_waiting = ser.in_waiting
                data = ser.read(in_waiting) if in_waiting > 0 else b""
            except (serial.SerialException, OSError) as e:
                logging.warning("Serial read failed during '%s': %s", command, e)
                break

            if data:
                response.extend(data)
                last_data_time = time.time()
                if not saw_echo and cmd_bytes in response:
                    saw_echo = True
                # Only terminate if we've seen output/echo and line ends with prompt
                if (saw_echo or len(response) > len(cmd_bytes)) and response.endswith(
                    _MAUI_PROMPT_SUFFIXES
                ):
                    break
            elif in_waiting <= 0:
                # Fast exit if no initial byte arrives within first_byte_timeout
                if not response and (time.time() - start_time) > first_byte_timeout:
                    break
                # Fast exit once stream pauses after receiving response
                if (
                    response
                    and (time.time() - last_data_time) > inter_byte_timeout
                    and (saw_echo or (time.time() - start_time) > 0.3)
                ):
                    break
                time.sleep(0.01)

        return response.decode("utf-8", errors="ignore")

    def run_all_tests(
        self,
        ser: Optional[serial.Serial] = None,
        first_byte_timeout: float = 0.8,
    ) -> Dict[str, ConsoleTestResult]:
        """Executes all registered test cases over the serial port (or the given session)."""
        results: Dict[str, ConsoleTestResult] = {}
        try:
            with self._session(ser) as s:
                for test in self.test_cases:
                    output = self.send_command(
                        s, test.command, first_byte_timeout=first_byte_timeout
                    )
                    success, message = test.evaluate(output)
                    results[test.name] = ConsoleTestResult(success, message, output)
        except (serial.SerialException, OSError, ValueError) as e:
            logging.warning(
                "Failed to connect to MCU console at %s: %s", self.port_node, e
            )
            results["SerialConnection"] = ConsoleTestResult(
                False, f"Could not open serial port {self.port_node}: {str(e)}"
            )
        return results

    def read_kernel_uptime(
        self, ser: Optional[serial.Serial] = None
    ) -> Tuple[int, str]:
        """Sends 'kernel uptime' and parses milliseconds. Returns (uptime_ms, raw_output)."""
        try:
            with self._session(ser) as s:
                output = self.send_command(s, "kernel uptime")
        except (serial.SerialException, OSError, ValueError) as e:
            logging.warning("Failed to read kernel uptime on %s: %s", self.port_node, e)
            return -1, f"Error: {e}"

        match = re.search(r"Uptime:\s*(\d+)\s*ms", output, re.IGNORECASE)
        if match:
            return int(match.group(1)), output
        return -1, output

    def ensure_min_uptime(
        self, min_uptime_ms: int = MIN_RESET_BASELINE_UPTIME_MS
    ) -> Tuple[int, str]:
        """Ensures device has been powered on for at least min_uptime_ms (7s) before reset test."""
        uptime_ms, output = self.read_kernel_uptime()
        if 0 <= uptime_ms < min_uptime_ms:
            wait_time_sec = (min_uptime_ms - uptime_ms) / 1000.0 + 0.5
            logging.info(
                "Uptime is %d ms (<%d ms). Waiting %.1f seconds...",
                uptime_ms,
                min_uptime_ms,
                wait_time_sec,
            )
            time.sleep(wait_time_sec)
            uptime_ms, output = self.read_kernel_uptime()
        return uptime_ms, output

    def sample_bsl_adc(
        self,
        max_duration_sec: float = BSL_SAMPLE_DURATION_SEC,
        interval_sec: float = BSL_SAMPLE_INTERVAL_SEC,
        threshold_mv: int = BSL_PRESSED_THRESHOLD_MV,
    ) -> Tuple[bool, int, str]:
        """Repeatedly samples 'adc status' every interval_sec to detect BSL button press (>=2500mV).

        Returns (success, peak_bsl_mv, last_raw_output).
        """
        peak_bsl_mv = 0
        last_output = ""
        start_time = time.time()
        try:
            with open_serial_port(
                self.port_node, self.baudrate, timeout=self.timeout
            ) as ser:
                while (time.time() - start_time) < max_duration_sec:
                    last_output = self.send_command(
                        ser,
                        "adc status",
                        first_byte_timeout=0.15,
                        inter_byte_timeout=0.03,
                    )
                    bsl_mv = _parse_mv(last_output, "BSL")
                    if bsl_mv is not None:
                        peak_bsl_mv = max(peak_bsl_mv, bsl_mv)
                        logging.info(
                            "BSL ADC sample: %d mV (Peak: %d mV)", bsl_mv, peak_bsl_mv
                        )
                        if bsl_mv >= threshold_mv:
                            return True, peak_bsl_mv, last_output
                    time.sleep(interval_sec)
        except (serial.SerialException, OSError, ValueError) as e:
            logging.warning("Failed to sample BSL ADC on %s: %s", self.port_node, e)
            return False, 0, f"Error: {e}"

        return peak_bsl_mv >= threshold_mv, peak_bsl_mv, last_output

    def read_pdc_port_status(
        self, port: str = "dut", ser: Optional[serial.Serial] = None
    ) -> Dict[str, Any]:
        """Sends 'pdc status <port>' and parses plug, orientation, active contract, and VBUS readings.

        Returns dictionary containing parsed status fields.
        """
        res: Dict[str, Any] = {
            "success": False,
            "plug_present": False,
            "orientation": "Unknown",
            "power_role": "Unknown",
            "data_role": "Unknown",
            "contract_type": "None",
            "voltage_v": 0.0,
            "max_current_a": 0.0,
            "op_current_a": 0.0,
            "measured_vbus_mv": 0,
            "raw_output": "",
            "error": "",
        }
        try:
            with self._session(ser) as s:
                output = self.send_command(
                    s, f"pdc status {port}", timeout=1.2, first_byte_timeout=0.8
                )
            res["raw_output"] = output
            self._parse_pdc_status(output, port, res)
        except (serial.SerialException, OSError, ValueError) as e:
            logging.warning(
                "Failed to read PDC port status for '%s' on %s: %s",
                port,
                self.port_node,
                e,
            )
            res["error"] = str(e)
            return res

        res["success"] = True
        return res

    @staticmethod
    def _parse_pdc_status(output: str, port: str, res: Dict[str, Any]) -> None:
        """Parses `pdc status <port>` output into `res` (in place)."""
        # Isolate the relevant section for the requested port if multiple outputs exist
        port_name = "charger" if port.lower() in ("charger", "chg", "2") else "dut"
        relevant_text = output
        m_hdr = re.search(
            r"===\s*PDC Status:\s*" + re.escape(port_name) + r"[^=]*===(.*?)(?====|\Z)",
            output,
            re.IGNORECASE | re.DOTALL,
        )
        if m_hdr:
            relevant_text = m_hdr.group(1)
            res["raw_output"] = relevant_text.strip()

        def field(pattern: str) -> Optional[str]:
            match = re.search(pattern, relevant_text, re.IGNORECASE)
            return match.group(1).strip() if match else None

        # 1. Plug detection
        m_plug_no = re.search(r"Plug Present:\s*No", relevant_text, re.IGNORECASE)
        m_plug_yes = re.search(
            r"Plug Present:\s*(?:Yes|Connected|Attached)", relevant_text, re.IGNORECASE
        )
        res["plug_present"] = bool(m_plug_yes) and not m_plug_no

        for key, label in (
            ("orientation", "Orientation"),
            ("power_role", "Power Role"),
            ("data_role", "Data Role"),
        ):
            value = field(label + r":\s*([^\r\n]+)")
            if value is not None:
                res[key] = value

        contract_type = field(r"Type:\s*([^\r\n]+)")
        if contract_type is not None:
            res["contract_type"] = contract_type
        else:
            contract_val = field(r"Active Contract:\s*([^\r\n]+)")
            if contract_val is not None and contract_val.lower() not in (
                "none",
                "unknown",
                "",
            ):
                res["contract_type"] = contract_val
                if not m_plug_no:
                    res["plug_present"] = True

        voltage = field(r"Voltage:\s*([\d.]+)\s*V")
        if voltage is not None:
            res["voltage_v"] = float(voltage)

        max_current = field(r"Max Current:\s*([\d.]+)\s*A")
        if max_current is not None:
            res["max_current_a"] = float(max_current)

        op_current = field(r"Op Current:\s*([\d.]+)\s*A")
        if op_current is not None:
            res["op_current_a"] = float(op_current)
        elif res["max_current_a"] > 0:
            res["op_current_a"] = res["max_current_a"]

        vbus_mv = field(r"Measured VBUS:\s*[\d.]+\s*V\s*\((\d+)\s*mV\)")
        if vbus_mv is not None:
            res["measured_vbus_mv"] = int(vbus_mv)
        else:
            vbus_v = field(r"(?:Measured\s+)?VBUS:\s*([\d.]+)\s*V")
            if vbus_v is not None:
                res["measured_vbus_mv"] = int(float(vbus_v) * 1000)

    def read_adc_status(self, ser: Optional[serial.Serial] = None) -> Dict[str, Any]:
        """Sends 'adc status' to MCU console and reads all analog voltage channels.

        Returns:
          dict with keys: 'orientation', 'sbu1_mv', 'sbu2_mv', 'chg_vbus_mv', 'dut_vbus_mv',
                          'bsl_mv', 'host_cc1_mv', 'host_cc2_mv', 'host_vbus_mv',
                          'host_orientation', 'raw_output'
        """
        res: Dict[str, Any] = {
            "orientation": "Unknown",
            "sbu1_mv": 0,
            "sbu2_mv": 0,
            "chg_vbus_mv": 0,
            "dut_vbus_mv": 0,
            "bsl_mv": 0,
            "host_cc1_mv": 0,
            "host_cc2_mv": 0,
            "host_vbus_mv": 0,
            "pp5000_mv": 0,
            "host_orientation": "Unknown",
            "raw_output": "",
        }
        try:
            with self._session(ser) as s:
                output = self.send_command(
                    s, "adc status", timeout=1.0, first_byte_timeout=0.5
                )
        except (serial.SerialException, OSError, ValueError) as e:
            logging.warning("Failed to read ADC status on %s: %s", self.port_node, e)
            res["orientation"] = f"Error: {e}"
            res["host_orientation"] = f"Error: {e}"
            res["raw_output"] = str(e)
            return res

        res["raw_output"] = output
        for key, channel in (
            ("host_cc1_mv", "USB_HOST_CC1"),
            ("host_cc2_mv", "USB_HOST_CC2"),
            ("host_vbus_mv", "PP5000_USB_HOST_VBUS"),
            ("pp5000_mv", "PP5000"),
            ("sbu1_mv", "SBU1_DET"),
            ("sbu2_mv", "SBU2_DET"),
            ("chg_vbus_mv", "PPVAR_USB_CHG_VBUS"),
            ("dut_vbus_mv", "PPVAR_USB_DUT_VBUS"),
            ("bsl_mv", "BSL"),
        ):
            value = _parse_mv(output, channel)
            if value is not None:
                res[key] = value

        # SBU orientation check
        res["orientation"] = _classify_orientation(
            res["sbu1_mv"], res["sbu2_mv"], _SBU_LIMITS
        )
        # Host CC orientation check matching Maui firmware CONFIG_HOST_MUX_HIGH_THRESH (200 mV)
        res["host_orientation"] = _classify_orientation(
            res["host_cc1_mv"], res["host_cc2_mv"], _HOST_CC_LIMITS
        )
        return res

    def read_adc_sbu_orientation(
        self, ser: Optional[serial.Serial] = None
    ) -> Tuple[str, int, int, str]:
        """Sends 'adc status' to MCU console and reads SBU1_DET and SBU2_DET voltages.

        Returns:
          (orientation_str, sbu1_mv, sbu2_mv, raw_output)
        """
        res = self.read_adc_status(ser=ser)
        return res["orientation"], res["sbu1_mv"], res["sbu2_mv"], res["raw_output"]

    def read_adc_host_orientation(
        self, ser: Optional[serial.Serial] = None
    ) -> Tuple[str, int, int, str]:
        """Sends 'adc status' to MCU console and reads USB_HOST_CC1 and USB_HOST_CC2 voltages.

        Returns:
          (host_orientation_str, cc1_mv, cc2_mv, raw_output)
        """
        res = self.read_adc_status(ser=ser)
        return (
            res["host_orientation"],
            res["host_cc1_mv"],
            res["host_cc2_mv"],
            res["raw_output"],
        )

    def read_hwrev(self, ser: Optional[serial.Serial] = None) -> Tuple[bool, int, str]:
        """Sends 'hwrev' to console and checks that hardware revision is EXPECTED_HWREV (2).

        Returns:
          Tuple of (is_valid, detected_rev, raw_output)
        """
        try:
            with self._session(ser) as s:
                output = self.send_command(
                    s, "hwrev", timeout=1.0, first_byte_timeout=0.5
                )
        except (serial.SerialException, OSError, ValueError) as e:
            logging.warning("Failed to read hwrev on %s: %s", self.port_node, e)
            return False, -1, str(e)

        m = re.search(r"Hardware\s+revision:\s*(\d+)", output, re.IGNORECASE)
        if m:
            rev = int(m.group(1))
            return rev == EXPECTED_HWREV, rev, output
        return False, -1, output

    def read_temperature(
        self, ser: Optional[serial.Serial] = None
    ) -> Tuple[bool, float, str]:
        """Sends 'temp' to console and verifies temperature is between 20.0 and 65.0 'C.

        Returns:
          Tuple of (is_within_limits, temp_c, raw_output)
        """
        try:
            with self._session(ser) as s:
                output = self.send_command(
                    s, "temp", timeout=1.0, first_byte_timeout=0.5
                )
            m = re.search(r"Temperature\s*=\s*([\d.]+)\s*'?C", output, re.IGNORECASE)
            temp_c = float(m.group(1)) if m else None
        except (serial.SerialException, OSError, ValueError) as e:
            logging.warning("Failed to read temperature on %s: %s", self.port_node, e)
            return False, -1.0, str(e)

        if temp_c is not None:
            return (TEMP_MIN_C <= temp_c <= TEMP_MAX_C), temp_c, output
        return False, -1.0, output

    def read_and_verify_gpios(
        self,
        expected_gpios: Optional[Dict[str, int]] = None,
        ser: Optional[serial.Serial] = None,
    ) -> Tuple[bool, Dict[str, int], List[str], str]:
        """Sends 'gpio get' to console and validates GPIO levels against expected values.

        Returns:
          Tuple of (all_matched, parsed_gpios, mismatches, raw_output)
        """
        expected = expected_gpios if expected_gpios is not None else EXPECTED_HOST_GPIOS
        try:
            with self._session(ser) as s:
                output = self.send_command(
                    s, "gpio get", timeout=1.0, first_byte_timeout=0.5
                )
        except (serial.SerialException, OSError, ValueError) as e:
            logging.warning("Failed to read GPIOs on %s: %s", self.port_node, e)
            return False, {}, [str(e)], str(e)

        parsed_gpios: Dict[str, int] = {}
        for m in re.finditer(
            r"\[\s*([a-zA-Z0-9_]+)\s*\]\s*(?:[IOio])\s*=\s*([01])", output
        ):
            parsed_gpios[m.group(1).strip()] = int(m.group(2))

        mismatches: List[str] = []
        for name, exp_val in expected.items():
            if name not in parsed_gpios:
                mismatches.append(f"{name}: missing in 'gpio get'")
            elif parsed_gpios[name] != exp_val:
                mismatches.append(
                    f"{name}: expected {exp_val}, got {parsed_gpios[name]}"
                )

        return len(mismatches) == 0, parsed_gpios, mismatches, output

    def ensure_dut_data_and_power(
        self, ser: Optional[serial.Serial] = None
    ) -> Tuple[bool, str]:
        """Ensures DUT power and data multiplexers are enabled (power_dut on && data_dut on).

        Returns (ok, log) where ok is True only if the console answered both commands.
        """
        logs = []
        try:
            with self._session(ser) as s:
                out_p = self.send_command(s, "power_dut on", first_byte_timeout=0.15)
                logs.append(f"power_dut on: {out_p.strip()}")
                out_d = self.send_command(s, "data_dut on", first_byte_timeout=0.15)
                logs.append(f"data_dut on: {out_d.strip()}")
        except (serial.SerialException, OSError, ValueError) as e:
            logging.warning(
                "Failed to enable DUT power/data muxes on %s: %s", self.port_node, e
            )
            return False, str(e)
        answered = bool(out_p.strip() and out_d.strip())
        if not answered:
            logging.warning(
                "MCU console on %s did not answer power_dut/data_dut commands.",
                self.port_node,
            )
        return answered, "; ".join(logs)

    @staticmethod
    def query_ccd_console(
        port_node: str,
        command: str = "version",
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = 0.3,
        first_byte_timeout: float = 0.15,
    ) -> Tuple[bool, str]:
        """Safely queries CCD console with fast non-blocking return."""
        if not port_node or not os.path.exists(port_node):
            return False, "Port does not exist"

        try:
            with port_lock(port_node), open_serial_port(
                port_node, baudrate, timeout=timeout, write_timeout=0.2
            ) as ser:
                ser.reset_input_buffer()
                ser.write(f"\r\n{command}\r\n".encode("utf-8"))
                response = MauiConsoleTester._read_ccd_response(
                    ser, timeout, first_byte_timeout
                )
        except ConsolePortBusyError as e:
            return False, str(e)
        except (serial.SerialException, OSError, ValueError) as e:
            logging.warning("Error querying CCD console at %s: %s", port_node, e)
            return False, f"Port error: {e}"

        raw_str = response.decode("utf-8", errors="ignore")
        clean_str = re.sub(
            r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", raw_str
        ).strip()
        lines = [
            line.strip()
            for line in clean_str.splitlines()
            if line.strip() and line.strip() != command.strip()
        ]
        out_summary = " | ".join(lines[:3]) if lines else clean_str

        is_ok = bool(clean_str and len(clean_str) > 3)
        return is_ok, out_summary or clean_str or "No response"

    @staticmethod
    def _read_ccd_response(
        ser: serial.Serial, timeout: float, first_byte_timeout: float
    ) -> bytearray:
        """Reads a CCD console response until a prompt, a pause, or the timeout."""
        response = bytearray()
        start_time = time.time()
        last_data_time = start_time
        while (time.time() - start_time) < timeout:
            try:
                in_waiting = ser.in_waiting
                data = ser.read(in_waiting) if in_waiting > 0 else b""
            except (serial.SerialException, OSError) as e:
                logging.warning("CCD console read failed: %s", e)
                break
            if data:
                response.extend(data)
                last_data_time = time.time()
                if response.endswith(_CCD_PROMPT_SUFFIXES):
                    break
            elif in_waiting <= 0:
                if not response and (time.time() - start_time) > first_byte_timeout:
                    break
                if response and (time.time() - last_data_time) > 0.04:
                    break
                time.sleep(0.01)
        return response
