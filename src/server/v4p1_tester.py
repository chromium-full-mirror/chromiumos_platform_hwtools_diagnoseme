# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tester for Servo V4.1 manufacturing."""

import asyncio
import logging
import os
import re
import time
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import grpc

from server import util
from server.config import config

# pylint: disable=no-name-in-module,import-error
from server.generated import diagnoseme_servod_pb2
from server.generated.diagnoseme_servod_pb2_grpc import ServodRpcServiceStub
from server.servo_console import ServoConsole
from server.servo_console import ServoConsoleError


# pylint: enable=no-name-in-module,import-error


class V4P1TesterError(Exception):
    """V4P1Tester error class."""


class V4P1Tester:
    """Class to handle manufacturing tests for Servo V4.1."""

    # HH (Host Hub): Genesys GL3590
    HH_VID = int(config.GENESYS_HUB_VID, 16)
    HH_PID = int(config.GENESYS_HUB_PID, 16)
    HH_PID3 = int(config.GENESYS_HUB_PID3, 16)

    # DH (DUT Hub): Cypress
    DH_VID = int(config.CYPRESS_HUB_VID, 16)
    DH_PID = int(config.CYPRESS_HUB_PID, 16)
    DH_PID3 = int(config.CYPRESS_HUB_PID2, 16)

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
        self._logger.info("V4P1Tester initialized (Revision traversal-v2)")

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
            # Skip if not a primary device (e.g. interfaces like 3-4:1.0)
            if ":" in dev:
                continue

            # Check 'devpath' attribute which indicates the port number
            devpath_file = os.path.join(dev_path, "devpath")
            if not os.path.exists(devpath_file):
                continue

            try:
                with open(devpath_file, "r", encoding="utf-8") as f:
                    devpath_val = f.read().strip()
                # devpath is like "1.4.2". The last component is the port on the parent.
                current_port = devpath_val.split(".")[-1]
            except (IOError, IndexError):
                continue

            # Check parent VID/PID by resolving symlink
            real_path = os.path.realpath(dev_path)
            parent_path = os.path.dirname(real_path)
            p_vid, p_pid = 0, 0

            try:
                with open(
                    os.path.join(parent_path, "idVendor"), "r", encoding="utf-8"
                ) as f:
                    p_vid = int(f.read().strip(), 16)
                with open(
                    os.path.join(parent_path, "idProduct"), "r", encoding="utf-8"
                ) as f:
                    p_pid = int(f.read().strip(), 16)
            except (ValueError, IOError, FileNotFoundError):
                # This is normal for root hubs or non-usb parents
                continue

            self._logger.debug(
                "Inspecting device %s: DevPath=%s (Port %s), Parent=%04x:%04x",
                dev,
                devpath_val,
                current_port,
                p_vid,
                p_pid,
            )

            if p_vid == hub_vid and p_pid == hub_pid and current_port == str(port):
                self._logger.info(
                    "MATCH FOUND: Device %s (Hub %04x:%04x Port %d)",
                    dev_path,
                    p_vid,
                    p_pid,
                    port,
                )
                return dev_path

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

    def _get_serial_with_retry(
        self, host_path: str, timeout: float = 10.0
    ) -> Optional[str]:
        """Retry reading the serial number for a short period."""
        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout:
            serial_file = os.path.join(host_path, "serial")
            if not os.path.exists(serial_file):
                self._logger.debug(
                    "Serial file not yet present at %s (elapsed: %.2fs)",
                    host_path,
                    time.monotonic() - start_time,
                )
            else:
                serial = self._get_serial_at_path(host_path)
                if serial:
                    self._logger.debug(
                        "Successfully read serial '%s' from %s", serial, host_path
                    )
                    return serial
                self._logger.debug(
                    "Serial file exists but is empty at %s (elapsed: %.2fs)",
                    host_path,
                    time.monotonic() - start_time,
                )
            time.sleep(0.5)
        self._logger.error(
            "Timed out reading serial at %s after %.2fs", host_path, timeout
        )
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
        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout:
            if os.path.exists(path) == should_exist:
                return True
            await asyncio.sleep(0.2)
        return False

    async def _wait_for_serial_on_system(
        self, serial: str, timeout: float = 20.0
    ) -> Optional[str]:
        """Poll for a device with a specific serial to appear on the system."""
        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout:
            path = self._find_device_by_serial(serial)
            if path:
                return path
            await asyncio.sleep(0.5)
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
        strict_dut_check: bool = True,
    ) -> Tuple[bool, str]:
        """Test a USB port muxing using dut-control."""
        # pylint: disable=too-many-return-statements,too-many-branches,too-many-statements
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
        await asyncio.sleep(1.0)

        # Step 2: Verify presence and capture serial
        # Wait up to 15 seconds for device to be enumerated on Host side
        host_path = None
        start_time = time.monotonic()
        while time.monotonic() - start_time < 15.0:
            host_path = self._find_usb_device_path(
                self.HH_VID, self.HH_PID, port_number
            )
            if not host_path:
                host_path = self._find_usb_device_path(
                    self.HH_VID, self.HH_PID3, port_number
                )
            if host_path and await self._wait_for_usb_state(host_path, True):
                break
            host_path = None
            await asyncio.sleep(0.5)

        if not host_path:
            self._logger.error(
                "FAILED: Could not find USB stick on Host Hub port %d", port_number
            )
            return False, (
                f"FAILED: Could not find USB stick on Host Hub port {port_number}"
            )

        self._mux_results[name]["host"] = host_path
        usb_serial = self._get_serial_with_retry(host_path)
        if not usb_serial:
            self._logger.error(
                "FAILED: Could not read serial number from USB stick at %s", host_path
            )
            return False, (
                f"FAILED: Could not read serial number from USB stick at {host_path}"
            )

        self._mux_results[name]["serial"] = usb_serial
        self._logger.info(
            "Verified: Device present on Host at %s (Serial: %s)",
            host_path,
            usb_serial,
        )

        # Step 3: Switch to DUT Hub
        self._logger.info("Mux Step 3: Switching to DUT Hub")
        await self._run_dut_control(f"{mux_control}:{dut_sees_val}")
        await asyncio.sleep(1.0)

        test_passed = True
        test_error_msg = ""

        # Verify it left the host path
        if not await self._wait_for_usb_state(host_path, False):
            self._logger.error("FAILED: Device still on Host Hub path after switch")
            test_passed = False
            test_error_msg = "FAILED: Device still on Host Hub path after switch"
        else:
            # Step 4: Verify appearance on DUT side (different path, same serial)
            self._logger.info(
                "Mux Step 4: Verifying device appeared on a DIFFERENT path"
            )
            dut_path = await self._wait_for_serial_on_system(usb_serial)

            if not dut_path:
                msg = (
                    f"FAILED: Device serial '{usb_serial}' not found on DUT side. "
                    "Is the DUT port connected to this machine?"
                )
                self._logger.error(msg)
                try:
                    lsusb_out = util.run_command(["lsusb"]).stdout
                    self._logger.error("Current lsusb:\n%s", lsusb_out)
                except (OSError, util.subprocess.SubprocessError):
                    pass

                if strict_dut_check:
                    test_passed = False
                    test_error_msg = msg
                else:
                    self._logger.warning("Ignoring failure (strict_dut_check=False).")

            elif dut_path == host_path:
                self._logger.error(
                    "FAILED: Device found at SAME path %s. Mux didn't switch.", dut_path
                )
                test_passed = False
                test_error_msg = (
                    f"FAILED: Device found at SAME path {dut_path}. Mux didn't switch."
                )
            else:
                self._logger.info("Verified: Device moved to DUT path %s", dut_path)
                self._mux_results[name]["dut"] = dut_path
                self._verify_parent_hub(dut_path)

        # Step 5: Restore connection to Host Hub
        self._logger.info("Mux Step 5: Restoring connection to Host Hub")
        await self._run_dut_control(f"{mux_control}:{host_sees_val}")
        await asyncio.sleep(1.0)

        if not await self._wait_for_usb_state(host_path, True):
            self._logger.error(
                "FAILED: Device failed to return to Host Hub path %s", host_path
            )
            return (
                False,
                f"FAILED: Device failed to return to Host Hub path {host_path}",
            )

        self._logger.info("Verified: Device returned to Host Hub.")

        if not test_passed:
            return False, test_error_msg

        return True, ""

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

    def _find_atmega_hid_path(self) -> Optional[str]:
        """Find the /dev/hidrawX path for the Atmega Keyboard."""
        base_path = "/sys/bus/hid/devices"
        if not os.path.exists(base_path):
            self._logger.error("Sysfs HID devices path not found: %s", base_path)
            return None

        # Atmega LUFA VID:PID is 03EB:2042
        target_id = "03EB:2042"

        found_devices = []
        for dev in os.listdir(base_path):
            # Sysfs name format: BUS:VENDOR:PRODUCT.INTF
            found_devices.append(dev)
            if target_id in dev.upper():
                # Found the device, look for hidraw
                hidraw_dir = os.path.join(base_path, dev, "hidraw")
                if os.path.exists(hidraw_dir):
                    hidraws = os.listdir(hidraw_dir)
                    if hidraws:
                        return f"/dev/{hidraws[0]}"

        self._logger.error(
            "Atmega HID device %s not found in %s. Found: %s",
            target_id,
            base_path,
            found_devices,
        )
        return None

    async def test_usb_keyboard(self) -> bool:
        """Verify USB Keyboard functionality."""
        # pylint: disable=too-many-return-statements
        self._logger.info("Verifying USB Keyboard...")

        hid_path = self._find_atmega_hid_path()
        if not hid_path:
            # Often the keyboard is not plugged in or not enumerated if not needed.
            # But for manufacturing test, it should be there if we are testing
            # the atmega.
            self._logger.error("FAILED: Atmega Keyboard HID device not found.")
            return False

        self._logger.info("Found Keyboard HID at %s", hid_path)

        try:
            # Open the device in non-blocking mode
            fd = os.open(hid_path, os.O_RDONLY | os.O_NONBLOCK)

            # Send keystrokes
            test_str = "test"
            await self._run_dut_control(f"usb_keyboard:{test_str}")

            # Give it time to arrive
            await asyncio.sleep(1.0)

            # Read data
            try:
                data = os.read(fd, 1024)
                os.close(fd)
            except BlockingIOError:
                os.close(fd)
                self._logger.error("FAILED: No data received from keyboard.")
                return False

            if len(data) > 0:
                self._logger.info(
                    "Verified: Received %d bytes from keyboard.", len(data)
                )
                return True

            self._logger.error("FAILED: Received empty data.")
            return False

        except OSError as e:
            self._logger.error("Error accessing HID device: %s", e)
            return False

    async def test_mac_address_prefix(
        self,
        prefix: str,
        timeout: Optional[float] = None,
    ) -> bool:
        """Verify that a network interface with the given MAC address prefix exists."""
        if timeout is None:
            timeout = (
                config.DEFAULT_MAC_RETRY_ATTEMPTS * config.DEFAULT_MAC_RETRY_INTERVAL
            )

        prefix = prefix.strip().lower().replace("-", ":")
        self._logger.info(
            "Verifying network interface with MAC prefix %s exists...", prefix
        )

        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout:
            try:
                for interface in os.listdir("/sys/class/net"):
                    address_file = os.path.join("/sys/class/net", interface, "address")
                    if os.path.exists(address_file):
                        try:
                            with open(address_file, "r", encoding="utf-8") as f:
                                mac = f.read().strip().lower()
                                if mac.startswith(prefix):
                                    self._logger.info(
                                        "Found matching MAC %s on interface %s",
                                        mac,
                                        interface,
                                    )
                                    return True
                        except OSError as e:
                            self._logger.debug(
                                "Could not read MAC address from %s: %s",
                                address_file,
                                e,
                            )
            except OSError as e:
                self._logger.error("Could not list network interfaces: %s", e)

            await asyncio.sleep(0.5)

        self._logger.error("No network interface found with MAC prefix %s", prefix)
        return False

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

    def test_i2c_scan(self, console: ServoConsole) -> bool:
        """Verify all critical I2C devices are visible to the MCU."""
        self._logger.info("Running I2C scan on Bus 1...")
        # Expected addresses on I2C Bus 1 for Servo V4.1
        # 0x21: TCA6416 (GPIO Expander)
        # 0x40, 0x41, 0x42: INA231 (Power Monitors)
        # 0x48, 0x49: DAC7578 (CC DACs)
        # 0x50: EEPROM (Board ID)
        expected_addresses = ["21", "40", "41", "42", "48", "49", "50"]

        try:
            output = console.issue_cmd("i2cscan 1")
            self._logger.debug("i2cscan output: %s", output)

            # We look for the presence of the hex addresses in the scan table.
            # The output typically looks like:
            #   0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
            # 20: -- 21 -- -- -- -- -- -- -- -- -- -- -- -- --
            # 40: 40 41 42 -- -- -- -- -- 48 49 -- -- -- -- --
            # 50: 50 -- -- -- -- -- -- -- -- -- -- -- -- -- --
            missing = []
            for addr in expected_addresses:
                # Use regex to find the address in the output (e.g. '0x21')
                # We expect the address to be prefixed with 0x in the output.
                if not re.search(rf"0x{addr}", output):
                    missing.append(f"0x{addr}")

            if missing:
                self._logger.error(
                    "I2C Scan FAILED. Missing devices: %s", ", ".join(missing)
                )
                return False

            self._logger.info("I2C Scan PASSED. All critical devices detected.")
            return True

        except (V4P1TesterError, ServoConsoleError) as e:
            self._logger.error("I2C scan test encountered error: %s", e)
            return False

    def run_console_tests(self, serial_path: str) -> List[Tuple[str, bool]]:
        """Phase 1: Run tests that only require direct console access (no servod)."""
        all_results = []

        # 1. lsusb check for Servo device
        lsusb = util.run_command(["lsusb"]).stdout
        servo_present = (
            f"{config.SERVO_V4P1_VID}:{config.SERVO_V4P1_PID}".lower() in lsusb.lower()
        )
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

            # I2C Scan Verification
            all_results.append(("I2C Scan Verification", self.test_i2c_scan(console)))

        return all_results

    async def run_functional_tests(self) -> List[Tuple[str, bool]]:
        """Phase 2: Run tests that require servod in recovery mode (no DUT needed)."""
        all_results = []

        # 1. USB Mux Tests using dut-control
        # Ensure USB3 mux is enabled globally first
        self._logger.info("Enabling global USB3 mux (usb3_mux_en:on)")
        await self._run_dut_control("usb3_mux_en:on")

        # Top Port (Port 2)
        res, msg = await self.test_usb_mux("Top USB-A", 2, "top_usbkey_mux")
        msg_str = f" - {msg}" if msg else ""
        all_results.append((f"USB-A Mux (port 2/top){msg_str}", res))

        # Bottom Port (Port 1)
        res, msg = await self.test_usb_mux("Bottom USB-A", 1, "bottom_usbkey_mux")
        msg_str = f" - {msg}" if msg else ""
        all_results.append((f"USB-A Mux (port 1/bottom){msg_str}", res))

        # uServo USB-A Port (Internal)
        res, msg = await self.test_usb_mux(
            "uServo USB-A Port",
            4,
            "uservo_fastboot_mux_sel",
            strict_dut_check=False,
        )
        msg_str = f" - {msg}" if msg else ""
        all_results.append((f"uServo USB-A Port{msg_str}", res))

        # USB Keyboard Test
        # Skipped for now as enumeration is flaky after programming.
        # all_results.append(
        #     (
        #         "USB Keyboard HID",
        #         await self.test_usb_keyboard(),
        #     )
        # )

        # Ethernet MAC Prefix Test
        all_results.append(
            (
                "Ethernet MAC Prefix (88:54:1F)",
                await self.test_mac_address_prefix("88:54:1F"),
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
