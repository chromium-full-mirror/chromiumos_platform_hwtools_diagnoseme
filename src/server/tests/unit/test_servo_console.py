# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for servo_console.py."""

import unittest
from unittest import mock

from server import servo_console


# pylint: disable=protected-access
class TestServoConsole(unittest.TestCase):
    """Tests for ServoConsole."""

    def setUp(self):
        self.port = "/dev/ttyUSB0"
        self.console = servo_console.ServoConsole(self.port)

    @mock.patch("serial.Serial")
    def test_check_connection_success(self, mock_serial_class):
        """Test _check_connection succeeds when help command returns serialno."""
        mock_ser = mock_serial_class.return_value
        mock_ser.in_waiting = True
        mock_ser.read.return_value = b"Available commands:\nserialno\nhelp\n"

        # Should not raise exception
        self.console._check_connection(mock_ser)
        # We expect a clean prompt \r\n and then help\r
        mock_ser.write.assert_has_calls([mock.call(b"\r\n"), mock.call(b"help\r")])

    @mock.patch("serial.Serial")
    def test_check_connection_fail(self, mock_serial_class):
        """Test _check_connection fails when help command result is missing."""
        mock_ser = mock_serial_class.return_value
        mock_ser.in_waiting = True
        mock_ser.read.return_value = b"Unknown command\n"

        with self.assertRaisesRegex(
            servo_console.ServoConsoleError, "unable to connect"
        ):
            self.console._check_connection(mock_ser)

    @mock.patch("serial.Serial")
    def test_issue_cmd_ephemeral(self, mock_serial_class):
        """Test issue_cmd opens/closes connection if not persistent."""
        mock_ser = mock_serial_class.return_value.__enter__.return_value
        # First read for _check_connection, second for command response
        mock_ser.in_waiting = True
        mock_ser.read.side_effect = [
            b"serialno\n",
            b"Command response\n",
        ]
        mock_ser.read_all.return_value = b"Command response\n"

        response = self.console.issue_cmd("test")

        self.assertEqual(response, "Command response\n")
        # Check connection + send command
        mock_ser.write.assert_any_call(b"help\r")
        mock_ser.write.assert_any_call(b"test\r")

    @mock.patch("serial.Serial")
    def test_issue_cmd_persistent(self, mock_serial_class):
        """Test issue_cmd reuses connection if persistent."""
        mock_ser = mock_serial_class.return_value
        mock_ser.in_waiting = True

        # side_effect for read:
        # 1. _check_connection (serialno)
        # 2. issue_cmd response (Persistent response)
        # Note: read_all is used in issue_cmd_on_ser, read is used in check_connection
        mock_ser.read.return_value = b"serialno\n"
        mock_ser.read_all.return_value = b"Persistent response"

        # Enter context manager
        with self.console:
            # Override read/read_all for the actual command
            mock_ser.read_all.return_value = b"Persistent response"

            response = self.console.issue_cmd("test_persist")
            self.assertEqual(response, "Persistent response")

            # verify _check_connection was called once (in __enter__)
            # and issue_cmd wrote to the same serial object without reopening
            self.assertEqual(mock_serial_class.call_count, 1)
            mock_ser.write.assert_any_call(b"test_persist\r")

    @mock.patch("serial.Serial")
    def test_legacy_shim(self, mock_serial_class):
        """Test compatibility shim .pty._issue_cmd()."""
        mock_ser = mock_serial_class.return_value.__enter__.return_value
        mock_ser.in_waiting = True
        mock_ser.read.return_value = b"serialno\n"
        mock_ser.read_all.return_value = b"Legacy response"

        # pty points to self
        self.assertIs(self.console.pty, self.console)

        response = self.console._issue_cmd("legacy")
        self.assertEqual(response, "Legacy response")
        mock_ser.write.assert_any_call(b"legacy\r")


if __name__ == "__main__":
    unittest.main()
