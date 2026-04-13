# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for servo_programmer.py."""

import subprocess
import unittest
from unittest import mock

from server import servo_programmer


class TestServoProgrammer(unittest.TestCase):
    """Tests for ServoProgrammer."""

    def setUp(self):
        patcher = mock.patch("server.util.wait_for_usb_devices", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.programmer = servo_programmer.ServoProgrammer(
            board="test_board", dfu_vid=0x1234, dfu_pid=0x5678
        )

    @mock.patch("glob.glob")
    @mock.patch("pathlib.Path.stat")
    @mock.patch("server.util.run_command")
    @mock.patch("time.sleep")
    def test_program_success_servo_v4p1(
        self, unused_mock_sleep, mock_run, mock_stat, mock_glob
    ):
        """Test successful programming flow for servo_v4p1 using config path."""
        prog = servo_programmer.ServoProgrammer(
            board="servo_v4p1", dfu_vid=0x1234, dfu_pid=0x5678
        )
        mock_glob.return_value = ["/path/to/v4p1/fw.bin"]
        mock_stat.return_value = mock.Mock(st_size=1024)
        mock_run.return_value = mock.Mock(stdout="Success", returncode=0)

        with mock.patch("server.config.config.SERVO_V4P1_FW_DIR", "/path/to/v4p1"):
            self.assertTrue(prog.program())

        mock_glob.assert_called_with("/path/to/v4p1/*.bin")

    @mock.patch("glob.glob")
    @mock.patch("pathlib.Path.stat")
    @mock.patch("server.util.run_command")
    @mock.patch("time.sleep")
    def test_program_success_servo_micro(
        self, unused_mock_sleep, mock_run, mock_stat, mock_glob
    ):
        """Test successful programming flow for servo_micro using config path."""
        prog = servo_programmer.ServoProgrammer(
            board="servo_micro", dfu_vid=0x1234, dfu_pid=0x5678
        )
        mock_glob.return_value = ["/path/to/micro/fw.bin"]
        mock_stat.return_value = mock.Mock(st_size=1024)
        mock_run.return_value = mock.Mock(stdout="Success", returncode=0)

        with mock.patch("server.config.config.SERVO_MICRO_FW_DIR", "/path/to/micro"):
            self.assertTrue(prog.program())

        mock_glob.assert_called_with("/path/to/micro/*.bin")

    @mock.patch("glob.glob")
    @mock.patch("pathlib.Path.stat")
    @mock.patch("server.util.run_command")
    @mock.patch("time.sleep")
    def test_program_success(self, mock_sleep, mock_run, mock_stat, mock_glob):
        """Test successful programming flow."""
        mock_glob.return_value = ["/usr/local/test_board/fw.bin"]
        mock_stat.return_value = mock.Mock(st_size=1024)
        mock_run.return_value = mock.Mock(stdout="Success", returncode=0)

        self.assertTrue(self.programmer.program())

        # Verify erase and write were called
        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 2)

        # Verify command arguments for the last call (write)
        expected_write_cmd = [
            "dfu-util",
            "-a",
            "0",
            "-d",
            "1234:5678",
            "-s",
            "0x08000000:1024:leave",
            "-D",
            "/usr/local/test_board/fw.bin",
        ]
        mock_run.assert_called_with(
            expected_write_cmd,
            timeout=120,
        )

    @mock.patch("glob.glob")
    def test_program_no_binaries(self, mock_glob):
        """Test program raises error if no binaries found."""
        mock_glob.return_value = []
        with self.assertRaises(servo_programmer.ServoProgrammerError):
            self.programmer.program()

    @mock.patch("glob.glob")
    @mock.patch("pathlib.Path.stat")
    @mock.patch("server.util.run_command")
    @mock.patch("time.sleep")
    def test_program_erase_fail_code_1(
        self, unused_mock_sleep, mock_run, mock_stat, mock_glob
    ):
        """Test program returns False if erase fails with return code 1."""
        mock_glob.return_value = ["/usr/local/test_board/fw.bin"]
        mock_stat.return_value = mock.Mock(st_size=1024)
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "dfu-util", output="", stderr="already programmed"
        )

        with self.assertRaises(servo_programmer.ServoProgrammerError):
            self.programmer.program()

    @mock.patch("glob.glob")
    @mock.patch("pathlib.Path.stat")
    @mock.patch("server.util.run_command")
    @mock.patch("time.sleep")
    def test_program_write_fail_code_1(
        self, unused_mock_sleep, mock_run, mock_stat, mock_glob
    ):
        """Test program returns False if write fails with return code 1."""
        mock_glob.return_value = ["/usr/local/test_board/fw.bin"]
        mock_stat.return_value = mock.Mock(st_size=1024)
        # Success for erase, fail for write
        mock_run.side_effect = [
            mock.Mock(stdout="Erase Success", returncode=0),
            subprocess.CalledProcessError(
                1, "dfu-util", output="", stderr="write fail"
            ),
            subprocess.CalledProcessError(
                1, "dfu-util", output="", stderr="write fail"
            ),
            subprocess.CalledProcessError(
                1, "dfu-util", output="", stderr="write fail"
            ),
        ]

        with self.assertRaises(servo_programmer.ServoProgrammerError):
            self.programmer.program()

    @mock.patch("glob.glob")
    @mock.patch("pathlib.Path.stat")
    @mock.patch("server.util.run_command")
    @mock.patch("time.sleep")
    def test_program_erase_timeout(
        self, unused_mock_sleep, mock_run, mock_stat, mock_glob
    ):
        """Test program raises error if erase command times out."""
        mock_glob.return_value = ["/usr/local/test_board/fw.bin"]
        mock_stat.return_value = mock.Mock(st_size=1024)
        mock_run.side_effect = subprocess.TimeoutExpired("dfu-util", 120)

        with self.assertRaises(servo_programmer.ServoProgrammerError):
            self.programmer.program()

    @mock.patch("glob.glob")
    @mock.patch("pathlib.Path.stat")
    @mock.patch("server.util.run_command")
    @mock.patch("time.sleep")
    def test_program_fatal_error(
        self, unused_mock_sleep, mock_run, mock_stat, mock_glob
    ):
        """Test program raises error if subprocess fails with other code."""
        mock_glob.return_value = ["/usr/local/test_board/fw.bin"]
        mock_stat.return_value = mock.Mock(st_size=1024)
        mock_run.side_effect = subprocess.CalledProcessError(
            2, "dfu-util", output="", stderr="fatal error"
        )

        with self.assertRaises(servo_programmer.ServoProgrammerError):
            self.programmer.program()


if __name__ == "__main__":
    unittest.main()
