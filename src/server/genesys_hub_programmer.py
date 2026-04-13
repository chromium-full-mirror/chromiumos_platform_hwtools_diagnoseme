# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Programmer for host side facing usb hub on v4p1 (genesys GL3590)."""

import logging
import os
import re
import shutil
import subprocess

from server import util
from server.config import config


class GenesysHubProgrammerError(Exception):
    """Genesys hub (GL3590) error class."""

    def __init__(
        self,
        message: str,
        stdout: os.PathLike | str | None = None,
        stderr: os.PathLike | str | None = None,
    ):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class GenesysHubProgrammer:
    """Class to program the genesys hub."""

    PROGRAMMER_BIN = "/usr/bin/fwupdtool"

    FW_VERSION = "64.18"

    # This is the file to program.
    FW_BIN = os.path.join(
        config.GENESYS_FW_DIR, f"GenesysLogic_GL3590_{FW_VERSION}.cab"
    )

    READ_CMD = [PROGRAMMER_BIN, "get-devices", "--plugins", "genesys"]

    WRITE_CMD = [
        PROGRAMMER_BIN,
        "install",
        "--plugins",
        "genesys",
        "--filter=updatable",
    ]

    VERSION_REGEX = re.compile(r"Current version:\s+(\d+.\d+)")

    def __init__(self, force=False):
        """Initialize the programmer.

        Args:
          force: whether to force programming if chip already appears programmed
        """
        self.force = force

    def program(self):
        """Helper to perform actual programming."""
        program_bin = shutil.which(self.PROGRAMMER_BIN)
        if not program_bin:
            raise GenesysHubProgrammerError(
                f"Programmer binary {self.PROGRAMMER_BIN} not found"
            )
        cwd = os.path.dirname(program_bin)

        # No need to program a chip already at the correct version.
        if not self.verify():
            fw_file = util.find_binfile(self.FW_BIN)
            if not fw_file:
                raise GenesysHubProgrammerError(
                    f"Firmware file {self.FW_BIN} not found"
                )

            write_cmd = list(self.WRITE_CMD)
            if self.force:
                write_cmd.append("--force")
            write_cmd.append(str(fw_file))
            logging.debug("write_cmd: %s", write_cmd)
            try:
                write_result = util.run_command(
                    write_cmd,
                    cwd=cwd,
                )
                logging.debug("WRITE result: %s", write_result.stdout)
            except subprocess.CalledProcessError as e:
                if e.returncode == 1:
                    logging.info(
                        "Programming failed with return code 1. This may be due "
                        "to the device already being at the target version."
                    )
                    return False
                logging.exception("Error during programming: %s", e.stderr)
                raise GenesysHubProgrammerError(
                    "Issue on write. Giving up.", stdout=e.stdout, stderr=e.stderr
                ) from e
        return True

    def verify(self):
        """Helper to verify that programming succeeded."""
        program_bin = shutil.which(self.PROGRAMMER_BIN)
        if not program_bin:
            raise GenesysHubProgrammerError(
                f"Programmer binary {self.PROGRAMMER_BIN} not found"
            )
        cwd = os.path.dirname(program_bin)

        try:
            read_result = util.run_command(
                self.READ_CMD,
                cwd=cwd,
            )
            logging.debug("READ result: %s", read_result.stdout)
        except subprocess.CalledProcessError as e:
            logging.exception("Error during validation: %s", e.stderr)
            raise GenesysHubProgrammerError(
                "Issue on read. Giving up.", stdout=e.stdout, stderr=e.stderr
            ) from e

        match = self.VERSION_REGEX.search(read_result.stdout)
        return bool(match and match.group(1) == self.FW_VERSION)
