# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Helper functions to find required files on the system."""

import fcntl
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


BIN_DIR = "binfiles"


def reset_usb_device(dev_path: str) -> bool:
    """Reset a USB device using USBDEVFS_RESET ioctl.

    Args:
        dev_path: Path to the device node (e.g., /dev/bus/usb/001/002).

    Returns:
        True if reset successful.
    """
    # USBDEVFS_RESET = _IO('U', 20) = (ord('U') << 8) | 20 = 21780
    usbdevfs_reset = 21780
    try:
        with open(dev_path, "wb") as f:
            fcntl.ioctl(f, usbdevfs_reset, 0)
        return True
    except OSError as e:
        logging.error("Failed to reset USB device %s: %s", dev_path, e)
        return False


def reset_servo_v4p1_hub() -> bool:
    """Finds and resets the USB hub upstream of the Servo V4.1."""
    base = "/sys/bus/usb/devices"
    if not os.path.exists(base):
        return False

    for dev in os.listdir(base):
        vid_path = os.path.join(base, dev, "idVendor")
        pid_path = os.path.join(base, dev, "idProduct")
        if os.path.exists(vid_path) and os.path.exists(pid_path):
            try:
                with open(vid_path, "r", encoding="utf-8") as f:
                    vid = f.read().strip()
                with open(pid_path, "r", encoding="utf-8") as f:
                    pid = f.read().strip()

                if vid == "18d1" and pid == "520d":
                    # Found Servo V4.1. Get Parent.
                    full_path = os.path.realpath(os.path.join(base, dev))
                    parent_dir = os.path.dirname(full_path)

                    with open(
                        os.path.join(parent_dir, "busnum"), "r", encoding="utf-8"
                    ) as f:
                        bus = int(f.read().strip())
                    with open(
                        os.path.join(parent_dir, "devnum"), "r", encoding="utf-8"
                    ) as f:
                        dev_num = int(f.read().strip())

                    dev_node = f"/dev/bus/usb/{bus:03d}/{dev_num:03d}"
                    logging.info(
                        "Found Servo Parent Hub at %s (Bus %d Dev %d). Resetting...",
                        dev_node,
                        bus,
                        dev_num,
                    )
                    return reset_usb_device(dev_node)
            except (IOError, ValueError):
                continue
    return False


def reset_servo_v4p1_dut_hub() -> bool:
    """Finds and resets the DUT USB hub on the Servo V4.1."""
    base = "/sys/bus/usb/devices"
    if not os.path.exists(base):
        return False

    # Cypress VID
    target_vid = "04b4"
    # PIDs observed: 6504 (USB3), 6502 (USB2)
    target_pids = ["6504", "6502"]

    reset_count = 0
    for dev in os.listdir(base):
        vid_path = os.path.join(base, dev, "idVendor")
        pid_path = os.path.join(base, dev, "idProduct")
        if os.path.exists(vid_path) and os.path.exists(pid_path):
            try:
                with open(vid_path, "r", encoding="utf-8") as f:
                    vid = f.read().strip()
                with open(pid_path, "r", encoding="utf-8") as f:
                    pid = f.read().strip()

                if vid == target_vid and pid in target_pids:
                    # Found DUT Hub. Get Device Node.
                    full_path = os.path.realpath(os.path.join(base, dev))

                    with open(
                        os.path.join(full_path, "busnum"), "r", encoding="utf-8"
                    ) as f:
                        bus = int(f.read().strip())
                    with open(
                        os.path.join(full_path, "devnum"), "r", encoding="utf-8"
                    ) as f:
                        dev_num = int(f.read().strip())

                    dev_node = f"/dev/bus/usb/{bus:03d}/{dev_num:03d}"
                    logging.info(
                        "Found Servo DUT Hub at %s (Bus %d Dev %d). Resetting...",
                        dev_node,
                        bus,
                        dev_num,
                    )
                    if reset_usb_device(dev_node):
                        reset_count += 1
            except (IOError, ValueError):
                continue
    return reset_count > 0


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
        else:
            logging.warning("/dev/serial/by-id not found, falling back to /dev/ttyUSB*")
            tty_devs = sorted(glob.glob("/dev/ttyUSB*"))
            # Servo V4.1 usually attaches to 3 ttyUSB devices.
            # If there are exactly 3, we can reasonably guess the first one
            # is the console (if00).
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
                "Waiting for Servo V4.1 serial port to appear (attempt %d/%d)...",
                attempt + 1,
                attempts,
            )
            time.sleep(interval)

    return None


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
