# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for servo_micro_manufacturing.py."""

import asyncio
import unittest
from unittest import mock

from google.protobuf import empty_pb2

# pylint: disable=no-member
# pylint: disable=no-name-in-module
# pylint: disable=import-error
# pylint: disable=duplicate-code
from server import serial_programmer
from server import servo_micro_manufacturing
from server import servo_programmer
from server.generated import servo_manufacturing_pb2


class TestServoMicroManufacturingServicer(unittest.TestCase):
    """Tests for ServoMicroManufacturingServicer."""

    def setUp(self):
        self.servicer = servo_micro_manufacturing.ServoMicroManufacturingServicer()
        self.context = mock.MagicMock()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

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
        mock_is_present.side_effect = [True, False]
        request = empty_pb2.Empty()
        response = self.servicer.get_device_presence(request, self.context)
        self.assertTrue(response.mcu_dfu_detected)
        self.assertFalse(response.servo_micro_detected)

    def test_validate_servo_serial_valid(self):
        """Test case for validate_servo_serial with valid input."""
        request = servo_manufacturing_pb2.ValidateServoSerialRequest(
            serial_number="MICRO-C-2210050007"
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
            serial_number="MICRO-C-2210050007"
        )
        response = self.servicer.program_serial(request, self.context)
        self.assertFalse(response.success)
        self.assertEqual(response.message, "Timeout waiting for Servo device")

    @mock.patch("time.sleep")
    @mock.patch("server.util.wait_for_usb_devices")
    @mock.patch("server.util.discover_servo_serial_path")
    @mock.patch("server.serial_programmer.SerialProgrammer.program")
    def test_program_serial_success(
        self, mock_program, mock_discover_path, mock_wait_usb, unused_mock_sleep
    ):
        """Test case for program_serial success."""
        mock_wait_usb.return_value = True
        mock_discover_path.return_value = "/dev/mock_ttyUSB0"
        mock_program.return_value = True
        request = servo_manufacturing_pb2.ProgramSerialRequest(
            serial_number="MICRO-C-2210050007"
        )
        response = self.servicer.program_serial(request, self.context)
        self.assertTrue(response.success)
        self.assertEqual(response.message, "Serial number programmed successfully")

    @mock.patch("time.sleep")
    @mock.patch("server.util.wait_for_usb_devices")
    @mock.patch("server.util.discover_servo_serial_path")
    @mock.patch("server.serial_programmer.SerialProgrammer.program")
    def test_program_serial_failure(
        self, mock_program, mock_discover_path, mock_wait_usb, unused_mock_sleep
    ):
        """Test case for program_serial failure."""
        mock_wait_usb.return_value = True
        mock_discover_path.return_value = "/dev/mock_ttyUSB0"
        mock_program.side_effect = serial_programmer.SerialProgrammerError(
            "Serial error", stdout="out", stderr="err"
        )
        request = servo_manufacturing_pb2.ProgramSerialRequest(
            serial_number="MICRO-C-2210050007"
        )
        response = self.servicer.program_serial(request, self.context)
        self.assertFalse(response.success)
        self.assertEqual(response.message, "Serial error")
        self.assertIn("STDOUT:\nout", response.logs)
        self.assertIn("STDERR:\nerr", response.logs)


if __name__ == "__main__":
    unittest.main()
