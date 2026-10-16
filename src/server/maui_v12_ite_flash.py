# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Whole-image MCU flashing of Maui v1.2 with itecomdbgr (ITE UART DBGR).

itecomdbgr (chromiumos/platform/ec util/itecomdbgr.cc) puts the IT82002 into
debugger mode over the MCU debug UART (8E1) of an external USB-UART bridge,
erases the eflash, programs the image and, unless -n is given, runs a blank
check and a read-back verify.

Things the wrapper has to handle:
* Progress is printed as "\\r<Phase>...<pad>: NN%" without a newline, so the
  output is read in raw chunks and split on CR/LF (never with readline()).
* stdout is block-buffered on a pipe; `stdbuf -oL` delivers the header lines
  when they are printed.
* Without a DBGR answer (wrong port or wiring, no power, no even parity)
  nothing is printed for ~125 s, so a no-output watchdog fails after 20 s.
* The exit code is 0 for some argument errors too, so a pass also needs
  "Programming...: 100%" (plus "Verifying...: 100%" when verifying) and the
  new MCU App has to enumerate afterwards.
"""

# Generated protobuf members are not visible to pylint.
# pylint: disable=no-member

import asyncio
import codecs
import collections
import contextlib
import dataclasses
import hashlib
import logging
import os
import re
import shutil
import threading
import time
from typing import AsyncGenerator
from typing import Callable
from typing import Deque
from typing import Dict
from typing import FrozenSet
from typing import List
from typing import Mapping
from typing import Optional
from typing import Pattern
from typing import Sequence
from typing import Set
from typing import Tuple

from server import maui_v12_device as device
from server import maui_v12_profile as profile
from server import maui_v12_uart as uart
from server.generated import maui_manufacturing_pb2
from server.maui_common import write_device_log


ITECOMDBGR = "/usr/local/bin/itecomdbgr"
# The only rates itecomdbgr accepts (anything else prints an error, exit 0).
BAUD_RATES = (115200, 3000000)
DEFAULT_BAUD = 115200

# No output at all for this long: the MCU does not answer or stopped
# answering mid-flash. The tool retries the debugger entry up to 24 times
# (~5 s each) before it prints anything and gives up only after ~125 s.
NO_OUTPUT_TIMEOUT_S = 40.0
# Station override of NO_OUTPUT_TIMEOUT_S in seconds (see config.env).
NO_OUTPUT_TIMEOUT_ENV = "MAUI_ITE_NO_OUTPUT_TIMEOUT_S"
NO_OUTPUT_TIMEOUT_RANGE_S = (5.0, 300.0)
# Whole run; 1 MiB takes ~1-3 min with -n and ~6-8 min with verify at
# 115200 baud, about twice that if the FTDI latency timer stays at 16 ms.
TIMEOUT_S = 15 * 60.0
VERIFY_TIMEOUT_S = 25 * 60.0
# SIGTERM makes itecomdbgr restore the port settings; SIGKILL after this.
TERMINATE_GRACE_S = 5.0
# The new App has to enumerate (18d1:5085, iProduct PRODUCT_APP) this fast.
REENUM_TIMEOUT_S = 30.0
REENUM_POLL_S = 0.5
VERSION_ATTEMPTS = 3
VERSION_RETRY_DELAY_S = 1.0

LOG_NAME = "mcu_full_flash"
READ_CHUNK_BYTES = 256
# A "line" without CR/LF is cut after this many characters.
MAX_PENDING_CHARS = 4096
MAX_LOG_LINES = 2000
# Progress lines kept in the log: every 10 % of each phase.
LOG_EVERY_PERCENT = 10

PHASE_CONNECTING = "connecting"
PHASE_REBOOTING = "rebooting"
PHASE_DONE = "done"
PHASE_FAILED = "failed"
# Overall progress (start, end) of the tool phases; the rest covers the
# reboot and the version check.
_PHASE_SPANS = {
    False: {"Erasing": (0, 10), "Programming": (10, 95)},
    True: {
        "Erasing": (0, 5),
        "Checking": (5, 35),
        "Programming": (35, 65),
        "Verifying": (65, 95),
    },
}
REBOOT_PERCENT = 96

_PROGRESS_RE = re.compile(
    r"(Erasing|Checking|Programming|Verifying)\.\.\.\s*:\s*(\d{1,3})%"
)
# A progress print ends with '%'; the next print only starts with '\r'.
_COMPLETE_PROGRESS_RE = re.compile(
    r"^\s*(?:Erasing|Checking|Programming|Verifying)\.\.\.\s*:\s*\d{1,3}%\s*$"
)
_LINE_SPLIT_RE = re.compile(r"[\r\n]")

SERIAL_ERASED_NOTE = (
    "A full flash erases the MCU serial number (NVS): provision it again."
)
_NO_ANSWER_HINT = (
    "Check the wiring (bridge TX -> DEBUG_UART_RX, bridge RX <- DEBUG_UART_TX, "
    "GND; no VCC), that the board is powered and that the bridge supports even "
    "parity. Firmware that boot-loops can block the debugger: power-cycle the "
    "board and retry."
)
_PARTIAL_HINT = "The MCU flash is incomplete now: flash the full image again."
_STOPPED_HINT = (
    "The MCU stopped answering (bridge unplugged or board reset?). " + _PARTIAL_HINT
)
# Known itecomdbgr messages of a failed run, first match wins.
_FAILURE_HINTS = (
    (
        "Invalid EFLASH TYPE!",
        "The MCU did not answer in debugger mode. " + _NO_ANSWER_HINT,
    ),
    ("Invalid EFLASH TYPE", "The MCU reported an unknown SPI flash type."),
    ("tcgetattr", "The selected port is not a serial port."),
    ("open: ", "Could not open the USB-UART bridge (unplugged or in use?)."),
    ("open file error", "Could not read the image file."),
    ("check_status timeout", _STOPPED_HINT),
    ("WIP err", _STOPPED_HINT),
    ("WEL err", _STOPPED_HINT),
    ("erase_", "Erasing the flash failed. " + _PARTIAL_HINT),
    ("check_flash : error", "The blank check after erasing failed. " + _PARTIAL_HINT),
    ("verify_flash : error", "The read-back verify failed. " + _PARTIAL_HINT),
    ("write_flash : error", "Programming failed. " + _PARTIAL_HINT),
)

_busy_ports_lock = threading.Lock()
# Bridges with a flash in progress (one flash per bridge).
_busy_ports: Set[str] = set()


@dataclasses.dataclass(frozen=True)
class ToolUpdate:
    """One line of itecomdbgr output."""

    line: str
    phase: str = ""  # Erasing | Checking | Programming | Verifying, or ''
    percent: int = 0  # Progress of |phase|.
    log: bool = True  # Worth a line in the operator log.


@dataclasses.dataclass
class ToolRun:
    """State and outcome of one itecomdbgr run."""

    returncode: Optional[int] = None
    error: str = ""  # Watchdog, timeout, cancellation or start failure.
    output: Deque[str] = dataclasses.field(
        default_factory=lambda: collections.deque(maxlen=MAX_LOG_LINES)
    )
    max_percent: Dict[str, int] = dataclasses.field(default_factory=dict)
    # The no-output watchdog of this run (see no_output_timeout_s()).
    no_output_timeout_s: float = NO_OUTPUT_TIMEOUT_S

    def record(self, update: ToolUpdate) -> None:
        """Adds |update| to the outcome."""
        if update.phase:
            self.max_percent[update.phase] = max(
                self.max_percent.get(update.phase, 0), update.percent
            )
        if update.log:
            self.output.append(update.line)


class LineSplitter:
    """Splits a byte stream into lines on CR or LF.

    Progress output often rewrites one line with a bare CR and no LF, which
    readline() would only return at the very end. A trailing fragment that
    matches |complete_re| is returned at once instead of waiting for the
    next CR.
    """

    def __init__(self, complete_re: Optional[Pattern[str]] = None) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending = ""
        self._complete_re = complete_re

    def feed(self, data: bytes) -> List[str]:
        """Returns the stripped, non-empty lines completed by |data|."""
        self._pending += self._decoder.decode(data)
        *lines, self._pending = _LINE_SPLIT_RE.split(self._pending)
        if (self._complete_re and self._complete_re.match(self._pending)) or len(
            self._pending
        ) > MAX_PENDING_CHARS:
            lines.append(self._pending)
            self._pending = ""
        return [line for line in (raw.strip() for raw in lines) if line]

    def flush(self) -> List[str]:
        """Returns what is left at the end of the stream."""
        rest = (self._pending + self._decoder.decode(b"", final=True)).strip()
        self._pending = ""
        return [rest] if rest else []


class OutputParser:
    """Splits raw itecomdbgr output into lines and progress updates."""

    def __init__(self) -> None:
        self._lines = LineSplitter(_COMPLETE_PROGRESS_RE)
        self._logged: Dict[str, int] = {}

    def feed(self, data: bytes) -> List[ToolUpdate]:
        """Returns the updates completed by |data|."""
        return self._parse(self._lines.feed(data))

    def flush(self) -> List[ToolUpdate]:
        """Returns what is left at the end of the output."""
        return self._parse(self._lines.flush())

    def _parse(self, lines: Sequence[str]) -> List[ToolUpdate]:
        updates = []
        for line in lines:
            m = _PROGRESS_RE.search(line)
            if not m:
                updates.append(ToolUpdate(line=line))
                continue
            phase, percent = m.group(1), min(int(m.group(2)), 100)
            last = self._logged.get(phase)
            log = (
                last is None
                or percent == 100
                or percent // LOG_EVERY_PERCENT > last // LOG_EVERY_PERCENT
            )
            if log:
                self._logged[phase] = percent
            updates.append(ToolUpdate(line, phase, percent, log))
        return updates


def no_output_timeout_s(environ: Mapping[str, str] = os.environ) -> float:
    """Returns the no-output watchdog: the station override or the default."""
    raw = environ.get(NO_OUTPUT_TIMEOUT_ENV, "").strip()
    if not raw:
        return NO_OUTPUT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    low, high = NO_OUTPUT_TIMEOUT_RANGE_S
    if not low <= value <= high:
        logging.warning(
            "Ignoring %s=%r (expected %g to %g s); using %g s.",
            NO_OUTPUT_TIMEOUT_ENV,
            raw,
            low,
            high,
            NO_OUTPUT_TIMEOUT_S,
        )
        return NO_OUTPUT_TIMEOUT_S
    return value


def overall_percent(phase: str, percent: int, verify: bool) -> int:
    """Maps the progress of a tool phase to the overall progress (0-95)."""
    start, end = _PHASE_SPANS[verify].get(phase, (0, 0))
    return start + (end - start) * max(0, min(percent, 100)) // 100


def build_command(
    port: str, image: str, baud: int, verify: bool, stdbuf: Optional[str] = None
) -> List[str]:
    """Returns the itecomdbgr command line (line-buffered with stdbuf)."""
    cmd = [ITECOMDBGR, "-b", str(baud), "-d", port, "-f", image]
    if not verify:
        cmd.append("-n")
    stdbuf = shutil.which("stdbuf") if stdbuf is None else stdbuf
    return [stdbuf, "-oL"] + cmd if stdbuf else cmd


def _silence_message(run: ToolRun) -> str:
    if run.max_percent:
        return (
            f"itecomdbgr printed nothing for {run.no_output_timeout_s:.0f} s. "
            + _STOPPED_HINT
        )
    return (
        f"No answer from the MCU within {run.no_output_timeout_s:.0f} s. "
        + _NO_ANSWER_HINT
    )


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Stops itecomdbgr: SIGTERM (restores the port settings), then SIGKILL."""
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), TERMINATE_GRACE_S)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


async def _read_output(
    proc: asyncio.subprocess.Process,
    run: ToolRun,
    timeout_s: float,
    deadline: float,
    is_cancelled: Callable[[], bool],
) -> Optional[bytes]:
    """Returns the next output chunk, b'' at the end of the output, or None
    when the run has to be stopped (the reason is stored in |run|)."""
    loop = asyncio.get_running_loop()
    silence_s = run.no_output_timeout_s
    while True:
        remaining = deadline - loop.time()
        if is_cancelled():
            run.error = "Cancelled. " + _PARTIAL_HINT
            return None
        if remaining <= 0:
            run.error = (
                f"itecomdbgr did not finish within {timeout_s / 60:.0f} min. "
                + _PARTIAL_HINT
            )
            return None
        try:
            return await asyncio.wait_for(
                proc.stdout.read(READ_CHUNK_BYTES),
                timeout=min(silence_s, remaining),
            )
        except asyncio.TimeoutError:
            if remaining > silence_s:
                run.error = _silence_message(run)
                return None
            # Otherwise the overall deadline passed; reported above.


async def run_itecomdbgr(
    cmd: Sequence[str],
    run: ToolRun,
    timeout_s: float = TIMEOUT_S,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> AsyncGenerator[ToolUpdate, None]:
    """Runs itecomdbgr and yields its output; the outcome goes to |run|.

    The tool is stopped by the no-output watchdog, the overall timeout,
    |is_cancelled| and when the generator is closed early (RPC cancelled).
    """
    run.no_output_timeout_s = no_output_timeout_s()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as e:
        run.error = f"Could not start itecomdbgr: {e}"
        return
    parser = OutputParser()
    deadline = asyncio.get_running_loop().time() + timeout_s
    try:
        while data := await _read_output(proc, run, timeout_s, deadline, is_cancelled):
            for update in parser.feed(data):
                run.record(update)
                yield update
        for update in parser.flush():
            run.record(update)
            yield update
        if proc.returncode is None and not run.error:
            try:
                await asyncio.wait_for(proc.wait(), TERMINATE_GRACE_S)
            except asyncio.TimeoutError:
                run.error = "itecomdbgr did not exit after closing its output."
    finally:
        # Also runs when the RPC is cancelled: never leave the tool running.
        if proc.returncode is None:
            run.error = run.error or "itecomdbgr was stopped. " + _PARTIAL_HINT
            await _terminate(proc)
        run.returncode = proc.returncode


def failure_reason(run: ToolRun, verify: bool) -> str:
    """Returns '' if |run| passed, else the reason for the operator."""
    if run.error:
        return run.error
    required = ("Programming", "Verifying") if verify else ("Programming",)
    incomplete = [p for p in required if run.max_percent.get(p, -1) < 100]
    if run.returncode == 0 and not incomplete:
        return ""
    text = "\n".join(run.output)
    hint = next((h for pattern, h in _FAILURE_HINTS if pattern in text), "")
    if run.returncode != 0:
        reason = f"itecomdbgr failed (exit code {run.returncode})."
    else:
        phase = incomplete[0]
        reason = (
            f"itecomdbgr exited without completing {phase} "
            f"({max(run.max_percent.get(phase, 0), 0)}%)."
        )
    return f"{reason} {hint}".strip()


def enumeration_key(console: device.McuConsole) -> Tuple[str, str]:
    """Identifies one USB enumeration of an MCU (devnum changes each time)."""
    devnum = device.read_sysfs(os.path.join(console.usb_path, "devnum"))
    return console.usb_path, devnum


def wait_for_new_app(
    before: FrozenSet[Tuple[str, str]],
    timeout_s: float = REENUM_TIMEOUT_S,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[device.McuConsole]:
    """Waits for an App console that was not enumerated before the flash.

    The NVS (and so the USB iSerial) is erased by a full flash, so the board
    is recognized by a new USB enumeration instead of its serial number.
    """
    deadline = clock() + timeout_s
    while True:
        for console in device.scan_mcu_consoles():
            if console.is_app and enumeration_key(console) not in before:
                return console
        if clock() >= deadline:
            return None
        sleep(REENUM_POLL_S)


def check_image(path: str) -> str:
    """Returns '' if |path| is a whole 1 MiB IT82002 image, else why not."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return f"Cannot read {os.path.basename(path)}: {e}"
    if size != profile.MCU_FULL_IMAGE_SIZE:
        return (
            f"{os.path.basename(path)} has {size} bytes; a full MCU image must "
            f"be exactly {profile.MCU_FULL_IMAGE_SIZE} bytes (1 MiB)."
        )
    return ""


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def _claim_port(dev_node: str) -> bool:
    with _busy_ports_lock:
        if dev_node in _busy_ports:
            return False
        _busy_ports.add(dev_node)
        return True


def _release_port(dev_node: str) -> None:
    with _busy_ports_lock:
        _busy_ports.discard(dev_node)


@dataclasses.dataclass
class FlashJob:
    """Validated inputs of one full-image flash."""

    bridge: uart.UartBridge
    image: str
    baud: int
    verify: bool


class FullImageFlasher:  # pylint: disable=too-few-public-methods
    """update_mcu_full_image: validation, itecomdbgr, reboot and version."""

    def __init__(self, read_version: Callable[[str], str]) -> None:
        # Reads `version` from the App console at the given /dev node.
        self._read_version = read_version
        self._log: Deque[str] = collections.deque(maxlen=MAX_LOG_LINES)
        self._log_key = ""
        self._started = time.monotonic()
        self._phase = PHASE_CONNECTING
        self._percent = 0

    def _elapsed(self) -> float:
        return round(time.monotonic() - self._started, 1)

    def _progress(self, **fields) -> maui_manufacturing_pb2.MauiMcuFullImageProgress:
        """A progress message; phase and overall percent default to the state."""
        fields.setdefault("phase", self._phase)
        fields.setdefault("progress_percent", self._percent)
        return maui_manufacturing_pb2.MauiMcuFullImageProgress(
            elapsed_s=self._elapsed(), **fields
        )

    def _finish(
        self, success: bool, message: str, **fields
    ) -> maui_manufacturing_pb2.MauiMcuFullImageProgress:
        """Writes mcu_full_flash.log and returns the final message."""
        if not success:
            logging.warning("MCU full image flash failed: %s", message)
        log_text = "\n".join(self._log)
        write_device_log(
            self._log_key,
            LOG_NAME,
            f"ALL_PASSED={success}\nRESULT: {'PASSED' if success else 'FAILED'}\n"
            f"{message}\nDuration: {self._elapsed():.1f} s\n\n{log_text}",
        )
        return self._progress(
            is_complete=True,
            success=success,
            progress_percent=100 if success else self._percent,
            phase=PHASE_DONE if success else PHASE_FAILED,
            status_message=message,
            error_message="" if success else message,
            **fields,
        )

    def _validate(
        self,
        request: maui_manufacturing_pb2.MauiMcuFullImageRequest,
        fw_file: str,
    ) -> Tuple[Optional[FlashJob], str]:
        """Returns the job, or None and why the request is rejected."""
        # pylint: disable=too-many-return-statements
        baud = request.baud or DEFAULT_BAUD
        if baud not in BAUD_RATES:
            return None, (
                f"Unsupported baud rate {baud}: itecomdbgr only supports "
                f"{' and '.join(str(b) for b in BAUD_RATES)}."
            )
        if not fw_file:
            return None, (
                f"MCU full image '{request.file_name or '*.bin'}' not found in "
                f"the {profile.FW_COMPONENT_MCU_FULL} firmware directory."
            )
        error = check_image(fw_file)
        if error:
            return None, error
        bridges = uart.scan_uart_bridges()
        if not bridges:
            return None, (
                "No USB-UART bridge detected. Supported: FTDI, Silicon Labs "
                "CP210x, CDC-ACM, Prolific PL2303 and WCH CH341 bridges."
            )
        if request.uart_port:
            bridge = uart.find_bridge(request.uart_port, bridges)
        else:
            bridge = uart.default_bridge(bridges)
        if bridge is None:
            return None, (
                f"'{request.uart_port}' is not a detected USB-UART bridge "
                f"(detected: {', '.join(b.dev_node for b in bridges)})."
            )
        if not os.access(ITECOMDBGR, os.X_OK):
            return None, f"{ITECOMDBGR} is not installed in this DiagnoseMe image."
        return FlashJob(bridge, fw_file, baud, request.verify), ""

    async def flash(
        self,
        request: maui_manufacturing_pb2.MauiMcuFullImageRequest,
        fw_file: str,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiMcuFullImageProgress, None]:
        """Validates the request and flashes the image, streaming progress."""
        self._started = time.monotonic()
        unit = device.find_unit()
        # The unit serial: the H2H keeps it, the MCU NVS copy gets erased.
        mcu_serial = unit.mcu.serial if unit.mcu else ""
        self._log_key = (
            (unit.h2h.serial if unit.h2h else "")
            or (mcu_serial if profile.is_valid_serial(mcu_serial) else "")
            or "UNKNOWN"
        )
        job, error = self._validate(request, fw_file)
        if job is None:
            yield self._finish(False, error)
            return
        if not _claim_port(job.bridge.dev_node):
            yield self._finish(
                False,
                f"A full image flash is already running on {job.bridge.dev_node}.",
            )
            return
        try:
            # aclosing: a cancelled RPC stops itecomdbgr right away.
            async with contextlib.aclosing(
                self._flash(job, unit, is_cancelled)
            ) as updates:
                async for update in updates:
                    yield update
        finally:
            _release_port(job.bridge.dev_node)

    def _prepare(
        self, job: FlashJob, unit: device.UnitInfo, cmd: Sequence[str]
    ) -> List[str]:
        """Logs the inputs and readies the bridge (blocking); returns the lines."""
        mcu = unit.mcu
        lines = [
            f"Image: {job.image} ({profile.MCU_FULL_IMAGE_SIZE} bytes, "
            f"sha256 {_sha256(job.image)})",
            f"Bridge: {job.bridge.label}",
            f"Baud: {job.baud}, verify: {'yes' if job.verify else 'no (-n)'}",
            f"H2H serial: {unit.h2h.serial if unit.h2h else '(none)'}",
            "MCU before: "
            + (f"{mcu.dev_node} {mcu.product} [{mcu.serial}]" if mcu else "(none)"),
        ]
        lines += uart.prepare_bridge(job.bridge)
        lines.append("Running: " + " ".join(cmd))
        logging.info("MCU full image flash: %s", " ".join(cmd))
        self._log.extend(lines)
        return lines

    async def _flash(
        self,
        job: FlashJob,
        unit: device.UnitInfo,
        is_cancelled: Callable[[], bool],
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiMcuFullImageProgress, None]:
        loop = asyncio.get_running_loop()
        before = frozenset(
            enumeration_key(c)
            for c in await loop.run_in_executor(None, device.scan_mcu_consoles)
        )
        cmd = build_command(job.bridge.dev_node, job.image, job.baud, job.verify)
        for line in await loop.run_in_executor(None, self._prepare, job, unit, cmd):
            yield self._progress(log_line=line)
        yield self._progress(
            status_message=(
                f"Connecting to the MCU debugger over {job.bridge.name}; "
                f"flashing {os.path.basename(job.image)}..."
            )
        )
        run = ToolRun()
        async with contextlib.aclosing(
            self._run_tool(cmd, job.verify, run, is_cancelled)
        ) as updates:
            async for update in updates:
                yield update
        reason = failure_reason(run, job.verify)
        if reason:
            yield self._finish(False, reason)
            return

        self._phase, self._percent = PHASE_REBOOTING, REBOOT_PERCENT
        yield self._progress(
            status_message=(
                "Image written. Waiting for the MCU App (18d1:5085) to enumerate..."
            )
        )
        console = await loop.run_in_executor(
            None, wait_for_new_app, before, REENUM_TIMEOUT_S
        )
        if console is None:
            yield self._finish(
                False,
                "itecomdbgr finished, but the MCU App did not enumerate within "
                f"{REENUM_TIMEOUT_S:.0f} s. Power-cycle the board (replug the Host "
                "USB-C cable) and check that it enumerates; otherwise flash the "
                "full image again.",
            )
            return
        version = await loop.run_in_executor(None, self._version, console.dev_node)
        self._log.append(
            f"MCU after: {console.dev_node} {console.product} [{console.serial}], "
            f"version {version or 'unknown'}"
        )
        yield self._finish(
            True,
            f"MCU full image {os.path.basename(job.image)} flashed; the App "
            f"enumerated on {console.dev_node} (version {version or 'unknown'}). "
            + SERIAL_ERASED_NOTE,
            mcu_version=version,
            mcu_serial=console.serial,
        )

    async def _run_tool(
        self,
        cmd: Sequence[str],
        verify: bool,
        run: ToolRun,
        is_cancelled: Callable[[], bool],
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiMcuFullImageProgress, None]:
        """Runs itecomdbgr, streaming its phases and log lines."""
        async with contextlib.aclosing(
            run_itecomdbgr(
                cmd, run, VERIFY_TIMEOUT_S if verify else TIMEOUT_S, is_cancelled
            )
        ) as updates:
            async for update in updates:
                if update.log:
                    self._log.append(update.line)
                if not update.phase:
                    yield self._progress(log_line=update.line)
                    continue
                self._phase = update.phase.lower()
                self._percent = max(
                    self._percent,
                    overall_percent(update.phase, update.percent, verify),
                )
                yield self._progress(
                    phase_percent=update.percent,
                    status_message=f"{update.phase}... {update.percent}%",
                    log_line=update.line if update.log else "",
                )
        self._log.append(f"itecomdbgr exit code: {run.returncode}")

    def _version(self, dev_node: str) -> str:
        """Reads the App version; the console may need a moment after boot."""
        for attempt in range(VERSION_ATTEMPTS):
            if attempt:
                time.sleep(VERSION_RETRY_DELAY_S)
            version = self._read_version(dev_node)
            if version:
                return version
        return ""
