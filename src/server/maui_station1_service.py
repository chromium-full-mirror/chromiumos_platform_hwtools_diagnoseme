#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Maui Step 1 (Initial Flashing, Provisioning & Multi-Device PDC) gRPC Servicers.

Also hosts the device management servicer (log analysis, label verification and
final results submission) shared by all Maui Steps.
"""

import asyncio
import dataclasses
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import AsyncGenerator
from typing import Dict
from typing import List
from typing import Mapping
from typing import Optional
from typing import Set
from typing import Tuple

from google.protobuf import empty_pb2
import grpc

from server import maui_common
from server import util
from server.generated import maui_manufacturing_pb2
from server.generated import maui_manufacturing_pb2_grpc
from server.maui_common import ensure_zephyr_shell_awake
from server.maui_common import find_offline_fw_file
from server.maui_common import FTDI_PID_PROGRAMMED
from server.maui_common import FTDI_PID_UNPROGRAMMED
from server.maui_common import FTDI_VID
from server.maui_common import get_device_log_dir
from server.maui_common import get_maui_env
from server.maui_common import LogStatus
from server.maui_common import resolve_maui_flash_command
from server.maui_common import resolve_maui_tool
from server.maui_common import SYSFS_WALK_STOP_DIRS
from server.maui_common import write_device_log


# Generated protobuf messages are not visible to pylint.
# pylint: disable=no-member

DEFAULT_TTY = "/dev/ttyUSB0"

# BSL auto-discovery stream.
BSL_WINDOW_SEC = 10
DISCOVERY_DEVICE_POLL_SEC = 1.0
DISCOVERY_IDLE_POLL_SEC = 0.5

# MCU BSL flashing.
MCU_FLASH_TIMEOUT_SEC = 30
MCU_BSL_RETRY_DELAY_SEC = 0.2
MCU_BSL_RETRY_MARKERS = ("Unable to connect to BSL", "Check if device is in BSL mode")
MCU_BSL_RETRY_EXIT_CODE = 1
DEFAULT_MCU_FW_FILE = "/var/lib/diagnoseme/firmware/mcu/zephyr.txt"
MCU_TOOL_MISSING_MESSAGE = "Flashing tool not installed on host system."

# FTDI EEPROM programming.
FTDI_PROGRAM_TIMEOUT_SEC = 10

# Topology discovery.
LSUSB_TIMEOUT_SEC = 2.0
TOPOLOGY_DISCOVERY_ATTEMPTS = 10
TOPOLOGY_RETRY_DELAY_SEC = 0.5
# Minimum topology score accepted by the Tier 4 matcher.
MIN_TOPOLOGY_MATCH_SCORE = 25

# PDC flashing.
PDC_TOTAL_BLOCKS = 11
PDC_READLINE_TIMEOUT_SEC = 30.0
PDC_EXIT_TIMEOUT_SEC = 15.0
PDC_WAKE_TIMEOUT_SEC = 2.5
PDC_RETRY_WAKE_TIMEOUT_SEC = 3.0
PDC_RETRY_DELAY_SEC = 0.3
PDC_PROMPT_TIMEOUT_MARKERS = (
    "Exclusive logging enabled",
    "Timeout while waiting for prompt",
    "Buffer empty",
)
# Support both 'Progess' and 'Progress'
_PDC_PROGRESS_RE = re.compile(
    r"Block\s*(\d+):\s+Prog(?:r)?ess:\s+(\d+)/(\d+)\s+bytes\s+transferred\s+\(([\d.]+)%\)"
)

# Process-wide lock to prevent concurrent USB EEPROM programming collisions
_ftdi_programming_lock = threading.Lock()

# Per-port lock to prevent concurrent MCU BSL flashing collisions
_mcu_flashing_locks: Dict[str, threading.Lock] = {}
_mcu_locks_guard = threading.Lock()


def _get_port_flashing_lock(dev_port: str) -> threading.Lock:
    with _mcu_locks_guard:
        if dev_port not in _mcu_flashing_locks:
            _mcu_flashing_locks[dev_port] = threading.Lock()
        return _mcu_flashing_locks[dev_port]


# Files older than their temp dir registration (minus this slack) belong to an
# earlier board that used the same temp dir and are never merged.
PRE_SERIAL_LOG_MTIME_SLACK_SEC = 2.0


class _PreSerialLogDirs:
    """Thread-safe record of which temporary log dirs belong to which board.

    Step 1 logs written before the ADB serial is known go to temporary dirs
    (PORT_<tty> for the MCU flash, MAUI-SLOT<n> for the PDC flash). Each board
    is a group identified by its initial FTDI serial and its current console
    port. The group follows the console when the backend relocates it after
    re-enumeration, so exactly the temp dirs of one board are merged once its
    serial is known. Another board's dirs are never merged.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_group = 0
        # ("port", "/dev/ttyUSB0") / ("ftdi", "<serial>") -> group ID.
        self._group_of: Dict[Tuple[str, str], int] = {}
        # Group ID -> {temp dir name: registration time}.
        self._dirs: Dict[int, Dict[str, float]] = {}

    @staticmethod
    def _keys(port: str = "", ftdi_serial: str = "") -> List[Tuple[str, str]]:
        keys = []
        if (ftdi_serial or "").strip():
            keys.append(("ftdi", ftdi_serial.strip()))
        if (port or "").strip():
            keys.append(("port", port.strip()))
        return keys

    def _new_group_locked(self) -> int:
        self._next_group += 1
        self._dirs[self._next_group] = {}
        return self._next_group

    def _assign_locked(self, keys: List[Tuple[str, str]], group: int) -> None:
        for key in keys:
            self._group_of[key] = group
        used = set(self._group_of.values())
        for unused in [g for g in self._dirs if g not in used]:
            del self._dirs[unused]

    def start_board(self, dir_name: str, port: str, ftdi_serial: str) -> None:
        """Starts a new board on `port` (MCU flash); `dir_name` is "" if serial known."""
        keys = self._keys(port, ftdi_serial)
        if not keys:
            return
        with self._lock:
            group = self._new_group_locked()
            if dir_name:
                self._dirs[group][dir_name] = time.time()
            self._assign_locked(keys, group)

    def add_dir(self, dir_name: str, port: str, ftdi_serial: str = "") -> None:
        """Adds a temp dir to the board currently on `port` (or with `ftdi_serial`)."""
        keys = self._keys(port, ftdi_serial)
        if not keys or not dir_name:
            return
        with self._lock:
            group = next((self._group_of[k] for k in keys if k in self._group_of), None)
            if group is None:
                group = self._new_group_locked()
            self._dirs[group].setdefault(dir_name, time.time())
            self._assign_locked(keys, group)

    def relocate(self, ftdi_serial: str, new_port: str) -> None:
        """Records that the console of board `ftdi_serial` is now `new_port`."""
        ftdi_keys = self._keys(ftdi_serial=ftdi_serial)
        port_keys = self._keys(port=new_port)
        if not ftdi_keys or not port_keys:
            return
        with self._lock:
            group = self._group_of.get(ftdi_keys[0])
            if group is not None:
                self._assign_locked(port_keys, group)

    def dirs_for(
        self, port: str = "", ftdi_serial: str = "", forget: bool = False
    ) -> Dict[str, float]:
        """Temp dirs of the board on `port` / `ftdi_serial` (name -> registered at).

        With `forget`, the board's group is dropped (its serial is final).
        """
        keys = self._keys(port, ftdi_serial)
        with self._lock:
            groups = {self._group_of[k] for k in keys if k in self._group_of}
            found: Dict[str, float] = {}
            for group in groups:
                for name, registered in self._dirs.get(group, {}).items():
                    found[name] = min(registered, found.get(name, registered))
            if forget and groups:
                for key in [k for k, g in self._group_of.items() if g in groups]:
                    del self._group_of[key]
                for group in groups:
                    self._dirs.pop(group, None)
        return found


_pre_serial_logs = _PreSerialLogDirs()


def _merge_pre_serial_logs(
    serial_no: str, port: str = "", ftdi_serial: str = "", forget: bool = False
) -> None:
    """Copies the logs of this board's temp dirs into the serial's log dir.

    Only files written after their temp dir was registered for this board are
    copied, and existing non-empty logs in the serial dir are kept.
    """
    if (
        not serial_no
        or serial_no.startswith(("PORT_", "MAUI-SLOT"))
        or "UNKNOWN" in serial_no.upper()
    ):
        return
    temp_dirs = _pre_serial_logs.dirs_for(port, ftdi_serial, forget=forget)
    if not temp_dirs:
        return
    target_dir = get_device_log_dir(serial_no)
    for dir_name, registered in sorted(temp_dirs.items()):
        src_dir = maui_common.device_log_dir_path(dir_name)
        if not os.path.isdir(src_dir) or os.path.abspath(src_dir) == os.path.abspath(
            target_dir
        ):
            continue
        for fname in sorted(os.listdir(src_dir)):
            src_f = os.path.join(src_dir, fname)
            dst_f = os.path.join(target_dir, fname)
            try:
                if not os.path.isfile(src_f) or (
                    os.path.getmtime(src_f)
                    < registered - PRE_SERIAL_LOG_MTIME_SLACK_SEC
                ):
                    continue
                if not os.path.exists(dst_f) or os.path.getsize(dst_f) == 0:
                    shutil.copy2(src_f, dst_f)
                    logging.info("Merged pre-serial log %s into %s", src_f, target_dir)
            except OSError as e:
                logging.warning(
                    "Could not merge log %s into %s: %s", src_f, target_dir, e
                )


@dataclasses.dataclass
class _UsbScanCache:
    """Per-scan cache of the expensive USB lookups (lsusb / sysfs walks)."""

    hub_cids: Optional[Dict[str, str]] = None
    adb_candidates: Optional[List[Dict[str, str]]] = None


@dataclasses.dataclass
class _FtdiUsbDevice:
    """USB device (normally the FTDI chip) found above a ttyUSB node in sysfs."""

    device_dir: Optional[str] = None
    pid: int = FTDI_PID_UNPROGRAMMED
    serial: str = ""
    container_id: str = ""
    devpath: str = ""
    hub_container_id: str = ""


def _parent_hub_container_id(device_dir: str, hub_cids: Dict[str, str]) -> str:
    """Container ID of the hub a USB device is plugged into."""
    return maui_common.resolve_hub_container_id(os.path.dirname(device_dir), hub_cids)


class MauiStation1Servicer(
    maui_manufacturing_pb2_grpc.MauiStation1InitialFlashingServicer
):
    """Step 1 gRPC servicer handling BSL auto-trigger and FTDI EEPROM programming."""

    async def stream_bsl_auto_discovery(
        self,
        request: empty_pb2.Empty,
        context: grpc.ServicerContext,
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiBslDiscoveryResponse, None]:
        """Streams real-time BSL auto-discovery notifications.

        Detects:
          - 0403:6001 (Unprogrammed FTDI): auto_flash_eligible = True -> UI auto-starts flashing.
          - 0403:6002 (Programmed FTDI): auto_flash_eligible = False -> Manual fallback button only.
          - 0483:DF11 / 2047:0013 (MSPM0 DFU / BSL): auto_flash_eligible = True -> UI auto-starts flashing.
        """
        del request  # Unused.
        logging.info("Starting BSL auto-discovery stream...")
        start_time = time.time()

        while not context.done():
            elapsed = time.time() - start_time
            remaining = max(0, BSL_WINDOW_SEC - (int(elapsed) % BSL_WINDOW_SEC))

            # 1. Check for FTDI UART devices (0403:6001 vs 0403:6002)
            ftdi_devs = await asyncio.to_thread(self._scan_ftdi_devices)
            if ftdi_devs:
                for d in ftdi_devs:
                    yield self._ftdi_discovery_response(d, remaining)
                await asyncio.sleep(DISCOVERY_DEVICE_POLL_SEC)
                continue

            # 2. Check for native MSPM0 BSL USB device (0483:DF11 or 2047:0013)
            if await asyncio.to_thread(self._is_bsl_usb_present):
                tty_nodes = maui_common.sorted_tty_nodes(
                    "/dev/ttyUSB*"
                ) + maui_common.sorted_tty_nodes("/dev/ttyACM*")
                port_node = tty_nodes[0] if tty_nodes else "/dev/ttyACM0"
                yield maui_manufacturing_pb2.MauiBslDiscoveryResponse(
                    bsl_detected=True,
                    device_port=port_node,
                    time_remaining_sec=remaining,
                    message=f"BSL DFU Mode Detected on {port_node}! Auto-starting MCU flashing.",
                    vid=maui_common.MAUI_BSL_VID,
                    pid=maui_common.MAUI_BSL_PID,
                    auto_flash_eligible=True,
                    detected_serial="",
                    ftdi_serial="",
                )
                await asyncio.sleep(DISCOVERY_DEVICE_POLL_SEC)
                continue

            # 3. No device detected
            yield maui_manufacturing_pb2.MauiBslDiscoveryResponse(
                bsl_detected=False,
                device_port="",
                time_remaining_sec=remaining,
                message="Waiting for Maui board in BSL mode (0403:6001)...",
                vid=0,
                pid=0,
                auto_flash_eligible=False,
                detected_serial="",
                ftdi_serial="",
            )
            await asyncio.sleep(DISCOVERY_IDLE_POLL_SEC)

    @staticmethod
    def _ftdi_discovery_response(
        device: Dict, remaining: int
    ) -> maui_manufacturing_pb2.MauiBslDiscoveryResponse:
        """Builds the discovery notification for one FTDI console."""
        is_unprog = device["pid"] == FTDI_PID_UNPROGRAMMED
        ser_display = device.get("adb_serial") or device.get("serial") or "Pending"
        msg = (
            f"Unprogrammed Board (0403:6001, Serial: {ser_display}) Detected on {device['port']}! "
            "Auto-starting MCU flashing."
            if is_unprog
            else f"Programmed Board (0403:6002, Serial: {ser_display}) Detected on {device['port']}."
        )
        return maui_manufacturing_pb2.MauiBslDiscoveryResponse(
            bsl_detected=True,
            device_port=device["port"],
            time_remaining_sec=remaining,
            message=msg,
            vid=FTDI_VID,
            pid=device["pid"],
            auto_flash_eligible=is_unprog,
            detected_serial=device.get("adb_serial") or device.get("serial") or "",
            ftdi_serial=device.get("serial") or "",
        )

    @staticmethod
    def _is_bsl_usb_present() -> bool:
        """True when a native MSPM0 BSL / TI BSL USB device is enumerated."""
        return util.is_usb_device_present(
            maui_common.MAUI_BSL_VID, [maui_common.MAUI_BSL_PID]
        ) or util.is_usb_device_present(
            maui_common.TI_BSL_VID_PID[0], [maui_common.TI_BSL_VID_PID[1]]
        )

    def _scan_ftdi_devices(self) -> List[Dict]:
        """Scans sysfs for all connected FTDI (0403:6001 and 0403:6002) devices and associates ADB serial."""
        devices = []
        assigned_serials: Set[str] = set()
        cache = _UsbScanCache()
        for tty_dir in maui_common.sorted_tty_nodes("/sys/class/tty/ttyUSB*"):
            if not os.path.exists(os.path.join(tty_dir, "device")):
                continue

            dev_node = f"/dev/{os.path.basename(tty_dir)}"
            curr_ser, curr_pid, c_id, adb_ser = self._find_ftdi_info_for_port(
                dev_node, excluded_serials=assigned_serials, cache=cache
            )
            if adb_ser:
                assigned_serials.add(adb_ser)
            if curr_pid in maui_common.FTDI_PIDS or curr_ser is not None:
                devices.append(
                    {
                        "port": dev_node,
                        "vid": FTDI_VID,
                        "pid": curr_pid,
                        "serial": curr_ser or "",
                        "container_id": c_id or "",
                        "adb_serial": adb_ser or "",
                    }
                )
        return devices

    def auto_program_mcu_bsl(
        self,
        request: maui_manufacturing_pb2.MauiAutoProgramMcuRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiProgramMcuResponse:
        """Triggers MCU flashing via fw-updater or maui-flash using port-based selection."""
        fw_channel = request.fw_channel or "stable"
        dev_port = request.device_port or DEFAULT_TTY
        serial_no = (
            request.serial_number or ""
        ).strip() or f"PORT_{os.path.basename(dev_port)}"
        logging.info(
            "Starting MCU BSL flashing on port %s (channel: %s, serial: %s)...",
            dev_port,
            fw_channel,
            serial_no,
        )

        curr_ftdi_ser, _, _, _ = self._find_ftdi_info_for_port(dev_port)

        port_lock = _get_port_flashing_lock(dev_port)
        if not port_lock.acquire(blocking=False):
            logging.warning(
                "MCU flashing already in progress on port %s. Rejecting duplicate concurrent request.",
                dev_port,
            )
            return maui_manufacturing_pb2.MauiProgramMcuResponse(
                success=False,
                message=f"Flashing already in progress on {dev_port}.",
                logs=f"Duplicate concurrent flash request on {dev_port} was rejected.",
            )

        try:
            # A new board starts on this port: logs written before its serial is
            # known (PORT_<tty>) are merged once the serial is programmed.
            _pre_serial_logs.start_board(
                serial_no if serial_no.startswith("PORT_") else "",
                dev_port,
                curr_ftdi_ser or "",
            )
            cmd, error_resp = self._build_mcu_flash_command(
                fw_channel, dev_port, serial_no
            )
            if error_resp is not None:
                return error_resp
            return self._run_mcu_flash(cmd, dev_port, serial_no, curr_ftdi_ser)
        finally:
            port_lock.release()

    @staticmethod
    def _build_mcu_flash_command(
        fw_channel: str, dev_port: str, serial_no: str
    ) -> Tuple[List[str], Optional[maui_manufacturing_pb2.MauiProgramMcuResponse]]:
        """Selects the MCU flashing tool. Returns (cmd, None) or ([], error_response).

        Precedence:
          1. fw-updater + local offline firmware (direct BSL mode, no shell sync).
          2. maui-flash (bin, maui_flash.py or scripts/maui-flash) with the local firmware file
             for the 'offline'/'stable' channels.
          3. 'offline' channel without local firmware -> error.
          4. maui-flash (bin or maui_flash.py) with --fw_channel, else fw-updater with the
             default firmware path.
        A missing tool is reported as an error instead of being simulated.
        """
        offline_fw = find_offline_fw_file("mcu")
        fw_updater_bin = resolve_maui_tool("fw-updater")
        port_args = ["--port", dev_port]
        cmd: Optional[List[str]] = None

        if fw_updater_bin and offline_fw:
            logging.info(
                "Directly invoking fw-updater on %s with firmware %s (BSL mode)...",
                dev_port,
                offline_fw,
            )
            cmd = [fw_updater_bin, offline_fw, "--uart", dev_port, "--bsl-mode"]
        elif offline_fw and fw_channel in ("offline", "stable"):
            logging.info(
                "Found local offline MCU firmware: %s. Using --file with maui-flash.",
                offline_fw,
            )
            if "ACM" in dev_port or (
                "usb" in dev_port.lower() and "ttyusb" not in dev_port.lower()
            ):
                port_args.append("--usb")
            maui_flash = resolve_maui_flash_command(include_script=True)
            if maui_flash:
                cmd = (
                    maui_flash + port_args + ["mcu", "--file", offline_fw, "--bsl-mode"]
                )
        elif fw_channel == "offline":
            logging.error(
                "Offline MCU firmware file not found in /usr/local/server/firmware/mcu or "
                "/var/lib/diagnoseme/firmware/mcu."
            )
            return [], maui_manufacturing_pb2.MauiProgramMcuResponse(
                success=False,
                message="Offline MCU firmware not found on system.",
                logs=(
                    "Error: Local MCU firmware binary (.txt/.hex/.bin) not found in "
                    "/usr/local/server/firmware/mcu or /var/lib/diagnoseme/firmware/mcu."
                ),
            )
        else:
            maui_flash = resolve_maui_flash_command(include_script=False)
            if maui_flash:
                cmd = (
                    maui_flash
                    + port_args
                    + ["mcu", "--fw_channel", fw_channel, "--bsl-mode"]
                )
            elif fw_updater_bin:
                cmd = [
                    fw_updater_bin,
                    DEFAULT_MCU_FW_FILE,
                    "--uart",
                    dev_port,
                    "--bsl-mode",
                ]

        if cmd is None:
            return [], MauiStation1Servicer._mcu_tool_missing(serial_no)
        return cmd, None

    @staticmethod
    def _mcu_tool_missing(
        serial_no: str,
    ) -> maui_manufacturing_pb2.MauiProgramMcuResponse:
        """Fails the MCU flash because no flashing tool is installed."""
        err = "ERROR: No MCU flashing tool (fw-updater / maui-flash) found on host system."
        logging.error(err)
        write_device_log(serial_no, "mcu_flashing", err, status=LogStatus.FAILED)
        return maui_manufacturing_pb2.MauiProgramMcuResponse(
            success=False,
            message=MCU_TOOL_MISSING_MESSAGE,
            logs=err,
        )

    @staticmethod
    def _run_flash_tool(
        cmd: List[str], env: Dict[str, str]
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
            timeout=MCU_FLASH_TIMEOUT_SEC,
            env=env,
        )

    @staticmethod
    def _is_bsl_retryable(error: subprocess.CalledProcessError) -> bool:
        output = str(error.output or "")
        return error.returncode == MCU_BSL_RETRY_EXIT_CODE or any(
            m in output for m in MCU_BSL_RETRY_MARKERS
        )

    def _run_mcu_flash(
        self, cmd: List[str], dev_port: str, serial_no: str, ftdi_serial: Optional[str]
    ) -> maui_manufacturing_pb2.MauiProgramMcuResponse:
        """Runs the MCU flashing tool with one fast retry inside the ROM BSL window."""
        env = get_maui_env()
        retried = False
        try:
            try:
                proc = self._run_flash_tool(cmd, env)
            except subprocess.CalledProcessError as first_error:
                # Fast 1x automatic retry while still inside the 10s hardware ROM BSL window
                if not self._is_bsl_retryable(first_error):
                    raise
                logging.warning(
                    "BSL connection failed on first attempt on %s. Retrying immediately...",
                    dev_port,
                )
                time.sleep(MCU_BSL_RETRY_DELAY_SEC)
                proc = self._run_flash_tool(cmd, env)
                retried = True
        except subprocess.CalledProcessError as e:
            err_logs = str(e.output or "")
            logging.error(
                "MCU BSL Flashing failed (exit %s):\n%s", e.returncode, err_logs
            )
            write_device_log(
                serial_no,
                "mcu_flashing",
                f"FAILED (exit {e.returncode}):\n{err_logs}",
                status=LogStatus.FAILED,
            )
            return maui_manufacturing_pb2.MauiProgramMcuResponse(
                success=False,
                message=f"MCU BSL Flashing failed with exit code {e.returncode}.",
                logs=err_logs,
            )
        except subprocess.TimeoutExpired as e:
            err_logs = str(e.output or "")
            logging.error(
                "MCU BSL Flashing timed out after %ss on %s", e.timeout, dev_port
            )
            write_device_log(
                serial_no,
                "mcu_flashing",
                f"TIMEOUT ({e.timeout}s):\n{err_logs}",
                status=LogStatus.TIMEOUT,
            )
            return maui_manufacturing_pb2.MauiProgramMcuResponse(
                success=False,
                message=f"MCU BSL Flashing timed out after {e.timeout}s. Please verify hardware connection.",
                logs=err_logs or f"Process timed out after {e.timeout}s",
            )
        except FileNotFoundError as e:
            logging.exception("Flashing binary not found")
            write_device_log(
                serial_no, "mcu_flashing", f"ERROR: {e}", status=LogStatus.FAILED
            )
            return maui_manufacturing_pb2.MauiProgramMcuResponse(
                success=False,
                message=MCU_TOOL_MISSING_MESSAGE,
                logs=str(e or ""),
            )
        except (OSError, subprocess.SubprocessError) as e:
            err_logs = util.get_logs_from_exception(e)
            logging.exception("Unexpected error during MCU BSL Flashing: %s", err_logs)
            write_device_log(
                serial_no,
                "mcu_flashing",
                f"EXCEPTION:\n{err_logs}",
                status=LogStatus.FAILED,
            )
            return maui_manufacturing_pb2.MauiProgramMcuResponse(
                success=False,
                message=f"MCU BSL Flashing error: {str(e)}",
                logs=str(err_logs or str(e)),
            )

        if retried:
            logging.info("MCU BSL Flashing succeeded on retry 2:\n%s", proc.stdout)
            write_device_log(
                serial_no,
                "mcu_flashing",
                f"SUCCESS (on retry 2):\n{proc.stdout}",
                status=LogStatus.PASSED,
            )
            message = "MCU firmware flashed successfully via BSL mode (retry 2)."
        else:
            logging.info("MCU BSL Flashing succeeded:\n%s", proc.stdout)
            write_device_log(
                serial_no,
                "mcu_flashing",
                f"SUCCESS:\n{proc.stdout}",
                status=LogStatus.PASSED,
            )
            message = "MCU firmware flashed successfully via BSL mode."
        return maui_manufacturing_pb2.MauiProgramMcuResponse(
            success=True,
            message=message,
            logs=str(proc.stdout or ""),
            ftdi_serial=ftdi_serial or "",
        )

    def _get_usb_hub_container_ids(self) -> Dict[str, str]:
        """Maps USB hub device name (e.g. '3-1', '2-3', '3-6', '4-2') to its 128-bit hardware ContainerID.

        Combines direct sysfs BOS binary descriptor parsing, sysfs container_id attributes,
        and targeted lsusb -v queries to guarantee reliable ContainerID resolution on all Linux kernels.
        """
        cids: Dict[str, str] = {}

        # 1. Read sysfs container_id files and raw BOS binary descriptors directly
        for d in glob.glob("/sys/bus/usb/devices/[0-9]*"):
            cid = maui_common.read_device_container_id(d)
            if cid:
                cids[os.path.basename(d)] = cid

        # 2. Query lsusb -v for TI TUSB8041 (0451:8140/8142) and Realtek (0bda) hubs as fallback
        for vid in maui_common.HUB_LSUSB_VENDOR_IDS:
            try:
                proc = subprocess.run(
                    ["lsusb", "-v", "-d", f"{vid}:"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=LSUSB_TIMEOUT_SEC,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as e:
                logging.debug(
                    "Error querying hub container IDs for VID %s via lsusb: %s", vid, e
                )
                continue
            if proc.returncode == 0 and proc.stdout:
                self._map_lsusb_container_ids(proc.stdout, cids)

        return cids

    @staticmethod
    def _map_lsusb_container_ids(lsusb_output: str, cids: Dict[str, str]) -> None:
        """Adds ContainerIDs from `lsusb -v` output to cids, keyed by sysfs device name."""
        bus_dev_cids: Dict[Tuple[int, int], str] = {}
        curr_bus_dev = None
        for line in lsusb_output.splitlines():
            m_dev = re.match(r"Bus\s+(\d+)\s+Device\s+(\d+):", line)
            if m_dev:
                curr_bus_dev = (int(m_dev.group(1)), int(m_dev.group(2)))
            m_cid = re.search(r"ContainerID\s+\{?([0-9a-fA-F-]+)\}?", line)
            if m_cid and curr_bus_dev:
                bus_dev_cids[curr_bus_dev] = m_cid.group(1).lower().strip("{}")

        # Map sysfs device names by matching (busnum, devnum)
        for d in glob.glob("/sys/bus/usb/devices/[0-9]*"):
            hub_name = os.path.basename(d)
            if hub_name in cids:
                continue
            b_file = os.path.join(d, "busnum")
            dn_file = os.path.join(d, "devnum")
            if not (os.path.exists(b_file) and os.path.exists(dn_file)):
                continue
            try:
                with open(b_file, encoding="utf-8") as f:
                    b_val = int(f.read().strip())
                with open(dn_file, encoding="utf-8") as f:
                    dn_val = int(f.read().strip())
            except (OSError, ValueError) as e:
                logging.debug("Cannot read bus/dev numbers of %s: %s", d, e)
                continue
            if (b_val, dn_val) in bus_dev_cids:
                cids[hub_name] = bus_dev_cids[(b_val, dn_val)]

    def _match_ftdi_to_adb(  # pylint: disable=too-many-return-statements
        self,
        ftdi_dir: Optional[str],
        ftdi_name: str,
        ftdi_devpath: str,
        ftdi_container_id: str,
        ftdi_hub_container_id: str = "",
        adb_candidates: Optional[List[Dict[str, str]]] = None,
        excluded_serials: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Matches an FTDI device to its sibling ADB chip on the same Maui board across arbitrary host buses & intermediate hubs.

        Hardware Invariants & Matching Hierarchy:
          Tier 1: Physical Maui On-Board Hub ContainerID Match (TI TUSB8041 USB2 0451:8142 <-> USB3 0451:8140)
          Tier 2: Direct Parent Hub Directory Match (when ADB is in USB 2.0 fallback mode)
          Tier 3: Device ContainerID Match
          Tier 4: External Hub / Root Port Topology Scoring
          Tier 5: Single Available ADB Candidate Fallback (authoritative for 1-board test setups)
        """
        excluded = excluded_serials or set()
        available_candidates = [
            c for c in (adb_candidates or []) if c.get("serial") not in excluded
        ]
        ftdi_label = ftdi_devpath or ftdi_name

        if not available_candidates:
            return None

        # If FTDI has a known hub container ID, prune any candidate whose hub container ID is known and different
        if ftdi_hub_container_id:
            hub_cid = ftdi_hub_container_id.lower()
            available_candidates = [
                c
                for c in available_candidates
                if not c.get("hub_container_id")
                or c["hub_container_id"].lower() == hub_cid
            ]
            if not available_candidates:
                return None

            # Tier 1 (PRIMARY & AUTHORITATIVE): Physical Maui On-Board Hub Container ID Match
            for cand in available_candidates:
                if (
                    cand.get("hub_container_id")
                    and cand["hub_container_id"].lower() == hub_cid
                ):
                    logging.info(
                        "Matched FTDI port (%s) to ADB serial '%s' via Maui On-Board Hub container_id '%s'",
                        ftdi_label,
                        cand["serial"],
                        ftdi_hub_container_id,
                    )
                    return cand["serial"]

        # Tier 2: Direct Parent Hub Directory Match (when ADB is in USB 2.0 fallback mode)
        if ftdi_dir:
            parent_hub = os.path.dirname(ftdi_dir)
            if parent_hub and parent_hub not in SYSFS_WALK_STOP_DIRS:
                for cand in available_candidates:
                    if cand.get("parent_hub") and cand.get("parent_hub") == parent_hub:
                        logging.info(
                            "Matched FTDI port (%s) to ADB serial '%s' via exact shared parent hub directory '%s'",
                            ftdi_label,
                            cand["serial"],
                            parent_hub,
                        )
                        return cand["serial"]

        # Tier 3: Direct Device Container ID Match
        if ftdi_container_id:
            for cand in available_candidates:
                if (
                    cand.get("container_id")
                    and cand["container_id"].lower() == ftdi_container_id.lower()
                ):
                    return cand["serial"]

        # Tier 4: External Hub / Root Port Path Matching (Ranked Best-Match Scoring)
        best_cand = self._match_by_topology_score(ftdi_label, available_candidates)
        if best_cand:
            return best_cand

        # Tier 5: Single available ADB candidate fallback (authoritative for 1-board test setups)
        if len(available_candidates) == 1:
            logging.info(
                "Matched FTDI port (%s) to single connected ADB serial '%s'",
                ftdi_label,
                available_candidates[0]["serial"],
            )
            return available_candidates[0]["serial"]

        return None

    @staticmethod
    def _path_tokens(raw_path: str) -> List[str]:
        """Numeric port tokens of a USB devpath/name without the bus prefix (e.g. '3-6.1.2' -> 6,1,2)."""
        clean = re.sub(r"^\d+-", "", raw_path)
        return [t for t in re.split(r"[.\-_/]", clean) if t.isdigit()]

    @staticmethod
    def _topology_score(ftdi_tokens: List[str], cand_tokens: List[str]) -> int:
        """Scores how likely an ADB candidate path is the FTDI's sibling (0 = no match)."""
        score = 0
        if len(ftdi_tokens) == len(cand_tokens) and len(ftdi_tokens) >= 2:
            if ftdi_tokens[:-1] == cand_tokens[:-1]:
                # Exact full path match except final endpoint port (e.g. 1.2.2 vs 1.2.3)
                score = 100
            elif len(ftdi_tokens) >= 3 and ftdi_tokens[1:-1] == cand_tokens[1:-1]:
                # Intermediate hub ports match on different root ports (e.g. 6.1.2 vs 2.1.3)
                score = 50
            elif ftdi_tokens[-1] == "2" and cand_tokens[-1] == "3":
                # Direct PC connection where root hub port numbers differ (e.g. 3.2 on usb3 vs 1.3 on usb2)
                score = 30
        elif len(ftdi_tokens) >= 3 and len(cand_tokens) >= 3:
            if ftdi_tokens[1:-1] == cand_tokens[1:-1]:
                score = 50
        elif (
            ftdi_tokens
            and cand_tokens
            and ftdi_tokens[-1] == "2"
            and cand_tokens[-1] == "3"
        ):
            score = 25
        return score

    def _match_by_topology_score(
        self, ftdi_label: str, candidates: List[Dict[str, str]]
    ) -> Optional[str]:
        """Tier 4 matcher: unambiguous best topology score >= MIN_TOPOLOGY_MATCH_SCORE."""
        ftdi_tokens = self._path_tokens(ftdi_label)
        if len(ftdi_tokens) < 2:
            return None

        best_cand = None
        best_score = 0
        score_counts: Dict[int, int] = {}
        for cand in candidates:
            c_ser = cand.get("serial", "")
            if not c_ser:
                continue
            cand_tokens = self._path_tokens(
                cand.get("devpath") or cand.get("name") or ""
            )
            score = self._topology_score(ftdi_tokens, cand_tokens)
            if score > 0:
                score_counts[score] = score_counts.get(score, 0) + 1
                if score > best_score:
                    best_score = score
                    best_cand = c_ser

        # Only accept match if best_score >= 25 and unambiguous at highest score tier
        if (
            best_score >= MIN_TOPOLOGY_MATCH_SCORE
            and score_counts.get(best_score, 0) == 1
            and best_cand
        ):
            logging.info(
                "Matched FTDI port (%s) to ADB serial '%s' via topology score %d",
                ftdi_label,
                best_cand,
                best_score,
            )
            return best_cand
        return None

    def _hub_cids(self, cache: _UsbScanCache) -> Dict[str, str]:
        if cache.hub_cids is None:
            cache.hub_cids = self._get_usb_hub_container_ids()
        return cache.hub_cids

    def _adb_candidates(self, cache: _UsbScanCache) -> List[Dict[str, str]]:
        if cache.adb_candidates is None:
            cache.adb_candidates = self._collect_adb_candidates(self._hub_cids(cache))
        return cache.adb_candidates

    @staticmethod
    def _locate_ftdi_usb_device(
        real_tty_path: str, hub_cids: Dict[str, str]
    ) -> _FtdiUsbDevice:
        """Walks up from a tty interface to its FTDI USB device (or the first USB device)."""
        info = _FtdiUsbDevice()

        # 1. Walk up from tty interface to find the FTDI USB device node (idVendor == 0403)
        curr = real_tty_path
        while curr and curr not in SYSFS_WALK_STOP_DIRS:
            vid = maui_common.read_sysfs_file(os.path.join(curr, "idVendor")).lower()
            pid = maui_common.read_sysfs_file(os.path.join(curr, "idProduct")).lower()
            if vid == f"{FTDI_VID:04x}" and pid in (
                f"{p:04x}" for p in maui_common.FTDI_PIDS
            ):
                info.device_dir = curr
                info.pid = int(pid, 16)
                info.serial = maui_common.read_sysfs_file(os.path.join(curr, "serial"))
                info.container_id = (
                    maui_common.read_sysfs_file(os.path.join(curr, "container_id"))
                    .lower()
                    .strip("{}")
                )
                if not info.container_id:
                    info.container_id = hub_cids.get(os.path.basename(curr), "")
                info.devpath = maui_common.read_sysfs_file(
                    os.path.join(curr, "devpath")
                )
                # Find parent hub of FTDI and read its container_id
                info.hub_container_id = _parent_hub_container_id(curr, hub_cids)
                return info
            curr = os.path.dirname(curr)

        # 2. Not an FTDI chip: fall back to the first USB device above the tty.
        curr = real_tty_path
        while curr and curr not in SYSFS_WALK_STOP_DIRS:
            if os.path.exists(os.path.join(curr, "idVendor")):
                info.device_dir = curr
                info.devpath = maui_common.read_sysfs_file(
                    os.path.join(curr, "devpath")
                )
                info.hub_container_id = _parent_hub_container_id(curr, hub_cids)
                return info
            curr = os.path.dirname(curr)
        return info

    @staticmethod
    def _collect_adb_candidates(hub_cids: Dict[str, str]) -> List[Dict[str, str]]:
        """Collects all candidate ADB devices on the host with their parent hub container IDs."""
        adb_candidates: List[Dict[str, str]] = []
        for d in glob.glob("/sys/bus/usb/devices/*"):
            if not os.path.exists(os.path.join(d, "idVendor")):
                continue
            v_val = maui_common.read_sysfs_file(os.path.join(d, "idVendor")).lower()
            s_val = maui_common.read_sysfs_file(os.path.join(d, "serial"))
            prod_val = maui_common.read_sysfs_file(os.path.join(d, "product")).lower()

            is_adb_vendor = v_val in (
                f"{maui_common.GOOGLE_VID:04x}",
                f"{maui_common.PROLIFIC_VID:04x}",
            )
            is_adb_product = any(k in prod_val for k in ("adb", "maui", "prolific"))
            if (
                not (is_adb_vendor or is_adb_product)
                or maui_common.normalize_container_id(s_val) == ""
            ):
                continue

            cand_real = os.path.realpath(d)
            cand_cid = (
                maui_common.read_sysfs_file(os.path.join(d, "container_id"))
                .lower()
                .strip("{}")
            )
            if not cand_cid:
                cand_cid = hub_cids.get(os.path.basename(d), "")

            # Find candidate's parent hub and read parent hub's container_id
            cand_parent_hub = os.path.dirname(cand_real)
            adb_candidates.append(
                {
                    "dir": cand_real,
                    "name": os.path.basename(d),
                    "serial": s_val,
                    "devpath": maui_common.read_sysfs_file(os.path.join(d, "devpath")),
                    "container_id": cand_cid,
                    "parent_hub": cand_parent_hub,
                    "hub_container_id": maui_common.resolve_hub_container_id(
                        cand_parent_hub, hub_cids
                    ),
                }
            )
        return adb_candidates

    def _find_ftdi_info_for_port(
        self,
        dev_node: str,
        excluded_serials: Optional[Set[str]] = None,
        cache: Optional[_UsbScanCache] = None,
    ) -> Tuple[Optional[str], int, Optional[str], Optional[str]]:
        """Inspects a specific ttyUSB port and returns (current_ftdi_serial, current_ftdi_pid, container_id, adb_serial).

        `cache` lets callers that inspect several ports share one hub/ADB scan.
        """
        if not dev_node:
            return None, FTDI_PID_UNPROGRAMMED, None, None

        sys_path = os.path.join("/sys/class/tty", os.path.basename(dev_node), "device")
        if not os.path.exists(sys_path):
            return None, FTDI_PID_UNPROGRAMMED, None, None

        cache = cache or _UsbScanCache()
        hub_cids = self._hub_cids(cache)
        ftdi = self._locate_ftdi_usb_device(os.path.realpath(sys_path), hub_cids)

        # Match FTDI to ADB serial using Hub container_id priority
        adb_serial = self._match_ftdi_to_adb(
            ftdi_dir=ftdi.device_dir,
            ftdi_name=os.path.basename(ftdi.device_dir) if ftdi.device_dir else "",
            ftdi_devpath=ftdi.devpath,
            ftdi_container_id=ftdi.container_id,
            ftdi_hub_container_id=ftdi.hub_container_id,
            adb_candidates=self._adb_candidates(cache),
            excluded_serials=excluded_serials,
        )

        if adb_serial:
            logging.info(
                "Matched FTDI port %s (FTDI Serial=%s, PID=0x%04x) to ADB Serial '%s'",
                dev_node,
                ftdi.serial or "NONE",
                ftdi.pid,
                adb_serial,
            )

        return ftdi.serial or None, ftdi.pid, ftdi.container_id or None, adb_serial

    def _relocate_port_by_ftdi_serial(
        self, expected_ftdi_serial: str, target_port: str, cache: _UsbScanCache
    ) -> str:
        """Returns the ttyUSB node whose FTDI serial is expected_ftdi_serial (else target_port)."""
        for dev_node in maui_common.sorted_tty_nodes():
            c_ser, _, _, _ = self._find_ftdi_info_for_port(dev_node, cache=cache)
            if c_ser and c_ser.strip() == expected_ftdi_serial:
                if target_port and target_port != dev_node:
                    logging.info(
                        "FTDI console %s re-enumerated from %s to %s after USB 3.0 MUX",
                        expected_ftdi_serial,
                        target_port,
                        dev_node,
                    )
                _pre_serial_logs.relocate(expected_ftdi_serial, dev_node)
                return dev_node
        return target_port

    def discover_topology_and_serial(
        self,
        request: maui_manufacturing_pb2.MauiTopologyDiscoveryRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiTopologyDiscoveryResponse:
        """Discovers pre-flashed serial from Prolific/ADB chip and verifies FTDI console serial for targeted programming."""
        target_port = request.device_port or ""
        expected_ftdi_serial = (request.ftdi_serial or "").strip()
        logging.info(
            "Starting USB topology discovery (target port: %s, expected FTDI: %s, 5s settling window)...",
            target_port or "any",
            expected_ftdi_serial or "any",
        )

        # If specific target FTDI serial provided, rescan ports to locate where it re-enumerated
        if expected_ftdi_serial:
            target_port = self._relocate_port_by_ftdi_serial(
                expected_ftdi_serial, target_port, _UsbScanCache()
            )

        # If target_port or expected_ftdi_serial is provided: probe specifically
        if target_port or expected_ftdi_serial:
            return self._discover_targeted_topology(target_port, expected_ftdi_serial)
        return self._discover_any_topology()

    def _discover_targeted_topology(
        self, target_port: str, expected_ftdi_serial: str
    ) -> maui_manufacturing_pb2.MauiTopologyDiscoveryResponse:
        """Polls a specific FTDI port (or serial) until its ADB sibling serial is known."""
        for attempt in range(1, TOPOLOGY_DISCOVERY_ATTEMPTS + 1):
            cache = _UsbScanCache()
            # If expected FTDI serial provided, verify target_port or relocate if needed
            if expected_ftdi_serial:
                curr_ser = None
                if target_port:
                    curr_ser, _, _, _ = self._find_ftdi_info_for_port(
                        target_port, cache=cache
                    )
                if not curr_ser or curr_ser.strip() != expected_ftdi_serial:
                    target_port = self._relocate_port_by_ftdi_serial(
                        expected_ftdi_serial, target_port, cache
                    )

            if target_port:
                curr_ser, curr_pid, c_id, adb_ser = self._find_ftdi_info_for_port(
                    target_port, cache=cache
                )
                serial_ok = curr_ser and (
                    not expected_ftdi_serial or curr_ser.strip() == expected_ftdi_serial
                )
                if serial_ok and adb_ser:
                    logging.info(
                        "Discovered topology on attempt %d for %s: ADB Serial=%s, FTDI Current Serial=%s, PID=0x%04x",
                        attempt,
                        target_port,
                        adb_ser,
                        curr_ser,
                        curr_pid,
                    )
                    return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
                        success=True,
                        read_serial_number=adb_ser,
                        usb2_hub_serial="",
                        ftdi_dev_node=target_port,
                        container_id=c_id or "",
                        ftdi_current_serial=curr_ser or "",
                        ftdi_current_pid=curr_pid,
                    )
            time.sleep(TOPOLOGY_RETRY_DELAY_SEC)

        # If port was found and FTDI is confirmed, return FTDI info
        if target_port:
            curr_ser, curr_pid, c_id, adb_ser = self._find_ftdi_info_for_port(
                target_port
            )
            if curr_ser:
                if expected_ftdi_serial and curr_ser.strip() != expected_ftdi_serial:
                    return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
                        success=False,
                        error_message=(
                            f"Port {target_port} has FTDI serial {curr_ser}, but expected {expected_ftdi_serial}."
                        ),
                    )
                return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
                    success=True,
                    read_serial_number=adb_ser or "",
                    ftdi_dev_node=target_port,
                    container_id=c_id or "",
                    ftdi_current_serial=curr_ser or "",
                    ftdi_current_pid=curr_pid,
                    error_message=(
                        ""
                        if adb_ser
                        else (
                            f"FTDI node {target_port} confirmed (serial: {curr_ser}), "
                            "awaiting ADB sibling enumeration."
                        )
                    ),
                )
        return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
            success=False,
            error_message=(
                f"Could not locate FTDI console (expected serial: {expected_ftdi_serial or 'any'}, "
                f"port: {target_port or 'any'})."
            ),
        )

    def _discover_any_topology(
        self,
    ) -> maui_manufacturing_pb2.MauiTopologyDiscoveryResponse:
        """Legacy/single device fallback (only when no target port is specified)."""
        for _ in range(TOPOLOGY_DISCOVERY_ATTEMPTS):
            cache = _UsbScanCache()
            for dev_node in maui_common.sorted_tty_nodes():
                curr_ser, curr_pid, c_id, adb_ser = self._find_ftdi_info_for_port(
                    dev_node, cache=cache
                )
                if adb_ser:
                    return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
                        success=True,
                        read_serial_number=adb_ser,
                        usb2_hub_serial="",
                        ftdi_dev_node=dev_node,
                        container_id=c_id or "",
                        ftdi_current_serial=curr_ser or "",
                        ftdi_current_pid=curr_pid,
                    )
            time.sleep(TOPOLOGY_RETRY_DELAY_SEC)

        return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
            success=False,
            error_message="Could not find any connected Maui board with ADB serial.",
        )

    @staticmethod
    def _count_connected_ftdi_consoles() -> int:
        """Number of connected FTDI 0403:6001/6002 devices."""
        return sum(
            1
            for d in maui_common.scan_usb_devices()
            if maui_common.is_maui_ftdi_endpoint(d.vid, d.pid)
        )

    def program_ftdi_eeprom(
        self,
        request: maui_manufacturing_pb2.MauiProgramFtdiRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiProgramFtdiResponse:
        """Programs FTDI EEPROM targeting specific device with --old-serial-number for multi-device safety."""
        serial_no = (request.serial_number or "").strip()
        dev_node = request.ftdi_dev_node or DEFAULT_TTY
        old_serial_no = (request.old_serial_number or "").strip()

        if not serial_no:
            return maui_manufacturing_pb2.MauiProgramFtdiResponse(
                success=False,
                message="No ADB target serial number provided. Cannot program FTDI EEPROM.",
                logs="Error: serial_number is empty.",
            )

        # If old_serial_no not provided, inspect dev_node directly
        if not old_serial_no and dev_node:
            curr_ser, _, _, _ = self._find_ftdi_info_for_port(dev_node)
            if curr_ser:
                old_serial_no = curr_ser

        # Without --old-serial-number ft232r_prog reprograms the first FTDI chip it finds,
        # which may belong to another board.
        if not old_serial_no:
            ftdi_count = self._count_connected_ftdi_consoles()
            if ftdi_count > 1:
                err = (
                    f"Refusing to program FTDI EEPROM: current FTDI serial of {dev_node} could not be "
                    f"resolved and {ftdi_count} FTDI devices (0403:6001/6002) are connected."
                )
                logging.error(err)
                write_device_log(
                    serial_no, "ftdi_programming", err, status=LogStatus.FAILED
                )
                return maui_manufacturing_pb2.MauiProgramFtdiResponse(
                    success=False, message=err, logs=err
                )

        logging.info(
            "Programming FTDI EEPROM: Target Serial=%s, Port=%s, Old Serial=%s...",
            serial_no,
            dev_node,
            old_serial_no or "ANY",
        )

        ft232r_prog_bin = resolve_maui_tool("ft232r_prog")
        if not ft232r_prog_bin:
            err = "FTDI programming tool (ft232r_prog) not installed on host system."
            logging.error(err)
            write_device_log(
                serial_no, "ftdi_programming", f"ERROR: {err}", status=LogStatus.FAILED
            )
            return maui_manufacturing_pb2.MauiProgramFtdiResponse(
                success=False, message=err, logs=err
            )

        cmd = [ft232r_prog_bin]
        if old_serial_no:
            cmd.extend(["--old-serial-number", old_serial_no])
        cmd.extend(
            [
                "--new-pid",
                f"0x{FTDI_PID_PROGRAMMED:04x}",
                "--new-serial-number",
                serial_no,
            ]
        )
        cmd_str = " ".join(cmd)

        with _ftdi_programming_lock:
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=True,
                    timeout=FTDI_PROGRAM_TIMEOUT_SEC,
                )
            except subprocess.CalledProcessError as e:
                err_logs = str(e.output or "")
                logging.error(
                    "ft232r_prog invocation failed (exit %d):\n%s",
                    e.returncode,
                    err_logs,
                )
                write_device_log(
                    serial_no,
                    "ftdi_programming",
                    f"COMMAND: {cmd_str}\nFAILED (exit {e.returncode}):\n{err_logs}",
                    status=LogStatus.FAILED,
                )
                return maui_manufacturing_pb2.MauiProgramFtdiResponse(
                    success=False,
                    message=f"FTDI EEPROM programming failed: {err_logs}",
                    logs=f"Command ({cmd_str}):\n{err_logs}",
                )
            except (OSError, subprocess.SubprocessError) as e:
                logging.exception("Unexpected error during FTDI programming")
                write_device_log(
                    serial_no,
                    "ftdi_programming",
                    f"EXCEPTION: {e}",
                    status=LogStatus.FAILED,
                )
                return maui_manufacturing_pb2.MauiProgramFtdiResponse(
                    success=False,
                    message=f"FTDI programming error: {str(e)}",
                    logs=str(util.get_logs_from_exception(e) or ""),
                )

            logging.info("ft232r_prog output:\n%s", proc.stdout)
            write_device_log(
                serial_no,
                "ftdi_programming",
                f"COMMAND: {cmd_str}\nSUCCESS:\n{proc.stdout}",
                status=LogStatus.PASSED,
            )
            # Consolidate the logs this board wrote before its serial was known
            # (temp dirs recorded for its console port / initial FTDI serial).
            _merge_pre_serial_logs(
                serial_no, port=dev_node, ftdi_serial=old_serial_no, forget=True
            )

            return maui_manufacturing_pb2.MauiProgramFtdiResponse(
                success=True,
                message=f"FTDI EEPROM programmed successfully with PID 0x6002 and ADB serial {serial_no}.",
                logs=str(proc.stdout or ""),
            )


@dataclasses.dataclass
class _PdcRunResult:
    """Outcome of one maui-flash PDC run."""

    returncode: Optional[int] = None
    output: str = ""


class MauiPdcServicer(maui_manufacturing_pb2_grpc.MauiPdcFlashingServicer):
    """Dedicated multi-device PDC flashing servicer (supports up to 4 parallel 0403:6002 devices)."""

    def __init__(self):
        self._topology = MauiStation1Servicer()

    def discover_connected_pdc_devices(
        self,
        unused_request: empty_pb2.Empty,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiConnectedPdcDevicesResponse:
        """Scans and returns all connected Maui physical units (ADB and FTDI endpoints)."""
        logging.info("Scanning for all connected Maui physical devices (ADB & FTDI)...")
        found_devices = [
            maui_manufacturing_pb2.MauiPdcDeviceInfo(
                slot_index=slot,
                serial_number=u.get("serial", "") or "",
                device_port=u.get("dev_node", "") or "",
                container_id=u.get("container_id", "") or "",
                product_description=u.get("product", "Maui Device"),
            )
            for slot, u in enumerate(maui_common.scan_connected_maui_units(), start=1)
        ]

        logging.info(
            "Found %d connected physical Maui device(s) (ADB/FTDI).", len(found_devices)
        )
        return maui_manufacturing_pb2.MauiConnectedPdcDevicesResponse(
            success=True,
            devices=found_devices,
            message=f"Found {len(found_devices)} connected Maui device(s).",
        )

    @staticmethod
    def _progress_response(
        line: str, status_label: str, slot_index: int, serial_no: str
    ) -> Optional[maui_manufacturing_pb2.MauiProgramPdcProgressResponse]:
        """Converts a maui-flash 'Block N: Progress' line into a progress response."""
        match = _PDC_PROGRESS_RE.search(line)
        if not match:
            return None
        block_num = int(match.group(1))
        transferred = int(match.group(2))
        total_bytes = int(match.group(3))
        pct_in_block = float(match.group(4))

        overall_pct = int(
            ((block_num + (pct_in_block / 100.0)) / PDC_TOTAL_BLOCKS) * 100.0
        )
        overall_pct = min(99, max(0, overall_pct))
        return maui_manufacturing_pb2.MauiProgramPdcProgressResponse(
            is_complete=False,
            progress_percent=overall_pct,
            current_block=f"Block {block_num}: {transferred}/{total_bytes} bytes",
            status_message=f"{status_label}... {overall_pct}%",
            slot_index=slot_index,
            serial_number=serial_no,
        )

    async def _stream_pdc_flash(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        cmd: List[str],
        env: Dict[str, str],
        context: grpc.ServicerContext,
        slot_index: int,
        serial_no: str,
        status_label: str,
        result: _PdcRunResult,
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiProgramPdcProgressResponse, None]:
        """Runs maui-flash once, yielding progress; stores exit code and output in `result`."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        tool_output: List[str] = []

        while proc.returncode is None and not context.done():
            try:
                line_bytes = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=PDC_READLINE_TIMEOUT_SEC
                )
            except asyncio.TimeoutError:
                logging.warning("[Slot %d] PDC Output readline timed out", slot_index)
                break
            if not line_bytes:
                break
            raw_line = line_bytes.decode("utf-8", errors="ignore")
            tool_output.append(raw_line)
            line = raw_line.strip()
            logging.debug("[Slot %d] PDC Output: %s", slot_index, line)
            progress = self._progress_response(
                line, status_label, slot_index, serial_no
            )
            if progress is not None:
                yield progress

        try:
            await asyncio.wait_for(proc.wait(), timeout=PDC_EXIT_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logging.warning("[Slot %d] Killing hung PDC flash process", slot_index)
            proc.kill()
            await proc.wait()

        # Drain remaining output if any
        try:
            rest_bytes = await proc.stdout.read()
            if rest_bytes:
                tool_output.append(rest_bytes.decode("utf-8", errors="ignore"))
        except (OSError, ValueError) as e:
            logging.warning(
                "[Slot %d] Could not drain PDC tool output: %s", slot_index, e
            )

        result.returncode = proc.returncode
        result.output = "".join(tool_output).strip()

    async def _resolve_pdc_serial(
        self, serial_no: str, dev_port: str, requested_port: str, slot_index: int
    ) -> str:
        """ADB serial of the board on dev_port, else serial_no or MAUI-SLOT<slot>.

        A MAUI-SLOT<n> log dir is recorded for the board on `requested_port`;
        once the serial is known, the board's earlier temp dir logs are merged.
        """
        if not serial_no or serial_no.startswith("MAUI-SLOT") or "UNKNOWN" in serial_no:
            ftdi_ser, _, _, adb_ser = await asyncio.to_thread(
                self._topology._find_ftdi_info_for_port,  # pylint: disable=protected-access
                dev_port,
            )
            serial_no = adb_ser or serial_no or f"MAUI-SLOT{slot_index}"
            if serial_no.startswith("MAUI-SLOT"):
                # Merged into the serial's dir once the FTDI EEPROM is programmed.
                _pre_serial_logs.add_dir(
                    serial_no, requested_port, ftdi_serial=ftdi_ser or ""
                )
        # Once the serial is known, earlier logs of this board (MCU flash under
        # PORT_<tty>) join the serial's dir.
        _merge_pre_serial_logs(serial_no, port=requested_port)
        return serial_no

    async def program_single_pdc(  # pylint: disable=too-many-locals
        self,
        request: maui_manufacturing_pb2.MauiProgramSinglePdcRequest,
        context: grpc.ServicerContext,
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiProgramPdcProgressResponse, None]:
        """Flashes PDC firmware on a single device and streams block transfer progress."""
        slot_index = request.slot_index or 1
        serial_no = (request.serial_number or "").strip()
        dev_port = request.device_port or DEFAULT_TTY
        fw_channel = request.fw_channel or "stable"

        def failure(
            block: str, status: str, error: str = ""
        ) -> maui_manufacturing_pb2.MauiProgramPdcProgressResponse:
            return maui_manufacturing_pb2.MauiProgramPdcProgressResponse(
                is_complete=True,
                progress_percent=0,
                current_block=block,
                status_message=status,
                error_message=error,
                slot_index=slot_index,
                serial_number=serial_no,
            )

        serial_no = await self._resolve_pdc_serial(
            serial_no, dev_port, request.device_port, slot_index
        )

        logging.info(
            "Starting PDC flash for slot %d (serial: %s, port: %s, channel: %s)...",
            slot_index,
            serial_no,
            dev_port,
            fw_channel,
        )

        port_exists = bool(dev_port) and os.path.exists(dev_port)
        common_args = (
            ["--port", dev_port]
            if port_exists
            else (["--serial", serial_no] if serial_no else [])
        )

        offline_fw = find_offline_fw_file("pdc")
        if offline_fw and fw_channel in ("offline", "stable"):
            logging.info(
                "Found local offline PDC firmware: %s. Using --file.", offline_fw
            )
            pdc_flash_args = ["pdc", "--file", offline_fw]
        elif fw_channel == "offline":
            logging.error(
                "[Slot %d] Offline PDC firmware file not found in firmware cache directories.",
                slot_index,
            )
            yield failure(
                "Failed",
                "Offline PDC firmware binary not found.",
                "Error: Local PDC firmware (.bin) not found in /usr/local/server/firmware/pdc or "
                "/var/lib/diagnoseme/firmware/pdc.",
            )
            return
        else:
            pdc_flash_args = ["pdc", "--fw_channel", fw_channel]

        maui_flash = resolve_maui_flash_command(include_script=False)
        if not maui_flash:
            err = "PDC flashing tool (maui-flash) not installed on host system."
            logging.error("[Slot %d] %s", slot_index, err)
            write_device_log(
                serial_no, "pdc_flashing", f"ERROR: {err}", status=LogStatus.FAILED
            )
            yield failure("Failed", err, err)
            return

        cmd = maui_flash + common_args + pdc_flash_args
        cmd_str = " ".join(cmd)

        # Ensure Zephyr shell on dev_port is responsive before launching maui-flash
        if port_exists:
            await asyncio.to_thread(
                ensure_zephyr_shell_awake, dev_port, PDC_WAKE_TIMEOUT_SEC
            )

        try:
            env = get_maui_env()
            result = _PdcRunResult()
            async for resp in self._stream_pdc_flash(
                cmd,
                env,
                context,
                slot_index,
                serial_no,
                "Flashing PDC Firmware",
                result,
            ):
                yield resp

            # If first attempt failed with prompt timeout, perform active wakeup and 1x auto-retry
            if result.returncode != 0 and any(
                m in result.output for m in PDC_PROMPT_TIMEOUT_MARKERS
            ):
                logging.warning(
                    "[Slot %d] PDC flash encountered prompt timeout on %s. Re-awakening Zephyr console and retrying...",
                    slot_index,
                    dev_port,
                )
                if dev_port and os.path.exists(dev_port):
                    await asyncio.to_thread(
                        ensure_zephyr_shell_awake, dev_port, PDC_RETRY_WAKE_TIMEOUT_SEC
                    )
                await asyncio.sleep(PDC_RETRY_DELAY_SEC)
                retry_result = _PdcRunResult()
                async for resp in self._stream_pdc_flash(
                    cmd,
                    env,
                    context,
                    slot_index,
                    serial_no,
                    "Flashing PDC Firmware (Retry)",
                    retry_result,
                ):
                    yield resp
                if retry_result.returncode == 0:
                    result = retry_result

            if result.returncode == 0:
                logging.info(
                    "[Slot %d] PDC Firmware update completed successfully.", slot_index
                )
                write_device_log(
                    serial_no,
                    "pdc_flashing",
                    f"COMMAND: {cmd_str}\nEXIT_CODE: 0\n\nTOOL_OUTPUT:\n{result.output}",
                    status=LogStatus.PASSED,
                )
                yield maui_manufacturing_pb2.MauiProgramPdcProgressResponse(
                    is_complete=True,
                    progress_percent=100,
                    current_block="Complete",
                    status_message="PDC Firmware update succeeded!",
                    slot_index=slot_index,
                    serial_number=serial_no,
                )
            else:
                logging.error(
                    "[Slot %d] PDC Firmware update failed with exit code %s",
                    slot_index,
                    result.returncode,
                )
                write_device_log(
                    serial_no,
                    "pdc_flashing",
                    f"COMMAND: {cmd_str}\nEXIT_CODE: {result.returncode}\n\nTOOL_OUTPUT:\n{result.output}",
                    status=LogStatus.FAILED,
                )
                yield failure(
                    "Failed",
                    "PDC Firmware update failed.",
                    f"Process exited with code {result.returncode}",
                )
        except (OSError, ValueError, RuntimeError) as e:
            logging.exception("[Slot %d] PDC Firmware update exception", slot_index)
            write_device_log(
                serial_no,
                "pdc_flashing",
                f"COMMAND: {cmd_str}\nRESULT: EXCEPTION\nERROR: {e}",
                status=LogStatus.FAILED,
            )
            yield failure("Error", str(e), str(e))

    async def program_pdc(
        self,
        request: maui_manufacturing_pb2.MauiProgramPdcRequest,
        context: grpc.ServicerContext,
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiProgramPdcProgressResponse, None]:
        """Legacy single-device PDC flashing proxy."""
        single_req = maui_manufacturing_pb2.MauiProgramSinglePdcRequest(
            slot_index=1,
            serial_number="",
            device_port="",
            fw_channel=request.fw_channel or "stable",
        )
        async for resp in self.program_single_pdc(single_req, context):
            yield resp


# ==========================================
# Log analysis
# ==========================================
# (stage key, log file, label) of the Step logs evaluated for the final verdict.
STAGE_LOGS = (
    ("mcu_bsl_flashing", "mcu_flashing.log", "MCU BSL Flashing"),
    ("ftdi_eeprom_programming", "ftdi_programming.log", "FTDI EEPROM Programming"),
    ("pdc_fw_flashing", "pdc_flashing.log", "PDC FW Flashing"),
    ("host_only_verification", "host_only_tests.log", "Host-Only Verification"),
    ("host_dut_verification", "host_dut_tests.log", "Host + DUT Verification"),
)
# Auxiliary subtest logs (BSL button, RESET button, Host replug) that can fail Host-Only Verification.
HOST_ONLY_AUX_LOGS = (
    "bsl_button_test.log",
    "reset_button_test.log",
    "host_replug_test.log",
    "host_usb_check.log",
)
# Per-stage Host+DUT logs (host_dut_<stage>.log, incl. legacy stage aliases).
HOST_DUT_AUX_LOGS = (
    "host_dut_stage1_with_charger.log",
    "host_dut_stage2_sbu_flip.log",
    "host_dut_stage3_no_charger.log",
    "host_dut_stage1_no_charger.log",
    "host_dut_stage2_with_charger.log",
    "host_dut_stage3_sbu_flip.log",
)
LABEL_LOG = "label_verification.log"

# Log STATUS -> (stage verdict, details).
_STATUS_VERDICTS = {
    LogStatus.PASSED: ("PASSED", "Completed successfully"),
    LogStatus.BYPASSED: ("BYPASSED", "Bypassed by operator"),
    LogStatus.FAILED: ("FAILED", "Stage reported test/command failure"),
    LogStatus.TIMEOUT: ("FAILED", "Stage timed out"),
    LogStatus.NOT_RUN: ("NOT_RUN", "Stage reported as not run"),
}
_MISSING_LOG_VERDICT = ("NOT_RUN", "Log file not found (stage not executed)")
_UNKNOWN_STATUS_VERDICT = ("FAILED", "Log has no valid machine-readable STATUS line")


def _read_log_file(path: str) -> Optional[str]:
    """Returns the content of a non-empty log file, None if missing/empty, "" if unreadable."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError as e:
        logging.warning("Could not read log %s: %s", path, e)
        return ""


def _log_verdict(content: Optional[str]) -> Tuple[str, str]:
    """(verdict, details) of a Step log, parsed only from its STATUS line."""
    if content is None:
        return _MISSING_LOG_VERDICT
    status = maui_common.parse_log_status(content)
    return (
        _STATUS_VERDICTS.get(status, _UNKNOWN_STATUS_VERDICT)
        if status
        else _UNKNOWN_STATUS_VERDICT
    )


def _aux_log_failed(content: Optional[str]) -> bool:
    """True when a present auxiliary subtest log did not report PASSED/BYPASSED/NOT_RUN."""
    if content is None:
        return False
    return maui_common.parse_log_status(content) not in (
        LogStatus.PASSED,
        LogStatus.BYPASSED,
        LogStatus.NOT_RUN,
    )


def _overall_verdict(stage_statuses: Dict[str, str]) -> str:
    if any(v == "FAILED" for v in stage_statuses.values()):
        return "FAIL"
    if any("BYPASSED" in v for v in stage_statuses.values()):
        return "PASS_WITH_BYPASS"
    return "PASS"


def _merge_ui_stage_statuses(
    log_statuses: Dict[str, str], ui_statuses: Mapping[str, str]
) -> Dict[str, str]:
    """Merges the per-step statuses reported by the UI into the log verdicts.

    Rules per step (UI value -> merged result):
      - FAILED (or TIMEOUT, normalised to FAILED) always wins.
      - BYPASSED overrides a log PASSED / NOT_RUN / missing log, but never a
        log FAILED.
      - PASSED / NOT_RUN is adopted only when the log is missing or NOT_RUN
        (e.g. Step 1 logs written before the serial was known); otherwise the
        log verdict stays authoritative.
    Unknown UI values are ignored.
    """
    merged = dict(log_statuses)
    for key, raw_value in ui_statuses.items():
        value = (raw_value or "").strip().upper()
        if value not in LogStatus.ALL:
            continue
        if value == LogStatus.TIMEOUT:
            value = LogStatus.FAILED
        current = merged.get(key)
        if value == LogStatus.FAILED:
            merged[key] = LogStatus.FAILED
        elif value == LogStatus.BYPASSED:
            if current != LogStatus.FAILED:
                merged[key] = LogStatus.BYPASSED
        elif current in (None, LogStatus.NOT_RUN):
            merged[key] = value
    return merged


def analyze_device_manufacturing_logs(
    serial_number: str,
) -> Tuple[Dict[str, str], str, List[maui_manufacturing_pb2.MauiLogFileInfo], int]:
    """Parses the device log dir <device log base>/<serial> (read-only) to evaluate stage statuses.

    Each Step log is judged only by its machine-readable STATUS line; a log without
    a valid STATUS line counts as FAILED, a missing log as NOT_RUN.

    Returns:
      (stage_statuses, overall_verdict, log_file_infos, present_count)
    """
    dir_path = maui_common.device_log_dir_path(serial_number)
    stage_statuses: Dict[str, str] = {}
    log_file_infos: List[maui_manufacturing_pb2.MauiLogFileInfo] = []
    present_count = 0

    for stage_key, log_name, _ in STAGE_LOGS:
        fpath = os.path.join(dir_path, log_name)
        content = _read_log_file(fpath)
        if content is not None:
            present_count += 1
        verdict, details = _log_verdict(content)
        stage_statuses[stage_key] = verdict
        log_file_infos.append(
            maui_manufacturing_pb2.MauiLogFileInfo(
                log_name=log_name,
                file_path=fpath,
                file_size_bytes=os.path.getsize(fpath) if os.path.exists(fpath) else 0,
                is_present=content is not None,
                stage_verdict=verdict,
                details=details,
            )
        )

    # Auxiliary subtest logs can fail their parent stage.
    for stage_key, stage_log, aux_logs, label in (
        ("host_only_verification", "host_only_tests.log", HOST_ONLY_AUX_LOGS, "Host"),
        ("host_dut_verification", "host_dut_tests.log", HOST_DUT_AUX_LOGS, "DUT"),
    ):
        if stage_statuses.get(stage_key) == "FAILED":
            continue
        for aux_log in aux_logs:
            if _aux_log_failed(_read_log_file(os.path.join(dir_path, aux_log))):
                stage_statuses[stage_key] = "FAILED"
                for info in log_file_infos:
                    if info.log_name == stage_log:
                        info.stage_verdict = "FAILED"
                        info.details = f"{label} subtest failed in {aux_log}"
                break

    # Check label_verification.log if present
    lbl_content = _read_log_file(os.path.join(dir_path, LABEL_LOG))
    if lbl_content is None:
        stage_statuses["label_verified"] = "NOT_RUN"
    else:
        lbl_status = maui_common.parse_log_status(lbl_content)
        if lbl_status in (LogStatus.PASSED, LogStatus.BYPASSED, LogStatus.NOT_RUN):
            stage_statuses["label_verified"] = lbl_status
        else:
            stage_statuses["label_verified"] = "FAILED"

    # Result is evaluated strictly from available logs (missing stages are acceptable)
    return (
        stage_statuses,
        _overall_verdict(stage_statuses),
        log_file_infos,
        present_count,
    )


_CARD_LOG_STATUS_RE = re.compile(
    r"^STATUS:\s*(" + "|".join(LogStatus.ALL) + r")\s*$", re.MULTILINE
)


# Card log verdict words -> STATUS (anything else is FAILED).
_CARD_VERDICT_STATUSES = {
    "PASS": LogStatus.PASSED,
    "PASSED": LogStatus.PASSED,
    "PROVISIONED": LogStatus.PASSED,
    "SUCCESS": LogStatus.PASSED,
    "TRUE": LogStatus.PASSED,
    "BYPASSED": LogStatus.BYPASSED,
    "PASS_WITH_BYPASS": LogStatus.BYPASSED,
    "TIMEOUT": LogStatus.TIMEOUT,
    "NOT_RUN": LogStatus.NOT_RUN,
    "IDLE": LogStatus.NOT_RUN,
}
# Verdict lines of UI card logs, in precedence order: summary verdict, ALL_PASSED
# flag, Step 1 slot record "Status: <slot stage>".
_CARD_VERDICT_RES = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"^\s*OVERALL RESULT:\s*([A-Z_]+)",
        r"^\s*ALL_PASSED=(\w+)",
        r"^\s*Status:\s*([A-Z_]+)",
    )
)


def derive_card_log_status(content: str) -> str:
    """Derives the STATUS of a log saved from UI card results (save_card_log)."""
    explicit = _CARD_LOG_STATUS_RE.search(content or "")
    if explicit:
        return explicit.group(1)
    for verdict_re in _CARD_VERDICT_RES:
        match = verdict_re.search(content or "")
        if match:
            return _CARD_VERDICT_STATUSES.get(match.group(1).upper(), LogStatus.FAILED)
    return LogStatus.FAILED


# Final submission phases: (station_id markers, results subfolder, file prefix).
_SUBMISSION_PHASES = (
    (("STEP1", "PROVISIONING"), "smt_provisioning", "SMT_PROVISIONING"),
    (("FATP", "QT0"), "fatp_tests", "FATP_TESTS"),
)
_DEFAULT_SUBMISSION_PHASE = ("smt_functional", "SMT_FUNCTIONAL")


class MauiDeviceManagementServicer(
    maui_manufacturing_pb2_grpc.MauiDeviceManagementServicer
):
    """Servicer for inspecting logs, label verification, and final manufacturing submission."""

    def read_connected_device_serial(
        self,
        unused_request: empty_pb2.Empty,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiReadSerialResponse:
        """Reads the ADB and FTDI endpoint serials of the connected Maui board."""
        adb_serial = ""
        adb_product = ""
        adb_connected = False

        ftdi_serial = ""
        ftdi_product = ""
        ftdi_port = ""
        ftdi_connected = False
        ftdi_pid_val = 0
        ftdi_is_prog = False

        devices = maui_common.scan_usb_devices()

        # 1. Scan for ADB endpoint: VID 18d1, PID 506d (or 5084) or Prolific 067b:27a1
        for dev in devices:
            if (
                dev.vid == maui_common.GOOGLE_VID
                and dev.pid in maui_common.GOOGLE_ADB_PIDS
            ):
                adb_connected = True
                adb_serial = dev.serial or adb_serial
                adb_product = dev.product or "ADB v1"
                logging.info(
                    "Found ADB Endpoint: Serial=%s, Product=%s (VID=18d1, PID=%04x)",
                    dev.serial,
                    dev.product,
                    dev.pid,
                )
                if adb_serial:
                    break
            elif (dev.vid, dev.pid) == (
                maui_common.PROLIFIC_VID,
                maui_common.PROLIFIC_PID_H2H,
            ):
                adb_connected = True
                adb_serial = dev.serial or adb_serial
                adb_product = dev.product or "Prolific H2H"
                logging.info(
                    "Found Prolific Endpoint: Serial=%s, Product=%s",
                    dev.serial,
                    dev.product,
                )
                if adb_serial:
                    break

        # 2. Scan for FTDI endpoint: VID 0403, PID 6002 (or 6001)
        for dev in devices:
            if maui_common.is_maui_ftdi_endpoint(dev.vid, dev.pid):
                ftdi_connected = True
                ftdi_pid_val = dev.pid
                ftdi_is_prog = dev.pid == FTDI_PID_PROGRAMMED
                ftdi_serial = dev.serial
                ftdi_product = dev.product or (
                    "FT232R Programmed" if ftdi_is_prog else "FT232R Default"
                )
                ftdi_port = dev.dev_node
                logging.info(
                    "Found FTDI Endpoint: Serial=%s, Product=%s, Port=%s (PID=0x%04x, Programmed=%s)",
                    dev.serial,
                    ftdi_product,
                    ftdi_port,
                    ftdi_pid_val,
                    ftdi_is_prog,
                )
                break

        if not ftdi_port:
            avail_ttys = maui_common.sorted_tty_nodes()
            if avail_ttys:
                ftdi_port = avail_ttys[0]

        dual_match = bool(
            adb_serial
            and ftdi_serial
            and (adb_serial.upper() == ftdi_serial.upper())
            and ftdi_is_prog
        )
        primary_serial = adb_serial or ftdi_serial

        if adb_connected and ftdi_connected:
            if ftdi_is_prog and dual_match:
                msg = f"Programmed Maui Board: {primary_serial}"
            elif ftdi_pid_val == FTDI_PID_UNPROGRAMMED:
                msg = f"Unprogrammed Maui Board (FTDI PID 0x6001). Target Serial: '{primary_serial}'"
            else:
                msg = f"Connected Serial: {primary_serial}"
        elif adb_connected:
            msg = f"Maui ADB Device: {primary_serial}"
        elif ftdi_connected:
            msg = f"Maui FTDI Device: {primary_serial}"
        else:
            msg = "No Device Detected"

        return maui_manufacturing_pb2.MauiReadSerialResponse(
            is_connected=bool(adb_connected or ftdi_connected),
            serial_number=primary_serial,
            device_port=ftdi_port,
            product_name=adb_product or ftdi_product or "Maui Device",
            message=msg,
            adb_serial=adb_serial,
            adb_connected=adb_connected,
            adb_product=adb_product,
            ftdi_serial=ftdi_serial,
            ftdi_connected=ftdi_connected,
            ftdi_product=ftdi_product,
            dual_endpoints_match=dual_match,
            ftdi_pid=ftdi_pid_val,
            ftdi_is_programmed=ftdi_is_prog,
        )

    def get_device_logs_status(
        self,
        request: maui_manufacturing_pb2.MauiLogsStatusRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiLogsStatusResponse:
        """Inspects the device log directory (read-only) and evaluates true stage verdicts."""
        serial_no = request.serial_number
        if not serial_no:
            conn_resp = self.read_connected_device_serial(
                empty_pb2.Empty(), unused_context
            )
            serial_no = conn_resp.serial_number if conn_resp.is_connected else "UNKNOWN"

        dir_path = maui_common.device_log_dir_path(serial_no)
        logging.info(
            "Analyzing manufacturing logs for serial %s at %s...", serial_no, dir_path
        )

        stage_statuses, overall_verdict, log_infos, present_count = (
            analyze_device_manufacturing_logs(serial_no)
        )

        return maui_manufacturing_pb2.MauiLogsStatusResponse(
            directory_exists=os.path.exists(dir_path),
            directory_path=dir_path,
            logs=log_infos,
            all_required_logs_present=overall_verdict != "FAIL",
            message=f"{present_count} stage logs present in {dir_path}. Overall log status: {overall_verdict}",
            stage_statuses=stage_statuses,
            overall_verdict=overall_verdict,
        )

    def save_card_log(
        self,
        request: maui_manufacturing_pb2.MauiSaveLogRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiSaveLogResponse:
        """Writes logs for a specific card into <device log base>/<serial>/<step>.log.

        A STATUS line derived from the card content is prepended.
        """
        serial_no = request.serial_number or "UNKNOWN"
        step_name = request.log_step_name or "card_execution"
        content = request.log_content or "No output"
        saved_path = write_device_log(
            serial_no, step_name, content, status=derive_card_log_status(content)
        )
        return maui_manufacturing_pb2.MauiSaveLogResponse(
            success=bool(saved_path),
            saved_file_path=saved_path,
            message=(
                f"Log saved to {saved_path}"
                if saved_path
                else f"Failed to save log '{step_name}'"
            ),
        )

    def verify_scanned_label(
        self,
        request: maui_manufacturing_pb2.MauiVerifyLabelRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiVerifyLabelResponse:
        """Verifies scanned barcode strictly against BOTH USB hardware serials (ADB & FTDI)."""
        conn_resp = self.read_connected_device_serial(empty_pb2.Empty(), unused_context)
        adb_serial = (request.adb_serial or conn_resp.adb_serial or "").strip().upper()
        ftdi_serial = (
            (request.ftdi_serial or conn_resp.ftdi_serial or "").strip().upper()
        )
        electronic_serial = (
            (
                request.electronic_serial
                or conn_resp.serial_number
                or adb_serial
                or ftdi_serial
                or ""
            )
            .strip()
            .upper()
        )
        scanned = (request.scanned_barcode or "").strip().upper()

        adb_matches = bool(adb_serial and scanned and (adb_serial == scanned))
        ftdi_matches = bool(ftdi_serial and scanned and (ftdi_serial == scanned))
        dual_match = bool(adb_serial and ftdi_serial and (adb_serial == ftdi_serial))

        # All match if both endpoints exist, match each other, and match scanned barcode
        all_match = bool(
            adb_serial and ftdi_serial and dual_match and (scanned == adb_serial)
        )

        if all_match:
            msg = (
                f"Verification PASSED: Scanned barcode '{scanned}' matches both ADB Endpoint "
                f"({adb_serial}) and FTDI Endpoint ({ftdi_serial})."
            )
        elif not adb_serial and not ftdi_serial:
            msg = "Verification FAILED: No connected Maui USB hardware (ADB/FTDI) detected on USB bus."
        elif not dual_match and adb_serial and ftdi_serial:
            msg = (
                f"Verification FAILED (Hardware Endpoint Mismatch): ADB Serial '{adb_serial}' "
                f"does not match FTDI Serial '{ftdi_serial}'."
            )
        elif adb_serial and not ftdi_serial:
            msg = (
                f"Verification FAILED: FTDI endpoint (VID 0403, PID 6002) not detected. "
                f"ADB Serial is '{adb_serial}'."
            )
        elif ftdi_serial and not adb_serial:
            msg = (
                f"Verification FAILED: ADB endpoint (VID 18d1, PID 506d) not detected. "
                f"FTDI Serial is '{ftdi_serial}'."
            )
        else:
            msg = (
                f"Verification FAILED (Sticker Mismatch): Scanned barcode '{scanned}' does not match "
                f"hardware serial '{electronic_serial}'."
            )

        # Automatically save label verification record to log directory
        log_content = (
            f"Label Verification Result: {'PASSED' if all_match else 'FAILED'}\n"
            f"Scanned Barcode: '{scanned}'\n"
            f"ADB Serial: '{adb_serial}'\n"
            f"FTDI Serial: '{ftdi_serial}'\n"
            f"Electronic Serial: '{electronic_serial}'\n"
            f"Message: {msg}\n"
        )
        target_serial = electronic_serial or adb_serial or ftdi_serial or "UNKNOWN"
        write_device_log(
            target_serial,
            "label_verification",
            log_content,
            status=LogStatus.PASSED if all_match else LogStatus.FAILED,
        )

        return maui_manufacturing_pb2.MauiVerifyLabelResponse(
            matches=all_match,
            electronic_serial=electronic_serial,
            scanned_barcode=scanned,
            adb_serial=adb_serial,
            ftdi_serial=ftdi_serial,
            adb_matches=adb_matches,
            ftdi_matches=ftdi_matches,
            dual_endpoints_match=dual_match,
            message=msg,
        )

    @staticmethod
    def _submission_phase(station_id: str) -> Tuple[str, str]:
        """(results subfolder, file prefix) for a station ID."""
        station_id_str = (station_id or "").upper()
        for markers, subfolder, prefix in _SUBMISSION_PHASES:
            if any(m in station_id_str for m in markers):
                return subfolder, prefix
        return _DEFAULT_SUBMISSION_PHASE

    @staticmethod
    def _combined_log(dir_path: str, header_lines: List[str]) -> str:
        """Builds one plain text log from all individual Step logs in dir_path."""
        combined_log_lines = list(header_lines)
        if os.path.isdir(dir_path):
            for root, _, files in os.walk(dir_path):
                for f in sorted(files):
                    if (
                        not f.endswith(".log")
                        or f.startswith("SMT_")
                        or f.startswith("FATP_")
                    ):
                        continue
                    fpath = os.path.join(root, f)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as lf:
                            content = lf.read().strip()
                    except OSError as e:
                        logging.warning(
                            "Could not read %s for combined log: %s", fpath, e
                        )
                        continue
                    if content:
                        combined_log_lines.extend(
                            [f"--- LOG STEP: {f} ---", content, ""]
                        )
        return "\n".join(combined_log_lines) + "\n"

    @staticmethod
    def _write_result_file(path: str, content: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(path, maui_common.RESULTS_FILE_MODE)

    @staticmethod
    def _submission_message(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        base_name: str,
        dir_path: str,
        local_error: str,
        export_error: str,
        base_dir: str,
        subfolder_name: str,
    ) -> str:
        """Operator message of the final submission (names every failed write)."""
        if local_error:
            message = (
                f"Results of {base_name} could not be saved in the device log "
                f"directory {dir_path}: {local_error}"
            )
            if export_error:
                message += f"; export to {base_dir} failed: {export_error}"
            return message
        if export_error:
            return (
                f"Results recorded locally ({base_name}) but export to {base_dir} "
                f"failed: {export_error}"
            )
        return (
            f"Results recorded ({base_name}). Exported JSON to {base_dir}/{subfolder_name}/results/"
            f"{base_name}.json and logs to {base_dir}/{subfolder_name}/logs/{base_name}.log."
        )

    def submit_final_manufacturing_results(  # pylint: disable=too-many-locals
        self,
        request: maui_manufacturing_pb2.MauiFinalSubmissionRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiFinalSubmissionResponse:
        """Generates authoritative final results, [SerialNumber]_[Date]_[PASS/FAIL] packages, and exports
        them to the results directory ($DIAGNOSEME_RESULTS_DIR).
        """
        serial_no = request.serial_number or "UNKNOWN"
        dir_path = maui_common.device_log_dir_path(serial_no)

        # Authoritatively analyze real log files from the device log directory
        stage_statuses, _, log_infos, _ = analyze_device_manufacturing_logs(serial_no)

        # If label verification was explicitly provided in request, update label_verified status
        if request.label_verified or (
            request.scanned_barcode
            and request.adb_serial
            and request.scanned_barcode.upper() == request.adb_serial.upper()
        ):
            stage_statuses["label_verified"] = "PASSED"
        else:
            stage_statuses["label_verified"] = stage_statuses.get(
                "label_verified", "BYPASSED"
            )

        # The UI reports bypassed / failed steps the logs cannot show.
        stage_statuses = _merge_ui_stage_statuses(
            stage_statuses, request.stage_statuses
        )

        # Re-evaluate overall verdict based on available stage_statuses (missing sections acceptable)
        overall_status = _overall_verdict(stage_statuses)
        pass_fail_tag = (
            "PASS" if overall_status in ("PASS", "PASS_WITH_BYPASS") else "FAIL"
        )
        ts_str = time.strftime("%Y%m%d_%H%M%S")

        station_id_str = (request.station_id or "").upper()
        subfolder_name, prefix = self._submission_phase(station_id_str)
        base_name = f"{prefix}_{serial_no}_{ts_str}_{pass_fail_tag}"

        adb_ser = (request.adb_serial or "").upper()
        ftdi_ser = (request.ftdi_serial or "").upper()
        if "FATP" in station_id_str:
            serials_matched = bool(
                adb_ser
                and ftdi_ser
                and request.scanned_barcode
                and adb_ser == ftdi_ser == request.scanned_barcode.upper()
            )
        else:
            serials_matched = bool(adb_ser and ftdi_ser and adb_ser == ftdi_ser)

        results_dict = {
            "serial_number": serial_no,
            "operator_id": request.operator_id or "OP_FACTORY",
            "station_id": request.station_id or prefix,
            "phase": subfolder_name,
            "completion_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "overall_status": overall_status,
            "verdict_tag": base_name,
            "endpoints": {
                "adb_serial": request.adb_serial or "",
                "ftdi_serial": request.ftdi_serial or "",
                "scanned_barcode": request.scanned_barcode or "",
                "serials_matched": serials_matched,
            },
            "stage_statuses": stage_statuses,
            "log_files": [
                {
                    "name": info.log_name,
                    "size_bytes": info.file_size_bytes,
                    "verdict": info.stage_verdict,
                    "details": info.details,
                }
                for info in log_infos
            ],
        }
        results_json = json.dumps(results_dict, indent=2)

        # 1. Build combined plain text log from all individual step logs in dir_path
        combined_log_str = self._combined_log(
            dir_path,
            [
                "=================================================================",
                f" DiagnoseMe Manufacturing Log: {base_name}",
                f" Serial Number: {serial_no}",
                f" Station: {request.station_id or prefix}",
                f" Operator: {request.operator_id or 'OP_FACTORY'}",
                f" Completion Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
                f" Overall Status: {overall_status}",
                "=================================================================\n",
            ],
        )

        # 2. Save local copies in the device log directory
        named_summary_path = os.path.join(dir_path, f"{base_name}.json")
        local_error = ""
        try:
            os.makedirs(dir_path, mode=maui_common.RESULTS_DIR_MODE, exist_ok=True)
            self._write_result_file(
                os.path.join(dir_path, "final_results.json"), results_json
            )
            self._write_result_file(named_summary_path, results_json)
            self._write_result_file(
                os.path.join(dir_path, f"{base_name}.log"), combined_log_str
            )
        except OSError as e:
            local_error = str(e)
            logging.exception(
                "Failed to write local result files in %s: %s", dir_path, e
            )

        # 3. Export to <results dir>/<subfolder_name>/results/ and logs/
        base_dir = maui_common.get_results_base_dir()
        export_error = ""
        try:
            phase_dir = os.path.join(base_dir, subfolder_name)
            results_dir = os.path.join(phase_dir, "results")
            logs_dir = os.path.join(phase_dir, "logs")
            os.makedirs(results_dir, mode=maui_common.RESULTS_DIR_MODE, exist_ok=True)
            os.makedirs(logs_dir, mode=maui_common.RESULTS_DIR_MODE, exist_ok=True)
            self._write_result_file(
                os.path.join(results_dir, f"{base_name}.json"), results_json
            )
            self._write_result_file(
                os.path.join(logs_dir, f"{base_name}.log"), combined_log_str
            )
        except OSError as e:
            export_error = str(e)
            logging.error("Could not write to results directory %s: %s", base_dir, e)

        tracking_id = f"MES-{base_name}"
        logging.info(
            "Submitted final manufacturing record for %s -> Tracking ID: %s (Overall: %s, Stages: %s)",
            serial_no,
            tracking_id,
            overall_status,
            stage_statuses,
        )

        message = self._submission_message(
            base_name, dir_path, local_error, export_error, base_dir, subfolder_name
        )
        return maui_manufacturing_pb2.MauiFinalSubmissionResponse(
            success=not (local_error or export_error),
            summary_file_path="" if local_error else named_summary_path,
            mes_tracking_id=tracking_id,
            message=message,
        )
