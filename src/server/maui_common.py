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


def scan_connected_maui_units() -> List[Dict[str, Any]]:
  """Authoritatively scans for all connected physical Maui boards via ADB, Prolific, or FTDI endpoints."""
  units: List[Dict[str, Any]] = []
  seen_keys = set()

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
    vid_str = _read_sysfs_file(os.path.join(dev_dir, "idVendor")).lower()
    pid_str = _read_sysfs_file(os.path.join(dev_dir, "idProduct")).lower()
    if not vid_str or not pid_str:
      continue

    try:
      vid = int(vid_str, 16)
      pid = int(pid_str, 16)
    except ValueError:
      continue

    serial = _read_sysfs_file(os.path.join(dev_dir, "serial"))
    product = _read_sysfs_file(os.path.join(dev_dir, "product"))
    container_id = _read_sysfs_file(os.path.join(dev_dir, "container_id"))

    # Check for Maui ADB / Prolific chip
    is_adb = (
        (vid == 0x18D1 and pid in (0x506D, 0x5084, 0x5085))
        or (vid == 0x067B and pid == 0x27A1)
        or ("adb" in product.lower() or "maui" in product.lower())
    )
    # Check for Maui FTDI UART chip
    is_ftdi = (vid == 0x0403 and pid in (0x6001, 0x6002))

    if is_adb or is_ftdi:
      real_dev_dir = os.path.realpath(dev_dir)
      dev_node = tty_map.get(real_dev_dir, "")
      if not dev_node and is_ftdi:
        for iface_dir in glob.glob(f"{dev_dir}/*:1.*"):
          ttys = glob.glob(f"{iface_dir}/ttyUSB*") + glob.glob(f"{iface_dir}/tty/ttyUSB*")
          if ttys:
            dev_node = f"/dev/{os.path.basename(ttys[0])}"
            break

      # Deduplicate by container_id or serial if both ADB and FTDI are detected on the same board
      dedup_key = container_id if container_id else (serial if serial else dev_dir)
      if dedup_key not in seen_keys:
        seen_keys.add(dedup_key)
        units.append({
            "sys_path": dev_dir,
            "dev_node": dev_node,
            "vid": vid,
            "pid": pid,
            "serial": serial,
            "product": product or ("Maui ADB" if is_adb else "Maui FTDI"),
            "container_id": container_id,
            "is_adb": is_adb,
            "is_ftdi": is_ftdi,
        })

  # Also query adb devices -l fallback to catch any active ADB endpoints
  try:
    import subprocess
    proc = subprocess.run(
        ["adb", "devices", "-l"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=1.0,
    )
    if proc.returncode == 0 and proc.stdout:
      for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
          continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("device", "recovery", "sideload", "unauthorized"):
          adb_ser = parts[0]
          if adb_ser not in seen_keys and adb_ser.lower() not in ("0", "none", "unknown"):
            seen_keys.add(adb_ser)
            units.append({
                "sys_path": "/sys/bus/usb/devices",
                "dev_node": "",
                "vid": 0x18D1,
                "pid": 0x506D,
                "serial": adb_ser,
                "product": "Maui ADB (adb server)",
                "container_id": "",
                "is_adb": True,
                "is_ftdi": False,
            })
  except Exception:
    pass

  return units


def extract_container_id_from_bos_file(bos_path: str) -> Optional[str]:
  """Extracts 128-bit hardware ContainerID UUID from raw USB 3.0 BOS binary descriptor in sysfs."""
  try:
    with open(bos_path, "rb") as f:
      data = f.read()
    # BOS descriptor header is 5 bytes: [bLength=5, bDescriptorType=15, wTotalLength (2 bytes), bNumDeviceCaps (1 byte)]
    if len(data) < 5 or data[1] != 15:
      return None
    offset = 5
    while offset + 3 <= len(data):
      cap_len = data[offset]
      if cap_len < 3 or offset + cap_len > len(data):
        break
      cap_type = data[offset + 1]
      dev_cap_type = data[offset + 2]
      # Device Capability (0x10) and Container ID (0x04) with minimum length 20 (0x14)
      if cap_type == 16 and dev_cap_type == 4 and cap_len >= 20:
        raw_uuid = data[offset + 4 : offset + 20]
        # Standard UUID formatting: 8-4-4-4-12
        return f"{raw_uuid[0:4].hex()}-{raw_uuid[4:6].hex()}-{raw_uuid[6:8].hex()}-{raw_uuid[8:10].hex()}-{raw_uuid[10:16].hex()}".lower()
      offset += cap_len
  except Exception:
    pass
  return None


def find_device_container_id(sys_path: str) -> str:
  """Walks up sysfs tree to locate container_id or USB root hub port."""
  curr = sys_path
  while curr and curr not in ("/sys", "/sys/bus/usb/devices", "/"):
    cid = _read_sysfs_file(os.path.join(curr, "container_id")).lower().strip("{}")
    if cid and cid not in ("0", "none", "unknown", ""):
      return cid
    bos_path = os.path.join(curr, "bos")
    if os.path.exists(bos_path):
      bos_cid = extract_container_id_from_bos_file(bos_path)
      if bos_cid:
        return bos_cid
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


def find_offline_fw_file(component: str) -> Optional[str]:
  """Checks for local offline firmware files in standard firmware directories."""
  candidates = [
      f"/var/lib/diagnoseme/firmware/{component}",
      f"/usr/local/server/firmware/{component}",
      f"/usr/share/diagnoseme/firmware/{component}",
      f"/usr/local/maui/firmware/{component}",
      f"/dist/firmware/{component}",
      f"/offline_assets/firmware/{component}",
      "/var/lib/diagnoseme/firmware",
      "/usr/local/server/firmware",
  ]
  exts = [".txt", ".bin", ".hex"] if component == "mcu" else [".bin", ".hex", ".txt"]
  for d in candidates:
    if os.path.isdir(d):
      for ext in exts:
        matches = glob.glob(os.path.join(d, f"*{ext}"))
        if matches:
          return sorted(matches)[-1]
  return None


def resolve_maui_tool(tool_name: str) -> str:
  """Resolves the executable path for a Maui CLI utility (e.g. maui-flash, maui-ctl)."""
  return shutil.which(tool_name) or f"/usr/local/bin/{tool_name}"


def get_maui_env() -> Dict[str, str]:
  """Constructs standard environment dictionary for invoking Maui Python CLI tools."""
  env = os.environ.copy()
  env["PYTHONPATH"] = f"/usr/local/maui/tools:/usr/local/maui:{env.get('PYTHONPATH', '')}"
  return env


def ensure_zephyr_shell_awake(port_node: str, timeout: float = 3.0) -> bool:
  """Sends CRLF pulses at 115200 baud to ensure the Zephyr interactive UART shell is responsive."""
  if not port_node or not os.path.exists(port_node):
    return False
  try:
    import serial
    with serial.Serial(
        port=port_node,
        baudrate=115200,
        timeout=0.4,
        write_timeout=0.3,
        rtscts=False,
        dsrdtr=False,
        xonxoff=False,
    ) as s:
      # Clear any boot noise
      if s.in_waiting > 0:
        s.read(s.in_waiting)
      s.reset_input_buffer()
      s.reset_output_buffer()

      start = time.time()
      for _ in range(4):
        s.write(b"\r\n")
        s.flush()
        time.sleep(0.08)
        resp = bytearray()
        read_start = time.time()
        while (time.time() - read_start) < 0.4:
          if s.in_waiting > 0:
            resp.extend(s.read(s.in_waiting))
            if any(p in resp for p in (b"maui$", b"maui:~$", b"uart:~$", b"$ ")):
              logging.info("Zephyr shell on %s responded to wakeup ping: %r", port_node, bytes(resp))
              return True
          time.sleep(0.02)
        if (time.time() - start) > timeout:
          break
      logging.info("Sent wakeup pulses to %s (last read: %r)", port_node, bytes(resp))
      return True
  except Exception as e:
    logging.warning("Failed to ping Zephyr shell on %s: %s", port_node, e)
    return False

