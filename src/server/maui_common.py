# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Shared utilities, USB sysfs discovery, logging, and hardware constants for Maui services."""

import dataclasses
import glob
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any
from typing import Dict
from typing import FrozenSet
from typing import List
from typing import Optional
from typing import Tuple

import serial


# ==========================================
# Hardware Constants (single source of truth for all Maui services)
# ==========================================
MAUI_BSL_VID = 0x0483
MAUI_BSL_PID = 0xDF11
TI_BSL_VID_PID = (0x2047, 0x0013)

FTDI_VID = 0x0403
FTDI_PID_UNPROGRAMMED = 0x6001
FTDI_PID_PROGRAMMED = 0x6002
FTDI_PIDS = (FTDI_PID_UNPROGRAMMED, FTDI_PID_PROGRAMMED)

# Google VID used by the Maui ADB / H2H bridge endpoints (and by Cr50/Ti50 CCD).
GOOGLE_VID = 0x18D1
GOOGLE_PID_ADB = 0x506D
GOOGLE_PID_H2H = 0x5084
GOOGLE_PID_H2H_ALT = 0x5085

# Prolific H2H bridge chip.
PROLIFIC_VID = 0x067B
PROLIFIC_PID_H2H = 0x27A1

# Google ADB / H2H endpoint PIDs (without the Prolific bridge).
GOOGLE_ADB_PIDS = (GOOGLE_PID_ADB, GOOGLE_PID_H2H, GOOGLE_PID_H2H_ALT)
# Exact VID:PID pairs of the Maui ADB / H2H endpoints (the single list used by
# all Maui services).
MAUI_ADB_VID_PIDS: FrozenSet[Tuple[int, int]] = frozenset(
    {(GOOGLE_VID, pid) for pid in GOOGLE_ADB_PIDS} | {(PROLIFIC_VID, PROLIFIC_PID_H2H)}
)

# On-board Maui USB hub (TI TUSB8041) and the Realtek variant.
HUB_USB2_VID_PIDS: FrozenSet[Tuple[int, int]] = frozenset(
    {(0x0451, 0x8142), (0x0BDA, 0x5411)}
)
HUB_USB3_VID_PIDS: FrozenSet[Tuple[int, int]] = frozenset(
    {(0x0451, 0x8140), (0x0BDA, 0x0411)}
)
# Hub vendor IDs queried through `lsusb -v` for BOS ContainerID fallback.
HUB_LSUSB_VENDOR_IDS = ("0451", "0bda")

# Google CCD (Cr50 / Ti50) PIDs.
CCD_PIDS = (0x5014, 0x504A, 0x5066, 0x520D)

# Sysfs locations that terminate a walk up the USB device tree.
SYSFS_WALK_STOP_DIRS = ("/sys", "/sys/bus/usb/devices", "/")
# Container ID values that mean "no container ID".
_INVALID_CONTAINER_IDS = ("0", "none", "unknown", "")

ADB_DEVICES_TIMEOUT_SEC = 1.0

# ==========================================
# Device logs & results (all under /var/log/diagnoseme on the station; the
# directory is bind-mounted 1:1 into the rpcserver container)
# ==========================================
# Base directory of the per-device log directories (<base>/<SERIAL>/).
DEVICE_LOG_DIR_ENV_VAR = "DIAGNOSEME_DEVICE_LOG_DIR"
DEFAULT_DEVICE_LOG_DIR = "/var/log/diagnoseme/devices"
# Final results export directory.
RESULTS_DIR_ENV_VAR = "DIAGNOSEME_RESULTS_DIR"
DEFAULT_RESULTS_DIR = "/var/log/diagnoseme/maui_v11_results"
RESULTS_DIR_MODE = 0o755
RESULTS_FILE_MODE = 0o644


class LogStatus:
    """Machine-readable Step log statuses written as `STATUS: <value>`."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    BYPASSED = "BYPASSED"
    NOT_RUN = "NOT_RUN"

    ALL = (PASSED, FAILED, TIMEOUT, BYPASSED, NOT_RUN)


STATUS_LINE_PREFIX = "STATUS: "
_LOG_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
# A STATUS line is only recognised directly after the timestamp header written
# by write_device_log, so tool output inside a log cannot fake a verdict.
_STATUS_LINE_RE = re.compile(
    r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\r?\n"
    + re.escape(STATUS_LINE_PREFIX)
    + r"(\S*)[ \t]*\r?$",
    re.MULTILINE,
)


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


def read_sysfs_file(path: str) -> str:
    """Safely reads single-line content from a sysfs file."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def normalize_container_id(raw: str) -> str:
    """Normalizes a container ID string; returns "" for missing/invalid values."""
    cid = (raw or "").strip().lower().strip("{}")
    return "" if cid in _INVALID_CONTAINER_IDS else cid


def _tty_sort_key(path: str) -> Tuple[str, int]:
    """Sort key ordering tty nodes numerically (ttyUSB2 before ttyUSB10)."""
    name = os.path.basename(path)
    match = re.match(r"^(.*?)(\d+)$", name)
    if not match:
        return name, -1
    return match.group(1), int(match.group(2))


def sorted_tty_nodes(pattern: str = "/dev/ttyUSB*") -> List[str]:
    """Returns the tty nodes matching `pattern`, sorted numerically."""
    return sorted(glob.glob(pattern), key=_tty_sort_key)


def build_tty_sysfs_map() -> Dict[str, str]:
    """Maps real sysfs device paths of /sys/class/tty/ttyUSB* to /dev/ttyUSB* nodes."""
    tty_map: Dict[str, str] = {}
    for tty_dir in glob.glob("/sys/class/tty/ttyUSB*"):
        device_link = os.path.join(tty_dir, "device")
        if os.path.exists(device_link):
            tty_map[os.path.realpath(device_link)] = f"/dev/{os.path.basename(tty_dir)}"
    return tty_map


def _find_tty_in_interfaces(dev_dir: str) -> str:
    """Returns the first /dev/ttyUSB* node found under the interfaces of a USB device."""
    for iface_dir in glob.glob(f"{dev_dir}/*:1.*"):
        ttys = sorted_tty_nodes(f"{iface_dir}/ttyUSB*") + sorted_tty_nodes(
            f"{iface_dir}/tty/ttyUSB*"
        )
        if ttys:
            return f"/dev/{os.path.basename(ttys[0])}"
    return ""


def _scan_pyusb_devices() -> List[UsbDeviceInfo]:
    """Scans USB devices using PyUSB (/dev/bus/usb via libusb) as a robust container fallback."""
    devices: List[UsbDeviceInfo] = []
    try:
        import usb.core  # pylint: disable=import-outside-toplevel
        import usb.util  # pylint: disable=import-outside-toplevel

        pydevs = list(usb.core.find(find_all=True) or [])
        avail_ttys = sorted_tty_nodes()
        default_tty = avail_ttys[0] if avail_ttys else ""

        for d in pydevs:
            serial_str = ""
            product = ""
            try:
                if d.iSerialNumber:
                    serial_str = (usb.util.get_string(d, d.iSerialNumber) or "").strip()
            except (OSError, ValueError, NotImplementedError) as e:
                logging.debug(
                    "PyUSB: cannot read serial of %04x:%04x: %s",
                    d.idVendor,
                    d.idProduct,
                    e,
                )

            try:
                if d.iProduct:
                    product = (usb.util.get_string(d, d.iProduct) or "").strip()
            except (OSError, ValueError, NotImplementedError) as e:
                logging.debug(
                    "PyUSB: cannot read product of %04x:%04x: %s",
                    d.idVendor,
                    d.idProduct,
                    e,
                )

            devices.append(
                UsbDeviceInfo(
                    sys_path=f"/dev/bus/usb/{d.bus:03d}/{d.address:03d}",
                    dev_node=default_tty if d.idVendor == FTDI_VID else "",
                    vid=d.idVendor,
                    pid=d.idProduct,
                    serial=serial_str,
                    product=product,
                )
            )
    except (ImportError, OSError, ValueError) as e:
        # NoBackendError is a ValueError, USBError is an OSError.
        logging.warning("PyUSB scan fallback failed: %s", e)
    return devices


def scan_usb_devices() -> List[UsbDeviceInfo]:
    """Authoritative USB sysfs & pyusb scanner for connected devices."""
    devices: List[UsbDeviceInfo] = []
    tty_map = build_tty_sysfs_map()

    for dev_dir in sorted(glob.glob("/sys/bus/usb/devices/*")):
        vid_str = read_sysfs_file(os.path.join(dev_dir, "idVendor"))
        pid_str = read_sysfs_file(os.path.join(dev_dir, "idProduct"))
        if not vid_str or not pid_str:
            continue

        try:
            vid = int(vid_str, 16)
            pid = int(pid_str, 16)
        except ValueError:
            continue

        # Resolve ttyUSB node if present
        real_dev_dir = os.path.realpath(dev_dir)
        dev_node = ""
        for tty_sys, node_name in tty_map.items():
            if tty_sys.startswith(real_dev_dir) or tty_sys.startswith(dev_dir):
                dev_node = node_name
                break

        if not dev_node:
            dev_node = _find_tty_in_interfaces(dev_dir)

        if not dev_node and vid == FTDI_VID:
            avail_ttys = sorted_tty_nodes()
            if avail_ttys:
                dev_node = avail_ttys[0]

        devices.append(
            UsbDeviceInfo(
                sys_path=dev_dir,
                dev_node=dev_node,
                vid=vid,
                pid=pid,
                serial=read_sysfs_file(os.path.join(dev_dir, "serial")),
                container_id=read_sysfs_file(os.path.join(dev_dir, "container_id")),
                product=read_sysfs_file(os.path.join(dev_dir, "product")),
                speed=read_sysfs_file(os.path.join(dev_dir, "speed")),
            )
        )

    if not devices:
        devices = _scan_pyusb_devices()

    return devices


def is_maui_adb_endpoint(vid: int, pid: int, product: str = "") -> bool:
    """True for the Maui ADB / H2H / Prolific endpoints (or an ADB/Maui product string)."""
    product_l = (product or "").lower()
    return (vid, pid) in MAUI_ADB_VID_PIDS or "adb" in product_l or "maui" in product_l


def is_maui_ftdi_endpoint(vid: int, pid: int) -> bool:
    """True for the Maui FTDI UART console (programmed or unprogrammed)."""
    return vid == FTDI_VID and pid in FTDI_PIDS


def list_adb_devices(
    accepted_states: Tuple[str, ...] = ("device",),
    long_format: bool = False,
    timeout: float = ADB_DEVICES_TIMEOUT_SEC,
) -> List[str]:
    """Returns the serials reported by `adb devices` whose state is in accepted_states.

    Raises:
      OSError / subprocess.SubprocessError if adb is missing or times out.
    """
    cmd = ["adb", "devices", "-l"] if long_format else ["adb", "devices"]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    serials: List[str] = []
    if proc.returncode != 0 or not proc.stdout:
        return serials
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] in accepted_states:
            serials.append(parts[0])
    return serials


def scan_connected_maui_units() -> List[Dict[str, Any]]:
    """Authoritatively scans for all connected physical Maui boards via ADB, Prolific, or FTDI endpoints."""
    units: List[Dict[str, Any]] = []
    seen_keys = set()
    tty_map = build_tty_sysfs_map()

    for dev_dir in sorted(glob.glob("/sys/bus/usb/devices/*")):
        vid_str = read_sysfs_file(os.path.join(dev_dir, "idVendor")).lower()
        pid_str = read_sysfs_file(os.path.join(dev_dir, "idProduct")).lower()
        if not vid_str or not pid_str:
            continue

        try:
            vid = int(vid_str, 16)
            pid = int(pid_str, 16)
        except ValueError:
            continue

        serial_str = read_sysfs_file(os.path.join(dev_dir, "serial"))
        product = read_sysfs_file(os.path.join(dev_dir, "product"))
        container_id = read_sysfs_file(os.path.join(dev_dir, "container_id"))

        is_adb = is_maui_adb_endpoint(vid, pid, product)
        is_ftdi = is_maui_ftdi_endpoint(vid, pid)
        if not (is_adb or is_ftdi):
            continue

        dev_node = tty_map.get(os.path.realpath(dev_dir), "")
        if not dev_node and is_ftdi:
            dev_node = _find_tty_in_interfaces(dev_dir)

        # Deduplicate by container_id or serial if both ADB and FTDI are detected on the same board
        dedup_key = container_id or serial_str or dev_dir
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        units.append(
            {
                "sys_path": dev_dir,
                "dev_node": dev_node,
                "vid": vid,
                "pid": pid,
                "serial": serial_str,
                "product": product or ("Maui ADB" if is_adb else "Maui FTDI"),
                "container_id": container_id,
                "is_adb": is_adb,
                "is_ftdi": is_ftdi,
            }
        )

    # Also query adb devices -l fallback to catch any active ADB endpoints
    try:
        adb_serials = list_adb_devices(
            accepted_states=("device", "recovery", "sideload", "unauthorized"),
            long_format=True,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logging.warning("adb devices query failed during Maui unit scan: %s", e)
        adb_serials = []

    for adb_ser in adb_serials:
        if adb_ser in seen_keys or adb_ser.lower() in ("0", "none", "unknown"):
            continue
        seen_keys.add(adb_ser)
        units.append(
            {
                "sys_path": "/sys/bus/usb/devices",
                "dev_node": "",
                "vid": GOOGLE_VID,
                "pid": GOOGLE_PID_ADB,
                "serial": adb_ser,
                "product": "Maui ADB (adb server)",
                "container_id": "",
                "is_adb": True,
                "is_ftdi": False,
            }
        )

    return units


def extract_container_id_from_bos_file(bos_path: str) -> Optional[str]:
    """Extracts 128-bit hardware ContainerID UUID from raw USB 3.0 BOS binary descriptor in sysfs."""
    try:
        with open(bos_path, "rb") as f:
            data = f.read()
    except OSError:
        return None
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
            raw = data[offset + 4 : offset + 20]
            # Standard UUID formatting: 8-4-4-4-12
            return (
                f"{raw[0:4].hex()}-{raw[4:6].hex()}-{raw[6:8].hex()}-"
                f"{raw[8:10].hex()}-{raw[10:16].hex()}"
            ).lower()
        offset += cap_len
    return None


def read_device_container_id(dev_dir: str) -> str:
    """Reads a USB device's container ID from its sysfs `container_id` file, then its BOS descriptor."""
    cid = normalize_container_id(read_sysfs_file(os.path.join(dev_dir, "container_id")))
    if cid:
        return cid
    bos_path = os.path.join(dev_dir, "bos")
    if os.path.exists(bos_path):
        return extract_container_id_from_bos_file(bos_path) or ""
    return ""


def resolve_hub_container_id(hub_dir: str, hub_cids: Dict[str, str]) -> str:
    """Resolves the container ID of a (parent) hub directory.

    Lookup order: precomputed hub_cids map, raw BOS descriptor, sysfs container_id.
    Returns "" for the sysfs walk stop directories or when nothing is found.
    """
    if not hub_dir or hub_dir in SYSFS_WALK_STOP_DIRS:
        return ""
    hub_name = os.path.basename(hub_dir)
    if hub_name in hub_cids:
        return hub_cids[hub_name]
    bos_path = os.path.join(hub_dir, "bos")
    if os.path.exists(bos_path):
        bos_cid = extract_container_id_from_bos_file(bos_path)
        if bos_cid:
            return bos_cid
    return read_sysfs_file(os.path.join(hub_dir, "container_id")).lower().strip("{}")


def find_device_container_id(sys_path: str) -> str:
    """Walks up sysfs tree to locate container_id or USB root hub port."""
    curr = sys_path
    while curr and curr not in SYSFS_WALK_STOP_DIRS:
        cid = normalize_container_id(
            read_sysfs_file(os.path.join(curr, "container_id"))
        )
        if cid:
            return cid
        bos_path = os.path.join(curr, "bos")
        if os.path.exists(bos_path):
            bos_cid = extract_container_id_from_bos_file(bos_path)
            if bos_cid:
                return bos_cid
        curr = os.path.dirname(curr)
    return ""


def _safe_serial(serial_number: Optional[str]) -> str:
    """Sanitizes a serial number for use as a directory name ("UNKNOWN" when empty)."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", serial_number or "")
    return safe or "UNKNOWN"


def is_unknown_serial(serial_number: Optional[str]) -> bool:
    """True when the serial is empty or the shared "UNKNOWN" placeholder."""
    return _safe_serial(serial_number) == "UNKNOWN"


def get_device_log_base_dir() -> str:
    """Returns the base directory of all per-device log dirs ($DIAGNOSEME_DEVICE_LOG_DIR)."""
    return os.environ.get(DEVICE_LOG_DIR_ENV_VAR) or DEFAULT_DEVICE_LOG_DIR


def device_log_dir_path(serial_number: Optional[str]) -> str:
    """Returns the <device log base>/<SERIAL> path without creating it."""
    return os.path.join(get_device_log_base_dir(), _safe_serial(serial_number))


def get_device_log_dir(serial_number: Optional[str]) -> str:
    """Returns the <device log base>/<SERIAL> path, creating it if absent."""
    dir_path = device_log_dir_path(serial_number)
    os.makedirs(dir_path, mode=RESULTS_DIR_MODE, exist_ok=True)
    return dir_path


def format_status_line(status: str) -> str:
    """Formats the machine-readable `STATUS: <value>` line of a Step log."""
    if status not in LogStatus.ALL:
        raise ValueError(f"Unknown log status '{status}'")
    return f"{STATUS_LINE_PREFIX}{status}"


def parse_log_status(content: str) -> Optional[str]:
    """Returns the STATUS of the latest write_device_log entry, or None if absent/unknown."""
    matches = _STATUS_LINE_RE.findall(content or "")
    if not matches:
        return None
    status = matches[-1].strip()
    return status if status in LogStatus.ALL else None


def safe_log_step_name(log_step_name: Optional[str]) -> str:
    """Returns |log_step_name| reduced to a plain file name (it may come from the UI)."""
    name = re.sub(r"[^A-Za-z0-9_.-]", "", os.path.basename(log_step_name or ""))
    return name.lstrip(".") or "device_log"


def write_device_log(
    serial_number: Optional[str],
    log_step_name: str,
    content: str,
    status: Optional[str] = None,
    append: bool = False,
) -> str:
    """Writes or overwrites a timestamped Step log in the device log directory.

    When `status` is given, a machine-readable `STATUS: <status>` line is written
    right after the timestamp header (see parse_log_status). The step name is
    reduced to a plain file name so the log always stays inside the device log
    directory.
    """
    try:
        log_dir = get_device_log_dir(serial_number)
        step = safe_log_step_name(log_step_name)
        filename = step if step.endswith(".log") else f"{step}.log"
        file_path = os.path.join(log_dir, filename)
        body = f"{format_status_line(status)}\n{content}" if status else content
        with open(file_path, "a" if append else "w", encoding="utf-8") as f:
            f.write(f"[{time.strftime(_LOG_TIMESTAMP_FORMAT)}]\n{body}\n\n")
    except OSError as e:
        logging.warning("Failed to write device log for %s: %s", serial_number, e)
        return ""
    try:
        os.chmod(file_path, RESULTS_FILE_MODE)
    except OSError as e:
        logging.warning("Failed to set permissions on %s: %s", file_path, e)
    return file_path


def get_results_base_dir() -> str:
    """Returns the single results export directory ($DIAGNOSEME_RESULTS_DIR)."""
    return os.environ.get(RESULTS_DIR_ENV_VAR) or DEFAULT_RESULTS_DIR


# Firmware search order: <root>/<component> for each root, then the flat dirs.
FIRMWARE_ROOT_DIRS = (
    "/var/lib/diagnoseme/firmware",
    "/usr/local/server/firmware",
    "/usr/share/diagnoseme/firmware",
    "/usr/local/maui/firmware",
    "/dist/firmware",
    "/offline_assets/firmware",
)
FIRMWARE_FLAT_DIRS = ("/var/lib/diagnoseme/firmware", "/usr/local/server/firmware")
# Marker written next to the firmware image by fetch-maui-firmware:
# {"version": ..., "file": <image file name>, "md5": <hex>, "source": "gs://..."}.
STABLE_FW_MARKER = "STABLE.json"
_MD5_CHUNK_BYTES = 1 << 20


def _file_md5(path: str) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_MD5_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


FW_UPDATE_HINT = (
    "Update the station firmware (start DiagnoseMe online, install the offline "
    "update bundle or reinstall from the ISO)."
)


class FirmwareSelectionError(Exception):
    """The station firmware of a component cannot be chosen safely."""


@dataclasses.dataclass(frozen=True)
class FwSelection:
    """Result of select_offline_fw().

    path: the image to flash (None if there is none or on |error|).
    version: version named by STABLE.json ('' without a marker).
    warning: set when the image is used without a STABLE.json.
    error: set when the image must not be guessed (broken marker, several
      images without a marker); the step must fail.
    """

    path: Optional[str] = None
    version: str = ""
    warning: str = ""
    error: str = ""


def read_stable_marker(marker: str) -> Dict[str, Any]:
    """Parsed STABLE.json |marker| (which exists); raises ValueError if invalid."""
    try:
        with open(marker, "r", encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f"{marker} is unreadable ({e})") from e
    if not isinstance(info, dict):
        raise ValueError(f"{marker} is not a JSON object")
    return info


def stable_marker_fw_file(component_dir: str) -> Tuple[str, str]:
    """Returns (image, version) named by <component_dir>/STABLE.json.

    The marker must exist. Raises ValueError when it is unreadable, names an
    invalid or missing file, or the MD5 of the image does not match.
    """
    marker = os.path.join(component_dir, STABLE_FW_MARKER)
    info = read_stable_marker(marker)
    file_name = str(info.get("file") or "")
    expected_md5 = str(info.get("md5") or "").strip().lower()
    if not file_name or os.path.basename(file_name) != file_name:
        raise ValueError(f"{marker} names an invalid file {file_name!r}")
    fw_path = os.path.join(component_dir, file_name)
    if not os.path.isfile(fw_path):
        raise ValueError(f"{fw_path} named by {marker} does not exist")
    try:
        actual_md5 = _file_md5(fw_path)
    except OSError as e:
        actual_md5 = f"unreadable ({e})"
    if actual_md5 != expected_md5:
        raise ValueError(
            f"MD5 mismatch for {fw_path} named by {marker} "
            f"(expected {expected_md5 or 'none'}, got {actual_md5})"
        )
    version = str(info.get("version", ""))
    logging.info(
        "Using stable firmware %s (version %s, source %s)",
        fw_path,
        version or "unknown",
        info.get("source", "unknown"),
    )
    return fw_path, version


def stable_marker_version(component_dir: str) -> str:
    """Version of the valid STABLE.json in |component_dir|, else ''.

    Entries whose image is missing or fails the MD5 check are skipped (for
    lists of approved firmware versions).
    """
    if not os.path.isfile(os.path.join(component_dir, STABLE_FW_MARKER)):
        return ""
    try:
        return stable_marker_fw_file(component_dir)[1]
    except ValueError as e:
        logging.error("Skipping the stable firmware version: %s", e)
        return ""


def _images_without_marker(
    component: str, directory: str, component_dir: bool
) -> FwSelection:
    """Applies the 'exactly one image' rule to |directory| without a marker."""
    exts = (".txt", ".bin", ".hex") if component == "mcu" else (".bin", ".hex", ".txt")
    if component_dir:
        images = sorted(
            {f for ext in exts for f in glob.glob(os.path.join(directory, f"*{ext}"))}
        )
    else:
        # Legacy flat directories hold every component: the preferred
        # extension of |component| decides.
        images = next(
            (
                sorted(matches)
                for matches in (
                    glob.glob(os.path.join(directory, f"*{ext}")) for ext in exts
                )
                if matches
            ),
            [],
        )
    if len(images) == 1:
        return FwSelection(
            path=images[0],
            warning=(
                f"No {STABLE_FW_MARKER} in {directory} - flashing the only "
                f"{component} image {os.path.basename(images[0])}."
            ),
        )
    if images:
        names = ", ".join(os.path.basename(f) for f in images)
        return FwSelection(
            error=(
                f"{len(images)} {component} images in {directory} ({names}) and "
                f"no {STABLE_FW_MARKER} to choose the stable one. {FW_UPDATE_HINT}"
            )
        )
    return FwSelection()


def select_offline_fw(component: str) -> FwSelection:
    """Chooses the station firmware image of |component| (fails closed).

    The first firmware directory (FIRMWARE_ROOT_DIRS/<component>, then the
    legacy FIRMWARE_FLAT_DIRS) holding a STABLE.json or an image decides:
      * STABLE.json present and valid (existing file, matching MD5): its image;
      * STABLE.json present but invalid: error with an update hint;
      * no STABLE.json and exactly one image: that image, with a warning;
      * no STABLE.json and several images: error (no guessing).
    Without any image, path is None and error is empty.
    """
    component_dirs = [f"{root}/{component}" for root in FIRMWARE_ROOT_DIRS]
    candidates = [(d, True) for d in component_dirs] + [
        (d, False) for d in FIRMWARE_FLAT_DIRS
    ]
    for d, is_component_dir in candidates:
        if not os.path.isdir(d):
            continue
        if is_component_dir and os.path.isfile(os.path.join(d, STABLE_FW_MARKER)):
            try:
                path, version = stable_marker_fw_file(d)
            except ValueError as e:
                return FwSelection(
                    error=f"Invalid stable {component} firmware: {e}. {FW_UPDATE_HINT}"
                )
            return FwSelection(path=path, version=version)
        selection = _images_without_marker(component, d, is_component_dir)
        if selection.path or selection.error:
            return selection
    return FwSelection()


def find_offline_fw_file(component: str) -> Optional[str]:
    """Returns the station firmware image of |component| (None if there is none).

    See select_offline_fw(). Raises FirmwareSelectionError when the image
    must not be guessed; a warning (no STABLE.json) is logged.
    """
    selection = select_offline_fw(component)
    if selection.error:
        logging.error("%s", selection.error)
        raise FirmwareSelectionError(selection.error)
    if selection.warning:
        logging.warning("%s", selection.warning)
    return selection.path


def resolve_maui_tool(tool_name: str) -> Optional[str]:
    """Resolves the executable path of a tool installed in the server image (ft232r_prog).

    The Maui host tools (maui-flash, maui-ctl, fw-updater) are not installed in
    the server image; they run in the maui-utils container (maui_utils_runner).
    Returns None when the tool is not installed ($PATH or /usr/local/bin).
    """
    found = shutil.which(tool_name)
    if found:
        return found
    fallback = f"/usr/local/bin/{tool_name}"
    return fallback if os.path.exists(fallback) else None


ZEPHYR_BAUDRATE = 115200
ZEPHYR_WAKE_PULSES = 4
ZEPHYR_WAKE_PULSE_DELAY_SEC = 0.08
ZEPHYR_WAKE_READ_WINDOW_SEC = 0.4
ZEPHYR_PROMPTS = (b"maui$", b"maui:~$", b"uart:~$", b"$ ")


def ensure_zephyr_shell_awake(port_node: str, timeout: float = 3.0) -> bool:
    """Sends CRLF pulses at 115200 baud to ensure the Zephyr interactive UART shell is responsive.

    Returns True only when a shell prompt was seen.
    """
    if not port_node or not os.path.exists(port_node):
        return False
    try:
        with serial.Serial(
            port=port_node,
            baudrate=ZEPHYR_BAUDRATE,
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
            resp = bytearray()
            for _ in range(ZEPHYR_WAKE_PULSES):
                s.write(b"\r\n")
                s.flush()
                time.sleep(ZEPHYR_WAKE_PULSE_DELAY_SEC)
                resp = bytearray()
                read_start = time.time()
                while (time.time() - read_start) < ZEPHYR_WAKE_READ_WINDOW_SEC:
                    if s.in_waiting > 0:
                        resp.extend(s.read(s.in_waiting))
                        if any(p in resp for p in ZEPHYR_PROMPTS):
                            logging.info(
                                "Zephyr shell on %s responded to wakeup ping: %r",
                                port_node,
                                bytes(resp),
                            )
                            return True
                    time.sleep(0.02)
                if (time.time() - start) > timeout:
                    break
            logging.warning(
                "Zephyr shell on %s did not answer wakeup pulses (last read: %r)",
                port_node,
                bytes(resp),
            )
            return False
    except (serial.SerialException, OSError, ValueError) as e:
        logging.warning("Failed to ping Zephyr shell on %s: %s", port_node, e)
        return False
