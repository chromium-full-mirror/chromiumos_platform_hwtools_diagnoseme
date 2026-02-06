# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for servo_manufacturing.py."""

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
from server import servo_manufacturing_pb2
from server import servo_programmer


class TestServoV41ManufacturingServicer(unittest.TestCase):
    """Tests for ServoV41ManufacturingServicer."""

    def setUp(self):
        self.servicer = servo_manufacturing.ServoV41ManufacturingServicer()
        self.context = mock.MagicMock()

    def test_get_status(self):
        """Test case for get_status."""
        request = empty_pb2.Empty()
        response = self.servicer.get_status(request, self.context)
        self.assertEqual(response.status, "OK")

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

    @mock.patch("server.genesys_hub_programmer.GenesysHubProgrammer.program")
    def test_program_genesys_hub_success(self, mock_program):
        """Test case for program_genesys_hub success."""
        mock_program.return_value = True
        request = servo_manufacturing_pb2.ProgramGenesysHubRequest()
        response = self.servicer.program_genesys_hub(request, self.context)
        self.assertTrue(response.success)
        self.assertEqual(response.message, "Genesys Hub programmed successfully")

    @mock.patch("server.genesys_hub_programmer.GenesysHubProgrammer.program")
    def test_program_genesys_hub_failure(self, mock_program):
        """Test case for program_genesys_hub failure."""
        mock_program.side_effect = genesys_hub_programmer.GenesysHubProgrammerError(
            "Hardware error", stdout="out", stderr="err"
        )
        request = servo_manufacturing_pb2.ProgramGenesysHubRequest()
        response = self.servicer.program_genesys_hub(request, self.context)
        self.assertFalse(response.success)
        self.assertEqual(response.message, "Hardware error")
        self.assertIn("STDOUT:\nout", response.logs)
        self.assertIn("STDERR:\nerr", response.logs)

    @mock.patch("server.servo_programmer.ServoProgrammer.program")
    def test_program_mcu_success(self, mock_program):
        """Test case for program_mcu success."""
        mock_program.return_value = True
        request = servo_manufacturing_pb2.ProgramMcuRequest(
            firmware_path="/path/to/firmware.bin"
        )
        response = self.servicer.program_mcu(request, self.context)
        self.assertTrue(response.success)
        self.assertIn("MCU programmed successfully", response.message)

    @mock.patch("server.servo_programmer.ServoProgrammer.program")
    def test_program_mcu_failure(self, mock_program):
        """Test case for program_mcu failure."""
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

    @mock.patch("server.serial_programmer.SerialProgrammer.program")
    def test_program_serial_success(self, mock_program):
        """Test case for program_serial success."""
        mock_program.return_value = True
        request = servo_manufacturing_pb2.ProgramSerialRequest(
            serial_number="SERVOV4P1-C-2210050007"
        )
        response = self.servicer.program_serial(request, self.context)
        self.assertTrue(response.success)
        self.assertEqual(response.message, "Serial number programmed successfully")

    @mock.patch("server.serial_programmer.SerialProgrammer.program")
    def test_program_serial_failure(self, mock_program):
        """Test case for program_serial failure."""
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
    @mock.patch("server.servo_console.ServoConsole")
    def test_program_ethernet_success(self, unused_mock_console, mock_program):
        """Test case for program_ethernet success."""
        mock_program.return_value = None
        request = servo_manufacturing_pb2.ProgramEthernetRequest(
            mac_address="00:11:22:33:44:55", serial_number="12345"
        )
        response = self.servicer.program_ethernet(request, self.context)
        self.assertTrue(response.success)
        self.assertIn("programmed successfully", response.message)

    @mock.patch("server.rtk_eth_programmer.RTKEthProgrammer.program")
    @mock.patch("server.servo_console.ServoConsole")
    def test_program_ethernet_failure(self, unused_mock_console, mock_program):
        """Test case for program_ethernet failure."""
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
