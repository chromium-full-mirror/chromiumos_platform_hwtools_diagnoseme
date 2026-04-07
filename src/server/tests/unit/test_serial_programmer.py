# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for serial_programmer.py."""

import unittest
from unittest import mock

from server import serial_programmer
from server.servo_console import ServoConsoleError


class TestSerialProgrammer(unittest.TestCase):
    """Tests for SerialProgrammer."""

    # pylint: disable=arguments-differ
    @mock.patch("server.util.discover_servo_serial_path")
    @mock.patch("server.serial_programmer.ServoConsole")
    def setUp(self, mock_console_class, mock_discover_path):
        mock_discover_path.return_value = "/dev/mock_ttyUSB0"
        self.serial_number = "SERVOV4P1-C202602050001"
        self.programmer = serial_programmer.SerialProgrammer(self.serial_number)
        self.mock_console = mock_console_class.return_value

    def test_verify_success(self):
        """Test verify returns True on serial number match."""
        # Context manager enter returns the console instance
        self.mock_console.__enter__.return_value = self.mock_console

        # read_until returns the response containing serial number
        self.mock_console.read_until.return_value = (
            f"Serial number: {self.serial_number}\r\n"
        )

        self.assertTrue(self.programmer.verify())
        self.mock_console.send_cmd.assert_called_once_with("serialno")

    def test_verify_mismatch(self):
        """Test verify returns False on serial number mismatch."""
        self.mock_console.__enter__.return_value = self.mock_console
        self.mock_console.read_until.return_value = "Serial number: WRONG\r\n"

        self.assertFalse(self.programmer.verify())

    def test_verify_console_error(self):
        """Test verify returns False if console raises error."""
        self.mock_console.__enter__.side_effect = ServoConsoleError("Fail")
        self.assertFalse(self.programmer.verify())

    def test_program_success(self):
        """Test successful programming."""
        self.mock_console.__enter__.return_value = self.mock_console

        # Sequence:
        # 1. First verify: returns mismatch
        # 2. Second verify (after programming): returns match
        self.mock_console.read_until.side_effect = [
            "Serial number: WRONG\r\n",
            f"Serial number: {self.serial_number}\r\n",
        ]

        self.assertTrue(self.programmer.program())

        # Check calls
        self.mock_console.issue_cmd.assert_called_once_with(
            f"serialno set {self.serial_number}"
        )
        # send_cmd called three times (twice for serialno during verify,
        # once for reboot)
        self.assertEqual(self.mock_console.send_cmd.call_count, 3)
        self.mock_console.send_cmd.assert_has_calls(
            [
                mock.call("serialno"),
                mock.call("serialno"),
                mock.call("reboot"),
            ]
        )

    def test_program_already_correct(self):
        """Test program does nothing if serial is already correct."""
        self.mock_console.__enter__.return_value = self.mock_console
        self.mock_console.read_until.return_value = (
            f"Serial number: {self.serial_number}\r\n"
        )

        self.assertTrue(self.programmer.program())

        # Should verify but NOT issue set command
        self.mock_console.send_cmd.assert_called_once_with("serialno")
        self.mock_console.issue_cmd.assert_not_called()

    def test_program_verification_fails(self):
        """Test program raises error if verification fails after write."""
        self.mock_console.__enter__.return_value = self.mock_console
        # Always return mismatch
        self.mock_console.read_until.return_value = "Serial number: WRONG\r\n"

        with self.assertRaises(serial_programmer.SerialProgrammerError):
            self.programmer.program()

    def test_program_console_error(self):
        """Test program raises error if console raises error."""
        self.mock_console.__enter__.side_effect = ServoConsoleError("Fail")
        with self.assertRaises(serial_programmer.SerialProgrammerError):
            self.programmer.program()


if __name__ == "__main__":
    unittest.main()
