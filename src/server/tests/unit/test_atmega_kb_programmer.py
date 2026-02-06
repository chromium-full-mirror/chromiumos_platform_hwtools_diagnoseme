# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for atmega_kb_programmer.py."""

from pathlib import Path
import unittest
from unittest import mock

from server.atmega_kb_programmer import AtmegaKBEmulatorProgrammer
from server.servo_console import ServoConsole


class TestAtmegaKBEmulatorProgrammer(unittest.TestCase):
    """Tests for AtmegaKBEmulatorProgrammer."""

    def setUp(self):
        self.mock_console = mock.Mock(spec=ServoConsole)
        self.i2caddr = 0x20
        self.i2coffset = 0x0
        self.programmer = AtmegaKBEmulatorProgrammer(
            self.mock_console, self.i2caddr, self.i2coffset
        )

    @mock.patch("server.util.run_command")
    def test_verify_success(self, mock_run):
        """Test verify returns True on successful lsusb match."""
        # Mocking _toggle_reset which calls _rd_atmega_reg and _wr_atmega_reg
        # _rd_atmega_reg returns the current state.
        # I2C_RD_MASK is 0x2.
        self.mock_console.issue_cmd.return_value = "0x02 [2]"  # already on

        vid = self.programmer.ATM_LUFA_VID
        pid = self.programmer.ATM_LUFA_PID
        mock_run.return_value = mock.Mock(
            stdout=f"{vid:04x}:{pid:04x}",
            returncode=0,
        )

        self.assertTrue(self.programmer.verify())
        # verify() calls _toggle_reset(on=True) and then _verify()
        self.mock_console.issue_cmd.assert_any_call(
            f"i2cxfer r 1 0x{self.i2caddr:02x} {self.i2coffset}"
        )

    @mock.patch("server.util.run_command")
    def test_verify_fail(self, mock_run):
        """Test verify returns False on lsusb mismatch."""
        self.mock_console.issue_cmd.return_value = "0x02 [2]"  # already on
        mock_run.return_value = mock.Mock(
            stdout="Bus 001 Device 001: ID 0000:0000", returncode=0
        )

        self.assertFalse(self.programmer.verify())

    @mock.patch("server.util.run_command")
    @mock.patch("server.util.find_binfile")
    @mock.patch("time.sleep")  # speed up tests
    def test_program_success(self, unused_mock_sleep, mock_find, mock_run):
        """Test successful programming."""
        # Mocking internal methods to avoid complex side_effect on console
        with mock.patch.object(self.programmer, "_toggle_reset"), mock.patch.object(
            self.programmer, "_reboot_in_dfu_mode"
        ), mock.patch.object(
            self.programmer, "_reboot_in_normal_mode"
        ), mock.patch.object(
            self.programmer, "verify", return_value=True
        ):

            def run_side_effect(cmd, **unused_kwargs):
                if cmd == ["lsusb"]:
                    v = self.programmer.ATM_LUFA_VID
                    p = self.programmer.ATM_LUFA_PID
                    return mock.Mock(stdout=f"{v:04x}:{p:04x}")
                return mock.Mock(stdout="", returncode=0)

            mock_run.side_effect = run_side_effect
            mock_find.return_value = Path("/tmp/Keyboard.hex")

            self.assertTrue(self.programmer.program())


if __name__ == "__main__":
    unittest.main()
