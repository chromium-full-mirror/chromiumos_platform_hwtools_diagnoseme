# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Hardware Abstraction Layer for DiagnoseMe."""

import fcntl
import logging
import os
from typing import Optional

from server.config import config


class HardwareManager:
    """Hardware Abstraction Layer for USB and Network device interactions."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self._logger = logger or logging.getLogger(__name__)

    def reset_usb_device(self, dev_path: str) -> bool:
        """Reset a USB device using USBDEVFS_RESET ioctl."""
        usbdevfs_reset = 21780
        try:
            with open(dev_path, "wb") as f:
                fcntl.ioctl(f, usbdevfs_reset, 0)
            return True
        except OSError as e:
            self._logger.error("Failed to reset USB device %s: %s", dev_path, e)
            return False

    def reset_servo_v4p1_hub(self) -> bool:
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

                    if (
                        vid.lower() == config.SERVO_V4P1_VID.lower()
                        and pid.lower() == config.SERVO_V4P1_PID.lower()
                    ):
                        full_path = os.path.realpath(os.path.join(base, dev))
                        parent_dir = os.path.dirname(full_path)

                        with open(
                            os.path.join(parent_dir, "busnum"), "r", encoding="utf-8"
                        ) as f:
                            busnum = int(f.read().strip())
                        with open(
                            os.path.join(parent_dir, "devnum"), "r", encoding="utf-8"
                        ) as f:
                            devnum = int(f.read().strip())

                        dev_node = f"/dev/bus/usb/{busnum:03d}/{devnum:03d}"
                        self._logger.info(
                            "Found Servo V4.1 hub parent at %s (Bus %d Dev %d)",
                            parent_dir,
                            busnum,
                            devnum,
                        )
                        return self.reset_usb_device(dev_node)

                except (OSError, ValueError) as e:
                    self._logger.warning("Error reading USB info for %s: %s", dev, e)

        self._logger.warning("Servo V4.1 not found in sysfs for hub reset.")
        return False

    def reset_servo_v4p1_dut_hub(self) -> bool:
        """Finds and resets the DUT USB hub on the Servo V4.1."""
        base = "/sys/bus/usb/devices"
        if not os.path.exists(base):
            return False

        target_vid = config.CYPRESS_HUB_VID.lower()
        target_pids = [
            config.CYPRESS_HUB_PID.lower(),
            config.CYPRESS_HUB_PID2.lower(),
            config.CYPRESS_HUB_PID3.lower(),
        ]

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

                    if vid.lower() == target_vid and pid.lower() in target_pids:
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
                        self._logger.info(
                            "Found Servo DUT Hub at %s (Bus %d Dev %d). Resetting...",
                            dev_node,
                            bus,
                            dev_num,
                        )
                        if self.reset_usb_device(dev_node):
                            reset_count += 1
                except (OSError, ValueError):
                    continue
        return reset_count > 0


hal = HardwareManager()
