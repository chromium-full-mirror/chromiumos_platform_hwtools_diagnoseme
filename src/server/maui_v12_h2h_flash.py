# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Maui v1.2 H2H (Prolific PL27A1) firmware flashing with `maui-flash h2h`.

The H2H firmware image is not part of DiagnoseMe. The operator copies it to
the H2H firmware directory of the station (MAUI_H2H_FW_DIR in config.env,
default ~/diagnoseme_h2h_firmware), which docker compose mounts read-only at
CONTAINER_DIR into the server container.

One flash:
1. Validates the image (a whole PL27A1 image, see maui_v12_h2h.check_image)
   and, for another serial number, patches it into a copy (patch_serial). The
   operator's file is never modified; the image goes to a maui-utils staging
   directory.
2. Reads the MCU App version on the console and compares it with the stable
   builds (any build may flash; older or unknown ones get a warning, and a
   failure then points to the MCU App update), and checks that the H2H SPI
   bus is idle.
3. Runs `maui-flash --port <console> h2h --file <image>` in maui-utils: over
   its RPC port the MCU holds the PL27A1 in reset, erases the whole SPI flash,
   programs the image and verifies it with a CRC-32.
4. Optionally reads the image back (`h2h --read`) and compares it.
5. Pulses the PL27A1 reset and waits for the bridge to enumerate with the
   VID:PID and iSerial of the image.

The caller holds the MCU console (device.claim_consoles), so the unit status
poll and other console users stay away while the flash runs.
"""

# Generated protobuf members are not visible to pylint.
# pylint: disable=no-member

import asyncio
import collections
import contextlib
import dataclasses
import hashlib
import logging
import os
import re
import time
from typing import AsyncGenerator
from typing import Callable
from typing import ContextManager
from typing import Deque
from typing import Dict
from typing import List
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple

from server import maui_utils_runner
from server import maui_v12_device as device
from server import maui_v12_h2h as h2h
from server import maui_v12_ite_flash as ite_flash
from server import maui_v12_profile as profile
from server.generated import maui_manufacturing_pb2
from server.maui_common import write_device_log


LOG_NAME = "h2h_fw_flashing"

# Station directory of the H2H images (config.env; docker compose mounts it).
HOST_DIR_ENV = "MAUI_H2H_FW_DIR"
DEFAULT_HOST_DIR = "~/diagnoseme_h2h_firmware"
# Mount point of that directory in the server container (read-only).
CONTAINER_DIR = "/mnt/h2h_firmware"
FILE_EXTENSION = ".bin"
# Larger files are not read: certainly no H2H image.
MAX_READ_BYTES = 16 * 1024 * 1024

# maui-flash allows 300 s for the chip erase (16 MB parts; the 512 KB part of
# v1.2 takes well under a second) plus program and verify.
FLASH_TIMEOUT_S = 360.0
READ_TIMEOUT_S = 60.0
# maui-flash output is polled this often (cancellation, deadline).
TOOL_POLL_S = 1.0
TOOL_EXIT_GRACE_S = 5.0
# The MCU hands the bus back to the PL27A1 at the end of the RPC session.
BUS_IDLE_TIMEOUT_S = 5.0
BUS_IDLE_POLL_S = 0.5
# The bridge has this long after its reset to enumerate.
REENUM_TIMEOUT_S = 10.0
MAX_LOG_LINES = 2000
# Progress lines kept in the log: every 10 % of each phase.
LOG_EVERY_PERCENT = 10
READBACK_FILE = "h2h_readback.bin"

PHASE_CHECKING = "checking"
PHASE_ERASING = "erasing"
PHASE_PROGRAMMING = "programming"
PHASE_VERIFYING = "verifying"
PHASE_READING = "reading"
PHASE_REBOOTING = "rebooting"
PHASE_DONE = "done"
PHASE_FAILED = "failed"
# maui-flash progress phases: (phase, overall start %, overall end %).
_TOOL_PHASES = {
    "erase": (PHASE_ERASING, 5, 55),
    "program": (PHASE_PROGRAMMING, 55, 75),
    "verify": (PHASE_VERIFYING, 75, 80),
    "read": (PHASE_READING, 80, 90),
}
FLASHED_PERCENT = 80
REBOOT_PERCENT = 92

# "  erase     61%  (5376/8704 B)" (terminal) or "INFO:   read  60% (5376/8704 B)".
_PROGRESS_RE = re.compile(r"\b(erase|program|verify|read)\s+(\d{1,3})%\s*\(\d+/\d+ B\)")
FLASH_OK = "H2H flash programmed and verified"
FLASH_STARTED = "to the H2H SPI flash..."
READ_OK = "H2H flash dumped to"
_PARTIAL_HINT = (
    "The H2H flash may be erased or incomplete now: the bridge does not boot "
    "until it is flashed again (the MCU stays reachable)."
)
# Known maui-flash messages of a failed run, first match wins.
_FAILURE_HINTS = (
    (
        "does not support h2h",
        f"This MCU App cannot access the H2H flash over RPC: {h2h.UPDATE_HINT}.",
    ),
    (
        "No 'Maui RPC' interface",
        f"The MCU App has no 'Maui RPC' port: {h2h.UPDATE_HINT}.",
    ),
    (
        "Multiple Maui RPC ports",
        "Several Maui RPC ports found: connect one Maui at a time.",
    ),
    ("H2H verify mismatch", "The flash does not match the image (CRC-32)."),
)

ConsoleFn = Callable[[str], str]


@dataclasses.dataclass
class FirmwareFile:  # pylint: disable=too-many-instance-attributes
    """An image in the H2H firmware directory."""

    file_name: str
    path: str
    size: int = 0
    mtime: float = 0.0
    sha256: str = ""
    image: Optional[h2h.H2hImage] = None
    error: str = ""
    serial_patch_error: str = ""

    @property
    def valid(self) -> bool:
        """True for a whole PL27A1 image that can be flashed."""
        return not self.error

    @property
    def serial(self) -> str:
        """Serial number in the image, '' if it could not be decoded."""
        return self.image.serial if self.image else ""


@dataclasses.dataclass
class FlashJob:  # pylint: disable=too-many-instance-attributes
    """Validated inputs of one H2H firmware flash."""

    console: device.McuConsole
    consoles: List[device.McuConsole]
    source: FirmwareFile
    data: bytes  # What is flashed: the file or its patched copy.
    image: h2h.H2hImage  # |data| decoded.
    serial_changed: bool
    read_back: bool
    staged_name: str

    @property
    def serial(self) -> str:
        """Serial number in the flashed image."""
        return self.image.serial


def firmware_dir(environ: Optional[Mapping[str, str]] = None) -> Tuple[str, str]:
    """Returns (directory read here, station directory shown to the operator).

    In the server container the station directory is mounted at
    CONTAINER_DIR; elsewhere (development) it is read directly.
    """
    env = os.environ if environ is None else environ
    host_dir = env.get(HOST_DIR_ENV, "").strip() or DEFAULT_HOST_DIR
    if os.path.isdir(CONTAINER_DIR):
        return CONTAINER_DIR, host_dir
    local_dir = os.path.expanduser(host_dir)
    return local_dir, local_dir


def _inside(path: str, directory: str) -> bool:
    """True if |path| resolves (symlinks included) to inside |directory|."""
    real_dir = os.path.realpath(directory)
    return os.path.commonpath([os.path.realpath(path), real_dir]) == real_dir


def file_name_error(file_name: str) -> str:
    """Returns why |file_name| is not a plain image file name, '' if OK."""
    if (
        not file_name
        or any(c in file_name for c in ("/", "\\", "\x00"))
        or ".." in file_name
        or not file_name.lower().endswith(FILE_EXTENSION)
    ):
        return (
            f"Invalid H2H image file name {file_name!r}: expected a *.bin file "
            "from the list."
        )
    return ""


def inspect_file(path: str) -> Tuple[FirmwareFile, bytes]:
    """Reads and checks one image file; returns its description and data."""
    info = FirmwareFile(file_name=os.path.basename(path), path=path)
    try:
        stat = os.stat(path)
        info.size, info.mtime = stat.st_size, stat.st_mtime
        if info.size > MAX_READ_BYTES:
            info.error = f"{info.size} bytes; {h2h.RAW_IMAGE_HINT}"
            return info, b""
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        info.error = f"cannot be read: {e.strerror or e}"
        return info, b""
    info.size = len(data)
    info.sha256 = hashlib.sha256(data).hexdigest()
    info.image, info.error = h2h.check_image(data)
    if info.valid:
        info.serial_patch_error = h2h.serial_patch_error(data)
    return info, data


def list_files(directory: str) -> List[FirmwareFile]:
    """Returns the *.bin files of |directory|, newest first."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    files = []
    for name in names:
        path = os.path.join(directory, name)
        if (
            name.lower().endswith(FILE_EXTENSION)
            and os.path.isfile(path)
            # Never follow a symlink out of the directory.
            and _inside(path, directory)
        ):
            files.append(inspect_file(path)[0])
    return sorted(files, key=lambda f: (-f.mtime, f.file_name))


def _proto_file(info: FirmwareFile) -> maui_manufacturing_pb2.MauiH2hFirmwareFile:
    image = info.image
    return maui_manufacturing_pb2.MauiH2hFirmwareFile(
        file_name=info.file_name,
        size_bytes=info.size,
        sha256=info.sha256,
        valid=info.valid,
        error=info.error,
        warnings=image.warnings if image else [],
        serial_number=info.serial,
        serial_valid=profile.is_valid_serial(info.serial),
        serial_patchable=info.valid and not info.serial_patch_error,
        serial_patch_error=info.serial_patch_error,
        vid=image.vid if image else 0,
        pid=image.pid if image else 0,
    )


def read_firmware_verdict(
    device_port: str,
    open_console_fn: Callable[[str], ContextManager[ConsoleFn]],
    environ: Optional[Mapping[str, str]] = None,
    roots: Optional[Sequence[str]] = None,
    scan_consoles: Optional[Callable[[], List[device.McuConsole]]] = None,
) -> Tuple[str, profile.McuVersionVerdict]:
    """Reads `version` of an MCU App console; returns (its port, verdict).

    |device_port| empty: the first MCU App console. Holds the console for
    the read (see device.console_busy).
    """
    consoles = (scan_consoles or device.scan_mcu_consoles)()
    console, error = _select_console(device_port, consoles)
    if console is None:
        return device_port, profile.McuVersionVerdict(message=error)
    try:
        with device.console_busy([console], "H2H firmware check"):
            with open_console_fn(console.dev_node) as run:
                out = run("version")
    except device.ConsoleBusyError as e:
        return console.dev_node, profile.McuVersionVerdict(
            profile.VERDICT_BUSY,
            message=f"MCU App version not read ({e}); refresh later.",
        )
    except Exception as e:  # pylint: disable=broad-except
        return console.dev_node, profile.McuVersionVerdict(
            profile.VERDICT_UNKNOWN,
            message=(
                f"MCU console error on {console.dev_node}: {e}; "
                f"{profile.UPDATE_FIRST_HINT}"
            ),
        )
    return console.dev_node, profile.compare_mcu_version(out, environ, roots)


def list_response(
    environ: Optional[Mapping[str, str]] = None,
    mcu_port: str = "",
    firmware: Optional[profile.McuVersionVerdict] = None,
) -> maui_manufacturing_pb2.MauiH2hFirmwareListResponse:
    """Lists the images of the H2H firmware directory for the UI, with the
    MCU App verdict of the console at |mcu_port|."""
    firmware = firmware or profile.McuVersionVerdict()
    local_dir, host_dir = firmware_dir(environ)
    exists = os.path.isdir(local_dir)
    files = list_files(local_dir) if exists else []
    valid = [f for f in files if f.valid]
    if not exists:
        msg = (
            f"The H2H firmware directory {host_dir} does not exist on this "
            "station: create it and copy the raw PL27A1 H2H image there "
            "(prolific_raw_<index>_<serial>.bin of prepare_raw_flash.py)."
        )
    elif not files:
        msg = (
            f"No H2H image (*.bin) in {host_dir}: copy the raw PL27A1 H2H image "
            "(prolific_raw_<index>_<serial>.bin of prepare_raw_flash.py) there "
            "and refresh."
        )
    else:
        msg = f"{len(files)} image(s) in {host_dir}, {len(valid)} valid."
        invalid = [f"{f.file_name}: {f.error}" for f in files if not f.valid]
        if invalid:
            msg += " Not flashable: " + "; ".join(invalid) + "."
    return maui_manufacturing_pb2.MauiH2hFirmwareListResponse(
        directory=host_dir,
        directory_exists=exists,
        files=[_proto_file(f) for f in files],
        message=msg,
        expected_size_bytes=h2h.IMAGE_SIZE,
        mcu_port=mcu_port,
        mcu_version=firmware.version,
        stable_version=firmware.stable,
        firmware_verdict=firmware.status,
        firmware_message=firmware.message,
        firmware_warning=firmware.warning,
    )


def _select_console(
    device_port: str, consoles: Sequence[device.McuConsole]
) -> Tuple[Optional[device.McuConsole], str]:
    """Returns the MCU App console to use, or None and why not."""
    if not device_port:
        apps = [c for c in consoles if c.is_app]
        if not apps:
            return None, "Maui MCU App console (18d1:5085) not detected."
        return apps[0], ""
    console = next((c for c in consoles if c.dev_node == device_port), None)
    if console is None:
        # Only ever hand detected Maui consoles to the privileged container.
        return None, f"'{device_port}' is not a detected Maui MCU console (18d1:5085)."
    if not console.is_app:
        return None, (
            f"The MCU on {device_port} runs its {console.stage} stage: H2H "
            "flashing needs the MCU App."
        )
    return console, ""


def prepare_job(  # pylint: disable=too-many-return-statements,too-many-locals
    request: maui_manufacturing_pb2.MauiH2hFlashRequest,
    environ: Optional[Mapping[str, str]] = None,
    scan_consoles: Optional[Callable[[], List[device.McuConsole]]] = None,
) -> Tuple[Optional[FlashJob], str, str]:
    """Validates a flash request.

    Returns (job, '', log key) or (None, why the request is rejected, log
    key). The log key is the serial number the board ends up with if known.
    """
    new_serial = request.new_serial.strip()
    log_key = new_serial if profile.is_valid_serial(new_serial) else ""
    error = file_name_error(request.file_name)
    if error:
        return None, error, log_key
    local_dir, host_dir = firmware_dir(environ)
    path = os.path.join(local_dir, request.file_name)
    if not os.path.isfile(path) or not _inside(path, local_dir):
        return None, f"H2H image {request.file_name} not found in {host_dir}.", log_key
    source, data = inspect_file(path)
    if not source.valid:
        return None, f"H2H image {source.file_name}: {source.error}.", log_key
    if request.expected_sha256 and request.expected_sha256 != source.sha256:
        return (
            None,
            (
                f"H2H image {source.file_name} changed since it was listed (sha256 "
                f"{source.sha256[:12]}): refresh the list and check it again."
            ),
            log_key,
        )
    assert source.image is not None
    log_key = log_key or (
        source.serial if profile.is_valid_serial(source.serial) else ""
    )
    image = source.image
    serial_changed = bool(new_serial) and new_serial != source.serial
    if new_serial and not profile.is_valid_serial(new_serial):
        return (
            None,
            (
                f"'{new_serial}' is not a valid Maui v1.2 serial number "
                "(MAUIV12H + 8 digits)."
            ),
            log_key,
        )
    if serial_changed:
        try:
            data = h2h.patch_serial(data, new_serial)
        except ValueError as e:
            return None, f"H2H image {source.file_name}: {e}.", log_key
        image = h2h.decode_image(data)
    elif not profile.is_valid_serial(source.serial):
        return (
            None,
            (
                f"The serial number '{source.serial}' of {source.file_name} is not a "
                "valid Maui v1.2 serial: use 'Change serial number'."
            ),
            log_key,
        )
    consoles = (scan_consoles or device.scan_mcu_consoles)()
    console, error = _select_console(request.device_port, consoles)
    if console is None:
        return None, error, log_key
    stem = os.path.splitext(source.file_name)[0]
    return (
        FlashJob(
            console=console,
            consoles=consoles,
            source=source,
            data=data,
            image=image,
            serial_changed=serial_changed,
            read_back=request.read_back,
            staged_name=(
                f"{stem}_{image.serial}.bin" if serial_changed else source.file_name
            ),
        ),
        "",
        image.serial,
    )


def failed_progress(
    log_key: str, message: str
) -> maui_manufacturing_pb2.MauiH2hFlashProgress:
    """Writes the failed h2h_fw_flashing.log of a rejected request."""
    write_device_log(
        log_key or "UNKNOWN",
        LOG_NAME,
        f"ALL_PASSED=False\nRESULT: FAILED\n{message}\nNothing was flashed.",
    )
    return maui_manufacturing_pb2.MauiH2hFlashProgress(
        is_complete=True,
        success=False,
        phase=PHASE_FAILED,
        status_message=message,
        error_message=message,
    )


@dataclasses.dataclass
class ToolRun:
    """State and outcome of one maui-flash run."""

    # Progress phases seen, in order (e.g. erase, program, verify).
    phases: Deque[str] = dataclasses.field(default_factory=collections.deque)
    # Output lines kept for the log and the failure hints.
    output: Deque[str] = dataclasses.field(
        default_factory=lambda: collections.deque(maxlen=MAX_LOG_LINES)
    )
    returncode: Optional[int] = None
    error: str = ""  # Timeout, cancellation or start failure.

    @property
    def text(self) -> str:
        """The kept output lines."""
        return "\n".join(self.output)


def tool_failure(run: ToolRun, what: str, ok_marker: str) -> str:
    """Returns '' if |run| passed (exit 0 and |ok_marker|), else the reason."""
    if not run.error and run.returncode == 0 and ok_marker in run.text:
        return ""
    reason = run.error or (
        f"{what} failed (exit code {run.returncode})."
        if run.returncode
        else f"{what} exited without '{ok_marker}'."
    )
    hint = maui_utils_runner.explain_failure(run.text) or next(
        (h for pattern, h in _FAILURE_HINTS if pattern in run.text), ""
    )
    return f"{reason} {hint}".strip()


async def _read_chunk(
    proc: asyncio.subprocess.Process, deadline: float
) -> Optional[bytes]:
    """Returns the next output of |proc| (b'' at its end), None after TOOL_POLL_S
    (or at |deadline|) without output."""
    timeout = max(0.0, min(TOOL_POLL_S, deadline - time.monotonic()))
    try:
        return await asyncio.wait_for(
            proc.stdout.read(ite_flash.READ_CHUNK_BYTES), timeout=timeout
        )
    except asyncio.TimeoutError:
        return None


class H2hFirmwareFlasher:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """flash_h2h_firmware: checks, maui-flash, read-back and iSerial check."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        open_console_fn: Callable[[str], ContextManager[ConsoleFn]],
        *,
        runner: Optional[maui_utils_runner.MauiUtilsRunner] = None,
        environ: Optional[Mapping[str, str]] = None,
        firmware_roots: Optional[Sequence[str]] = None,
        scan_h2h: Callable[[], List[device.H2hDevice]] = device.scan_h2h_devices,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        reenum_timeout_s: float = REENUM_TIMEOUT_S,
    ) -> None:
        self._open_console = open_console_fn
        self._runner = runner
        self._environ = environ
        self._firmware_roots = firmware_roots
        self._scan_h2h = scan_h2h
        self._sleep = sleep
        self._clock = clock
        self._reenum_timeout_s = reenum_timeout_s
        self._log: Deque[str] = collections.deque(maxlen=MAX_LOG_LINES)
        self._started = time.monotonic()
        self._phase = PHASE_CHECKING
        self._percent = 0
        self._job: Optional[FlashJob] = None
        self._verdict = profile.McuVersionVerdict()

    def _elapsed(self) -> float:
        return round(time.monotonic() - self._started, 1)

    def _progress(self, **fields) -> maui_manufacturing_pb2.MauiH2hFlashProgress:
        """A progress message; phase and overall percent default to the state."""
        fields.setdefault("phase", self._phase)
        fields.setdefault("progress_percent", self._percent)
        if self._job:
            fields.setdefault("image_serial", self._job.serial)
            fields.setdefault("serial_changed", self._job.serial_changed)
        return maui_manufacturing_pb2.MauiH2hFlashProgress(
            elapsed_s=self._elapsed(), **fields
        )

    def _finish(
        self, success: bool, message: str, **fields
    ) -> maui_manufacturing_pb2.MauiH2hFlashProgress:
        """Writes h2h_fw_flashing.log and returns the final message."""
        job = self._job
        assert job is not None
        if not success:
            logging.warning("H2H firmware flash failed: %s", message)
        changed = f" (changed from {job.source.serial})" if job.serial_changed else ""
        write_device_log(
            job.serial,
            LOG_NAME,
            f"ALL_PASSED={success}\nRESULT: {'PASSED' if success else 'FAILED'}\n"
            f"{message}\nImage: {job.source.file_name} (sha256 "
            f"{job.source.sha256})\nSerial: {job.serial}{changed}"
            f"\nDuration: {self._elapsed():.1f} s\n\n" + "\n".join(self._log),
        )
        fields.update(
            is_complete=True,
            success=success,
            phase=PHASE_DONE if success else PHASE_FAILED,
            status_message=message,
            error_message="" if success else message,
        )
        fields.setdefault("progress_percent", 100 if success else self._percent)
        return self._progress(**fields)

    def _console_cmd(self, run: ConsoleFn) -> ConsoleFn:
        """Wraps |run| so that every command and answer goes to the log."""

        def cmd(command: str) -> str:
            out = run(command)
            cleaned = "\n".join(
                line.rstrip() for line in device.clean_console_output(out).splitlines()
            ).strip()
            self._log.append(f"$ {command}\n{cleaned}")
            return out

        return cmd

    async def flash(
        self,
        job: FlashJob,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiH2hFlashProgress, None]:
        """Flashes |job| (console claimed by the caller), streaming progress."""
        self._job = job
        self._started = time.monotonic()
        loop = asyncio.get_running_loop()
        for line in self._describe(job):
            self._log.append(line)
            yield self._progress(log_line=line)
        yield self._progress(
            status_message=(
                f"Checking the MCU App and the H2H SPI bus on {job.console.dev_node}..."
            )
        )
        error = await loop.run_in_executor(None, self._precheck, job)
        if self._verdict.message:
            yield self._progress(log_line=self._verdict_line())
        if error:
            yield self._finish(False, f"{error} Nothing was flashed.")
            return
        session_cm = (self._runner or maui_utils_runner.MauiUtilsRunner()).session()
        with session_cm as session:
            run = ToolRun()
            async with contextlib.aclosing(
                self._flash_image(session, job, run, is_cancelled)
            ) as updates:
                async for update in updates:
                    yield update
            error = tool_failure(run, "maui-flash h2h", FLASH_OK)
            if error:
                started = FLASH_STARTED in run.text or bool(run.phases)
                if started:
                    error += f" {_PARTIAL_HINT}"
                yield self._finish(False, error + self._old_firmware_hint())
                return
            self._percent = FLASHED_PERCENT
            if job.read_back:
                async with contextlib.aclosing(
                    self._read_back(session, job, is_cancelled)
                ) as updates:
                    async for update in updates:
                        yield update
                        if update.is_complete:
                            return
        self._phase, self._percent = PHASE_REBOOTING, REBOOT_PERCENT
        yield self._progress(
            status_message="Image written. Resetting the H2H bridge..."
        )
        found, error = await loop.run_in_executor(None, self._reset_and_wait, job)
        yield self._verify(job, found, error)

    @staticmethod
    def _describe(job: FlashJob) -> List[str]:
        """The inputs of the flash, for the log."""
        lines = [
            f"Image: {job.source.path} ({job.source.size} bytes, sha256 "
            f"{job.source.sha256})",
            f"Image serial: {job.source.serial}",
        ]
        if job.serial_changed:
            lines.append(
                f"Serial changed to {job.serial} in a copy of the image (sha256 "
                f"{hashlib.sha256(job.data).hexdigest()}); the file is not modified."
            )
        lines += [f"Image warning: {w}" for w in job.image.warnings]
        lines.append(
            f"MCU console: {job.console.dev_node} [{job.console.serial}] "
            f"({job.console.usb_path})"
        )
        return lines

    def _precheck(self, job: FlashJob) -> str:
        """Checks the MCU App build and that the H2H SPI bus is idle."""
        before = device.pair_h2h(job.console, self._scan_h2h(), job.consoles)
        self._log.append(
            "Host H2H before: "
            + (
                f"iSerial '{before.serial}', {before.usb_path} (device {before.devnum})"
                if before
                else "not detected"
            )
        )
        try:
            with self._open_console(job.console.dev_node) as run:
                cmd = self._console_cmd(run)
                out = cmd("version")
                if "Maui version" not in device.strip_ansi(out):
                    return "The MCU console did not answer 'version'."
                # Any MCU App may flash; an older or unknown one is warned of.
                self._verdict = profile.compare_mcu_version(
                    out, self._environ, self._firmware_roots
                )
                self._log.append(self._verdict_line())
                mismatches = h2h.bus_gpio_mismatches(h2h.parse_gpios(cmd("gpio get")))
                if mismatches:
                    return (
                        f"The H2H SPI bus is not idle ({'; '.join(mismatches)}): "
                        "wait 10 s for a running flashing session or power-cycle "
                        "the board."
                    )
        except Exception as e:  # pylint: disable=broad-except
            return f"MCU console error on {job.console.dev_node}: {e}"
        return ""

    async def _flash_image(
        self,
        session: maui_utils_runner.MauiUtilsSession,
        job: FlashJob,
        run: ToolRun,
        is_cancelled: Callable[[], bool],
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiH2hFlashProgress, None]:
        """Runs `maui-flash h2h --file`."""
        image = session.stage_data(job.staged_name, job.data)
        args = ["--port", job.console.dev_node, "h2h", "--file", image]
        self._phase, self._percent = PHASE_ERASING, _TOOL_PHASES["erase"][1]
        yield self._progress(
            status_message=f"Flashing {job.staged_name} to the H2H SPI flash..."
        )
        async with contextlib.aclosing(
            self._run_tool(session, args, FLASH_TIMEOUT_S, run, is_cancelled)
        ) as updates:
            async for update in updates:
                yield update

    async def _read_back(
        self,
        session: maui_utils_runner.MauiUtilsSession,
        job: FlashJob,
        is_cancelled: Callable[[], bool],
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiH2hFlashProgress, None]:
        """Reads the flash back and compares it; ends with _finish on failure."""
        self._phase = PHASE_READING
        yield self._progress(status_message="Reading the H2H flash back...")
        local = session.local_path(READBACK_FILE)
        args = [
            "--port",
            job.console.dev_node,
            "h2h",
            "--read",
            f"{maui_utils_runner.CONTAINER_FW_DIR}/{READBACK_FILE}",
            "--size",
            str(len(job.data)),
        ]
        run = ToolRun()
        async with contextlib.aclosing(
            self._run_tool(session, args, READ_TIMEOUT_S, run, is_cancelled)
        ) as updates:
            async for update in updates:
                yield update
        error = tool_failure(run, "maui-flash h2h --read", READ_OK)
        if error:
            error += self._old_firmware_hint()
        else:
            try:
                with open(local, "rb") as f:
                    data = f.read()
            except OSError as e:
                error = f"Could not read the dump of the H2H flash: {e}."
            else:
                error = self._compare(job.data, data)
        if error:
            yield self._finish(
                False,
                f"Read-back: {error} The image was written and verified by the MCU "
                "(CRC-32); flash it again or power-cycle the board and check it.",
            )

    def _verdict_line(self) -> str:
        """The MCU App verdict for the log (a WARNING if it may be too old)."""
        prefix = "WARNING: " if self._verdict.warning else ""
        return prefix + self._verdict.message

    def _old_firmware_hint(self) -> str:
        """Points to the MCU App update when the MCU App may be too old."""
        if not self._verdict.warning:
            return ""
        return (
            f" The MCU App ({self._verdict.version or 'unknown version'}) is "
            "probably too old for H2H flashing: update it with 'MCU App update "
            "over USB (RW bootloader)' on this page, then flash again."
        )

    def _compare(self, expected: bytes, data: bytes) -> str:
        """Returns '' if the dump |data| equals the image, else the difference."""
        self._log.append(
            f"Read back {len(data)} bytes (sha256 {hashlib.sha256(data).hexdigest()})"
        )
        if data == expected:
            self._log.append("Read-back matches the image.")
            return ""
        if len(data) != len(expected):
            return f"the dump has {len(data)} bytes, the image {len(expected)}."
        diffs = [i for i, (a, b) in enumerate(zip(expected, data)) if a != b]
        return (
            f"{len(diffs)} byte(s) differ from the image (first at 0x{diffs[0]:04x}: "
            f"0x{data[diffs[0]]:02x}, expected 0x{expected[diffs[0]]:02x})."
        )

    async def _run_tool(  # pylint: disable=too-many-arguments
        self,
        session: maui_utils_runner.MauiUtilsSession,
        args: Sequence[str],
        timeout_s: float,
        run: ToolRun,
        is_cancelled: Callable[[], bool],
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiH2hFlashProgress, None]:
        """Runs maui-flash |args| in maui-utils, streaming its progress."""
        cmd = session.command(maui_utils_runner.TOOL_MAUI_FLASH, list(args))
        logging.info("Running: %s", " ".join(cmd))
        self._log.append("Running: maui-flash " + " ".join(args))
        try:
            sub = asyncio.subprocess
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=sub.DEVNULL, stdout=sub.PIPE, stderr=sub.STDOUT
            )
        except OSError as e:
            run.error = f"Could not start maui-flash: {e}"
            self._log.append(run.error)
            return
        lines = ite_flash.LineSplitter()
        logged: Dict[str, int] = {}
        deadline = time.monotonic() + timeout_s
        try:
            while True:
                if is_cancelled():
                    run.error = "Cancelled."
                    break
                if time.monotonic() >= deadline:
                    run.error = f"maui-flash did not finish within {timeout_s:.0f} s."
                    break
                data = await _read_chunk(proc, deadline)
                if data is None:
                    continue  # Check for cancellation and the deadline again.
                for line in lines.feed(data) if data else lines.flush():
                    yield self._tool_line(line, run, logged)
                if not data:
                    break
            if proc.returncode is None and not run.error:
                try:
                    await asyncio.wait_for(proc.wait(), TOOL_EXIT_GRACE_S)
                except asyncio.TimeoutError:
                    run.error = "maui-flash did not exit after closing its output."
        finally:
            # Also runs when the RPC is cancelled: never leave the tool running.
            if proc.returncode is None:
                run.error = run.error or "maui-flash was stopped."
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await asyncio.get_running_loop().run_in_executor(None, session.kill)
                await proc.wait()
            run.returncode = proc.returncode
            if run.error:
                self._log.append(run.error)
            self._log.append(f"maui-flash exit code: {run.returncode}")

    def _tool_line(
        self, line: str, run: ToolRun, logged: Dict[str, int]
    ) -> maui_manufacturing_pb2.MauiH2hFlashProgress:
        """Records one maui-flash output line; returns its progress message."""
        m = _PROGRESS_RE.search(line)
        if not m:
            run.output.append(line)
            self._log.append(line)
            return self._progress(log_line=line)
        tool_phase, percent = m.group(1), min(int(m.group(2)), 100)
        phase, start, end = _TOOL_PHASES[tool_phase]
        if not run.phases or run.phases[-1] != tool_phase:
            run.phases.append(tool_phase)
        # The first line of a phase, then every LOG_EVERY_PERCENT and 100 %.
        bucket = percent // LOG_EVERY_PERCENT
        log = percent == 100 or bucket > logged.get(tool_phase, -1)
        if log:
            logged[tool_phase] = bucket
            run.output.append(line)
            self._log.append(line)
        self._phase = phase
        self._percent = max(self._percent, start + (end - start) * percent // 100)
        return self._progress(
            phase_percent=percent,
            status_message=f"{phase.capitalize()}... {percent}%",
            log_line=line if log else "",
        )

    def _wait_bus_idle(self, cmd: ConsoleFn) -> List[str]:
        """Waits for the MCU to hand the bus back; returns what is still wrong."""
        deadline = self._clock() + BUS_IDLE_TIMEOUT_S
        while True:
            mismatches = h2h.bus_gpio_mismatches(h2h.parse_gpios(cmd("gpio get")))
            if not mismatches or self._clock() >= deadline:
                return mismatches
            self._sleep(BUS_IDLE_POLL_S)

    def _reset_and_wait(self, job: FlashJob) -> Tuple[Optional[device.H2hDevice], str]:
        """Resets the PL27A1 and waits for it; returns (bridge, error)."""
        try:
            with self._open_console(job.console.dev_node) as run:
                cmd = self._console_cmd(run)
                mismatches = self._wait_bus_idle(cmd)
                if mismatches:
                    return None, (
                        "The H2H SPI bus was not handed back to the bridge after "
                        f"flashing ({'; '.join(mismatches)}): power-cycle the board "
                        "and check the H2H serial number."
                    )
                h2h.reset_bridge(cmd, self._sleep, self._log.append)
        except h2h.CheckError as e:
            return None, str(e)
        except Exception as e:  # pylint: disable=broad-except
            return None, f"MCU console error on {job.console.dev_node}: {e}"
        found = h2h.wait_for_h2h(
            job.console,
            job.consoles,
            timeout_s=self._reenum_timeout_s,
            scan_h2h=self._scan_h2h,
            sleep=self._sleep,
            clock=self._clock,
        )
        return found, ""

    def _verify(
        self, job: FlashJob, found: Optional[device.H2hDevice], error: str
    ) -> maui_manufacturing_pb2.MauiH2hFlashProgress:
        """Checks the bridge after its reset; returns the final message."""
        expected = f"{profile.GOOGLE_VID:04x}:{job.image.pid:04x}"
        if error:
            return self._finish(False, error)
        if found is None:
            return self._finish(
                False,
                f"The image was written, but the H2H bridge ({expected}) did not "
                f"enumerate within {self._reenum_timeout_s:.0f} s of its reset. "
                "Power-cycle the board; if it still does not show up, check the "
                "image and flash it again.",
            )
        seen = f"{profile.GOOGLE_VID:04x}:{found.pid:04x}"
        self._log.append(
            f"Host H2H after the reset: {seen}, iSerial '{found.serial}', product "
            f"'{found.product}', {found.speed or '?'} Mb/s, {found.usb_path} "
            f"(device {found.devnum or '?'})"
        )
        if found.pid != job.image.pid or found.serial != job.serial:
            return self._finish(
                False,
                f"The H2H bridge enumerated as {seen} with iSerial '{found.serial}', "
                f"expected {expected} with '{job.serial}'.",
                host_serial=found.serial,
            )
        mismatch = job.console.serial != job.serial
        message = (
            f"H2H firmware {job.source.file_name} flashed"
            + (f" with serial number {job.serial}" if job.serial_changed else "")
            + f"; the bridge enumerated as {seen} with iSerial {found.serial}."
        )
        if mismatch:
            message += (
                f" The MCU serial number is '{job.console.serial}': provision it "
                "(H2H -> MCU)."
            )
        return self._finish(
            True, message, host_serial=found.serial, mcu_serial_mismatch=mismatch
        )
