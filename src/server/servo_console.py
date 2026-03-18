# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Connector for Servo V4.1 MCU serial console."""

import logging
import time
from typing import Optional

import serial


class ServoConsoleError(Exception):
    """Servo console error class."""

    def __init__(
        self,
        message: str,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
    ):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class ServoConsole:
    """Class to manage connection to the Servo V4.1 serial console."""

    BAUDRATE = 115200
    TIMEOUT = 2

    def __init__(self, port: str):
        """Initialize the console connector.

        Args:
          port: Path to the serial port (e.g. /dev/serial/by-id/...)
        """
        self.port = port
        self._ser: Optional[serial.Serial] = None
        # Compatibility for legacy tools (like RTKEthProgrammer) that expect
        # a tiny_servod-like object with .pty._issue_cmd()
        self.pty = self

    def _issue_cmd(self, cmd: str) -> str:
        """Alias for issue_cmd to satisfy legacy interfaces."""
        return self.issue_cmd(cmd)

    def __enter__(self):
        """Open the serial connection."""
        try:
            self._ser = serial.Serial(self.port, self.BAUDRATE, timeout=self.TIMEOUT)
            self._check_connection(self._ser)
        except serial.SerialException as e:
            raise ServoConsoleError(
                f"Failed to open serial port {self.port}: {e}"
            ) from e
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Close the serial connection."""
        if self._ser:
            self._ser.close()
            self._ser = None

    def _read_until(self, ser: serial.Serial, condition) -> str:
        """Read from serial port until condition is met or timeout.

        Args:
          ser: open serial port instance.
          condition: a callable that takes the accumulated response string and
                     returns True if the read should stop.

        Returns:
          The accumulated response string.
        """
        response = ""
        start_time = time.monotonic()
        while time.monotonic() - start_time < self.TIMEOUT:
            if ser.in_waiting:
                response += ser.read(ser.in_waiting).decode(errors="ignore")
                if condition(response):
                    break
            time.sleep(0.1)
        return response

    def flush(self) -> None:
        """Flush the serial buffers."""
        if self._ser:
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()

    def _check_connection(self, ser: serial.Serial) -> None:
        """Verify that we are connected to the correct device.

        Args:
          ser: open serial port instance.

        Raises:
          ServoConsoleError: if verification fails.
        """
        # Ensure we are at a clean prompt
        ser.write(b"\r\n")
        time.sleep(0.1)
        ser.reset_input_buffer()

        logging.debug("Verifying connection with 'help' command...")
        ser.write(b"help\r")

        help_response = self._read_until(ser, lambda r: "serialno" in r)

        logging.debug("Help command response: %s", help_response)
        if "serialno" not in help_response:
            # Try one more time with a newline
            ser.write(b"\r\nhelp\r")
            help_response = self._read_until(ser, lambda r: "serialno" in r)
            if "serialno" not in help_response:
                raise ServoConsoleError(
                    "unable to connect to serial device",
                    stdout=help_response,
                )

    def _issue_cmd_on_ser(self, ser: serial.Serial, cmd: str) -> str:
        """Internal helper to issue command on a specific serial instance."""
        # Try up to 3 times for flaky I2C/Serial commands
        for attempt in range(3):
            logging.info(
                "Sending command to servo console (attempt %d): %s", attempt + 1, cmd
            )
            ser.reset_input_buffer()
            ser.write(f"{cmd}\r".encode("utf-8"))

            # Give the device a moment to process and respond
            time.sleep(0.5)
            response = ser.read_all().decode(errors="ignore")

            logging.debug("Console response: %s", response)

            if response.strip() and "Unknown error" not in response:
                return response

            logging.warning(
                "Retrying console command due to bad response: %s", response
            )
            ser.write(b"\r\n")  # Clear prompt
            time.sleep(0.2)

        return response

    def read_until(self, condition) -> str:
        """Read from the persistent serial port until condition is met or timeout.

        Args:
          condition: a callable that takes the accumulated response string and
                     returns True if the read should stop.

        Returns:
          The accumulated response string.

        Raises:
          ServoConsoleError: if not connected.
        """
        if not self._ser:
            raise ServoConsoleError("Not connected to serial port.")
        return self._read_until(self._ser, condition)

    def send_cmd(self, cmd: str) -> None:
        """Send a command to the console without waiting for response.

        Args:
          cmd: The command string to send (without newline).

        Raises:
          ServoConsoleError: if not connected.
        """
        if not self._ser:
            raise ServoConsoleError("Not connected to serial port.")
        logging.info("Sending command to servo console: %s", cmd)
        self._ser.write(f"{cmd}\r".encode("utf-8"))

    def issue_cmd(self, cmd: str) -> str:
        """Issue a command to the console and return the response.

        Args:
          cmd: The command string to send (without newline).

        Returns:
          The full response string from the console.

        Raises:
          ServoConsoleError: If connection fails or command execution fails.
        """
        if self._ser:
            return self._issue_cmd_on_ser(self._ser, cmd)

        try:
            with serial.Serial(self.port, self.BAUDRATE, timeout=self.TIMEOUT) as ser:
                self._check_connection(ser)
                return self._issue_cmd_on_ser(ser, cmd)
        except serial.SerialException as e:
            raise ServoConsoleError(
                f"Failed to communicate with serial port {self.port}: {e}"
            ) from e
