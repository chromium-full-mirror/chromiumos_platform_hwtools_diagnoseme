# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Helper functions to find required files on the system."""

import glob
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from typing import List
from typing import Optional
from typing import Tuple

from server.config import config


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
        Path(config.BINFILES_DIR),
        Path(config.SERVO_V4P1_FW_DIR),
        Path(config.GENESYS_FW_DIR),
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


def discover_servo_serial_path(  # pylint: disable=too-many-branches
    serial_number: str = "",
    attempts: int = 15,
    interval: float = 1.0,
    board: str = "v4p1",
    target_if: str = "if00",
) -> Optional[str]:
    """Find the serial port for a Servo device.

    Args:
        serial_number: The serial number of the device to find.
        attempts: Number of search attempts.
        interval: Time in seconds between attempts.
        board: The board type (e.g. 'v4p1', 'micro').
        target_if: The target interface (e.g. 'if00', 'if03').

    Returns:
        The path to the serial port (target_if) or None if not found.
    """
    serial_number = serial_number.strip()
    serial_path = None

    for attempt in range(attempts):
        if os.path.exists("/dev/serial/by-id"):
            entries = os.listdir("/dev/serial/by-id")

            # Priorities: specific serial, uninitialized, or any v4p1/micro
            priorities = []
            if serial_number:
                priorities.append(serial_number)

            priorities.append("Uninitialized")

            if board in ("micro", "servo_micro"):
                priorities.append("Servo_Micro")
            elif board in ("v4p1", "servo_v4p1"):
                priorities.append("Servo_V4p1")
            elif board:
                priorities.append(f"Servo_{board.capitalize()}")

            for priority in priorities:
                for entry in entries:
                    if (
                        priority.lower() in entry.lower()
                        and target_if.lower() in entry.lower()
                    ):
                        serial_path = os.path.join("/dev/serial/by-id", entry)
                        break
                if serial_path:
                    break
        else:
            logging.warning("/dev/serial/by-id not found, falling back to /dev/ttyUSB*")
            tty_devs = sorted(glob.glob("/dev/ttyUSB*"))
            # Servo usually attaches to 3 ttyUSB devices.
            # If there are exactly 3, we can reasonably guess the first one
            # is the console (target_if).
            if len(tty_devs) == 3:
                serial_path = tty_devs[0]
                logging.info(
                    "Found exactly 3 raw serial devices, guessing first: %s",
                    serial_path,
                )
            elif tty_devs:
                logging.warning(
                    "Found %d raw serial devices (expected 3), skipping fallback.",
                    len(tty_devs),
                )

        if serial_path:
            logging.info("Found serial port: %s", serial_path)
            return serial_path

        if attempt < attempts - 1:
            logging.info(
                "Waiting for Servo serial port to appear (attempt %d/%d)...",
                attempt + 1,
                attempts,
            )
            time.sleep(interval)

    return None


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
    return discover_servo_serial_path(serial_number, attempts, interval, "v4p1", "if00")


def discover_servo_micro_serial_path(
    serial_number: str = "", attempts: int = 15, interval: float = 1.0
) -> Optional[str]:
    """Find the serial port for a Servo Micro device.

    Args:
        serial_number: The serial number of the device to find.
        attempts: Number of search attempts.
        interval: Time in seconds between attempts.

    Returns:
        The path to the serial port (if03) or None if not found.
    """
    return discover_servo_serial_path(
        serial_number, attempts, interval, "servo_micro", "if03"
    )


def is_usb_device_present(vid: int, pids: List[int]) -> bool:
    """Check if a USB device is present on the system.

    Args:
        vid: Vendor ID.
        pids: List of Product IDs.

    Returns:
        True if found.
    """
    lsusb = run_command(["lsusb"]).stdout.lower()
    for pid in pids:
        if f"{vid:04x}:{pid:04x}" in lsusb:
            return True
    return False


def wait_for_usb_devices(
    device_list: List[Tuple[int, List[int]]], timeout: float = 30.0
) -> bool:
    """Wait for all devices in the list to be present on the system.

    Args:
        device_list: List of (VID, [PID1, PID2, ...]) tuples to search for.
        timeout: Maximum time to wait in seconds.

    Returns:
        True if all devices are found within the timeout, False otherwise.
    """
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        lsusb = run_command(["lsusb"]).stdout.lower()
        all_found = True
        for vid, pids in device_list:
            found_vid_any_pid = False
            for pid in pids:
                if f"{vid:04x}:{pid:04x}" in lsusb:
                    found_vid_any_pid = True
                    break
            if not found_vid_any_pid:
                all_found = False
                break
        if all_found:
            return True
        time.sleep(1.0)
    return False
