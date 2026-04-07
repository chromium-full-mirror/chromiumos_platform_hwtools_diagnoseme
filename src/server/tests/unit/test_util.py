# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for util.py."""

from pathlib import Path
import unittest
from unittest import mock

from server import util


class TestUtil(unittest.TestCase):
    """Tests for utility functions."""

    def test_get_bindir(self):
        """Test get_bindir returns a Path object ending with BIN_DIR."""
        bindir = util.get_bindir()
        self.assertIsInstance(bindir, Path)
        self.assertEqual(bindir.name, util.BIN_DIR)
        self.assertTrue(bindir.is_absolute())

    @mock.patch("pathlib.Path.exists")
    def test_find_binfile_found(self, mock_exists):
        """Test find_binfile returns Path when file exists."""
        mock_exists.return_value = True
        filename = "test_file.bin"
        result = util.find_binfile(filename)

        self.assertIsInstance(result, Path)
        self.assertEqual(result.name, filename)
        self.assertEqual(result.parent.name, util.BIN_DIR)

    @mock.patch("pathlib.Path.exists")
    def test_find_binfile_not_found(self, mock_exists):
        """Test find_binfile returns None when file does not exist."""
        mock_exists.return_value = False
        result = util.find_binfile("nonexistent.bin")
        self.assertIsNone(result)

    @mock.patch("os.path.exists")
    @mock.patch("os.listdir")
    def test_discover_servo_serial_path_v4p1(self, mock_listdir, mock_exists):
        """Test discover_servo_serial_path finds v4p1 serial path."""
        mock_exists.return_value = True
        mock_listdir.return_value = [
            "usb-Google_Inc._Servo_V4p1_Uninitialized-if00-port0",
            "usb-Google_Inc._Servo_V4p1_Uninitialized-if01-port0",
        ]
        result = util.discover_servo_serial_path(board="v4p1")
        self.assertEqual(
            result,
            "/dev/serial/by-id/usb-Google_Inc._Servo_V4p1_Uninitialized-if00-port0",
        )

    @mock.patch("os.path.exists")
    @mock.patch("os.listdir")
    def test_discover_servo_serial_path_micro(self, mock_listdir, mock_exists):
        """Test discover_servo_serial_path finds micro serial path with if03."""
        mock_exists.return_value = True
        mock_listdir.return_value = [
            "usb-Google_Inc._Servo_Micro_Uninitialized-if00-port0",
            "usb-Google_Inc._Servo_Micro_Uninitialized-if03-port0",
        ]
        result = util.discover_servo_serial_path(board="micro", target_if="if03")
        self.assertEqual(
            result,
            "/dev/serial/by-id/usb-Google_Inc._Servo_Micro_Uninitialized-if03-port0",
        )

    @mock.patch("server.util.discover_servo_serial_path")
    def test_discover_servo_v4p1_serial_path(self, mock_discover):
        """Test discover_servo_v4p1_serial_path delegates properly."""
        mock_discover.return_value = "ttyUSB0"
        result = util.discover_servo_v4p1_serial_path("12345", 5, 0.1)
        self.assertEqual(result, "ttyUSB0")
        mock_discover.assert_called_once_with("12345", 5, 0.1, "v4p1", "if00")

    @mock.patch("server.util.discover_servo_serial_path")
    def test_discover_servo_micro_serial_path(self, mock_discover):
        """Test discover_servo_micro_serial_path delegates properly."""
        mock_discover.return_value = "ttyUSB1"
        result = util.discover_servo_micro_serial_path("12345", 5, 0.1)
        self.assertEqual(result, "ttyUSB1")
        mock_discover.assert_called_once_with("12345", 5, 0.1, "servo_micro", "if03")


if __name__ == "__main__":
    unittest.main()
