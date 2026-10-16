# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""External USB-UART bridges for whole-image MCU flashing (Maui v1.2).

itecomdbgr writes the whole ITE IT82002 flash over the MCU debug UART
(DEBUG_UART_TX/RX test points) through a USB-UART bridge plugged into the
station. Candidate bridges are found in sysfs:

* /sys/class/tty/ttyUSB* (usb-serial) and /sys/class/tty/ttyACM* (cdc_acm);
* realpath(<tty>/device) is the USB interface (cdc_acm) or the usb-serial
  port below it; the first directory with bInterfaceNumber is the interface
  and its parent the USB device;
* the interface driver must be one of BRIDGE_DRIVERS (the usb-serial port
  driver of CH341 is named ch341-uart, so the interface driver is used);
* Google devices (VID 18d1: Maui MCU and H2H, GSC/CCD consoles, servo) and
  the Maui v1.1 on-board FTDI (0403:6002) are never offered.
"""

import dataclasses
import glob
import logging
import os
import re
import termios
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple

from server import maui_common
from server import maui_v12_profile as profile
from server.maui_v12_device import read_sysfs


SYSFS_ROOT = "/sys"
DEV_ROOT = "/dev"
TTY_PATTERNS = ("ttyUSB*", "ttyACM*")

# Accepted USB interface drivers, most preferred first.
BRIDGE_DRIVERS = ("ftdi_sio", "cp210x", "cdc_acm", "pl2303", "ch341")
_DRIVER_RANK = {driver: rank for rank, driver in enumerate(BRIDGE_DRIVERS)}

# Never a flashing bridge: every Google device (Maui MCU 5085, H2H 506d/5084,
# GSC/CCD 5014/504a/5066/520d, servo) and the Maui v1.1 on-board FT232R.
EXCLUDED_VIDS = (profile.GOOGLE_VID,)
EXCLUDED_VID_PIDS = ((maui_common.FTDI_VID, maui_common.FTDI_PID_PROGRAMMED),)

# ftdi_sio holds received bytes for latency_timer ms (default 16) before it
# sends a USB packet. itecomdbgr makes ~25k small round trips, so 1 ms about
# halves the flashing time.
FTDI_LATENCY_TIMER_MS = 1

# termios c_cflag bit for mark/space parity (not exported on every Python).
_CMSPAR = getattr(termios, "CMSPAR", 0o10000000000)


@dataclasses.dataclass(frozen=True)
class UartBridge:  # pylint: disable=too-many-instance-attributes
    """A USB-UART bridge that itecomdbgr can use."""

    dev_node: str  # e.g. /dev/ttyUSB0
    by_id: str  # /dev/serial/by-id/<name>, or '' if udev did not create one
    driver: str  # USB interface driver, one of BRIDGE_DRIVERS
    vid: int
    pid: int
    manufacturer: str
    product: str
    serial: str
    usb_path: str  # USB device name, e.g. 1-2.3 (stable per physical port)
    interface: int  # bInterfaceNumber
    port_sys_path: str = ""  # realpath(<tty>/device); ftdi_sio latency_timer

    @property
    def name(self) -> str:
        """Short human readable name: the by-id name if there is one."""
        if self.by_id:
            return os.path.basename(self.by_id)
        desc = f"{self.manufacturer} {self.product}".strip()
        if self.serial:
            desc = f"{desc} [{self.serial}]".strip()
        return desc or f"{self.vid:04x}:{self.pid:04x}"

    @property
    def label(self) -> str:
        """Label for the UI, e.g. 'usb-FTDI_..-if00-port0 (/dev/ttyUSB0, ...)'."""
        return (
            f"{self.name} ({self.dev_node}, {self.vid:04x}:{self.pid:04x}, "
            f"{self.driver}, USB {self.usb_path})"
        )

    def matches(self, port: str) -> bool:
        """True if |port| names this bridge (dev node or by-id path)."""
        return bool(port) and port in (self.dev_node, self.by_id)


def _usb_path_key(usb_path: str) -> Tuple[int, ...]:
    """Natural sort key of a USB device name: '1-10.2' -> (1, 10, 2)."""
    return tuple(int(n) for n in re.findall(r"\d+", usb_path))


def sort_key(bridge: UartBridge) -> Tuple:
    """Deterministic bridge order: driver preference, USB path, interface."""
    return (
        _DRIVER_RANK.get(bridge.driver, len(BRIDGE_DRIVERS)),
        _usb_path_key(bridge.usb_path),
        bridge.usb_path,
        bridge.interface,
        bridge.dev_node,
    )


def _by_id_links(dev_root: str) -> Dict[str, str]:
    """Maps tty names (ttyUSB0) to their /dev/serial/by-id symlink."""
    links: Dict[str, str] = {}
    for link in sorted(glob.glob(os.path.join(dev_root, "serial", "by-id", "*"))):
        links.setdefault(os.path.basename(os.path.realpath(link)), link)
    return links


def _usb_interface_dir(port_dir: str) -> str:
    """Returns the USB interface directory at or above |port_dir|, or ''."""
    path = port_dir
    while path and path != os.path.dirname(path):
        if os.path.exists(os.path.join(path, "bInterfaceNumber")):
            return path
        path = os.path.dirname(path)
    return ""


def _read_bridge(tty_dir: str, by_id: Dict[str, str], dev_root: str):
    """Returns the UartBridge of a /sys/class/tty entry, or None."""
    name = os.path.basename(tty_dir)
    dev_link = os.path.join(tty_dir, "device")
    if not os.path.exists(dev_link):
        return None
    port_dir = os.path.realpath(dev_link)
    iface_dir = _usb_interface_dir(port_dir)
    if not iface_dir:
        return None
    usb_dir = os.path.dirname(iface_dir)
    driver = os.path.basename(os.path.realpath(os.path.join(iface_dir, "driver")))
    if driver not in _DRIVER_RANK:
        return None
    try:
        vid = int(read_sysfs(os.path.join(usb_dir, "idVendor")), 16)
        pid = int(read_sysfs(os.path.join(usb_dir, "idProduct")), 16)
    except ValueError:
        return None
    if vid in EXCLUDED_VIDS or (vid, pid) in EXCLUDED_VID_PIDS:
        return None
    try:
        interface = int(read_sysfs(os.path.join(iface_dir, "bInterfaceNumber")), 16)
    except ValueError:
        interface = 0
    return UartBridge(
        dev_node=os.path.join(dev_root, name),
        by_id=by_id.get(name, ""),
        driver=driver,
        vid=vid,
        pid=pid,
        manufacturer=read_sysfs(os.path.join(usb_dir, "manufacturer")),
        product=read_sysfs(os.path.join(usb_dir, "product")),
        serial=read_sysfs(os.path.join(usb_dir, "serial")),
        usb_path=os.path.basename(usb_dir),
        interface=interface,
        port_sys_path=port_dir,
    )


def scan_uart_bridges(
    sysfs_root: str = SYSFS_ROOT, dev_root: str = DEV_ROOT
) -> List[UartBridge]:
    """Returns the candidate USB-UART bridges in the deterministic order."""
    by_id = _by_id_links(dev_root)
    bridges = []
    for pattern in TTY_PATTERNS:
        for tty_dir in glob.glob(os.path.join(sysfs_root, "class", "tty", pattern)):
            bridge = _read_bridge(tty_dir, by_id, dev_root)
            if bridge:
                bridges.append(bridge)
    return sorted(bridges, key=sort_key)


def default_bridge(bridges: Sequence[UartBridge]) -> Optional[UartBridge]:
    """The bridge preselected for the operator: the first one."""
    return bridges[0] if bridges else None


def find_bridge(port: str, bridges: Sequence[UartBridge]) -> Optional[UartBridge]:
    """Returns the bridge named by |port| (dev node or by-id path), or None."""
    for bridge in bridges:
        if bridge.matches(port):
            return bridge
    return None


def set_ftdi_latency_timer(
    bridge: UartBridge, value_ms: int = FTDI_LATENCY_TIMER_MS
) -> str:
    """Sets the ftdi_sio latency_timer of |bridge| (best effort).

    Returns a log line ('' for other drivers). The kernel resets the value
    to 16 ms on every plug-in, so it is set before every flash.
    """
    if bridge.driver != "ftdi_sio" or not bridge.port_sys_path:
        return ""
    path = os.path.join(bridge.port_sys_path, "latency_timer")
    old = read_sysfs(path)
    if old == str(value_ms):
        msg = f"FTDI latency_timer of {bridge.dev_node} is already {value_ms} ms."
    else:
        try:
            with open(path, "w", encoding="ascii") as f:
                f.write(str(value_ms))
            msg = (
                f"FTDI latency_timer of {bridge.dev_node} set to {value_ms} ms "
                f"(was {old or 'unknown'})."
            )
        except OSError as e:
            msg = (
                f"Could not set the FTDI latency_timer of {bridge.dev_node} to "
                f"{value_ms} ms ({e}); flashing takes about twice as long."
            )
            logging.warning(msg)
            return msg
    logging.info(msg)
    return msg


def normalize_termios(dev_node: str) -> str:
    """Clears odd and mark/space parity left on |dev_node| (best effort).

    itecomdbgr switches the port to 8E1 by setting PARENB but keeps PARODD
    and CMSPAR from the previous user of the tty, which would make the MCU
    ignore every byte. Returns a log line ('' if nothing had to change).
    """
    try:
        fd = os.open(dev_node, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as e:
        return f"Could not open {dev_node} to check its parity settings: {e}"
    try:
        attrs = termios.tcgetattr(fd)
        cflag = attrs[2]
        attrs[2] = cflag & ~(termios.PARODD | _CMSPAR)
        if attrs[2] == cflag:
            return ""
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        msg = f"Cleared odd/mark parity left on {dev_node}."
        logging.info(msg)
        return msg
    except termios.error as e:
        return f"Could not normalize the termios settings of {dev_node}: {e}"
    finally:
        os.close(fd)


def prepare_bridge(bridge: UartBridge) -> List[str]:
    """Readies |bridge| for itecomdbgr. Returns log lines (best effort)."""
    lines = [set_ftdi_latency_timer(bridge), normalize_termios(bridge.dev_node)]
    return [line for line in lines if line]
