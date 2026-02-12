# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Programmer for the ethernet dongle found on v4, v4p1."""

import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Optional
from typing import TYPE_CHECKING

# pylint: disable=no-name-in-module
# pylint: disable=import-error
from server import util


if TYPE_CHECKING:
    from server.servo_console import ServoConsole


class RTKEthProgrammerError(Exception):
    """RTK eth programmer error class."""

    def __init__(
        self,
        message: str,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
    ):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class RTKEthProgrammer:
    """Class to program the rtk eth dongle."""

    # pylint: disable=too-few-public-methods

    NAME = "Ethernet Adapter (RTL8153)"

    # The VID/PID of the ethernet dongle showing up as a USB device.
    DONGLE_VID = 0x0BDA
    DONGLE_PID = 0x8153

    PROGRAMMER_BIN = "rtunicpg"

    # These are the programmer parts to erase and write with the tool
    BASE_CMD = [PROGRAMMER_BIN, "/eeprom", "/93c46"]

    ERASE_CMD = BASE_CMD + ["/erase"]

    WRITE_CMD = BASE_CMD + ["/w"]

    # These are config files to run the programming with the |PROGRAMMER_BIN|.
    # These need to contain (a) the right information (especially macaddr), and
    # be in the working directory when executing the programmer.
    CONFIG_TEMPLATE = "EE8153BvB.cfg"
    SECONDARY_CONFIG = "EF8153BvB.cfg"

    # These are the fields to write the desired values into.
    MACADDR_FIELDS = ["NODEID", "STARTID", "ENDID"]
    LED_CFG_FIELD = "LED_SEL_CFG"
    # Documented value for Servo V4.1: 24 8F
    LED_CFG_VALUE = "24 8F"

    # .cfg files have ; as a comment delim
    COMMENT_DELIM = ";"
    # .cfg files have ' = ' as a separator between keys and values
    KEY_VALUE_SEP = " = "

    def __init__(
        self,
        force: bool,
        parent_hub_vid: Optional[int] = None,
        parent_hub_pid3: Optional[int] = None,
        parent_hub_pid: Optional[int] = None,
    ):
        """Initialize the programmer.

        If the parent information is not provided, then only dongle presence is
        is checked, and not whether the dongle is hanging on the right hub.
        For the double detection _both_ vid and pid need to be provided.

        Args:
          force: whether to force programming if chip already appears programmed
          parent_hub_vid: vid for the hub the dongle is hanging on
          parent_hub_pid3: pid for the same hub but as usb3 that the dongle is
                           hanging on
          parent_hub_pid: pid for the hub the dongle is hanging on
        """
        self.force = force
        self._parent_hub_vid = parent_hub_vid
        self._parent_hub_pid = parent_hub_pid
        self._parent_hub_pid3 = parent_hub_pid3
        self._vid = self.DONGLE_VID
        self._pid = self.DONGLE_PID
        self._template_path: Optional[Path] = None

    def _standardize_macaddr(self, macaddr: str) -> str:
        """Standardize on : as a separator and upper-case.

        Args:
          macaddr: macaddr in any case with - or : as delimited

        Returns:
          macaddr: all upper case with exclusively : as delimiter
        """
        return macaddr.upper().replace("-", ":")

    def _macaddr_for_cfg(self, macaddr: str) -> str:
        """The .cfg format requires spaces rather than : as separators.

        Args:
          macaddr: macaddr (after it has been passed through |_standardize_macaddr|

        Returns:
          macaddr: with all delimiters (:) replaced with spaces
        """
        return self._standardize_macaddr(macaddr).replace(":", " ")

    def _move_config(self, dst: Path, macaddr: str) -> None:
        """Move and prepare the config files to |dst|.

        Since the original config files serve as a template, they need to be copied
        to a directory and modified to have the unique macaddr for a device before
        programming. This method does not clean up |dst| but merely populates it.

        Args:
          dst: directory to move the config files to
          macaddr: macaddr to program onto the chip
        """
        secondary_config = util.find_binfile(self.SECONDARY_CONFIG)
        if not secondary_config:
            raise RTKEthProgrammerError(
                f"Secondary config {self.SECONDARY_CONFIG} not found"
            )
        shutil.copy(secondary_config, dst)

        # The primary config needs to be rewritten so that the macaddr
        # is properly programmed.
        src = util.find_binfile(self.CONFIG_TEMPLATE)
        if not src:
            raise RTKEthProgrammerError(
                f"Primary config template {self.CONFIG_TEMPLATE} not found"
            )

        dst_file = dst / src.name
        macaddr_out = self._macaddr_for_cfg(macaddr)
        output = []

        with open(src, "r", encoding="utf-8") as s:
            for line in s:
                # Goal of this loop is to copy all lines that have
                # nothing to do with macaddr, and modify the macaddr ones so that
                # the resulting .cfg file can be used to program for a specific
                # macaddr.
                if line.startswith(self.COMMENT_DELIM):
                    output.append(line)
                elif self.KEY_VALUE_SEP not in line:
                    output.append(line)
                else:
                    k, _ = line.split(self.KEY_VALUE_SEP, 1)
                    if k in self.MACADDR_FIELDS:
                        output.append(f"{k}{self.KEY_VALUE_SEP}{macaddr_out}\n")
                    elif k == self.LED_CFG_FIELD:
                        output.append(f"{k}{self.KEY_VALUE_SEP}{self.LED_CFG_VALUE}\n")
                    else:
                        output.append(line)

        with open(dst_file, "w", encoding="utf-8") as d:
            d.writelines(output)

    def program(self, macaddr: str, servo_mcu_connector: "ServoConsole") -> None:
        """Helper to perform actual programming.

        Prepare the config files and program the ethernet chip using the
        binary, config files, and |macaddr|.

        Args:
          macaddr: macaddr to program onto the chip
          servo_mcu_connector: ServoConsole instance to talk to the servo EC
        """
        if not util.find_binfile(self.SECONDARY_CONFIG) or not util.find_binfile(
            self.CONFIG_TEMPLATE
        ):
            raise RTKEthProgrammerError(
                f"Config files missing: {self.SECONDARY_CONFIG} and/or "
                f"{self.CONFIG_TEMPLATE}"
            )

        program_bin = shutil.which(self.PROGRAMMER_BIN)
        if not program_bin:
            raise RTKEthProgrammerError(
                f"Programmer binary {self.PROGRAMMER_BIN} not found"
            )

        with tempfile.TemporaryDirectory() as program_dir:
            program_path = Path(program_dir)
            self._move_config(program_path, macaddr)

            try:
                if self.ERASE_CMD:
                    logging.info("Erasing RTK Ethernet EEPROM")
                    logging.debug("Running: %s", " ".join(self.ERASE_CMD))
                    erase_result = util.run_command(self.ERASE_CMD, cwd=program_dir)
                    logging.debug("ERASE result: %s", erase_result.stdout)

                logging.info("Writing RTK Ethernet EEPROM/eFUSE")
                logging.debug("Running: %s", " ".join(self.WRITE_CMD))
                write_result = util.run_command(self.WRITE_CMD, cwd=program_dir)
                logging.debug("WRITE result: %s", write_result.stdout)

                # If everything went well so far, then we can write the macaddr
                # into the servo eeprom as well.
                cmd = f"macaddr set {self._standardize_macaddr(macaddr)}"
                logging.debug("Running console command: %s", cmd)
                servo_mcu_connector.issue_cmd(cmd)
            except subprocess.CalledProcessError as e:
                logging.exception("Error during RTK Ethernet programming")
                raise RTKEthProgrammerError(
                    f"Issue during programming: {e}",
                    stdout=e.stdout,
                    stderr=e.stderr,
                ) from e
            except Exception as e:
                logging.exception("Error during RTK Ethernet programming")
                raise RTKEthProgrammerError(f"Issue during programming: {e}") from e

        # Wait for the device to come back.
        # device_util.wait_for_usb_device(vid=self._vid, pid=self._pid)
        # Re-enable device reset/wait if needed and if dependencies are available.
        logging.info(
            "RTK Ethernet programming complete. Re-enumeration may be required."
        )
