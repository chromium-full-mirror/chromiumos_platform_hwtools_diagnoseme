# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Programmer to perform DFU flashing on servo devices."""

import glob
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Optional

from server import util
from server.config import config


class ServoProgrammerError(Exception):
    """Error type for servo programming issues."""

    def __init__(
        self,
        message: str,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
    ):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


# pylint: disable=too-few-public-methods
class ServoProgrammer:
    """Class to perform DFU flashing on the servo stm."""

    NAME = "Servo Firmware"

    # The default logger uses 'Programmer' but this is technically a flasher.
    LOGGER_SUFFIX = "Flasher"

    PROGRAMMER_BIN = "dfu-util"

    BASE_CMD = [PROGRAMMER_BIN, "-a", "0"]

    ADDR = 0x08000000

    # Timeout after which to kill the subprocess. 2 minutes here.
    # Note: this is used twice. one, then a warning is printed, then once again.
    PROCESS_TIMEOUT_S = 2 * 60

    def __init__(self, board: str, dfu_vid: int, dfu_pid: int):
        """Initialize the logger.

        Args:
          board: servo board name
          dfu_vid: servo VID in DFU mode
          dfu_pid: servo PID in DFU mode
        """
        self._vid = dfu_vid
        self._pid = dfu_pid
        self._board = board
        # |_bin| and |_size| get populated during programming.
        self._bin: Optional[Path] = None
        self._size: Optional[int] = None

    def program(self) -> bool:
        # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        """Helper to perform actual programming."""
        if self._board == "servo_v4p1":
            fw_dir = config.SERVO_V4P1_FW_DIR
        elif self._board == "servo_micro":
            fw_dir = config.SERVO_MICRO_FW_DIR
        else:
            fw_dir = f"/usr/local/{self._board}"

        search_path = os.path.join(fw_dir, "*.bin")
        bins = glob.glob(search_path)
        if not bins:
            raise ServoProgrammerError(
                f"No firmware binaries found for board {self._board} in {fw_dir}"
            )

        self._bin = Path(bins[0])
        self._size = self._bin.stat().st_size

        id_cmd = ["-d", f"{self._vid:04x}:{self._pid:04x}"]
        file_cmd = ["-D", str(self._bin)]

        base_address = f"0x{self.ADDR:08x}:{self._size}"
        image_address = f"{base_address}:leave"
        erase_address = f"{base_address}:unprotect:force:leave"

        erase_cmd = self.BASE_CMD + id_cmd + ["-s", erase_address] + file_cmd
        write_cmd = self.BASE_CMD + id_cmd + ["-s", image_address] + file_cmd

        time.sleep(2)
        try:
            logging.debug("Running unprotect/erase command: %s", " ".join(erase_cmd))
            util.run_command(erase_cmd, timeout=self.PROCESS_TIMEOUT_S)
        except subprocess.CalledProcessError as e:
            # The unprotect command causes a mass erase and device reset, which
            # invariably results in a LIBUSB_ERROR_PIPE when the connection drops.
            # We log it and gracefully proceed.
            logging.info(
                "Erase command returned non-zero (expected if chip resets): %s\n%s",
                e.returncode,
                e.stderr,
            )
        except subprocess.TimeoutExpired:
            logging.warning("Erase command timed out (ignoring)")

        # Wait for device to finish mass-erase and re-enumerate
        time.sleep(5)
        util.wait_for_usb_devices([(self._vid, [self._pid])], timeout=15.0)

        write_success = False
        last_error = None
        for attempt in range(3):
            try:
                logging.debug(
                    "Running write command (attempt %d): %s",
                    attempt + 1,
                    " ".join(write_cmd),
                )
                write_result = util.run_command(
                    write_cmd, timeout=self.PROCESS_TIMEOUT_S
                )
                logging.debug("WRITE result: %s", write_result.stdout)
                write_success = True
                break
            except subprocess.CalledProcessError as e:
                # The :leave modifier causes a device reset, which often
                # results in a libusb control transfer error when the connection drops.
                stderr_upper = e.stderr.upper()
                stdout_upper = e.stdout.upper()
                if (
                    "LIBUSB" in stderr_upper
                    or "ERROR DURING DOWNLOAD" in stderr_upper
                    or "LEAVE" in stderr_upper
                    or "DISCONNECTS" in stdout_upper
                    or "RESETS" in stdout_upper
                ):
                    logging.info(
                        "Write command caused device reset (expected from :leave)."
                    )
                    write_success = True
                    break

                logging.warning(
                    "Error during write attempt %d: %s", attempt + 1, e.stderr
                )
                last_error = e
                time.sleep(3)
                util.wait_for_usb_devices([(self._vid, [self._pid])], timeout=15.0)
            except subprocess.TimeoutExpired as e:
                logging.warning("Write command timed out on attempt %d", attempt + 1)
                last_error = e
                time.sleep(3)
                util.wait_for_usb_devices([(self._vid, [self._pid])], timeout=15.0)

        if not write_success:
            raise ServoProgrammerError(
                "Issue on write. Giving up.",
                stdout=getattr(last_error, "stdout", None),
                stderr=getattr(last_error, "stderr", None),
            )

        return True
