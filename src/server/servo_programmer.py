# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Programmer to perform DFU flashing on servo devices."""

import glob
import logging
from pathlib import Path
import subprocess
import time
from typing import Optional


class ServoProgrammerError(Exception):
    """Error type for servo programming issues."""


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
        """Helper to perform actual programming."""
        search_path = f"/usr/local/{self._board}/*.bin"
        bins = glob.glob(search_path)
        if not bins:
            raise ServoProgrammerError(
                f"No firmware binaries found for board {self._board} in "
                f"/usr/local/{self._board}/"
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
            logging.debug("Running erase command: %s", " ".join(erase_cmd))
            erase_result = subprocess.run(
                erase_cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=self.PROCESS_TIMEOUT_S,
            )
            logging.debug("ERASE result: %s", erase_result.stdout)
        except subprocess.CalledProcessError as e:
            if e.returncode == 1:
                logging.info(
                    "Erase failed with return code 1. This may be due to the device "
                    "already being at the target version."
                )
                return False
            logging.exception("Error during erase: %s", e.stderr)
            raise ServoProgrammerError("Issue on erase. Giving up.") from e
        except subprocess.TimeoutExpired as e:
            logging.exception("Erase command timed out")
            raise ServoProgrammerError("Erase command timed out.") from e

        time.sleep(2)
        try:
            logging.debug("Running write command: %s", " ".join(write_cmd))
            write_result = subprocess.run(
                write_cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=self.PROCESS_TIMEOUT_S,
            )
            logging.debug("WRITE result: %s", write_result.stdout)
        except subprocess.CalledProcessError as e:
            if e.returncode == 1:
                logging.info(
                    "Write failed with return code 1. This may be due to the device "
                    "already being at the target version."
                )
                return False
            logging.exception("Error during write: %s", e.stderr)
            raise ServoProgrammerError("Issue on write. Giving up.") from e
        except subprocess.TimeoutExpired as e:
            logging.exception("Write command timed out")
            raise ServoProgrammerError("Write command timed out.") from e

        return True
