# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for hal.py."""

import unittest
from unittest import mock

from server.config import config
from server.hal import HardwareManager


# pylint: disable=too-many-arguments,too-many-positional-arguments,unused-argument
class TestHardwareManager(unittest.TestCase):
    """Tests for HardwareManager."""

    def setUp(self):
        self.hm = HardwareManager()

    @mock.patch("os.path.exists")
    @mock.patch("os.listdir")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    @mock.patch("os.path.realpath")
    @mock.patch("server.hal.HardwareManager.reset_usb_device")
    def test_reset_servo_v4p1_dut_hub_found(
        self, mock_reset, mock_realpath, mock_open, mock_listdir, mock_exists
    ):
        """Test reset_servo_v4p1_dut_hub when device is found."""
        mock_exists.return_value = True
        mock_listdir.return_value = ["dev1"]
        mock_realpath.return_value = "/sys/bus/usb/devices/dev1"

        # Mock VID and PID files
        def side_effect(path, *args, **kwargs):
            if "idVendor" in path:
                return mock.mock_open(read_data=config.CYPRESS_HUB_VID).return_value
            if "idProduct" in path:
                return mock.mock_open(read_data=config.CYPRESS_HUB_PID).return_value
            if "busnum" in path:
                return mock.mock_open(read_data="1").return_value
            if "devnum" in path:
                return mock.mock_open(read_data="2").return_value
            return mock.mock_open().return_value

        mock_open.side_effect = side_effect
        mock_reset.return_value = True

        result = self.hm.reset_servo_v4p1_dut_hub()

        self.assertTrue(result)
        mock_reset.assert_called_once_with("/dev/bus/usb/001/002")

    @mock.patch("os.path.exists")
    @mock.patch("os.listdir")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    def test_reset_servo_v4p1_dut_hub_not_found(
        self, mock_open, mock_listdir, mock_exists
    ):
        """Test reset_servo_v4p1_dut_hub when device is not found."""
        mock_exists.return_value = True
        mock_listdir.return_value = ["dev1"]

        # Mock VID and PID files with wrong values
        def side_effect(path, *args, **kwargs):
            if "idVendor" in path:
                return mock.mock_open(read_data="wrong").return_value
            if "idProduct" in path:
                return mock.mock_open(read_data="wrong").return_value
            return mock.mock_open().return_value

        mock_open.side_effect = side_effect

        result = self.hm.reset_servo_v4p1_dut_hub()

        self.assertFalse(result)

    @mock.patch("os.path.exists")
    @mock.patch("os.listdir")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    @mock.patch("os.path.realpath")
    @mock.patch("server.hal.HardwareManager.reset_usb_device")
    def test_reset_servo_v4p1_dut_hub_found_pid3(
        self, mock_reset, mock_realpath, mock_open, mock_listdir, mock_exists
    ):
        """Test reset_servo_v4p1_dut_hub when device is found with PID3."""
        mock_exists.return_value = True
        mock_listdir.return_value = ["dev1"]
        mock_realpath.return_value = "/sys/bus/usb/devices/dev1"

        # Mock VID and PID files
        def side_effect(path, *args, **kwargs):
            if "idVendor" in path:
                return mock.mock_open(read_data=config.CYPRESS_HUB_VID).return_value
            if "idProduct" in path:
                return mock.mock_open(read_data=config.CYPRESS_HUB_PID3).return_value
            if "busnum" in path:
                return mock.mock_open(read_data="1").return_value
            if "devnum" in path:
                return mock.mock_open(read_data="2").return_value
            return mock.mock_open().return_value

        mock_open.side_effect = side_effect
        mock_reset.return_value = True

        result = self.hm.reset_servo_v4p1_dut_hub()

        self.assertTrue(result)
        mock_reset.assert_called_once_with("/dev/bus/usb/001/002")


if __name__ == "__main__":
    unittest.main()
