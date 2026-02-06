# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for rtk_eth_programmer.py."""

from pathlib import Path
import unittest
from unittest import mock

from server import rtk_eth_programmer


class TestRTKEthProgrammer(unittest.TestCase):
    """Tests for RTKEthProgrammer."""

    def setUp(self):
        self.programmer = rtk_eth_programmer.RTKEthProgrammer(force=False)

    def test_standardize_macaddr(self):
        """Test macaddr standardization."""
        # pylint: disable=protected-access
        self.assertEqual(
            self.programmer._standardize_macaddr("aa-bb-cc-dd-ee-ff"),
            "AA:BB:CC:DD:EE:FF",
        )
        self.assertEqual(
            self.programmer._standardize_macaddr("AA:BB:CC:DD:EE:FF"),
            "AA:BB:CC:DD:EE:FF",
        )

    def test_macaddr_for_cfg(self):
        """Test macaddr format for .cfg files."""
        # pylint: disable=protected-access
        self.assertEqual(
            self.programmer._macaddr_for_cfg("AA:BB:CC:DD:EE:FF"), "AA BB CC DD EE FF"
        )

    @mock.patch("server.util.find_binfile")
    @mock.patch("shutil.copy")
    @mock.patch(
        "builtins.open",
        new_callable=mock.mock_open,
        read_data="NODEID = 00 00 00\nOTHER = 1",
    )
    def test_move_config(self, mock_file, mock_copy, mock_find):
        """Test preparing config files with unique macaddr."""
        mock_find.side_effect = [Path("secondary.cfg"), Path("template.cfg")]
        dst = Path("/tmp/test")
        macaddr = "11:22:33:44:55:66"

        # pylint: disable=protected-access
        self.programmer._move_config(dst, macaddr)

        mock_copy.assert_called_once_with(Path("secondary.cfg"), dst)
        mock_file.assert_any_call(Path("template.cfg"), "r", encoding="utf-8")

        # Verify that the macaddr was written to the output file
        # The mock_file() call for writing is the second one in the list
        handle = mock_file()
        calls = handle.writelines.call_args_list
        written_lines = calls[0][0][0]
        self.assertIn("NODEID = 11 22 33 44 55 66\n", written_lines)
        self.assertIn("OTHER = 1", written_lines)

    @mock.patch("server.util.find_binfile")
    @mock.patch("shutil.which")
    @mock.patch("tempfile.TemporaryDirectory")
    @mock.patch("server.util.run_command")
    def test_program_success(self, mock_run, mock_temp, mock_which, mock_find):
        """Test successful programming flow."""
        mock_find.return_value = Path("some_file")
        mock_which.return_value = "/usr/bin/rtunicpg"
        mock_temp.return_value.__enter__.return_value = "/tmp/fake_dir"

        servo_mcu_connector = mock.Mock()
        macaddr = "AA:BB:CC:DD:EE:FF"

        # Mock _move_config to avoid actual file IO
        with mock.patch.object(self.programmer, "_move_config"):
            self.programmer.program(macaddr, servo_mcu_connector)

        self.assertEqual(mock_run.call_count, 2)
        servo_mcu_connector.issue_cmd.assert_called_once_with(
            "macaddr set AA:BB:CC:DD:EE:FF"
        )

    @mock.patch("server.util.find_binfile")
    def test_program_missing_configs(self, mock_find):
        """Test program raises error if config files are missing."""
        mock_find.return_value = None
        with self.assertRaises(rtk_eth_programmer.RTKEthProgrammerError):
            self.programmer.program("AA:BB:CC:DD:EE:FF", mock.Mock())

    @mock.patch("server.util.find_binfile")
    @mock.patch("shutil.which")
    def test_program_missing_binary(self, mock_which, mock_find):
        """Test program raises error if programmer binary is missing."""
        mock_find.return_value = Path("some_file")
        mock_which.return_value = None
        with self.assertRaises(rtk_eth_programmer.RTKEthProgrammerError):
            self.programmer.program("AA:BB:CC:DD:EE:FF", mock.Mock())


if __name__ == "__main__":
    unittest.main()
