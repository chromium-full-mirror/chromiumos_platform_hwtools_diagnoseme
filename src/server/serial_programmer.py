# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Programmer for serial number on v4p1 via serial console."""

import logging
import re
from typing import Optional

from server.servo_console import ServoConsole
from server.servo_console import ServoConsoleError


class SerialProgrammerError(Exception):
    """Serial programmer error class."""

    def __init__(
        self,
        message: str,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
    ):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class SerialProgrammer:
    """Class to program the serial number on the device via serial console."""

    SERIAL_PORT = "/dev/serial/by-id/usb-Google_LLC_Servo_V4p1_Uninitialized-if00-port0"

    # Expected response from 'serialno' command is "Serial number: <serial>"
    SERIALNO_REGEX = re.compile(r"Serial number:\s+(\S+)")

    def __init__(self, serial_number: str):
        """Initialize the programmer.

        Args:
          serial_number: the desired serial number to program.
        """
        self._serial_number = serial_number
        self._console = ServoConsole(self.SERIAL_PORT)

    def _verify(self, console: ServoConsole) -> bool:
        """Verify serial number on an open console connection.

        Args:
          console: Open ServoConsole instance.

        Returns:
          True if the device serial number matches self._serial_number.
        """
        logging.debug("Reading current serial number...")
        console.send_cmd("serialno")

        response = console.read_until(
            lambda r: "Serial number:" in r and "\r\n" in r[r.find("Serial number:") :]
        )

        logging.debug("Verify response: %s", response.strip())
        match = self.SERIALNO_REGEX.search(response)
        if match:
            current_serial = match.group(1)
            logging.info("Current device serial number: %s", current_serial)
            return current_serial == self._serial_number

        logging.warning("Could not find 'Serial number:' in response: %s", response)
        return False

    def program(self) -> bool:
        """Helper to perform actual programming.

        Returns:
          True if programming succeeded (or was already correct).

        Raises:
          SerialProgrammerError: if programming or verification fails.
        """
        logging.info("Programming serial number to: %s", self._serial_number)
        try:
            with self._console as console:
                if self._verify(console):
                    logging.info(
                        "Serial number already matches: %s", self._serial_number
                    )
                    return True

                cmd = f"serialno set {self._serial_number}"
                logging.debug("Running console command: %s", cmd)
                console.issue_cmd(cmd)

                if not self._verify(console):
                    raise SerialProgrammerError(
                        "Verification failed after attempt to program serial "
                        f"number to {self._serial_number}"
                    )

        except ServoConsoleError as e:
            raise SerialProgrammerError(
                f"Failed to communicate with serial port: {e}",
                stdout=e.stdout,
                stderr=e.stderr,
            ) from e

        return True

    def verify(self) -> bool:
        """Helper to verify that the serial number is correct.

        Returns:
          True if the device serial number matches self._serial_number.
        """
        try:
            with self._console as console:
                return self._verify(console)
        except ServoConsoleError as e:
            logging.debug("Serial port not available for verification: %s", e)

        return False
