# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for servo_manufacturing.py."""

import asyncio
import unittest
from unittest import mock

from google.protobuf import empty_pb2

# pylint: disable=no-member
# pylint: disable=no-name-in-module
# pylint: disable=import-error
from server import genesys_hub_programmer
from server import rtk_eth_programmer
from server import serial_programmer
from server import servo_manufacturing
from server import servo_programmer
from server.generated import servo_manufacturing_pb2


class TestServoV41ManufacturingServicer(unittest.TestCase):
    """Tests for ServoV41ManufacturingServicer."""

    # pylint: disable=too-many-public-methods

    def setUp(self):
        self.servicer = servo_manufacturing.ServoV41ManufacturingServicer()
        self.context = mock.MagicMock()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        # Mock util.run_command to avoid failures on systems without lsusb
        self.mock_run_command = mock.patch("server.util.run_command").start()
        # Mock lsusb output so wait_for_usb_devices finds the devices immediately
        self.mock_run_command.return_value = mock.Mock(
            stdout=(
                "Bus 001 Device 002: ID 0bda:8153\n"
                "Bus 001 Device 003: ID 03eb:2ff4\n"
                "Bus 001 Device 004: ID 18d1:520d\n"
            ),
            returncode=0,
        )
        # Mock reset_servo_v4p1_hub
        self.mock_reset_hub = mock.patch("server.hal.hal.reset_servo_v4p1_hub").start()
        self.mock_reset_hub.return_value = True
        # Mock reset_servo_v4p1_dut_hub
        self.mock_reset_dut_hub = mock.patch(
            "server.hal.hal.reset_servo_v4p1_dut_hub"
        ).start()
        self.mock_reset_dut_hub.return_value = True

    def tearDown(self):
        mock.patch.stopall()
        self.loop.close()

    def test_get_status(self):
        """Test case for get_status."""
        request = empty_pb2.Empty()
        response = self.servicer.get_status(request, self.context)
        self.assertEqual(response.status, "OK")

    @mock.patch("server.util.is_usb_device_present")
    def test_get_device_presence(self, mock_is_present):
        """Test case for get_device_presence."""
        mock_is_present.side_effect = [True, False, True]
        request = empty_pb2.Empty()
        response = self.servicer.get_device_presence(request, self.context)
        self.assertTrue(response.mcu_dfu_detected)
        self.assertFalse(response.servo_v4p1_detected)
        self.assertTrue(response.realtek_eth_detected)

    def test_validate_servo_serial_valid(self):
        """Test case for validate_servo_serial with valid input."""
        request = servo_manufacturing_pb2.ValidateServoSerialRequest(
            serial_number="SERVOV4P1-C-2210050007"
        )
        response = self.servicer.validate_servo_serial(request, self.context)
        self.assertTrue(response.is_valid)
        self.assertEqual(response.error, "")

    def test_validate_servo_serial_invalid(self):
        """Test case for validate_servo_serial with invalid input."""
        request = servo_manufacturing_pb2.ValidateServoSerialRequest(
            serial_number="invalid serial"
        )
        response = self.servicer.validate_servo_serial(request, self.context)
        self.assertFalse(response.is_valid)
        self.assertEqual(response.error, "Invalid serial number format")

    def test_validate_servo_mac_address_valid(self):
        """Test case for validate_servo_mac_address with valid input."""
        request = servo_manufacturing_pb2.ValidateServoMacAddressRequest(
            mac_address="00:11:22:33:44:55"
        )
        response = self.servicer.validate_servo_mac_address(request, self.context)
        self.assertTrue(response.is_valid)
        self.assertEqual(response.error, "")

    def test_validate_servo_mac_address_invalid(self):
        """Test case for validate_servo_mac_address with invalid input."""
        request = servo_manufacturing_pb2.ValidateServoMacAddressRequest(
            mac_address="invalid mac"
        )
        response = self.servicer.validate_servo_mac_address(request, self.context)
        self.assertFalse(response.is_valid)
        self.assertEqual(response.error, "Invalid MAC address format")

    @mock.patch("time.sleep")
    @mock.patch("server.util.wait_for_usb_devices")
    @mock.patch("server.genesys_hub_programmer.GenesysHubProgrammer.program")
    def test_program_genesys_hub_success(
        self, mock_program, mock_wait_usb, unused_mock_sleep
    ):
        """Test case for program_genesys_hub success."""
        mock_wait_usb.return_value = True
        mock_program.return_value = True
        request = servo_manufacturing_pb2.ProgramGenesysHubRequest()
        response = self.servicer.program_genesys_hub(request, self.context)
        self.assertTrue(response.success)
        self.assertEqual(response.message, "Genesys Hub programmed successfully")

    @mock.patch("time.sleep")
    @mock.patch("server.util.wait_for_usb_devices")
    @mock.patch("server.genesys_hub_programmer.GenesysHubProgrammer.program")
    def test_program_genesys_hub_failure(
        self, mock_program, mock_wait_usb, unused_mock_sleep
    ):
        """Test case for program_genesys_hub failure."""
        mock_wait_usb.return_value = True
        mock_program.side_effect = genesys_hub_programmer.GenesysHubProgrammerError(
            "Hardware error", stdout="out", stderr="err"
        )
        request = servo_manufacturing_pb2.ProgramGenesysHubRequest()
        response = self.servicer.program_genesys_hub(request, self.context)
        self.assertFalse(response.success)
        self.assertEqual(response.message, "Hardware error")
        self.assertIn("STDOUT:\nout", response.logs)
        self.assertIn("STDERR:\nerr", response.logs)

    @mock.patch("server.util.wait_for_usb_devices")
    def test_program_genesys_hub_timeout(self, mock_wait_usb):
        """Test case for program_genesys_hub timeout."""
        mock_wait_usb.return_value = False
        request = servo_manufacturing_pb2.ProgramGenesysHubRequest()
        response = self.servicer.program_genesys_hub(request, self.context)
        self.assertFalse(response.success)
        self.assertEqual(response.message, "Timeout waiting for Genesys Hub device")

    @mock.patch("time.sleep")
    @mock.patch("server.util.wait_for_usb_devices")
    @mock.patch("server.servo_programmer.ServoProgrammer.program")
    def test_program_mcu_success(self, mock_program, mock_wait_usb, unused_mock_sleep):
        """Test case for program_mcu success."""
        mock_wait_usb.return_value = True
        mock_program.return_value = True
        request = servo_manufacturing_pb2.ProgramMcuRequest(
            firmware_path="/path/to/firmware.bin"
        )
        response = self.servicer.program_mcu(request, self.context)
        self.assertTrue(response.success)
        self.assertIn("MCU programmed successfully", response.message)

    @mock.patch("time.sleep")
    @mock.patch("server.util.wait_for_usb_devices")
    @mock.patch("server.servo_programmer.ServoProgrammer.program")
    def test_program_mcu_failure(self, mock_program, mock_wait_usb, unused_mock_sleep):
        """Test case for program_mcu failure."""
        mock_wait_usb.return_value = True
        mock_program.side_effect = servo_programmer.ServoProgrammerError(
            "Flash error", stdout="out", stderr="err"
        )
        request = servo_manufacturing_pb2.ProgramMcuRequest(
            firmware_path="/path/to/firmware.bin"
        )
        response = self.servicer.program_mcu(request, self.context)
        self.assertFalse(response.success)
        self.assertEqual(response.message, "Flash error")
        self.assertIn("STDOUT:\nout", response.logs)
        self.assertIn("STDERR:\nerr", response.logs)

    @mock.patch("server.util.wait_for_usb_devices")
    def test_program_mcu_timeout(self, mock_wait_usb):
        """Test case for program_mcu timeout."""
        mock_wait_usb.return_value = False
        request = servo_manufacturing_pb2.ProgramMcuRequest(
            firmware_path="/path/to/firmware.bin"
        )
        response = self.servicer.program_mcu(request, self.context)
        self.assertFalse(response.success)
        self.assertEqual(response.message, "Timeout waiting for MCU in DFU mode")

    @mock.patch("server.util.wait_for_usb_devices")
    def test_program_serial_timeout(self, mock_wait_usb):
        """Test case for program_serial timeout."""
        mock_wait_usb.return_value = False
        request = servo_manufacturing_pb2.ProgramSerialRequest(
            serial_number="SERVOV4P1-C-2210050007"
        )
        response = self.servicer.program_serial(request, self.context)
        self.assertFalse(response.success)
        self.assertEqual(response.message, "Timeout waiting for Servo device")

    @mock.patch("server.serial_programmer.SerialProgrammer.__init__", return_value=None)
    @mock.patch("server.util.discover_servo_v4p1_serial_path")
    @mock.patch("server.serial_programmer.SerialProgrammer.program")
    def test_program_serial_success(
        self, mock_program, mock_discover_path, unused_mock_sp_init
    ):
        """Test case for program_serial success."""
        mock_discover_path.return_value = "/dev/mock_ttyUSB0"
        mock_program.return_value = True
        request = servo_manufacturing_pb2.ProgramSerialRequest(
            serial_number="SERVOV4P1-C-2210050007"
        )
        response = self.servicer.program_serial(request, self.context)
        self.assertTrue(response.success)
        self.assertEqual(response.message, "Serial number programmed successfully")

    @mock.patch(
        "server.util.discover_servo_serial_path", return_value="/dev/mock_ttyUSB0"
    )
    @mock.patch("server.util.discover_servo_v4p1_serial_path")
    @mock.patch("server.serial_programmer.SerialProgrammer.program")
    def test_program_serial_failure(
        self, mock_program, mock_discover_path, unused_mock_discover_serial
    ):
        """Test case for program_serial failure."""
        mock_discover_path.return_value = "/dev/mock_ttyUSB0"
        mock_program.side_effect = serial_programmer.SerialProgrammerError(
            "Serial error", stdout="out", stderr="err"
        )
        request = servo_manufacturing_pb2.ProgramSerialRequest(
            serial_number="SERVOV4P1-C-2210050007"
        )
        response = self.servicer.program_serial(request, self.context)
        self.assertFalse(response.success)
        self.assertEqual(response.message, "Serial error")
        self.assertIn("STDOUT:\nout", response.logs)
        self.assertIn("STDERR:\nerr", response.logs)

    @mock.patch("server.rtk_eth_programmer.RTKEthProgrammer.program")
    @mock.patch("server.servo_manufacturing.ServoConsole")
    @mock.patch("server.util.discover_servo_v4p1_serial_path")
    def test_program_ethernet_success(
        self, mock_discover, unused_mock_console, mock_program
    ):
        """Test case for program_ethernet success."""
        mock_discover.return_value = "/dev/ttyUSB0"
        mock_program.return_value = None
        request = servo_manufacturing_pb2.ProgramEthernetRequest(
            mac_address="00:11:22:33:44:55", serial_number="12345"
        )
        response = self.servicer.program_ethernet(request, self.context)
        self.assertTrue(response.success)
        self.assertIn("programmed successfully", response.message)

    @mock.patch("server.atmega_kb_programmer.AtmegaKBEmulatorProgrammer.program")
    @mock.patch("server.servo_manufacturing.ServoConsole")
    @mock.patch("server.util.discover_servo_v4p1_serial_path")
    def test_program_atmega_success(
        self, mock_discover, unused_mock_console, mock_program
    ):
        """Test case for program_atmega success."""
        mock_discover.return_value = "/dev/ttyUSB0"
        mock_program.return_value = True
        request = servo_manufacturing_pb2.ProgramAtmegaRequest(serial_number="12345")
        response = self.servicer.program_atmega(request, self.context)
        self.assertTrue(response.success)
        self.assertEqual(response.message, "Atmega programmed successfully")

    @mock.patch("server.v4p1_tester.V4P1Tester.run_console_tests")
    @mock.patch("server.util.discover_servo_v4p1_serial_path")
    def test_run_console_tests_success(self, mock_discover, mock_run):
        """Test case for run_console_tests success."""
        mock_discover.return_value = "/dev/ttyUSB0"
        mock_run.return_value = [("test1", True)]
        request = servo_manufacturing_pb2.RunTestsRequest(serial_number="12345")
        response = self.loop.run_until_complete(
            self.servicer.run_console_tests(request, self.context)
        )
        self.assertTrue(response.all_passed)
        self.assertIn("test1: PASS", response.summary)

    @mock.patch("server.v4p1_tester.V4P1Tester.run_functional_tests")
    def test_run_functional_tests_success(self, mock_run):
        """Test case for run_functional_tests success."""
        mock_run.return_value = [("test2", True)]
        request = servo_manufacturing_pb2.RunTestsRequest(serial_number="12345")
        response = self.loop.run_until_complete(
            self.servicer.run_functional_tests(request, self.context)
        )
        self.assertTrue(response.all_passed)
        self.assertIn("test2: PASS", response.summary)

    @mock.patch("server.rtk_eth_programmer.RTKEthProgrammer.program")
    @mock.patch("server.servo_manufacturing.ServoConsole")
    @mock.patch("server.util.discover_servo_v4p1_serial_path")
    def test_program_ethernet_failure(
        self, mock_discover, unused_mock_console, mock_program
    ):
        """Test case for program_ethernet failure."""
        mock_discover.return_value = "/dev/ttyUSB0"
        mock_program.side_effect = rtk_eth_programmer.RTKEthProgrammerError(
            "Ethernet error", stdout="out", stderr="err"
        )
        request = servo_manufacturing_pb2.ProgramEthernetRequest(
            mac_address="00:11:22:33:44:55", serial_number="12345"
        )
        response = self.servicer.program_ethernet(request, self.context)
        self.assertFalse(response.success)
        self.assertEqual(response.message, "Ethernet error")
        self.assertIn("STDOUT:\nout", response.logs)
        self.assertIn("STDERR:\nerr", response.logs)


if __name__ == "__main__":
    unittest.main()
