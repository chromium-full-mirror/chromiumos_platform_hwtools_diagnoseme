# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for v4p1_tester.py."""

import asyncio
import unittest
from unittest import mock

# pylint: disable=no-member
# pylint: disable=no-name-in-module
# pylint: disable=import-error
from server import v4p1_tester


class TestV4P1Tester(unittest.TestCase):
    """Tests for V4P1Tester."""

    def setUp(self):
        self.tester = v4p1_tester.V4P1Tester(
            serial_number="SERVOV4P1-C-2210050007",
            mac_address="00:11:22:33:44:55",
            servod_address="localhost:6002",
        )
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_test_console_success(self):
        """Test test_console with successful regex match."""
        mock_console = mock.MagicMock()
        mock_console.issue_cmd.return_value = "serialno: SERVOV4P1-C-2210050007"

        result = self.tester.test_console(
            mock_console, "serial", r"SERVOV4P1", "serialno"
        )
        self.assertTrue(result)
        mock_console.issue_cmd.assert_called_with("serialno")

    def test_test_console_failure(self):
        """Test test_console with regex mismatch."""
        mock_console = mock.MagicMock()
        mock_console.issue_cmd.return_value = "serialno: UNKNOWN"

        result = self.tester.test_console(
            mock_console, "serial", r"SERVOV4P1", "serialno"
        )
        self.assertFalse(result)

    @mock.patch("server.util.run_command")
    @mock.patch("server.v4p1_tester.ServoConsole")
    def test_run_console_tests(self, mock_console_class, mock_run_command):
        """Test run_console_tests executes all expected checks."""
        mock_run_command.return_value.stdout = "18d1:520d"
        mock_console = mock_console_class.return_value.__enter__.return_value
        # Mock responses for serialno, macaddr get, pd role, and i2cxfer
        mock_console.issue_cmd.side_effect = [
            "SERVOV4P1-C-2210050007",  # serialno
            "00 11 22 33 44 55",  # macaddr get
            "Role: SNK",  # pd 0 state
            "20: -- 0x21 -- -- -- -- -- -- -- -- -- -- -- -- --\n"
            "40: 0x40 0x41 0x42 -- -- -- -- -- 0x48 0x49 -- -- -- -- --\n"
            "50: 0x50 -- -- -- -- -- -- -- -- -- -- -- -- -- --",  # i2cscan 1
        ]

        # We need to mock test_usb_mux as it involves sysfs access
        with mock.patch.object(self.tester, "test_usb_mux", return_value=True):
            results = self.tester.run_console_tests("/dev/ttyUSB0")

        self.assertTrue(all(passed for _, passed in results))
        self.assertIn(("lsusb Servo Presence", True), results)

    @mock.patch("grpc.aio.insecure_channel")
    @mock.patch("server.v4p1_tester.ServodRpcServiceStub")
    def test_run_integration_tests(self, mock_stub_class, unused_mock_channel):
        """Test run_integration_tests via async gRPC."""
        mock_stub = mock_stub_class.return_value

        # Mock responses for console connectivity and INA
        async def mock_run_dut_control(request):
            response = mock.MagicMock()
            response.error_code = 0
            if "uart_pty" in request.command:
                response.result = "/dev/pts/1"
            else:
                response.result = "12.34"
            return response

        mock_stub.run_dut_control.side_effect = mock_run_dut_control

        # Need to mock os.path.exists for PTY check
        with mock.patch("os.path.exists", return_value=True):
            results = self.loop.run_until_complete(self.tester.run_integration_tests())

        self.assertTrue(all(passed for _, passed in results))
        self.assertIn(("EC Console Connectivity", True), results)


if __name__ == "__main__":
    unittest.main()
