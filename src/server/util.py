# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Helper functions to find required files on the system."""

import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from typing import List
from typing import Optional


BIN_DIR = "binfiles"


def get_bindir() -> Path:
    """Get the directory with the manufacturing binary files.

    Returns:
      Path object representing the |BIN_DIR| absolute path.
    """
    return Path(__file__).resolve().parent / BIN_DIR


def find_binfile(binfile: str) -> Optional[Path]:
    """Find the full path for |binfile| if available.

    Args:
      binfile: name of the binfile to find or an absolute path.

    Returns:
      Path to |binfile| if found, or None if not found.
    """
    input_path = Path(binfile)
    if input_path.is_absolute():
        return input_path if input_path.exists() else None

    search_paths = [
        get_bindir(),
        Path("/usr/local/server/binfiles"),
        Path("/usr/local/servo_v4p1"),
        Path("/usr/local/genesys"),
    ]
    for search_path in search_paths:
        path = search_path / binfile
        if path.exists():
            return path
    return None


def run_command(cmd: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Run a subprocess command with common default arguments.

    Args:
      cmd: command to run as a list of strings.
      **kwargs: additional arguments passed to subprocess.run.

    Returns:
      CompletedProcess instance.
    """
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    check = kwargs.pop("check", True)
    return subprocess.run(cmd, check=check, **kwargs)


def get_logs_from_exception(e: Exception) -> str:
    """Helper to extract stdout and stderr from exception if available.

    Args:
      e: The exception object.

    Returns:
      Formatted string containing stdout and stderr.
    """
    stdout = getattr(e, "stdout", "") or ""
    stderr = getattr(e, "stderr", "") or ""
    return f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"


def discover_servo_v4p1_serial_path(
    serial_number: str = "", attempts: int = 15, interval: float = 1.0
) -> Optional[str]:
    """Find the serial port for a Servo V4.1 device.

    Args:
        serial_number: The serial number of the device to find.
        attempts: Number of search attempts.
        interval: Time in seconds between attempts.

    Returns:
        The path to the serial port (if00) or None if not found.
    """
    serial_number = serial_number.strip()
    serial_path = None

    for attempt in range(attempts):
        if os.path.exists("/dev/serial/by-id"):
            entries = os.listdir("/dev/serial/by-id")

            # Define search priorities: specific serial, uninitialized, or any v4p1
            priorities = []
            if serial_number:
                priorities.append(serial_number)
            priorities.extend(["Uninitialized", "Servo_V4p1"])

            for priority in priorities:
                for entry in entries:
                    if priority in entry and "if00" in entry:
                        serial_path = os.path.join("/dev/serial/by-id", entry)
                        break
                if serial_path:
                    break

        if serial_path:
            logging.info("Found serial port: %s", serial_path)
            return serial_path

        if attempt < attempts - 1:
            logging.info(
                "Waiting for Servo V4.1 serial port to appear (attempt %d/%d)...",
                attempt + 1,
                attempts,
            )
            time.sleep(interval)

    return None
