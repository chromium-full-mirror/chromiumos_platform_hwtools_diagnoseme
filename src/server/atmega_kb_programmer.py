# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Atmega USB Keyboard emulator programmer."""

import logging
import re
import subprocess
import time
from typing import Optional

from server import util
from server.servo_console import ServoConsole


class AtmegaKBEmulatorProgrammerError(Exception):
    """AtmegaKBEmulatorProgrammer error class."""

    def __init__(
        self,
        message: str,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
    ):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class AtmegaKBEmulatorProgrammer:
    """Atmega USB Keyboard emulator programmer."""

    NAME = "ATMEGA32U4 Programmer"

    # USB KB emulator firmware binary filename
    BIN = "Keyboard.hex"
    # The VID of the USB KB emulator. Same pre and post flashing.
    ATM_LUFA_VID = ATM_DFU_VID = 0x03EB
    # The PID of the USB KB emulator before programming/in DFU mode
    ATM_DFU_PID = 0x2FF4
    # The PID of the USB KB emulator after programming
    ATM_LUFA_PID = 0x2042

    # Binary to program the atmega programmer.
    PROGRAMMER_BIN = "dfu-programmer"

    # These are the programmer parts to erase and write with the tool
    BASE_CMD = [PROGRAMMER_BIN, "atmega32u4"]

    # command to erase the atmega chip content.
    ERASE_CMD = BASE_CMD + ["erase"]

    # command to write the binary to the atmega chip.
    WRITE_CMD = BASE_CMD + ["flash"]

    # The mask for the gpio expander input/output to determine reset signal
    # status.
    I2C_RD_MASK = 0x2

    # Time to wait after reset/power-cycle for the keyboard emulator to enumerate.
    ATM_BOOT_TIMEOUT_S = 10.0

    # The mask for the gpio expander input/output to determine reset signal
    # status. For Pin 1 on V4.1, this is 1 << 1 = 0x02.
    I2C_RD_MASK = 0x02

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        console: ServoConsole,
        i2caddr: int,
        i2coffset: int,
        *,
        parent_hub_vid: Optional[int] = None,
        parent_hub_pid: Optional[int] = None,
    ):
        """Initialize the programmer.

        Args:
          console: ServoConsole instance to communicate with servo ec
          i2caddr: i2c address of the reset pin on the gpio expander
          i2coffset: offset of the reset pin on the gpio expander
          parent_hub_vid: vid of the usb hub the keyboard emulator enumerates on
          parent_hub_pid: pid of the usb hub the keyboard emulator enumerates on
        """
        self._console = console
        self._parent_hub_vid = parent_hub_vid
        self._parent_hub_pid = parent_hub_pid
        self._i2caddr = i2caddr
        self._i2coffset = i2coffset
        # The command to read the reset enable pin.
        self._i2crd_cmd = f"i2cxfer r 1 0x{self._i2caddr:02x} {self._i2coffset}"
        # The command to write to the reset enable pin.
        self._i2cwr_cmd = f"i2cxfer w 1 0x{self._i2caddr:02x} {self._i2coffset}"

    def _find(self, timeout: float = ATM_BOOT_TIMEOUT_S) -> bool:
        """Check if the chip is present in either DFU or LUFA mode.

        Args:
          timeout: timeout in s for the chip to enumerate

        Returns:
          True if found, False otherwise.
        """
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            lsusb = util.run_command(["lsusb"]).stdout
            # Check for DFU mode
            if f"{self.ATM_DFU_VID:04x}:{self.ATM_DFU_PID:04x}" in lsusb:
                return True
            # Check for LUFA mode
            if f"{self.ATM_LUFA_VID:04x}:{self.ATM_LUFA_PID:04x}" in lsusb:
                return True
            time.sleep(0.5)
        return False

    def program(self) -> bool:
        """Program the Atmega chip.

        Returns:
          True on successful program, False otherwise
        """
        try:
            # Need to make sure that the chip is not in reset if possible.
            # Original: on=True means Release Reset (High)
            self._toggle_reset(on=True)
            self._reboot_in_dfu_mode()

            logging.info("Erasing Atmega chip...")
            logging.debug("Running: %s", " ".join(self.ERASE_CMD))
            util.run_command(self.ERASE_CMD)

            bin_path = util.find_binfile(self.BIN)
            if not bin_path:
                raise AtmegaKBEmulatorProgrammerError(f"Binary {self.BIN} not found")

            logging.info("Writing firmware...")
            write_cmd = self.WRITE_CMD + [str(bin_path)]
            logging.debug("Running: %s", " ".join(write_cmd))
            util.run_command(write_cmd)

            self._reboot_in_normal_mode()
            return self._verify()

        except (subprocess.CalledProcessError, AtmegaKBEmulatorProgrammerError) as e:
            logging.error("Failed to program Atmega: %s", e)
            if isinstance(e, subprocess.CalledProcessError):
                logging.error(util.get_logs_from_exception(e))
            return False

    def verify(self) -> bool:
        """Verify the programming state.

        Returns:
          True on successful verify, False otherwise
        """
        # Need to make sure that the chip is not in reset if possible.
        try:
            self._toggle_reset(on=True)
            return self._verify()
        except AtmegaKBEmulatorProgrammerError as e:
            logging.error("Verification failed: %s", e)
            return False

    def _rd_atmega_reg(self) -> int:
        """Get atmega reset signal value.

        Returns:
          int, i2c read result from RST register.
        """
        # Sample output: 0x8e [142]
        logging.debug("Running console command: %s", self._i2crd_cmd)
        output = self._console.issue_cmd(self._i2crd_cmd)
        match = re.search(r"(0x[0-9a-f][0-9a-f])\s\[\d+\]", output, re.IGNORECASE)
        if not match:
            raise AtmegaKBEmulatorProgrammerError(
                f"Failed to parse i2c read result: {output}"
            )
        return int(match.group(1), 16)

    def _wr_atmega_reg(self, wr_val: int) -> None:
        """Write to gpio expander to set atmega reset signal."""
        cmd = f"{self._i2cwr_cmd} 0x{wr_val:02x}"
        logging.debug("Running console command: %s", cmd)
        self._console.issue_cmd(cmd)

    def _toggle_dfu_mode(self, on: bool = True) -> None:
        """Toggle the DFU mode on the atmega to |on|."""
        # The command needs to be inverted as it's active low.
        val = int(not on)
        cmd = f"gpioset ATMEL_HWB_L {val}"
        logging.debug("Running console command: %s", cmd)
        self._console.issue_cmd(cmd)

    def _toggle_reset(self, on: bool = True) -> None:
        """Toggle the reset pin on the atmega to |on|.

        Match original 2021 logic: on=True is Release (High), on=False is Assert (Low).
        """
        reg = self._rd_atmega_reg()
        state = bool(reg & self.I2C_RD_MASK)
        if state == on:
            logging.debug(
                "Atmega reset already %s", "off (released)" if on else "on (asserted)"
            )
            return

        # One can flip the value simply.
        wr_val = reg ^ self.I2C_RD_MASK
        self._wr_atmega_reg(wr_val)
        time.sleep(0.2)  # Match original 0.2s timing

        reg = self._rd_atmega_reg()
        state = bool(reg & self.I2C_RD_MASK)
        if state != on:
            raise AtmegaKBEmulatorProgrammerError(
                f"Failed to turn atmega reset {'off' if on else 'on'}"
            )

    def _reboot_in_dfu_mode(self) -> None:
        """Wrapper to go through full dfu boot flow.
        Matches original: Assert (on=False) -> Set Straps -> Release (on=True)
        """
        self._toggle_reset(on=False)
        self._toggle_dfu_mode(on=True)
        self._toggle_reset(on=True)
        if not self._find(timeout=self.ATM_BOOT_TIMEOUT_S):
            raise AtmegaKBEmulatorProgrammerError("Failed to find Atmega in DFU mode")

    def _reboot_in_normal_mode(self) -> None:
        """Wrapper to go through full normal boot flow.
        Matches original: Assert (on=False) -> Clear Straps -> Release (on=True)
        """
        self._toggle_reset(on=False)
        self._toggle_dfu_mode(on=False)
        self._toggle_reset(on=True)
        if not self._find(timeout=self.ATM_BOOT_TIMEOUT_S):
            raise AtmegaKBEmulatorProgrammerError(
                "Failed to find Atmega in normal mode"
            )

    def _verify(self) -> bool:
        """Verify that the device came up with the LUFA pid."""
        lsusb = util.run_command(["lsusb"]).stdout
        if f"{self.ATM_LUFA_VID:04x}:{self.ATM_LUFA_PID:04x}" in lsusb:
            return True
        logging.warning("Failed to find atmega programmed PID.")
        return False
