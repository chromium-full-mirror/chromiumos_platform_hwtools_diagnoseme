# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Maui v1.2 implementations of the shared Maui gRPC services.

Maui v1.2 runs the same functional tests, PDC flashing and labeling flow as
v1.1; only the MCU console (native CDC-ACM instead of FTDI), the serial
number source and a few checks differ. The servicers below subclass the v1.1
servicers and only override the revision hooks.
"""

# Generated protobuf members are not visible to pylint.
# pylint: disable=no-member

import logging
import os
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple

from google.protobuf import empty_pb2
import grpc

from server import maui_common
from server import maui_v12_device as device
from server import maui_v12_profile as profile
from server.generated import maui_manufacturing_pb2
from server.maui_common import write_device_log
from server.maui_station1_service import MauiDeviceManagementServicer
from server.maui_station1_service import MauiPdcServicer
from server.maui_station2_service import MauiStation2Servicer


DEFAULT_ACM_PORT = "/dev/ttyACM0"

# (stage_key, log file, label) of the Maui v1.2 manufacturing stages.
V12_STAGE_LOG_MAPPING = (
    (
        "mcu_serial_provisioning",
        "mcu_serial_provisioning.log",
        "MCU Serial Provisioning",
    ),
    ("h2h_spi_check", "h2h_spi_check.log", "H2H SPI Flash Access"),
    ("pdc_fw_flashing", "pdc_flashing.log", "PDC FW Flashing"),
    ("host_only_verification", "host_only_tests.log", "Host-Only Verification"),
    ("host_dut_verification", "host_dut_tests.log", "Host + DUT Verification"),
    ("adb_transfer_test", "adb_transfer_test.log", "ADB Transfer Reliability"),
    (
        "serial_label_check",
        "serial_label_check.log",
        "Enumeration, Serial & Barcode Check",
    ),
)


class MauiV12Station2Servicer(MauiStation2Servicer):
    """Functional tests of Maui v1.2 (ITE IT82002, CDC-ACM console)."""

    CONSOLE_USB_LABEL = "MCU"
    MCU_CONSOLE_IS_TTYUSB = False
    # Injectable for tests (USB topology polling).
    _clock = staticmethod(time.monotonic)
    _sleep = staticmethod(time.sleep)

    def _make_console_tester(self, port_node: str) -> device.MauiV12ConsoleTester:
        return device.MauiV12ConsoleTester(
            port_node=port_node, timeout=profile.CONSOLE_TIMEOUT_S
        )

    def _expected_hwrev_text(self) -> str:
        return profile.expected_hwrev_text()

    def _find_unit_serial(self) -> str:
        unit = device.find_unit()
        if unit.h2h and unit.h2h.serial:
            return unit.h2h.serial
        return unit.mcu.serial if unit.mcu else ""

    def _find_real_h2h_serial(self) -> str:
        h2hs = device.scan_h2h_devices()
        return next((h.serial for h in h2hs if h.serial), "")

    def _resolve_console_port(self, dev_port: str) -> str:
        if dev_port and os.path.exists(dev_port):
            return dev_port
        return device.find_app_console_port(DEFAULT_ACM_PORT)

    def _fallback_console_port(self, dev_port: str) -> str:
        return self._resolve_console_port(dev_port)

    def _rediscover_console_port(self, dev_port: str) -> str:
        if os.path.exists(dev_port):
            return dev_port
        return device.find_app_console_port(dev_port)

    def _gpio_ok_text(self) -> str:
        return (
            f"All {len(device.V12_EXPECTED_HOST_GPIOS)} GPIOs verified "
            "(connection-independent subset)"
        )

    def _verify_voltages(self, adc_status: Dict[str, Any]) -> Tuple[bool, List[str]]:
        return device.verify_host_adc(adc_status)

    def _scan_host_usb_topology(self) -> Tuple[bool, bool, bool, bool]:
        hub_usb2_ok = hub_usb3_ok = h2h_ok = False
        for dev in maui_common.scan_usb_devices():
            if (dev.vid, dev.pid) in profile.HUB_USB2_VID_PIDS:
                hub_usb2_ok = True
            elif (dev.vid, dev.pid) in profile.HUB_USB3_VID_PIDS:
                hub_usb3_ok = True
            elif dev.vid == profile.GOOGLE_VID and dev.pid in profile.H2H_PIDS:
                h2h_ok = True
        mcu_ok = any(c.is_app for c in device.scan_mcu_consoles())
        return hub_usb2_ok, hub_usb3_ok, mcu_ok, h2h_ok

    def _check_host_usb_topology(self) -> Tuple[bool, bool, bool, bool]:
        """Checks the hubs, the MCU App console (18d1:5085) and the H2H bridge.

        The App drops and re-enumerates the USB3 hub about 2.4 s after
        plug-in, so this polls for up to USB_TOPOLOGY_SETTLE_S until all four
        are present. The third value (reported as ftdi_detected) is the MCU
        App console.
        """
        deadline = self._clock() + profile.USB_TOPOLOGY_SETTLE_S
        while True:
            found = self._scan_host_usb_topology()
            if all(found) or self._clock() >= deadline:
                if not all(found):
                    logging.info("USB topology incomplete after polling: %s", found)
                return found
            self._sleep(profile.USB_TOPOLOGY_POLL_S)

    def test_bsl_button(
        self,
        request: maui_manufacturing_pb2.MauiBslButtonTestRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiBslButtonTestResponse:
        """BSL button test polling the ec_bsl_invoke GPIO (no BSL ADC on v1.2)."""
        dev_port = self._fallback_console_port(request.device_port or "")
        tester = self._make_console_tester(dev_port)
        duration = request.max_sample_duration_sec or 3.0
        logging.info(
            "Polling BSL GPIO %s on %s for %.1fs...",
            profile.BSL_GPIO_NAME,
            dev_port,
            duration,
        )
        passed, level, raw = tester.sample_bsl_gpio(max_duration_sec=duration)
        serial_no = request.serial_number or self._find_unit_serial()

        if passed:
            msg = (
                f"BSL Button Test PASSED! GPIO {profile.BSL_GPIO_NAME} "
                f"read {level} (pressed)."
            )
        elif device.gpio_missing(raw, profile.BSL_GPIO_NAME):
            msg = (
                "BSL Button Test FAILED. This MCU firmware has no "
                f"'{profile.BSL_GPIO_NAME}' GPIO - update the MCU App."
            )
        else:
            msg = (
                f"BSL Button Test FAILED. GPIO {profile.BSL_GPIO_NAME} did not read "
                f"{profile.BSL_GPIO_PRESSED} within {duration:.1f}s (last: {level})."
            )
        write_device_log(
            serial_no,
            "bsl_button_test",
            f"ALL_PASSED={passed}\n"
            f"RESULT: {'PASSED' if passed else 'FAILED'}\n{msg}\n\n"
            f"Raw GPIO Output:\n{raw}",
        )
        return maui_manufacturing_pb2.MauiBslButtonTestResponse(
            success=passed,
            peak_bsl_mv=max(level, 0),
            threshold_mv=profile.BSL_GPIO_PRESSED,
            message=msg,
            raw_adc_table=raw,
        )


class MauiV12PdcServicer(MauiPdcServicer):
    """PDC flashing over the Maui v1.2 CDC-ACM console (up to 5 boards)."""

    def _resolve_slot_serial(self, dev_port: str) -> str:
        consoles = device.scan_mcu_consoles()
        for console in consoles:
            if console.dev_node == dev_port:
                h2h = device.pair_h2h(console, device.scan_h2h_devices(), consoles)
                return (h2h.serial if h2h else "") or console.serial
        return ""

    def discover_connected_pdc_devices(
        self,
        unused_request: empty_pb2.Empty,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiConnectedPdcDevicesResponse:
        """Returns the MCU App consoles (18d1:5085) of all connected boards."""
        h2hs = device.scan_h2h_devices()
        consoles = device.scan_mcu_consoles()
        found = []
        for console in consoles:
            if not console.is_app:
                continue
            h2h = device.pair_h2h(console, h2hs, consoles)
            found.append(
                maui_manufacturing_pb2.MauiPdcDeviceInfo(
                    slot_index=len(found) + 1,
                    serial_number=(h2h.serial if h2h else "") or console.serial,
                    device_port=console.dev_node,
                    container_id="",
                    product_description=console.product,
                )
            )
            if len(found) >= profile.PDC_MAX_SLOTS:
                break
        logging.info("Found %d Maui v1.2 MCU console(s) for PDC flashing.", len(found))
        return maui_manufacturing_pb2.MauiConnectedPdcDevicesResponse(
            success=True,
            devices=found,
            message=f"Found {len(found)} connected Maui v1.2 device(s).",
        )


class MauiV12DeviceManagementServicer(MauiDeviceManagementServicer):
    """Logs, labeling and final submission of Maui v1.2.

    The MCU console endpoint (18d1:5085) takes the place of the v1.1 FTDI
    endpoint in the shared response messages (ftdi_* fields).
    """

    STAGE_LOG_MAPPING = V12_STAGE_LOG_MAPPING
    CONSOLE_LABEL = "MCU"
    CONSOLE_USB_ID_TEXT = "VID 18d1, PID 5085"

    def read_connected_device_serial(
        self,
        unused_request: empty_pb2.Empty,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiReadSerialResponse:
        unit = device.find_unit()
        h2h_serial = unit.h2h.serial if unit.h2h else ""
        h2h_product = (unit.h2h.product if unit.h2h else "") or (
            "H2H" if unit.h2h else ""
        )
        mcu = unit.mcu
        mcu_serial = mcu.serial if mcu else ""
        mcu_programmed = bool(
            mcu and mcu.is_app and profile.is_valid_serial(mcu_serial)
        )
        dual_match = bool(
            h2h_serial
            and mcu_serial
            and h2h_serial.upper() == mcu_serial.upper()
            and mcu_programmed
        )
        primary = h2h_serial or mcu_serial

        if unit.h2h and mcu:
            if dual_match:
                msg = f"Provisioned Maui v1.2 Board: {primary}"
            elif not mcu.is_app:
                msg = (
                    f"Maui v1.2 MCU in {mcu.stage} ({mcu.product}). "
                    f"H2H Serial: '{h2h_serial}'"
                )
            else:
                msg = (
                    f"MCU serial '{mcu_serial}' not provisioned. "
                    f"H2H Serial: '{h2h_serial}'"
                )
        elif unit.h2h:
            msg = f"Maui H2H Device: {primary} (MCU console not detected)"
        elif mcu:
            msg = f"Maui v1.2 MCU: {primary} (H2H not detected)"
        else:
            msg = "No Device Detected"

        return maui_manufacturing_pb2.MauiReadSerialResponse(
            is_connected=bool(unit.h2h or mcu),
            serial_number=primary,
            device_port=mcu.dev_node if mcu else "",
            product_name=h2h_product or (mcu.product if mcu else "") or "Maui Device",
            message=msg,
            adb_serial=h2h_serial,
            adb_connected=bool(unit.h2h),
            adb_product=h2h_product,
            ftdi_serial=mcu_serial,
            ftdi_connected=bool(mcu),
            ftdi_product=mcu.product if mcu else "",
            dual_endpoints_match=dual_match,
            ftdi_pid=profile.MCU_PID if mcu else 0,
            ftdi_is_programmed=mcu_programmed,
        )
