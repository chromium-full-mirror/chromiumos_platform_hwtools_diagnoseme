# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""ADB data transfer reliability test through the Maui H2H bridge.

Generates two random files with independent content (DEFAULT_SIZE_MIB and
1-16 MiB more), pushes both to the DUT, hashes both on the DUT, pulls both
back and compares every SHA-1 (host original, DUT copy, pulled copy). Before
that, the H2H link must run at USB 3 speed on both the host and the DUT side.
The transfer phase (push, DUT SHA-1, pull) must finish within a deadline (40 s
by default as a backstop, ~25-35 s on a USB 3 link); generating the files is
not counted but capped. Shared by all Maui revisions.
"""

import dataclasses
import hashlib
import logging
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple


MIB = 1024 * 1024
# Rates are reported in MB/s with MB = 10^6 bytes (adb itself prints MiB/s).
MB = 1000 * 1000
# Size of the first random test file; the second one is a random 1-16 MiB
# larger, so the two transfers differ in size and content.
DEFAULT_SIZE_MIB = 400
MAX_SIZE_MIB = 2048
EXTRA_MIB_RANGE = (1, 16)
# Limit of the transfer phase: push both files, SHA-1 both on the DUT and
# pull both back.
DEFAULT_DEADLINE_S = 40.0
# Generating the random files is not part of the deadline but must not take
# longer than this.
GENERATION_LIMIT_S = 30.0
# Limit of the DUT sha1sum of both files (counts towards the deadline).
DUT_SHA1_TIMEOUT_S = 20.0
# Directory on the DUT writable by the adb shell user (no `adb root`).
DUT_DIR = os.environ.get("MAUI_ADB_TEST_DIR", "/data/local/tmp")
DUT_FILE_NAMES = ("diagnoseme_adb_transfer_1.bin", "diagnoseme_adb_transfer_2.bin")
# Minimum push and pull rate in MB/s (0: the speed is only logged).
MIN_RATE_MB_S = 0.0
# Station overrides of the defaults above (config.env, passed to the server
# by docker compose), e.g. for USB 2.0 links or once hardware numbers exist.
SIZE_ENV = "MAUI_ADB_SIZE_MB"  # Size of the first file in MiB.
DEADLINE_ENV = "MAUI_ADB_DEADLINE_S"
MIN_RATE_ENV = "MAUI_ADB_MIN_RATE_MB_S"
# Minimum USB link speed (Mb/s) of the H2H on the host and on the DUT side
# before the transfer; 0 disables the check (station override, e.g. a USB 2.0
# test setup).
MIN_LINK_MBPS = 5000
MIN_LINK_ENV = "MAUI_ADB_MIN_LINK_MBPS"
# USB IDs (VID, PID) of the H2H bridge (ADB function / Prolific).
H2H_USB_IDS = (("18d1", "506d"), ("18d1", "5084"), ("067b", "27a1"))
SYSFS_USB_DIR = "/sys/bus/usb/devices"
# Lists "vid:pid serial=<iSerial> speed=<Mb/s>" of every USB device on the DUT.
DUT_USB_LIST_CMD = (
    "for d in /sys/bus/usb/devices/*; do if [ -f $d/idVendor ]; then "
    "echo $(cat $d/idVendor 2>/dev/null):$(cat $d/idProduct 2>/dev/null) "
    "serial=$(cat $d/serial 2>/dev/null) speed=$(cat $d/speed 2>/dev/null); "
    "fi; done"
)
LINK_HINT = "use USB 3 cables and a USB 3 host port"

_CHUNK = 4 * MIB
_SHA1_LINE_RE = re.compile(r"^\s*([0-9a-f]{40})\s+\*?(\S+)", re.MULTILINE)
_DUT_USB_LINE_RE = re.compile(
    r"^\s*([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s+serial=(.*?)\s*speed=(\S*)\s*$",
    re.MULTILINE,
)

Runner = Callable[..., subprocess.CompletedProcess]


def _rate(size: int, seconds: float) -> float:
    return size / MB / seconds if seconds > 0 else 0.0


@dataclasses.dataclass
class AdbFileResult:
    """Outcome of one test file of run_adb_transfer_test()."""

    name: str = ""
    size_bytes: int = 0
    push_s: float = 0.0
    pull_s: float = 0.0
    host_sha1: str = ""
    dut_sha1: str = ""
    pulled_sha1: str = ""

    @property
    def push_mb_s(self) -> float:
        """Push rate in MB/s (0 if not pushed)."""
        return _rate(self.size_bytes, self.push_s)

    @property
    def pull_mb_s(self) -> float:
        """Pull rate in MB/s (0 if not pulled)."""
        return _rate(self.size_bytes, self.pull_s)


@dataclasses.dataclass
class AdbTransferResult:  # pylint: disable=too-many-instance-attributes
    """Outcome of run_adb_transfer_test().

    file_size_bytes, push_s / pull_s and the rates cover both files; the
    SHA-1 fields list the per-file hashes separated by ", ". transfer_s is
    the transfer phase limited by deadline_s; total_s also includes
    generating the files.
    """

    success: bool = False
    message: str = ""
    adb_serial: str = ""
    file_size_bytes: int = 0
    push_s: float = 0.0
    pull_s: float = 0.0
    push_mb_s: float = 0.0
    pull_mb_s: float = 0.0
    host_sha1: str = ""
    dut_sha1: str = ""
    pulled_sha1: str = ""
    total_s: float = 0.0
    log: List[str] = dataclasses.field(default_factory=list)
    transfer_s: float = 0.0
    deadline_s: float = 0.0
    files: List[AdbFileResult] = dataclasses.field(default_factory=list)
    # USB link speed of the H2H in Mb/s on the host / DUT side (0: unknown).
    host_link_mbps: int = 0
    dut_link_mbps: int = 0


class _Deadline:  # pylint: disable=too-few-public-methods
    """Remaining time of the transfer phase."""

    def __init__(self, seconds: float, clock: Callable[[], float]):
        self._clock = clock
        self._end = clock() + seconds

    def remaining(self) -> float:
        """Returns the seconds left until the deadline (>= 0)."""
        return max(0.0, self._end - self._clock())


def list_adb_devices(run: Runner = subprocess.run) -> List[str]:
    """Returns the serials of ADB devices in the 'device' state."""
    proc = run(
        ["adb", "devices"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=5.0,
        check=False,
    )
    serials = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def select_adb_serial(
    devices: List[str], preferred: str = ""
) -> Tuple[str, Optional[str]]:
    """Returns (serial, warning) of the DUT to test ('' if none)."""
    if preferred and preferred in devices:
        return preferred, None
    if not devices:
        return "", None
    if len(devices) > 1:
        return devices[0], (
            f"{len(devices)} ADB devices connected ({', '.join(devices)}); "
            f"testing {devices[0]}."
        )
    return devices[0], None


def supports_no_compression(run: Runner = subprocess.run) -> bool:
    """True if this adb knows `push/pull -Z` (compression, adb >= 30)."""
    try:
        proc = run(
            ["adb", "help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "-Z" in (proc.stdout or "")


def write_random_file(
    path: str, size: int, check: Optional[Callable[[], None]] = None
) -> str:
    """Writes |size| random bytes to |path|. Returns their SHA-1.

    |check| is called before every chunk and may raise to abort.
    """
    sha = hashlib.sha1()
    left = size
    with open(path, "wb") as f:
        while left > 0:
            if check:
                check()
            chunk = os.urandom(min(_CHUNK, left))
            sha.update(chunk)
            f.write(chunk)
            left -= len(chunk)
    return sha.hexdigest()


def sha1_of_file(path: str) -> str:
    """Returns the SHA-1 of the file at |path|."""
    sha = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            sha.update(chunk)
    return sha.hexdigest()


def parse_sha1sum(output: str) -> Dict[str, str]:
    """Returns {path: sha1} of `sha1sum <paths>` output."""
    return {m.group(2): m.group(1) for m in _SHA1_LINE_RE.finditer(output or "")}


def _env_number(name: str, default: float, minimum: float) -> float:
    """Returns the number >= |minimum| in environment variable |name|."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        number = float(value)
    except ValueError:
        number = float("nan")
    if not math.isfinite(number) or number < minimum:
        logging.warning("Ignoring invalid %s=%r", name, value)
        return default
    return number


def get_size_mib() -> int:
    """Returns the size of the first file in MiB (MAUI_ADB_SIZE_MB, else 400)."""
    return min(int(_env_number(SIZE_ENV, DEFAULT_SIZE_MIB, 1)), MAX_SIZE_MIB)


def get_deadline_s() -> float:
    """Returns the transfer deadline in seconds (MAUI_ADB_DEADLINE_S, else 40)."""
    return _env_number(DEADLINE_ENV, DEFAULT_DEADLINE_S, 1.0)


def get_min_rate_mb_s() -> float:
    """Returns the minimum push / pull rate in MB/s (0: only logged)."""
    return _env_number(MIN_RATE_ENV, MIN_RATE_MB_S, 0.0)


def get_min_link_mbps() -> int:
    """Returns the minimum H2H link speed in Mb/s (0: not checked)."""
    return int(_env_number(MIN_LINK_ENV, MIN_LINK_MBPS, 0.0))


def _parse_speed(value: str) -> Optional[int]:
    """Returns the USB `speed` sysfs value in Mb/s (None if not a number)."""
    try:
        return int(float(value))
    except ValueError:
        return None


def _pick_h2h_speed(
    devices: List[Tuple[str, str, str, str]], serial: str
) -> Tuple[Optional[int], str]:
    """Returns (speed, description) of the H2H in (vid, pid, serial, speed) list.

    Prefers the device whose iSerial is |serial|; speed is None if no H2H was
    found or its speed is unreadable.
    """
    h2hs = [d for d in devices if (d[0].lower(), d[1].lower()) in H2H_USB_IDS]
    if not h2hs:
        return None, "no H2H device"
    matching = [d for d in h2hs if serial and d[2].strip().upper() == serial.upper()]
    vid, pid, dev_serial, speed = (matching or h2hs)[0]
    desc = f"{vid}:{pid} serial={dev_serial or '-'} speed={speed or '-'}"
    if not matching and serial:
        desc += f" (no H2H with serial {serial}; {len(h2hs)} found)"
    return _parse_speed(speed), desc


def _read_sysfs(path: str) -> str:
    """Returns the stripped content of a sysfs attribute, '' if unreadable."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as attr:
            text = attr.read()
    except OSError:
        text = ""
    return text.strip()


def list_host_usb_devices(
    sysfs_dir: str = SYSFS_USB_DIR,
) -> List[Tuple[str, str, str, str]]:
    """Returns (vid, pid, serial, speed) of the USB devices in sysfs."""
    devices = []
    try:
        names = sorted(os.listdir(sysfs_dir))
    except OSError:
        return devices
    for name in names:
        if ":" in name:  # Interfaces.
            continue
        path = os.path.join(sysfs_dir, name)
        vid = _read_sysfs(os.path.join(path, "idVendor"))
        pid = _read_sysfs(os.path.join(path, "idProduct"))
        if vid and pid:
            devices.append(
                (
                    vid,
                    pid,
                    _read_sysfs(os.path.join(path, "serial")),
                    _read_sysfs(os.path.join(path, "speed")),
                )
            )
    return devices


def parse_dut_usb_list(output: str) -> List[Tuple[str, str, str, str]]:
    """Returns (vid, pid, serial, speed) of DUT_USB_LIST_CMD output."""
    return [m.groups() for m in _DUT_USB_LINE_RE.finditer(output or "")]


def _mbps_text(speed: Optional[int]) -> str:
    return "unknown" if speed is None else f"{speed} Mb/s"


def _join(values: List[str]) -> str:
    """Returns |values| separated by ", " ('' if all are empty)."""
    return ", ".join(v or "n/a" for v in values) if any(values) else ""


class _Failure(Exception):
    """Ends the test with the failure message of the exception."""


class _TransferTest:  # pylint: disable=too-many-instance-attributes
    """State and steps of one run_adb_transfer_test() run."""

    def __init__(self, run: Runner, clock: Callable[[], float], deadline_s: float):
        self.res = AdbTransferResult(deadline_s=deadline_s)
        self._run = run
        self._clock = clock
        self._local: List[str] = []
        self._remote: List[str] = []
        self._pulled: List[str] = []
        # Set by start_transfers() / end_transfers().
        self._deadline: Optional[_Deadline] = None
        self._transfer_start: Optional[float] = None
        self._transfer_end: Optional[float] = None
        # `push/pull -Z` (no compression) when this adb supports it.
        self._no_compress: List[str] = []

    def log(self, line: str) -> None:
        """Appends |line| to the test log."""
        self.res.log.append(line)

    def fail(self, message: str) -> None:
        """Marks the test as failed with |message|."""
        self.res.success = False
        self.res.message = message
        self.log(f"FAILED: {message}")

    def adb(self, args: List[str], timeout: float) -> subprocess.CompletedProcess:
        """Runs adb on the selected device and logs the command and output."""
        cmd = ["adb", "-s", self.res.adb_serial] + args
        try:
            proc = self._run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=max(timeout, 0.1),
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.log(f"$ {' '.join(cmd)} -> timed out after {max(timeout, 0.1):.1f} s")
            raise
        out = (proc.stdout or "").strip()
        self.log(f"$ {' '.join(cmd)} -> rc={proc.returncode}")
        if out:
            self.log(out)
        return proc

    def select_device(self, preferred_serial: str) -> None:
        """Picks the DUT and the adb transfer options."""
        try:
            devices = list_adb_devices(self._run)
        except (OSError, subprocess.SubprocessError) as e:
            raise _Failure(f"adb is not available: {e}") from e
        self.res.adb_serial, warning = select_adb_serial(devices, preferred_serial)
        self.log(f"ADB devices: {devices or 'none'}")
        if warning:
            self.log(f"WARNING: {warning}")
        if not self.res.adb_serial:
            raise _Failure("No ADB device (DUT) found. Check the H2H link and DUT.")
        self._no_compress = ["-Z"] if supports_no_compression(self._run) else []

    def check_link(self, sysfs_dir: str, min_mbps: int) -> None:
        """Fails unless the H2H runs at >= |min_mbps| on the host and the DUT."""
        serial = self.res.adb_serial
        host, host_desc = _pick_h2h_speed(list_host_usb_devices(sysfs_dir), serial)
        self.log(f"Host USB (H2H): {host_desc}")
        try:
            proc = self.adb(["shell", DUT_USB_LIST_CMD], 5.0)
            dut_devices = parse_dut_usb_list(proc.stdout or "")
        except (OSError, subprocess.SubprocessError) as e:
            self.log(f"DUT USB query failed: {e}")
            dut_devices = []
        dut, dut_desc = _pick_h2h_speed(dut_devices, serial)
        self.log(f"DUT USB (H2H): {dut_desc}")
        self.res.host_link_mbps = host or 0
        self.res.dut_link_mbps = dut or 0
        self.log(
            f"H2H USB link: host {_mbps_text(host)}, DUT {_mbps_text(dut)} "
            + (f"(required >= {min_mbps} Mb/s)" if min_mbps else "(not checked)")
        )
        if not min_mbps:
            return
        if host is not None and dut is not None and min(host, dut) >= min_mbps:
            return
        message = (
            f"H2H link is not USB 3 (host {_mbps_text(host)}, DUT "
            f"{_mbps_text(dut)}) - {LINK_HINT}."
        )
        if host is None:
            message += (
                " The H2H USB speed could not be read on the host "
                f"({host_desc} in {sysfs_dir})."
            )
        if dut is None:
            message += f" The H2H USB speed could not be read on the DUT ({dut_desc})."
        raise _Failure(message)

    def generate(self, tmp_dir: str, sizes: List[int], limit_s: float) -> None:
        """Writes the random files; fails if that takes longer than |limit_s|."""
        start = self._clock()

        def check() -> None:
            if self._clock() - start > limit_s:
                raise _Failure(
                    "Generating the random test files took longer than "
                    f"{limit_s:g} s (host too slow?)."
                )

        for i, size in enumerate(sizes):
            name = f"file {i + 1}"
            self._local.append(os.path.join(tmp_dir, f"random_{i + 1}.bin"))
            self._remote.append(f"{DUT_DIR.rstrip('/')}/{DUT_FILE_NAMES[i]}")
            self._pulled.append(os.path.join(tmp_dir, f"pulled_{i + 1}.bin"))
            f = AdbFileResult(name=name, size_bytes=size)
            self.res.files.append(f)
            f.host_sha1 = write_random_file(self._local[i], size, check)
            self.log(f"Host SHA-1 {name} ({size // MIB} MiB, {size} B): {f.host_sha1}")
        check()
        self.log(
            f"Generated the test files in {self._clock() - start:.1f} s "
            "(not part of the deadline)."
        )

    def start_transfers(self) -> None:
        """Starts the deadline of the transfer phase."""
        self._transfer_start = self._clock()
        self._deadline = _Deadline(self.res.deadline_s, self._clock)

    @property
    def transfers_started(self) -> bool:
        """True once the transfer phase has started."""
        return self._transfer_start is not None

    def end_transfers(self) -> None:
        """Records the duration of the transfer phase (once)."""
        if self._transfer_start is not None and self._transfer_end is None:
            self._transfer_end = self._clock()
            self.res.transfer_s = self._transfer_end - self._transfer_start

    def _remaining(self) -> float:
        return self._deadline.remaining() if self._deadline else 0.0

    def _timeout_message(self, what: str) -> str:
        res = self.res
        # Each file is pushed and pulled once.
        needed = _rate(2 * res.file_size_bytes, res.deadline_s)
        return (
            f"{what} did not finish within the {res.deadline_s:g} s transfer "
            f"deadline (needs >= {needed:.1f} MB/s on average)."
        )

    def _transfer(self, args: List[str], name: str) -> float:
        """Runs `adb push/pull` of file |name| within the deadline.

        Returns the duration of the transfer.
        """
        t0 = self._clock()
        try:
            proc = self.adb([args[0]] + self._no_compress + args[1:], self._remaining())
        except subprocess.TimeoutExpired as e:
            what = f"{args[0].capitalize()} of {name}"
            raise _Failure(self._timeout_message(what)) from e
        if proc.returncode != 0:
            raise _Failure(f"adb {args[0]} of {name} failed (rc={proc.returncode}).")
        return self._clock() - t0

    def push(self) -> None:
        """Pushes every file to the DUT and measures the rates."""
        for f, local, remote in zip(self.res.files, self._local, self._remote):
            f.push_s = self._transfer(["push", local, remote], f.name)
            self.log(
                f"Push {f.name} ({f.size_bytes // MIB} MiB): {f.push_s:.2f} s, "
                f"{f.push_mb_s:.1f} MB/s"
            )

    def remove_host_files(self) -> None:
        """Deletes the pushed originals (their SHA-1 is known) to save /tmp."""
        for path in self._local:
            if os.path.exists(path):
                os.remove(path)

    def check_dut_sha1(self) -> None:
        """Compares the DUT copies, telling push from pull corruption."""
        files = self.res.files
        try:
            proc = self.adb(
                ["shell", "sha1sum"] + self._remote,
                min(DUT_SHA1_TIMEOUT_S, self._remaining()),
            )
            hashes = parse_sha1sum(proc.stdout or "")
        except subprocess.TimeoutExpired:
            self.log("DUT sha1sum timed out (skipped).")
            hashes = {}
        for f, remote in zip(files, self._remote):
            f.dut_sha1 = hashes.get(remote, "")
            self.log(f"DUT SHA-1 {f.name}: {f.dut_sha1 or 'n/a'}")
        if not all(f.dut_sha1 for f in files):
            self.log("WARNING: no DUT SHA-1 for every file; the pulled copies decide.")
        bad = [f.name for f in files if f.dut_sha1 and f.dut_sha1 != f.host_sha1]
        if bad:
            raise _Failure(
                f"Data corrupted on push: DUT SHA-1 of {' and '.join(bad)} "
                "differs from the host."
            )

    def pull(self) -> None:
        """Pulls the DUT copies of every file and measures the rates."""
        for f, remote, pulled in zip(self.res.files, self._remote, self._pulled):
            f.pull_s = self._transfer(["pull", remote, pulled], f.name)
            if not os.path.exists(pulled):
                raise _Failure(f"adb pull did not write {f.name}.")
            self.log(
                f"Pull {f.name} ({f.size_bytes // MIB} MiB): {f.pull_s:.2f} s, "
                f"{f.pull_mb_s:.1f} MB/s"
            )

    def check_pulled_sha1(self) -> None:
        """Compares the pulled copies with the originals."""
        files = self.res.files
        for f, pulled in zip(files, self._pulled):
            f.pulled_sha1 = sha1_of_file(pulled)
            self.log(f"Pulled SHA-1 {f.name}: {f.pulled_sha1}")
        bad = [f.name for f in files if f.pulled_sha1 != f.host_sha1]
        if bad:
            raise _Failure(
                f"Data corrupted on pull: pulled SHA-1 of {' and '.join(bad)} differs."
            )

    def check_deadline(self) -> None:
        """Fails when the transfer phase overran the deadline."""
        res = self.res
        if res.transfer_s > res.deadline_s:
            raise _Failure(
                f"Transfer phase took {res.transfer_s:.1f} s, more than the "
                f"{res.deadline_s:g} s deadline."
            )

    def check_rate(self, min_rate_mb_s: float) -> None:
        """Fails when a push or pull was slower than |min_rate_mb_s| (if > 0)."""
        rates = [(f.push_mb_s, f"push {f.name}") for f in self.res.files] + [
            (f.pull_mb_s, f"pull {f.name}") for f in self.res.files
        ]
        slowest, what = min(rates)
        if min_rate_mb_s > 0 and slowest < min_rate_mb_s:
            raise _Failure(
                f"Transfer too slow: {slowest:.1f} MB/s < {min_rate_mb_s:.1f} MB/s "
                f"({what})."
            )

    def summarize(self) -> None:
        """Fills the totals of the result from the per-file results."""
        res, files = self.res, self.res.files
        res.push_s = sum(f.push_s for f in files)
        res.pull_s = sum(f.pull_s for f in files)
        res.push_mb_s = _rate(sum(f.size_bytes for f in files if f.push_s), res.push_s)
        res.pull_mb_s = _rate(sum(f.size_bytes for f in files if f.pull_s), res.pull_s)
        res.host_sha1 = _join([f.host_sha1 for f in files])
        res.dut_sha1 = _join([f.dut_sha1 for f in files])
        res.pulled_sha1 = _join([f.pulled_sha1 for f in files])

    def remove_dut_files(self) -> None:
        """Deletes the test files on the DUT (best effort)."""
        if not self.res.adb_serial or not self._remote:
            return
        try:
            self.adb(["shell", "rm", "-f"] + self._remote, 5.0)
        except (OSError, subprocess.SubprocessError) as e:
            logging.warning("Failed to remove %s on the DUT: %s", self._remote, e)


def run_adb_transfer_test(  # pylint: disable=too-many-arguments,too-many-locals
    preferred_serial: str = "",
    size_mib: Optional[int] = None,
    deadline_s: Optional[float] = None,
    *,
    run: Runner = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
    work_dir: Optional[str] = None,
    min_rate_mb_s: Optional[float] = None,
    extra_mib: Optional[int] = None,
    generation_limit_s: float = GENERATION_LIMIT_S,
    sysfs_dir: str = SYSFS_USB_DIR,
    min_link_mbps: Optional[int] = None,
) -> AdbTransferResult:
    """Pushes and pulls two random files over ADB and verifies them.

    Args:
      preferred_serial: ADB serial of the DUT to test (default: the first).
      size_mib: size of the first file in MiB (1..MAX_SIZE_MIB); default:
        get_size_mib().
      deadline_s: limit of the transfer phase (push, DUT SHA-1, pull);
        default: get_deadline_s().
      run: subprocess.run replacement (tests).
      clock: time.monotonic replacement (tests).
      work_dir: parent of the temporary host files.
      min_rate_mb_s: minimum push and pull rate; None: get_min_rate_mb_s().
      extra_mib: the second file is this much larger; None: random 1-16.
      generation_limit_s: limit of generating the random files.
      sysfs_dir: host USB devices in sysfs (tests).
      min_link_mbps: minimum H2H link speed on the host and the DUT side
        (0: not checked); None: get_min_link_mbps().
    """
    start = clock()
    deadline_s = deadline_s or get_deadline_s()
    size_mib = max(1, min(int(size_mib or get_size_mib()), MAX_SIZE_MIB))
    if extra_mib is None:
        extra_mib = random.randint(*EXTRA_MIB_RANGE)
    sizes = [size_mib * MIB, (size_mib + extra_mib) * MIB]
    if min_rate_mb_s is None:
        min_rate_mb_s = get_min_rate_mb_s()
    if min_link_mbps is None:
        min_link_mbps = get_min_link_mbps()
    test = _TransferTest(run, clock, deadline_s)
    res = test.res
    res.file_size_bytes = sum(sizes)
    tmp_dir = tempfile.mkdtemp(prefix="maui_adb_", dir=work_dir)
    try:
        test.select_device(preferred_serial)
        test.check_link(sysfs_dir, min_link_mbps)
        test.log(
            f"Test files: {size_mib} MiB + {size_mib + extra_mib} MiB "
            f"({res.file_size_bytes} B) -> {res.adb_serial}:{DUT_DIR}; transfer "
            f"deadline {deadline_s:g} s"
            + (f"; minimum {min_rate_mb_s:.1f} MB/s" if min_rate_mb_s > 0 else "")
        )
        test.generate(tmp_dir, sizes, generation_limit_s)
        test.start_transfers()
        test.push()
        test.remove_host_files()
        test.check_dut_sha1()
        test.pull()
        test.end_transfers()
        test.check_pulled_sha1()
        test.check_deadline()
        test.check_rate(min_rate_mb_s)
        test.summarize()
        res.success = True
        res.message = (
            f"ADB transfer OK: transfer {res.transfer_s:.1f} s / limit "
            f"{deadline_s:g} s, {size_mib} + {size_mib + extra_mib} MiB, push "
            f"{res.push_mb_s:.1f} MB/s, pull {res.pull_mb_s:.1f} MB/s, SHA-1 "
            f"match, USB link host {res.host_link_mbps} / DUT "
            f"{res.dut_link_mbps} Mb/s."
        )
        test.log(res.message)
    except _Failure as e:
        test.fail(str(e))
    except OSError as e:
        test.fail(f"Host file error: {e}")
    finally:
        test.end_transfers()
        test.summarize()
        res.total_s = clock() - start
        if test.transfers_started:
            test.log(
                f"Transfer phase: {res.transfer_s:.1f} s (deadline {deadline_s:g} s)."
            )
        test.log(f"Total: {res.total_s:.1f} s.")
        test.remove_dut_files()
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return res
