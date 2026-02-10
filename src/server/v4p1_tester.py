# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tester for Servo V4.1 manufacturing."""

import logging
import os
import re
import time
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import grpc

# pylint: disable=no-name-in-module,import-error
from server import diagnoseme_servod_pb2
from server import util
from server.diagnoseme_servod_pb2_grpc import ServodRpcServiceStub
from server.servo_console import ServoConsole
from server.servo_console import ServoConsoleError


# pylint: enable=no-name-in-module,import-error


class V4P1TesterError(Exception):
    """V4P1Tester error class."""


class V4P1Tester:
    """Class to handle manufacturing tests for Servo V4.1."""

    # HH (Host Hub): Genesys GL3590
    HH_VID = 0x05E3
    HH_PID = 0x0610
    HH_PID3 = 0x0625

    # DH (DUT Hub): Cypress
    DH_VID = 0x04B4
    DH_PID = 0x6502
    DH_PID3 = 0x6500

    def __init__(
        self,
        serial_number: str = "",
        mac_address: str = "",
        servod_address: str = "localhost:6002",
    ):
        """Initialize the tester.

        Args:
          serial_number: Expected serial number.
          mac_address: Expected MAC address.
          servod_address: Address of the ServodRpcService.
        """
        self._logger = logging.getLogger(__name__)
        self._serial_number = serial_number.strip().upper()
        self._mac_address = mac_address.strip().lower().replace("-", ":")
        self._servod_address = servod_address
        # Store results for report generation
        self._mux_results: Dict[str, Dict[str, str]] = {}

    async def _run_dut_control(self, command: str) -> str:
        """Run a dut-control command via RPC."""
        # pylint: disable=no-member
        async with grpc.aio.insecure_channel(self._servod_address) as channel:
            stub = ServodRpcServiceStub(channel)
            try:
                request = diagnoseme_servod_pb2.RunDutControlRequest(command=command)
                response = await stub.run_dut_control(request)
                if response.error_code != 0:
                    raise V4P1TesterError(
                        f"dut-control {command} failed with code "
                        f"{response.error_code}: {response.result}"
                    )
                return response.result.strip()
            except grpc.RpcError as e:
                raise V4P1TesterError(f"RPC failed: {e}") from e

    def test_console(
        self, console: ServoConsole, name: str, regex: str, command: str
    ) -> bool:
        """Test a console command against a regex."""
        self._logger.info("Running console test '%s' using command '%s'", name, command)
        try:
            output = console.issue_cmd(command)
            self._logger.info("Console output for '%s' received:\n%s", command, output)
            match = re.search(regex, output, re.IGNORECASE)
            if match:
                self._logger.info(
                    "Console test '%s' passed (matched regex '%s')", name, regex
                )
                return True
            self._logger.error(
                "Console test '%s' failed: output did not match regex '%s'", name, regex
            )
            return False
        except (ServoConsoleError, IOError) as e:
            self._logger.error("Console test '%s' encountered error: %s", name, e)
            return False

    def _find_usb_device_path(
        self, hub_vid: int, hub_pid: int, port: int
    ) -> Optional[str]:
        """Find the sysfs path of a device on a specific hub port."""
        self._logger.debug(
            "Searching for device on Hub %04x:%04x port %d", hub_vid, hub_pid, port
        )
        base_path = "/sys/bus/usb/devices"
        if not os.path.exists(base_path):
            self._logger.error("Sysfs USB devices path not found: %s", base_path)
            return None

        for dev in os.listdir(base_path):
            dev_path = os.path.join(base_path, dev)
            id_vendor_path = os.path.join(dev_path, "idVendor")
            id_product_path = os.path.join(dev_path, "idProduct")

            if os.path.exists(id_vendor_path) and os.path.exists(id_product_path):
                try:
                    with open(id_vendor_path, "r", encoding="utf-8") as f:
                        vid = int(f.read().strip(), 16)
                    with open(id_product_path, "r", encoding="utf-8") as f:
                        pid = int(f.read().strip(), 16)

                    if vid == hub_vid and pid == hub_pid:
                        target_port_path = f"{dev}.{port}"
                        full_target_path = os.path.join(base_path, target_port_path)
                        if os.path.exists(full_target_path):
                            self._logger.info(
                                "Found device path: %s (Hub %04x:%04x Port %d)",
                                full_target_path,
                                vid,
                                pid,
                                port,
                            )
                            return full_target_path
                except (ValueError, IOError) as e:
                    self._logger.debug("Error reading sysfs for %s: %s", dev, e)
                    continue
        return None

    def _get_serial_at_path(self, sysfs_path: str) -> Optional[str]:
        """Read the serial number of a USB device from its sysfs path."""
        # The sysfs_path might be a port (e.g., 3-6.2). We need the device directory.
        # Often the device directory is the path itself if a device is connected.
        serial_file = os.path.join(sysfs_path, "serial")
        if os.path.exists(serial_file):
            try:
                with open(serial_file, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except IOError:
                return None
        return None

    def _find_device_by_serial(self, serial: str) -> Optional[str]:
        """Search all USB devices for a specific serial number."""
        base_path = "/sys/bus/usb/devices"
        if not os.path.exists(base_path):
            return None
        for dev in os.listdir(base_path):
            dev_path = os.path.join(base_path, dev)
            serial_file = os.path.join(dev_path, "serial")
            if os.path.exists(serial_file):
                try:
                    with open(serial_file, "r", encoding="utf-8") as f:
                        if f.read().strip() == serial:
                            return dev_path
                except IOError:
                    continue
        return None

    async def _wait_for_usb_state(
        self, path: str, should_exist: bool, timeout: float = 5.0
    ) -> bool:
        """Wait for a USB sysfs path to appear or disappear."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if os.path.exists(path) == should_exist:
                return True
            time.sleep(0.2)
        return False

    async def _wait_for_serial_on_system(
        self, serial: str, timeout: float = 10.0
    ) -> Optional[str]:
        """Poll for a device with a specific serial to appear on the system."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            path = self._find_device_by_serial(serial)
            if path:
                return path
            time.sleep(0.5)
        return None

    async def _verify_dut_side_path(
        self, usb_serial: str, host_path: str
    ) -> Optional[str]:
        """Search for serial on DUT side and verify it is a new path."""
        self._logger.info(
            "Mux Step 4: Searching for device serial '%s' on DUT side", usb_serial
        )
        dut_side_path = await self._wait_for_serial_on_system(usb_serial)
        if not dut_side_path:
            self._logger.warning("Optional check: serial '%s' not found.", usb_serial)
            return None

        if dut_side_path == host_path:
            self._logger.error(
                "FAILED: Device found at SAME path %s. Mux didn't switch.",
                dut_side_path,
            )
            return None

        self._logger.info("Verified: Device found at NEW path %s", dut_side_path)
        return dut_side_path

    def _verify_parent_hub(self, dev_path: str) -> None:
        """Log and optionally verify the parent hub of a USB device."""
        parent_path = os.path.dirname(dev_path)
        parent_vid_file = os.path.join(parent_path, "idVendor")
        parent_pid_file = os.path.join(parent_path, "idProduct")

        if os.path.exists(parent_vid_file) and os.path.exists(parent_pid_file):
            try:
                with open(parent_vid_file, "r", encoding="utf-8") as f:
                    p_vid = int(f.read().strip(), 16)
                with open(parent_pid_file, "r", encoding="utf-8") as f:
                    p_pid = int(f.read().strip(), 16)
                self._logger.info("Device parent hub is %04x:%04x", p_vid, p_pid)
                if p_vid == self.DH_VID and p_pid in [self.DH_PID, self.DH_PID3]:
                    self._logger.info("Verified: Device is on the DUT Hub.")
                else:
                    self._logger.info(
                        "Note: Device is on hub %04x:%04x (not Servo DUT Hub)",
                        p_vid,
                        p_pid,
                    )
            except (ValueError, IOError):
                pass

    async def test_usb_mux(
        self,
        name: str,
        port_number: int,
        mux_control: str,
    ) -> bool:
        """Test a USB port muxing using dut-control."""
        self._logger.info("Verifying USB Mux '%s' (Mux Port %d)", name, port_number)
        self._mux_results[name] = {"serial": "Unknown", "host": "None", "dut": "None"}

        # Step 1: Connect to Host Hub
        # For uservo_fastboot_mux_sel, the values are uservo/fastboot.
        # For top/bottom_usbkey_mux, they are servo_sees_usbkey/dut_sees_usbkey.
        host_sees_val = "servo_sees_usbkey"
        dut_sees_val = "dut_sees_usbkey"
        if mux_control == "uservo_fastboot_mux_sel":
            host_sees_val = "uservo"
            dut_sees_val = "fastboot"

        await self._run_dut_control(f"{mux_control}:{host_sees_val}")
        time.sleep(1.0)

        # Step 2: Verify presence and capture serial
        host_path = self._find_usb_device_path(self.HH_VID, self.HH_PID, port_number)
        if not host_path:
            host_path = self._find_usb_device_path(
                self.HH_VID, self.HH_PID3, port_number
            )

        # Wait for device to be enumerated on Host side
        if not host_path or not await self._wait_for_usb_state(host_path, True):
            # Try finding it one more time if wait failed or path was None
            host_path = self._find_usb_device_path(
                self.HH_VID, self.HH_PID, port_number
            )
            if not host_path:
                host_path = self._find_usb_device_path(
                    self.HH_VID, self.HH_PID3, port_number
                )

        if not host_path:
            self._logger.error(
                "FAILED: Could not find USB stick on Host Hub port %d", port_number
            )
            return False

        self._mux_results[name]["host"] = host_path
        usb_serial = self._get_serial_at_path(host_path)
        if not usb_serial:
            self._logger.error(
                "FAILED: Could not read serial number from USB stick at %s", host_path
            )
            return False

        self._mux_results[name]["serial"] = usb_serial
        self._logger.info(
            "Verified: Device present on Host at %s (Serial: %s)",
            host_path,
            usb_serial,
        )

        # Step 3: Switch to DUT Hub
        self._logger.info("Mux Step 3: Switching to DUT Hub")
        await self._run_dut_control(f"{mux_control}:{dut_sees_val}")
        time.sleep(1.0)

        # Verify it left the host path
        if not await self._wait_for_usb_state(host_path, False):
            self._logger.error("FAILED: Device still on Host Hub path after switch")
            return False

        # Step 4: Verify appearance on DUT side (different path, same serial)
        self._logger.info("Mux Step 4: Verifying device appeared on a DIFFERENT path")
        dut_path = await self._wait_for_serial_on_system(usb_serial)

        if not dut_path:
            self._logger.warning(
                "Optional: Device serial '%s' not found on DUT side. "
                "Is the DUT port connected to this machine?",
                usb_serial,
            )
        elif dut_path == host_path:
            self._logger.error(
                "FAILED: Device found at SAME path %s. Mux didn't switch.", dut_path
            )
            return False
        else:
            self._logger.info("Verified: Device moved to DUT path %s", dut_path)
            self._mux_results[name]["dut"] = dut_path
            self._verify_parent_hub(dut_path)

        # Step 5: Restore connection to Host Hub
        self._logger.info("Mux Step 5: Restoring connection to Host Hub")
        await self._run_dut_control(f"{mux_control}:{host_sees_val}")
        time.sleep(1.0)

        if not await self._wait_for_usb_state(host_path, True):
            self._logger.error(
                "FAILED: Device failed to return to Host Hub path %s", host_path
            )
            return False

        self._logger.info("Verified: Device returned to Host Hub.")
        return True

    def log_mux_report(self) -> None:
        """Log a summary report of all mux tests."""
        self._logger.info("--- USB Mux Verification Report ---")
        for port_name, data in self._mux_results.items():
            self._logger.info(
                "%s has serial : %s Host USB path is %s DUT USB Path is %s",
                port_name,
                data["serial"],
                data["host"],
                data["dut"],
            )
        self._logger.info("------------------------------------")

    async def test_ina(
        self, name: str, address: int, description: str
    ) -> List[Tuple[str, bool]]:
        """Test an INA sensor by reading its voltage."""
        self._logger.info("Testing INA sensor: %s at 0x%02x", description, address)
        results = []
        try:
            # We assume the servod control name for voltage is <name>_mv
            mv_str = await self._run_dut_control(f"{name}_mv")
            mv = float(mv_str)
            # A basic check: voltage should be > 0 for a powered rail
            is_valid = mv > 0
            results.append((f"INA {name} ({description}) Presence", True))
            results.append((f"INA {name} ({description}) Voltage > 0", is_valid))
            self._logger.info("INA %s read: %s mV", name, mv)
        except (V4P1TesterError, ValueError) as e:
            self._logger.error("INA %s test failed: %s", name, e)
            results.append((f"INA {name} ({description}) Presence", False))
        return results

    def run_console_tests(self, serial_path: str) -> List[Tuple[str, bool]]:
        """Phase 1: Run tests that only require direct console access (no servod)."""
        all_results = []

        # 1. lsusb check for Servo device
        lsusb = util.run_command(["lsusb"]).stdout
        servo_present = "18d1:520d" in lsusb.lower()
        all_results.append(("lsusb Servo Presence", servo_present))

        # 2. Console tests
        with ServoConsole(serial_path) as console:
            # Verify serial number
            all_results.append(
                (
                    "Verify Serial Number",
                    self.test_console(
                        console,
                        "Verify Serial Number",
                        re.escape(self._serial_number),
                        "serialno",
                    ),
                )
            )

            # Verify MAC address
            mac_regex = self._mac_address.replace(":", "[ :]{1,2}")
            all_results.append(
                (
                    "Verify MAC Address",
                    self.test_console(
                        console, "Verify MAC Address", mac_regex, "macaddr"
                    ),
                )
            )

            # PD role check
            all_results.append(
                (
                    "servo_pd_role (console)",
                    self.test_console(
                        console, "servo_pd_role", r"Role: (SRC|SNK)", "pd 0 state"
                    ),
                )
            )

        return all_results

    async def run_functional_tests(self) -> List[Tuple[str, bool]]:
        """Phase 2: Run tests that require servod in recovery mode (no DUT needed)."""
        all_results = []

        # 1. USB Mux Tests using dut-control
        # Ensure USB3 mux is enabled globally first
        self._logger.info("Enabling global USB3 mux (usb3_mux_en:on)")
        await self._run_dut_control("usb3_mux_en:on")

        # Top Port (Port 2)
        all_results.append(
            (
                "USB-A Mux (port 2/top)",
                await self.test_usb_mux("Top USB-A", 2, "top_usbkey_mux"),
            )
        )

        # Bottom Port (Port 1)
        all_results.append(
            (
                "USB-A Mux (port 1/bottom)",
                await self.test_usb_mux("Bottom USB-A", 1, "bottom_usbkey_mux"),
            )
        )

        # uServo USB-A Port (Internal)
        all_results.append(
            (
                "uServo USB-A Port",
                await self.test_usb_mux(
                    "uServo USB-A Port", 4, "uservo_fastboot_mux_sel"
                ),
            )
        )

        # Output the report to logs
        self.log_mux_report()

        return all_results

    async def run_integration_tests(self) -> List[Tuple[str, bool]]:
        """Phase 3: Run tests that require servod with a connected DUT."""
        all_results = []

        # 1. Console connectivity tests via servod
        # We check if we can get the PTY path and it's a valid character device
        for console_name, control in [
            ("EC", "ec_uart_pty"),
            ("AP", "cpu_uart_pty"),
            ("GSC", "cr50_uart_pty"),
        ]:
            try:
                pty_path = await self._run_dut_control(control)
                is_valid = os.path.exists(pty_path)
                all_results.append((f"{console_name} Console Connectivity", is_valid))
            except V4P1TesterError:
                all_results.append((f"{console_name} Console Connectivity", False))

        # 2. INA Tests
        all_results.extend(await self.test_ina("ppdut5", 0x80, "INA231 [U7]"))
        all_results.extend(await self.test_ina("ppchg5", 0x82, "INA231 [U23]"))
        all_results.extend(await self.test_ina("ppservo5", 0x84, "INA231 [U51]"))

        return all_results
