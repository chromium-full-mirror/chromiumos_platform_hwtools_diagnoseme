# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Shared utilities, USB sysfs discovery, logging, and hardware constants for Maui services."""

import dataclasses
import glob
import logging
import os
import re
import shutil
import time
from typing import Dict, List, Optional, Tuple

# ==========================================
# Hardware Constants
# ==========================================
MAUI_BSL_VID = 0x0483
MAUI_BSL_PID = 0xDF11
TI_BSL_VID_PID = (0x2047, 0x0013)
FTDI_VID = 0x0403
FTDI_PID_UNPROGRAMMED = 0x6001
FTDI_PID_PROGRAMMED = 0x6002
PROLIFIC_VID = 0x18D1
PROLIFIC_PID_ADB = 0x506D
PROLIFIC_PID_H2H = 0x5084
HUB_USB2_VID_PID = (0x0451, 0x8142)
HUB_USB3_VID_PID = (0x0451, 0x8140)


@dataclasses.dataclass
class UsbDeviceInfo:
  """Represents a discovered USB device with topology and descriptor metadata."""
  sys_path: str
  dev_node: str = ""
  vid: int = 0
  pid: int = 0
  serial: str = ""
  container_id: str = ""
  product: str = ""
  speed: str = ""


def _read_sysfs_file(path: str) -> str:
  """Safely reads single-line content from a sysfs file."""
  try:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
      return f.read().strip()
  except (OSError, UnicodeDecodeError):
    return ""


def _scan_pyusb_devices() -> List[UsbDeviceInfo]:
  """Scans USB devices using PyUSB (/dev/bus/usb via libusb) as a robust container fallback."""
  devices: List[UsbDeviceInfo] = []
  try:
    import usb.core
    import usb.util
    pydevs = list(usb.core.find(find_all=True) or [])
    avail_ttys = sorted(glob.glob("/dev/ttyUSB*"))
    default_tty = avail_ttys[0] if avail_ttys else ""

    for d in pydevs:
      vid = d.idVendor
      pid = d.idProduct
      serial = ""
      product = ""
      try:
        if d.iSerialNumber:
          serial = (usb.util.get_string(d, d.iSerialNumber) or "").strip()
      except Exception:
        serial = ""

      try:
        if d.iProduct:
          product = (usb.util.get_string(d, d.iProduct) or "").strip()
      except Exception:
        product = ""

      dev_node = default_tty if vid == FTDI_VID else ""

      devices.append(
          UsbDeviceInfo(
              sys_path=f"/dev/bus/usb/{d.bus:03d}/{d.address:03d}",
              dev_node=dev_node,
              vid=vid,
              pid=pid,
              serial=serial,
              container_id="",
              product=product,
              speed="",
          )
      )
  except Exception as e:
    logging.warning("PyUSB scan fallback failed: %s", e)
  return devices


def scan_usb_devices() -> List[UsbDeviceInfo]:
  """Authoritative USB sysfs & pyusb scanner for connected devices."""
  devices: List[UsbDeviceInfo] = []

  # Map real sysfs device links to /dev/ttyUSB*
  tty_map = {}
  for tty_dir in glob.glob("/sys/class/tty/ttyUSB*"):
    device_link = os.path.join(tty_dir, "device")
    if os.path.exists(device_link):
      try:
        real_dev = os.path.realpath(device_link)
        tty_map[real_dev] = f"/dev/{os.path.basename(tty_dir)}"
      except Exception:
        pass

  for dev_dir in sorted(glob.glob("/sys/bus/usb/devices/*")):
    vid_str = _read_sysfs_file(os.path.join(dev_dir, "idVendor"))
    pid_str = _read_sysfs_file(os.path.join(dev_dir, "idProduct"))
    if not vid_str or not pid_str:
      continue

    try:
      vid = int(vid_str, 16)
      pid = int(pid_str, 16)
    except ValueError:
      continue

    serial = _read_sysfs_file(os.path.join(dev_dir, "serial"))
    product = _read_sysfs_file(os.path.join(dev_dir, "product"))
    speed = _read_sysfs_file(os.path.join(dev_dir, "speed"))
    container_id = _read_sysfs_file(os.path.join(dev_dir, "container_id"))

    # Resolve ttyUSB node if present
    real_dev_dir = os.path.realpath(dev_dir)
    dev_node = ""
    for tty_sys, node_name in tty_map.items():
      if tty_sys.startswith(real_dev_dir) or tty_sys.startswith(dev_dir):
        dev_node = node_name
        break

    if not dev_node:
      for iface_dir in glob.glob(f"{dev_dir}/*:1.*"):
        ttys = glob.glob(f"{iface_dir}/ttyUSB*") + glob.glob(f"{iface_dir}/tty/ttyUSB*")
        if ttys:
          dev_node = f"/dev/{os.path.basename(ttys[0])}"
          break

    if not dev_node and vid == FTDI_VID:
      avail_ttys = sorted(glob.glob("/dev/ttyUSB*"))
      if avail_ttys:
        dev_node = avail_ttys[0]

    devices.append(
        UsbDeviceInfo(
            sys_path=dev_dir,
            dev_node=dev_node,
            vid=vid,
            pid=pid,
            serial=serial,
            container_id=container_id,
            product=product,
            speed=speed,
        )
    )

  if not devices:
    devices = _scan_pyusb_devices()

  return devices


def find_device_container_id(sys_path: str) -> str:
  """Walks up sysfs tree to locate container_id or USB root hub port."""
  curr = sys_path
  while curr and curr != "/sys" and curr != "/sys/bus/usb/devices":
    cid = _read_sysfs_file(os.path.join(curr, "container_id"))
    if cid:
      return cid
    curr = os.path.dirname(curr)
  return ""


def get_device_log_dir(serial_number: str) -> str:
  """Returns /tmp/<maui-serial>_diagnoseme_logs path, creating it if absent."""
  safe_serial = re.sub(r"[^a-zA-Z0-9_-]", "", serial_number) if serial_number else "UNKNOWN"
  dir_path = f"/tmp/{safe_serial}_diagnoseme_logs"
  os.makedirs(dir_path, exist_ok=True)
  return dir_path


def write_device_log(
    serial_number: Optional[str],
    log_step_name: str,
    content: str,
    append: bool = False,
) -> str:
  """Writes or overwrites formatted timestamped logs to the device log directory in /tmp."""
  try:
    safe_serial = re.sub(r"[^a-zA-Z0-9_-]", "", serial_number) if serial_number else "UNKNOWN"
    log_dir = get_device_log_dir(safe_serial)
    filename = f"{log_step_name}.log" if not log_step_name.endswith(".log") else log_step_name
    file_path = os.path.join(log_dir, filename)
    mode = "a" if append else "w"
    with open(file_path, mode, encoding="utf-8") as f:
      f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]\n{content}\n\n")
    try:
      os.chmod(file_path, 0o666)
    except Exception:
      pass

    return file_path
  except OSError as e:
    logging.warning("Failed to write device log for %s: %s", serial_number, e)
    return ""


def resolve_maui_tool(tool_name: str) -> str:
  """Resolves the executable path for a Maui CLI utility (e.g. maui-flash, maui-ctl)."""
  return shutil.which(tool_name) or f"/usr/local/bin/{tool_name}"


def get_maui_env() -> Dict[str, str]:
  """Constructs standard environment dictionary for invoking Maui Python CLI tools."""
  env = os.environ.copy()
  env["PYTHONPATH"] = f"/usr/local/maui/tools:/usr/local/maui:{env.get('PYTHONPATH', '')}"
  return env
