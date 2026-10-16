# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Maui v1.2 device access: CDC-ACM discovery, H2H pairing and console.

Maui v1.2 (ITE IT82002) exposes its shell on a native USB CDC-ACM interface
(18d1:5085, /dev/ttyACM*) instead of the FTDI bridge of v1.1. The console
command set is mostly the same as on v1.1, so MauiV12ConsoleTester reuses
MauiConsoleTester and only overrides what differs.
"""

import asyncio
import contextlib
import dataclasses
import glob
import logging
import os
import re
import threading
import time
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import Union

import serial

from server import maui_common
from server import maui_v12_profile as profile
from server.maui_console_tester import ConsoleTestCase
from server.maui_console_tester import MauiConsoleTester
from server.maui_console_tester import open_serial_port
from server.maui_console_tester import PdcInfoTestCase


SYS_CLASS_TTY = "/sys/class/tty"
# The shell is on the first CDC-ACM function. A second ACM function (RPC) is
# planned in the firmware and must not be treated as a console.
SHELL_INTERFACE_SUFFIX = ":1.0"

# Expected `gpio get` levels of the host-only cards: the connection-independent
# subset of the profile (the v1.1 table of maui_console_tester stays as is).
V12_EXPECTED_HOST_GPIOS: Dict[str, int] = dict(profile.EXPECTED_HOST_GPIOS)

# `adc status` prints "[%20s] =% 5d mV" or "[%20s] = error (%d)".
_ADC_LINE_RE = re.compile(
    r"\[\s*([A-Za-z0-9_]+)\s*\]\s*=\s*(?:(-?\d+)\s*mV|(error\b[^\r\n]*))"
)
ADC_CHANNELS = (
    "USB_HOST_CC1",
    "USB_HOST_CC2",
    "SBU1_DET",
    "SBU2_DET",
    "PPVAR_USB_CHG_VBUS",
    "PPVAR_USB_DUT_VBUS",
    "PP5000_USB_HOST_VBUS",
    "PP5000",
)
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_PROMPT_RE = re.compile(r"(?:^|\n)\s*\S*\$ ?")


@dataclasses.dataclass
class McuConsole:
    """A Maui v1.2 MCU CDC-ACM interface."""

    dev_node: str
    usb_path: str
    serial: str = ""
    product: str = ""
    stage: str = profile.STAGE_UNKNOWN
    interface: str = ""

    @property
    def is_app(self) -> bool:
        """True when the MCU runs the App (shell available)."""
        return self.stage == profile.STAGE_APP


@dataclasses.dataclass
class H2hDevice:
    """The host-to-host bridge of a Maui board (carries the unit serial)."""

    usb_path: str
    serial: str = ""
    product: str = ""
    pid: int = 0
    speed: str = ""
    # USB device number; changes on every (re-)enumeration.
    devnum: str = ""


@dataclasses.dataclass
class UnitInfo:
    """The MCU console and H2H bridge of one Maui v1.2 board."""

    mcu: Optional[McuConsole] = None
    h2h: Optional[H2hDevice] = None
    consoles: List[McuConsole] = dataclasses.field(default_factory=list)


def read_sysfs(path: str) -> str:
    """Returns the stripped content of a sysfs attribute, or ''."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except OSError:
        return ""


def scan_mcu_consoles(sys_class_tty: str = SYS_CLASS_TTY) -> List[McuConsole]:
    """Returns every Maui v1.2 MCU CDC-ACM shell interface (any boot stage)."""
    consoles: List[McuConsole] = []
    for tty_dir in sorted(glob.glob(os.path.join(sys_class_tty, "ttyACM*"))):
        dev_link = os.path.join(tty_dir, "device")
        if not os.path.exists(dev_link):
            continue
        iface_dir = os.path.realpath(dev_link)
        usb_dir = os.path.dirname(iface_dir)
        vid = read_sysfs(os.path.join(usb_dir, "idVendor")).lower()
        pid = read_sysfs(os.path.join(usb_dir, "idProduct")).lower()
        try:
            if int(vid, 16) != profile.GOOGLE_VID or int(pid, 16) != profile.MCU_PID:
                continue
        except ValueError:
            continue
        interface = os.path.basename(iface_dir)
        if not interface.endswith(SHELL_INTERFACE_SUFFIX):
            continue
        product = read_sysfs(os.path.join(usb_dir, "product"))
        consoles.append(
            McuConsole(
                dev_node=f"/dev/{os.path.basename(tty_dir)}",
                usb_path=usb_dir,
                serial=read_sysfs(os.path.join(usb_dir, "serial")),
                product=product,
                stage=profile.stage_for_product(product),
                interface=interface,
            )
        )
    return consoles


def scan_h2h_devices() -> List[H2hDevice]:
    """Returns every H2H bridge (18d1:506d / 18d1:5084) on the bus."""
    devices = []
    for dev in maui_common.scan_usb_devices():
        if dev.vid == profile.GOOGLE_VID and dev.pid in profile.H2H_PIDS:
            devices.append(
                H2hDevice(
                    usb_path=os.path.realpath(dev.sys_path),
                    serial=dev.serial,
                    product=dev.product,
                    pid=dev.pid,
                    speed=dev.speed,
                    devnum=read_sysfs(os.path.join(dev.sys_path, "devnum")),
                )
            )
    return devices


def _port_chain(usb_path: str) -> str:
    """Returns the hub port chain of a USB device without the bus number.

    E.g. ".../usb1/1-2/1-2.1" -> "2.1". The USB2 and USB3 halves of the
    on-board hub sit on different buses but usually share the port chain.
    """
    name = os.path.basename(usb_path)
    return name.split("-", 1)[1] if "-" in name else name


def pair_h2h(
    mcu: McuConsole,
    h2hs: List[H2hDevice],
    consoles: Optional[Sequence[McuConsole]] = None,
) -> Optional[H2hDevice]:
    """Returns the H2H bridge that belongs to the same board as |mcu|.

    The bridge must sit on the same hub as the MCU (same parent device, or the
    same hub port chain on the other bus). A lone bridge is taken without that
    match only if there is a single MCU console (one board); with several
    boards it may belong to another one. |consoles| are all MCU consoles on
    the bus (None: scan them).
    """
    if not h2hs:
        return None
    if len(h2hs) == 1:
        if consoles is None:
            consoles = scan_mcu_consoles()
        if len(consoles) <= 1:
            return h2hs[0]
    mcu_parent = os.path.dirname(mcu.usb_path)
    for h2h in h2hs:
        if os.path.dirname(h2h.usb_path) == mcu_parent:
            return h2h
    mcu_hub_chain = _port_chain(mcu_parent)
    for h2h in h2hs:
        if _port_chain(os.path.dirname(h2h.usb_path)) == mcu_hub_chain:
            return h2h
    # TODO(maui-v1.2): pair through the hub container ID when several boards
    # hang off different root ports.
    return None


def find_unit(
    scan_consoles: Callable[[], List[McuConsole]] = scan_mcu_consoles,
    scan_h2h: Callable[[], List[H2hDevice]] = scan_h2h_devices,
) -> UnitInfo:
    """Returns the (first) connected Maui v1.2 board.

    The App console is preferred over bootloader stages. Stations test one
    board at a time; the programming page uses scan_mcu_consoles() directly.
    """
    consoles = scan_consoles()
    h2hs = scan_h2h()
    apps = [c for c in consoles if c.is_app]
    mcu = (apps or consoles or [None])[0]
    h2h = pair_h2h(mcu, h2hs, consoles) if mcu else (h2hs[0] if h2hs else None)
    return UnitInfo(mcu=mcu, h2h=h2h, consoles=consoles)


def find_app_console_port(default: str = "") -> str:
    """Returns the /dev node of the first MCU App console, else |default|."""
    for console in scan_mcu_consoles():
        if console.is_app and os.path.exists(console.dev_node):
            return console.dev_node
    return default


def wait_for_console(  # pylint: disable=too-many-arguments
    serial_number: Optional[str],
    *,
    usb_path: Optional[str] = None,
    initial_delay_s: float = profile.REENUM_INITIAL_DELAY_S,
    grace_s: float = profile.REENUM_GRACE_S,
    poll_interval_s: float = profile.REENUM_POLL_INTERVAL_S,
    scan: Callable[[], List[McuConsole]] = scan_mcu_consoles,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[McuConsole]:
    """Waits for the App console with iSerial |serial_number| to enumerate.

    Sleeps |initial_delay_s| and then polls for up to |grace_s| seconds.
    A |serial_number| of None accepts any App console. A |usb_path| only
    accepts the console on that physical USB port, e.g. the board that was
    just updated when several unprovisioned boards share a serial.
    """
    sleep(initial_delay_s)
    deadline = clock() + grace_s
    while True:
        for console in scan():
            if (
                console.is_app
                and serial_number in (None, console.serial)
                and usb_path in (None, console.usb_path)
            ):
                return console
        if clock() >= deadline:
            return None
        sleep(poll_interval_s)


# ---------------------------------------------------------------------------
# Exclusive use of the MCU consoles
# ---------------------------------------------------------------------------
# Every v1.2 console at once: the whole-image flash cannot tell which board
# its USB-UART bridge is wired to.
ALL_CONSOLES = "*"
# What the background console read of get_unit_status holds the console for.
STATUS_QUERY = "unit status query"
# An exclusive operation waits this long for a running status query.
STATUS_QUERY_WAIT_S = 3.0


class ConsoleBusyError(RuntimeError):
    """An exclusive MCU console operation would collide with a running one."""


# Holders by key: a console dev node, its USB path or ALL_CONSOLES.
_busy: Dict[str, str] = {}
_busy_cond = threading.Condition()

ConsoleRef = Union[str, McuConsole]


def _console_keys(consoles: Iterable[Optional[ConsoleRef]]) -> Tuple[str, ...]:
    """Dev nodes and USB paths of |consoles| (the node changes on re-plug)."""
    keys: List[str] = []
    for console in consoles:
        if isinstance(console, McuConsole):
            keys.extend((console.dev_node, console.usb_path))
        elif console:
            keys.append(console)
    return tuple(dict.fromkeys(k for k in keys if k))


def _holder(keys: Tuple[str, ...]) -> str:
    """Returns who holds any of |keys|, or ''; needs _busy_cond."""
    if ALL_CONSOLES in _busy:
        return _busy[ALL_CONSOLES]
    if ALL_CONSOLES in keys:
        return next(iter(_busy.values()), "")
    return next((_busy[k] for k in keys if k in _busy), "")


def busy_reason(*consoles: Optional[ConsoleRef]) -> str:
    """Returns what uses the given MCU consoles exclusively, '' if free.

    Consoles are McuConsole objects, dev nodes or USB paths.
    """
    keys = _console_keys(consoles)
    with _busy_cond:
        return _holder(keys)


def claim_consoles(
    consoles: Iterable[Optional[ConsoleRef]],
    what: str,
    wait_s: float = STATUS_QUERY_WAIT_S,
) -> Tuple[str, ...]:
    """Marks |consoles| as used by |what|; returns the claimed keys.

    Raises ConsoleBusyError at once if another operation holds one of them;
    only a running unit status query is waited for (up to |wait_s|). May
    block that long, so async code calls it in an executor. Release the
    claim with release_consoles().
    """
    keys = _console_keys(consoles)
    deadline = time.monotonic() + wait_s
    with _busy_cond:
        while True:
            holder = _holder(keys)
            if not holder:
                break
            remaining = deadline - time.monotonic()
            if holder != STATUS_QUERY or remaining <= 0:
                raise ConsoleBusyError(f"MCU console busy: {holder}.")
            _busy_cond.wait(remaining)
        for key in keys:
            _busy[key] = what
    return keys


def release_consoles(keys: Iterable[str]) -> None:
    """Ends a claim of claim_consoles()."""
    with _busy_cond:
        for key in keys:
            _busy.pop(key, None)
        _busy_cond.notify_all()


def _release_if_claimed(future: "asyncio.Future[Tuple[str, ...]]") -> None:
    if not future.cancelled() and future.exception() is None:
        release_consoles(future.result())


async def claim_consoles_async(
    consoles: Iterable[Optional[ConsoleRef]],
    what: str,
    wait_s: float = STATUS_QUERY_WAIT_S,
) -> Tuple[str, ...]:
    """claim_consoles() for async code: any wait runs in an executor thread.

    Raises ConsoleBusyError like claim_consoles(); release_consoles() ends
    the claim.
    """
    future = asyncio.get_running_loop().run_in_executor(
        None, claim_consoles, list(consoles), what, wait_s
    )
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # The claim may still succeed in the thread: release it then.
        future.add_done_callback(_release_if_claimed)
        raise


@contextlib.contextmanager
def console_busy(
    consoles: Iterable[Optional[ConsoleRef]],
    what: str,
    wait_s: float = STATUS_QUERY_WAIT_S,
) -> Iterator[Tuple[str, ...]]:
    """Holds |consoles| for |what| (see claim_consoles)."""
    keys = claim_consoles(consoles, what, wait_s)
    try:
        yield keys
    finally:
        release_consoles(keys)


def strip_ansi(text: str) -> str:
    """Removes ANSI escape sequences (shell colors, line clearing)."""
    return _ANSI_RE.sub("", text or "")


def gpio_missing(output: str, name: str) -> bool:
    """True if the shell answered `gpio get <name>` without listing |name|.

    The firmware prints nothing (and no error) for an unknown GPIO name, so
    only the echo of the command and the prompt come back. An empty output
    (no answer at all) does not count as missing.
    """
    command = f"gpio get {name}"
    answered = False
    for line in strip_ansi(output).splitlines():
        if command in line:
            answered = True  # Echo of the command.
        elif name in line:
            return False
        elif _PROMPT_RE.search(line):
            answered = True
    return answered


def parse_adc_channels(output: str) -> Dict[str, Optional[int]]:
    """Returns {channel: mV} of `adc status`; None for a channel that printed error."""
    channels: Dict[str, Optional[int]] = {}
    for m in _ADC_LINE_RE.finditer(strip_ansi(output)):
        channels[m.group(1)] = int(m.group(2)) if m.group(2) is not None else None
    return channels


def _check_range(
    channels: Dict[str, Optional[int]],
    name: str,
    limits: Tuple[int, int],
    mismatches: List[str],
) -> None:
    value = channels.get(name)
    if value is not None and not limits[0] <= value <= limits[1]:
        mismatches.append(f"{name}: {value} mV outside [{limits[0]}, {limits[1]}] mV")


def _check_idle(
    channels: Dict[str, Optional[int]],
    name: str,
    max_mv: int,
    why: str,
    mismatches: List[str],
) -> None:
    value = channels.get(name)
    if value is not None and value > max_mv:
        mismatches.append(f"{name}: {value} mV above {max_mv} mV ({why})")


def verify_host_adc(
    adc_status: Dict[str, object], charger_connected: bool = True
) -> Tuple[bool, List[str]]:
    """Checks the `adc status` readings of the v1.2 host-only cards.

    Used instead of verify_host_adc_voltages(): host connected (exactly one
    active host CC line), nothing on the DUT port and, if |charger_connected|,
    the charger plugged in (vSafe5V or a 20 V PD contract on CHG VBUS), else
    vSafe0V there. The limits are USB Type-C / PD spec values (see the
    profile). Any channel that prints `error` fails.
    """
    channels = parse_adc_channels(str(adc_status.get("raw_output", "")))
    if not channels:
        return False, ["'adc status' returned no readings"]
    mismatches = [
        f"{name}: ADC read error" for name, mv in channels.items() if mv is None
    ]
    mismatches += [
        f"{name}: missing in 'adc status'"
        for name in ADC_CHANNELS
        if name not in channels
    ]
    _check_range(channels, "PP5000", profile.PP5000_MV, mismatches)
    _check_range(channels, "PP5000_USB_HOST_VBUS", profile.HOST_VBUS_MV, mismatches)
    cc1 = channels.get("USB_HOST_CC1")
    cc2 = channels.get("USB_HOST_CC2")
    if cc1 is not None and cc2 is not None:
        low, high = profile.HOST_CC_ACTIVE_MV
        idle = profile.HOST_CC_IDLE_MAX_MV
        if not (
            (low <= cc1 <= high and cc2 <= idle) or (low <= cc2 <= high and cc1 <= idle)
        ):
            mismatches.append(
                f"Host CC: USB_HOST_CC1={cc1} mV, USB_HOST_CC2={cc2} mV (expected "
                f"exactly one in [{low}, {high}] mV and the other <= {idle} mV)"
            )
    for name in ("SBU1_DET", "SBU2_DET"):
        _check_idle(channels, name, profile.SBU_IDLE_MAX_MV, "no DUT", mismatches)
    _check_idle(
        channels,
        "PPVAR_USB_DUT_VBUS",
        profile.VSAFE0V_MAX_MV,
        "vSafe0V, no DUT",
        mismatches,
    )
    chg = channels.get("PPVAR_USB_CHG_VBUS")
    if not charger_connected:
        _check_idle(
            channels,
            "PPVAR_USB_CHG_VBUS",
            profile.VSAFE0V_MAX_MV,
            "vSafe0V, no charger",
            mismatches,
        )
    elif chg is not None and not any(
        low <= chg <= high
        for low, high in (profile.CHG_VBUS_5V_MV, profile.CHG_VBUS_20V_MV)
    ):
        mismatches.append(
            f"PPVAR_USB_CHG_VBUS: {chg} mV is neither vSafe5V "
            f"[{profile.CHG_VBUS_5V_MV[0]}, {profile.CHG_VBUS_5V_MV[1]}] mV nor a "
            f"20 V contract [{profile.CHG_VBUS_20V_MV[0]}, "
            f"{profile.CHG_VBUS_20V_MV[1]}] mV (is the charger plugged in?)"
        )
    return not mismatches, mismatches


class McuVersionTestCase(ConsoleTestCase):  # pylint: disable=too-few-public-methods
    """`version` must report an approved MCU App build."""

    def __init__(self) -> None:
        super().__init__("MCU Version Check", "version")

    def evaluate(self, output: str) -> Tuple[bool, str]:
        check = profile.check_mcu_version(output)
        return check.ok, check.message


class MauiV12ConsoleTester(MauiConsoleTester):
    """MauiConsoleTester for the IT82002 App shell."""

    EXPECTED_PDC_APP_CONFIG_VER = profile.EXPECTED_PDC_APP_CONFIG_VER
    EXPECTED_PDC_FW_VER = profile.EXPECTED_PDC_FW_VER

    def _register_default_tests(self) -> None:
        self.test_cases.append(McuVersionTestCase())
        self.test_cases.append(
            PdcInfoTestCase(
                name="PDC Info Check",
                expected_app_config_ver=self.EXPECTED_PDC_APP_CONFIG_VER,
                expected_fw_ver=self.EXPECTED_PDC_FW_VER,
            )
        )

    @contextlib.contextmanager
    def _session(self, ser: Optional[serial.Serial]) -> Iterator[serial.Serial]:
        if ser is not None:
            yield ser
            return
        with open_serial_port(self.port_node, self.baudrate, timeout=self.timeout) as s:
            yield s

    def read_hwrev(self, ser: Optional[serial.Serial] = None) -> Tuple[bool, int, str]:
        """Reads `hwrev`; any revision of profile.valid_hwrevs() passes."""
        try:
            with self._session(ser) as s:
                output = self.send_command(
                    s, "hwrev", timeout=1.0, first_byte_timeout=0.5
                )
            m = re.search(r"Hardware\s+revision:\s*(-?\d+)", output, re.IGNORECASE)
            if m:
                rev = int(m.group(1))
                return rev in profile.valid_hwrevs(), rev, output
            return False, -1, output
        except Exception as e:  # pylint: disable=broad-except
            logging.warning("Failed to read hwrev on %s: %s", self.port_node, e)
            return False, -1, str(e)

    def read_and_verify_gpios(
        self,
        expected_gpios: Optional[Dict[str, int]] = None,
        ser: Optional[serial.Serial] = None,
    ) -> Tuple[bool, Dict[str, int], List[str], str]:
        """Reads `gpio get` and checks it against the v1.2 table by default."""
        return super().read_and_verify_gpios(
            expected_gpios=(
                V12_EXPECTED_HOST_GPIOS if expected_gpios is None else expected_gpios
            ),
            ser=ser,
        )

    def read_gpio(
        self, name: str, ser: Optional[serial.Serial] = None
    ) -> Tuple[int, str]:
        """Returns (level, raw_output) of GPIO |name|; level is -1 if unknown."""
        with self._session(ser) as s:
            output = self.send_command(
                s, f"gpio get {name}", first_byte_timeout=0.15, inter_byte_timeout=0.03
            )
        m = re.search(
            r"\[?\s*" + re.escape(name) + r"\s*\]?\s*(?:[IOio]\s*)?[=:]\s*([01])",
            output,
        )
        return (int(m.group(1)) if m else -1), output

    def sample_bsl_gpio(
        self,
        max_duration_sec: float = 3.0,
        interval_sec: float = profile.BSL_POLL_INTERVAL_S,
    ) -> Tuple[bool, int, str]:
        """Polls the BSL button GPIO until it reads pressed.

        Stops at once if the firmware has no such GPIO (see gpio_missing()).
        Returns (pressed, last_level, last_raw_output).
        """
        last_level = -1
        last_output = ""
        start = time.time()
        try:
            with self._session(None) as s:
                while (time.time() - start) < max_duration_sec:
                    last_level, last_output = self.read_gpio(
                        profile.BSL_GPIO_NAME, ser=s
                    )
                    if last_level == profile.BSL_GPIO_PRESSED:
                        return True, last_level, last_output
                    if gpio_missing(last_output, profile.BSL_GPIO_NAME):
                        break
                    time.sleep(interval_sec)
        except Exception as e:  # pylint: disable=broad-except
            logging.warning("Failed to sample BSL GPIO on %s: %s", self.port_node, e)
            return False, -1, f"Error: {e}"
        return False, last_level, last_output

    def serial_get(self, ser: Optional[serial.Serial] = None) -> Tuple[str, str]:
        """Returns (serial_number, raw_output) from `serial get`."""
        with self._session(ser) as s:
            output = self.send_command(s, "serial get")
        m = re.search(r"Serial Number:\s*(\S+)", output)
        return (m.group(1) if m else ""), output

    def serial_set(
        self, serial_number: str, ser: Optional[serial.Serial] = None
    ) -> Tuple[bool, str]:
        """Stores |serial_number| in the MCU NVS. Returns (ok, raw_output)."""
        with self._session(ser) as s:
            output = self.send_command(s, f"serial set {serial_number}")
        return f"Serial Number updated to: {serial_number}" in output, output

    def reboot(self, ser: Optional[serial.Serial] = None) -> str:
        """Sends `reboot`. The console disappears, so errors are ignored."""
        try:
            with self._session(ser) as s:
                return self.send_command(
                    s, "reboot", timeout=0.5, first_byte_timeout=0.3
                )
        except Exception as e:  # pylint: disable=broad-except
            logging.info("Console closed during reboot of %s: %s", self.port_node, e)
            return ""


@dataclasses.dataclass
class ProvisionResult:  # pylint: disable=too-many-instance-attributes
    """Outcome of provision_serial()."""

    success: bool
    message: str
    already_provisioned: bool = False
    h2h_serial: str = ""
    previous_mcu_serial: str = ""
    mcu_serial: str = ""
    device_port: str = ""
    reenumeration_s: float = 0.0
    log: List[str] = dataclasses.field(default_factory=list)


def provision_serial(  # pylint: disable=too-many-return-statements
    unit: UnitInfo,
    expected_serial: str = "",
    make_tester: Callable[
        [str], MauiV12ConsoleTester
    ] = lambda port: MauiV12ConsoleTester(
        port_node=port, timeout=profile.CONSOLE_TIMEOUT_S
    ),
    wait: Callable[[str], Optional[McuConsole]] = wait_for_console,
    clock: Callable[[], float] = time.monotonic,
) -> ProvisionResult:
    """Copies the H2H serial into the MCU NVS and verifies the new iSerial.

    1. The H2H iSerial must be a valid unit serial (and match
       |expected_serial| when given).
    2. If the MCU iSerial already equals it, pass straight away.
    3. Otherwise `serial set <SN>` (overwriting any previous MCU serial),
       `reboot`, wait for the App console with iSerial == SN and confirm with
       `serial get`.
    """
    log: List[str] = []
    h2h_sn = (unit.h2h.serial if unit.h2h else "").strip()
    res = ProvisionResult(success=False, message="", h2h_serial=h2h_sn, log=log)

    if not unit.h2h:
        res.message = "H2H bridge (18d1:506d/5084) not detected."
        return res
    if not profile.is_valid_serial(h2h_sn):
        res.message = f"H2H serial '{h2h_sn}' is not a valid Maui v1.2 serial."
        return res
    if expected_serial and expected_serial.strip().upper() != h2h_sn.upper():
        res.message = (
            f"H2H serial '{h2h_sn}' does not match expected '{expected_serial}'."
        )
        return res
    if not unit.mcu:
        res.message = "Maui MCU console (18d1:5085) not detected."
        return res
    if not unit.mcu.is_app:
        res.message = f"MCU is not running the App (iProduct '{unit.mcu.product}')."
        return res

    res.previous_mcu_serial = unit.mcu.serial
    res.device_port = unit.mcu.dev_node
    log.append(
        f"H2H serial: {h2h_sn}; MCU iSerial: {unit.mcu.serial} ({unit.mcu.dev_node})"
    )

    if unit.mcu.serial == h2h_sn:
        res.success = True
        res.already_provisioned = True
        res.mcu_serial = h2h_sn
        res.message = f"MCU serial already matched H2H serial {h2h_sn} (skipped)."
        log.append(res.message)
        return res

    tester = make_tester(unit.mcu.dev_node)
    ok, out = tester.serial_set(h2h_sn)
    log.append(f"serial set {h2h_sn}:\n{out}")
    if not ok:
        res.message = f"'serial set {h2h_sn}' was not confirmed by the MCU."
        return res

    log.append(f"reboot:\n{tester.reboot()}")
    start = clock()
    console = wait(h2h_sn)
    res.reenumeration_s = round(clock() - start, 2)
    if console is None:
        res.message = (
            f"MCU did not re-enumerate as '{profile.PRODUCT_APP}' with serial {h2h_sn} "
            f"within {profile.REENUM_INITIAL_DELAY_S + profile.REENUM_GRACE_S:.0f} s."
        )
        log.append(res.message)
        return res

    res.device_port = console.dev_node
    confirmed, out = make_tester(console.dev_node).serial_get()
    log.append(f"serial get ({console.dev_node}):\n{out}")
    res.mcu_serial = confirmed or console.serial
    if not confirmed:
        res.message = "'serial get' did not return the MCU serial number."
        log.append(res.message)
        return res
    if confirmed != h2h_sn:
        res.message = f"'serial get' returned '{confirmed}', expected '{h2h_sn}'."
        log.append(res.message)
        return res

    res.success = True
    res.message = (
        f"MCU serial set to {h2h_sn} (was '{res.previous_mcu_serial}'); "
        f"re-enumerated in {res.reenumeration_s:.1f} s."
    )
    log.append(res.message)
    return res
