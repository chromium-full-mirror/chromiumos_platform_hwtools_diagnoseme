# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""MauiMcuService: Maui v1.2 (ITE IT82002) MCU status, serial and updates."""

# Generated protobuf members are not visible to pylint; the aio servicer
# implements some RPCs as async generators.
# pylint: disable=no-member,invalid-overridden-method

import asyncio
import collections
import contextlib
import dataclasses
import glob
import json
import logging
import os
import re
import threading
import time
from typing import Any
from typing import AsyncGenerator
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from google.protobuf import empty_pb2
import grpc

from server import maui_common
from server import maui_utils_runner
from server import maui_v12_device as device
from server import maui_v12_h2h as h2h_check
from server import maui_v12_h2h_flash as h2h_flash
from server import maui_v12_ite_flash as ite_flash
from server import maui_v12_profile as profile
from server import maui_v12_uart as uart
from server.generated import maui_manufacturing_pb2
from server.generated import maui_manufacturing_pb2_grpc
from server.maui_common import write_device_log
from server.maui_console_tester import open_serial_port


# Directories searched for station firmware (see deb_builder/start-diagnoseme).
FIRMWARE_ROOTS = profile.FIRMWARE_ROOTS
FIRMWARE_EXTENSIONS = (".bin",)
KNOWN_COMPONENTS = (
    profile.FW_COMPONENT_MCU_APP,
    profile.FW_COMPONENT_MCU_FULL,
    profile.FW_COMPONENT_PDC,
)
# Components whose images must have exactly this size.
COMPONENT_SIZES = {profile.FW_COMPONENT_MCU_FULL: profile.MCU_FULL_IMAGE_SIZE}
# Written by the firmware sync into each component directory:
# {"version": ..., "file": <file name>, "md5": ..., "source": ...}.
STABLE_MANIFEST = "STABLE.json"
# A build id in a file name, e.g. maui_full_flash_1mb_0.69-5324f43.bin.
_NAME_VERSION_RE = re.compile(r"\d+\.\d+-[0-9a-f]{7,40}")
# File names of images that must never be written to the App slot.
_NOT_APP_IMAGE_RE = re.compile(r"full_flash|bootloader", re.IGNORECASE)

# maui-flash mcu --target app: reboot to RW bootloader + SMP upload + reboot.
APP_UPDATE_TIMEOUT_S = 180.0
# Time for the new App to boot and enumerate after the upload.
APP_BOOT_GRACE_S = 15.0

# Lines of maui-flash output kept for the log (bounded memory).
MAX_TOOL_LOG_LINES = 2000
# maui-flash output is polled this often (cancellation, deadline).
TOOL_POLL_S = 1.0
# Upload progress lines kept in the log: every 10 %.
LOG_EVERY_PERCENT = 10

_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_VERSION_RE = re.compile(r"Maui version\s+(\S+)")


def firmware_name_error(file_name: str) -> str:
    """Returns why |file_name| is not a plain firmware file name, '' if OK."""
    if file_name and (
        any(c in file_name for c in ("/", "\\", "\x00")) or ".." in file_name
    ):
        return (
            f"Invalid firmware file name {file_name!r}: expected a plain file "
            "name from the firmware list."
        )
    return ""


def _inside(path: str, directory: str) -> bool:
    """True if |path| resolves (symlinks included) to inside |directory|."""
    real_dir = os.path.realpath(directory)
    return os.path.commonpath([os.path.realpath(path), real_dir]) == real_dir


def list_firmware_files(component: str) -> List[str]:
    """Returns firmware files of |component|, newest last."""
    if component not in KNOWN_COMPONENTS:
        return []
    files = []
    for root in FIRMWARE_ROOTS:
        directory = os.path.join(root, component)
        for ext in FIRMWARE_EXTENSIONS:
            for path in glob.glob(os.path.join(directory, f"*{ext}")):
                # Never follow a symlink out of the component directory.
                if os.path.isfile(path) and _inside(path, directory):
                    files.append(path)
    return sorted(set(files), key=lambda p: (os.path.getmtime(p), p))


def stable_firmware(component: str) -> Tuple[str, str]:
    """Returns (path, version) of the file named by STABLE.json, or ('', '')."""
    if component not in KNOWN_COMPONENTS:
        return "", ""
    for root in FIRMWARE_ROOTS:
        directory = os.path.join(root, component)
        manifest = os.path.join(directory, STABLE_MANIFEST)
        try:
            with open(manifest, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            logging.warning("Ignoring %s: %s", manifest, e)
            continue
        name = str(data.get("file") or "") if isinstance(data, dict) else ""
        name = os.path.basename(name)
        path = os.path.join(directory, name)
        if (
            name
            and not firmware_name_error(name)
            and path in list_firmware_files(component)
        ):
            return path, str(data.get("version") or "")
        logging.warning("%s does not name a firmware file here: %r", manifest, name)
    return "", ""


def version_from_name(file_name: str) -> str:
    """Returns the build id in a firmware file name, e.g. '0.69-5324f43'."""
    m = _NAME_VERSION_RE.search(file_name)
    return m.group(0) if m else ""


def app_image_error(path: str) -> str:
    """Returns why |path| is not an MCU App image, '' if it can be one."""
    name = os.path.basename(path)
    use_full = "Flash it with 'Full MCU image (1 MiB) over USB-UART with itecomdbgr'."
    m = _NOT_APP_IMAGE_RE.search(name)
    if m and m.group(0).lower() == "full_flash":
        return f"{name} is a full MCU image, not an MCU App image. {use_full}"
    if m:
        return f"{name} is a bootloader image, not an MCU App image."
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return f"Cannot read {name}: {e}"
    if size == profile.MCU_FULL_IMAGE_SIZE:
        return (
            f"{name} has {size} bytes: that is a full MCU image (1 MiB), not an "
            f"MCU App image. {use_full}"
        )
    return ""


def select_firmware_file(
    component: str,
    file_name: str = "",
    accept: Optional[Callable[[str], bool]] = None,
) -> str:
    """Returns the firmware file of |component| to use, else ''.

    That is |file_name| if given, else the stable file (STABLE.json) or the
    newest file that |accept|s. |file_name| has to be a plain file name (see
    firmware_name_error); the result always lies inside the component
    directory.
    """
    if firmware_name_error(file_name):
        return ""
    files = list_firmware_files(component)
    if file_name:
        files = [f for f in files if os.path.basename(f) == file_name]
        return files[-1] if files else ""
    files = [f for f in files if accept is None or accept(f)]
    stable, _ = stable_firmware(component)
    if stable in files:
        return stable
    return files[-1] if files else ""


def _is_app_image(path: str) -> bool:
    return not app_image_error(path)


async def _stop_tool(
    proc: asyncio.subprocess.Process,
    maui_utils: maui_utils_runner.MauiUtilsSession,
) -> None:
    """Kills a maui-utils run: the docker client and its container."""
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    await asyncio.get_running_loop().run_in_executor(None, maui_utils.kill)
    await proc.wait()


async def _read_tool_lines(
    proc: asyncio.subprocess.Process,
    context: grpc.ServicerContext,
    deadline: float,
) -> AsyncGenerator[str, None]:
    """Yields the output lines of |proc| until EOF, |deadline| or cancel.

    Reads raw chunks and splits on CR or LF: progress bars that only rewrite
    their line with CR stream as they come instead of blocking readline().
    """
    lines = ite_flash.LineSplitter()
    while not context.done():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            data = await asyncio.wait_for(
                proc.stdout.read(ite_flash.READ_CHUNK_BYTES),
                timeout=min(TOOL_POLL_S, remaining),
            )
        except asyncio.TimeoutError:
            continue  # Check for cancellation and the deadline again.
        for line in lines.feed(data) if data else lines.flush():
            yield line
        if not data:
            return


def _app_update_failed(
    serial_no: str, msg: str, log: str = ""
) -> maui_manufacturing_pb2.MauiMcuUpdateProgress:
    """Writes the failed mcu_app_update.log and returns the final message."""
    write_device_log(
        serial_no,
        "mcu_app_update",
        f"ALL_PASSED=False\nSTATUS: FAILED\n{msg}\n\n{log}",
    )
    return maui_manufacturing_pb2.MauiMcuUpdateProgress(
        is_complete=True, success=False, status_message=msg, error_message=msg
    )


def _proto_console(
    console: Optional[device.McuConsole],
) -> maui_manufacturing_pb2.MauiMcuConsoleInfo:
    if not console:
        return maui_manufacturing_pb2.MauiMcuConsoleInfo()
    return maui_manufacturing_pb2.MauiMcuConsoleInfo(
        device_port=console.dev_node,
        serial_number=console.serial,
        product=console.product,
        stage=console.stage,
        usb_path=console.usb_path,
    )


def _proto_bridge(bridge: uart.UartBridge) -> maui_manufacturing_pb2.MauiUartBridge:
    return maui_manufacturing_pb2.MauiUartBridge(
        device_port=bridge.dev_node,
        by_id=bridge.by_id,
        driver=bridge.driver,
        vid=bridge.vid,
        pid=bridge.pid,
        manufacturer=bridge.manufacturer,
        product=bridge.product,
        serial_number=bridge.serial,
        usb_path=bridge.usb_path,
        interface_number=bridge.interface,
        label=bridge.label,
    )


def _paired_h2h(
    console: device.McuConsole, unit: device.UnitInfo
) -> Optional[device.H2hDevice]:
    """Returns the H2H bridge on the board of |console| (any board)."""
    if unit.mcu and console.dev_node == unit.mcu.dev_node:
        return unit.h2h
    return device.pair_h2h(console, device.scan_h2h_devices())


def read_mcu_version(
    tester: device.MauiV12ConsoleTester, ser: Optional[Any] = None
) -> str:
    """Returns the MCU App version reported by `version`, or ''.

    Uses the open port |ser| if given, else opens the tester's port.
    """
    try:
        if ser is not None:
            out = tester.send_command(ser, "version")
        else:
            with open_serial_port(
                tester.port_node, tester.baudrate, timeout=tester.timeout
            ) as port:
                out = tester.send_command(port, "version")
        m = _VERSION_RE.search(out)
        return m.group(1) if m else ""
    except Exception as e:  # pylint: disable=broad-except
        logging.warning("Failed to read MCU version on %s: %s", tester.port_node, e)
        return ""


@dataclasses.dataclass(frozen=True)
class ConsoleInfo:
    """What the MCU App console reports about itself."""

    version: str = ""
    hwrev: int = -1
    nvs_serial: str = ""

    @property
    def answered(self) -> bool:
        """True if the console answered at least one command."""
        return bool(self.version or self.hwrev >= 0 or self.nvs_serial)


def _enumeration_key(console: device.McuConsole) -> Tuple[str, ...]:
    """Identifies one USB enumeration of |console|.

    The USB device number changes on every (re-)enumeration, e.g. after a
    replug, a `reboot` or an App update, which invalidates cached answers.
    """
    devnum = device.read_sysfs(os.path.join(console.usb_path, "devnum"))
    return (console.dev_node, console.usb_path, console.serial, devnum)


class MauiMcuServicer(maui_manufacturing_pb2_grpc.MauiMcuServiceServicer):
    """Maui v1.2 MCU services (unit status, serial provisioning, updates)."""

    def __init__(self) -> None:
        super().__init__()
        # Console answers of the current enumeration (see get_unit_status).
        self._console_cache: Dict[Tuple[str, ...], ConsoleInfo] = {}
        # Enumeration whose console is being read in the background.
        self._console_query_key: Optional[Tuple[str, ...]] = None
        self._console_lock = threading.Lock()

    @staticmethod
    def _make_tester(port: str) -> device.MauiV12ConsoleTester:
        return device.MauiV12ConsoleTester(
            port_node=port, timeout=profile.CONSOLE_TIMEOUT_S
        )

    def _query_console(self, dev_node: str) -> ConsoleInfo:
        """Reads version, hwrev and NVS serial in a single console session."""
        tester = self._make_tester(dev_node)
        start = time.monotonic()
        try:
            with open_serial_port(
                dev_node, tester.baudrate, timeout=tester.timeout
            ) as ser:
                version = read_mcu_version(tester, ser=ser)
                _, hwrev, _ = tester.read_hwrev(ser=ser)
                nvs_serial, _ = tester.serial_get(ser=ser)
        except Exception as e:  # pylint: disable=broad-except
            logging.warning("MCU console query on %s failed: %s", dev_node, e)
            return ConsoleInfo()
        logging.info(
            "Read MCU console info on %s in %.2f s", dev_node, time.monotonic() - start
        )
        return ConsoleInfo(version=version, hwrev=hwrev, nvs_serial=nvs_serial)

    def _console_info(self, console: device.McuConsole) -> Optional[ConsoleInfo]:
        """Returns the console answers of this enumeration, None while pending.

        The MCU console is queried once per enumeration in a background
        thread, so status polls never wait for the console. No query starts
        while another operation uses the console (see device.console_busy).
        """
        key = _enumeration_key(console)
        with self._console_lock:
            cached = self._console_cache.get(key)
            if cached is not None:
                return cached
            if self._console_query_key is not None:
                return None  # A query is already running.
            if device.busy_reason(console):
                return None  # Ask again once the console is free.
            self._console_query_key = key
        threading.Thread(
            target=self._background_query,
            args=(key, console),
            name="maui-console-query",
            daemon=True,
        ).start()
        return None

    def _background_query(
        self, key: Tuple[str, ...], console: device.McuConsole
    ) -> None:
        info = ConsoleInfo()
        try:
            with device.console_busy([console], device.STATUS_QUERY, wait_s=0):
                info = self._query_console(console.dev_node)
        except device.ConsoleBusyError as e:
            logging.info("Skipped the MCU console query: %s", e)
        with self._console_lock:
            self._console_query_key = None
            # Keep only the current board; retry next poll if it did not answer.
            self._console_cache = {key: info} if info.answered else {}

    def get_unit_status(
        self,
        unused_request: empty_pb2.Empty,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiUnitStatusResponse:
        """Reports the MCU console, H2H bridge and serial numbers."""
        unit = device.find_unit()
        hub2 = hub3 = False
        for dev in maui_common.scan_usb_devices():
            hub2 = hub2 or (dev.vid, dev.pid) in profile.HUB_USB2_VID_PIDS
            hub3 = hub3 or (dev.vid, dev.pid) in profile.HUB_USB3_VID_PIDS

        h2h_serial = unit.h2h.serial if unit.h2h else ""
        info: Optional[ConsoleInfo] = ConsoleInfo()
        if unit.mcu and unit.mcu.is_app and os.path.exists(unit.mcu.dev_node):
            info = self._console_info(unit.mcu)
        pending = info is None
        info = info or ConsoleInfo()

        serials_match = bool(h2h_serial and unit.mcu and unit.mcu.serial == h2h_serial)
        if not unit.mcu and not unit.h2h:
            msg = "No Maui v1.2 device detected."
        elif not unit.mcu:
            msg = (
                f"H2H {h2h_serial or '(no serial)'} detected, "
                "MCU console (18d1:5085) missing."
            )
        elif not unit.h2h:
            msg = (
                f"MCU {unit.mcu.serial} ({unit.mcu.product}) detected, "
                "H2H bridge missing."
            )
        elif serials_match:
            msg = f"Maui v1.2 board {h2h_serial}: MCU and H2H serials match."
        else:
            msg = f"Serial mismatch: H2H '{h2h_serial}', MCU '{unit.mcu.serial}'."

        return maui_manufacturing_pb2.MauiUnitStatusResponse(
            mcu_detected=bool(unit.mcu),
            mcu=_proto_console(unit.mcu),
            h2h_detected=bool(unit.h2h),
            h2h_serial=h2h_serial,
            h2h_product=unit.h2h.product if unit.h2h else "",
            h2h_pid=unit.h2h.pid if unit.h2h else 0,
            h2h_serial_valid=profile.is_valid_serial(h2h_serial),
            serials_match=serials_match,
            mcu_version=info.version,
            hwrev=info.hwrev,
            mcu_nvs_serial=info.nvs_serial,
            hub_usb2_detected=hub2,
            hub_usb3_detected=hub3,
            consoles=[_proto_console(c) for c in unit.consoles],
            message=msg,
            console_pending=pending,
        )

    def provision_mcu_serial(
        self,
        request: maui_manufacturing_pb2.MauiProvisionSerialRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiProvisionSerialResponse:
        """Copies the H2H serial into the MCU and verifies re-enumeration."""
        unit = device.find_unit()
        try:
            with device.console_busy([unit.mcu], "MCU serial provisioning"):
                res = device.provision_serial(
                    unit,
                    expected_serial=request.expected_serial,
                    make_tester=self._make_tester,
                )
        except device.ConsoleBusyError as e:
            # Nothing was attempted: keep the board's previous log.
            return maui_manufacturing_pb2.MauiProvisionSerialResponse(
                h2h_serial=unit.h2h.serial if unit.h2h else "",
                message=str(e),
                log=str(e),
            )
        log_text = "\n".join(res.log)
        write_device_log(
            res.h2h_serial or res.previous_mcu_serial or "UNKNOWN",
            "mcu_serial_provisioning",
            f"ALL_PASSED={res.success}\n"
            f"RESULT: {'PASSED' if res.success else 'FAILED'}\n"
            f"{res.message}\n"
            f"H2H Serial: {res.h2h_serial}\n"
            f"Previous MCU Serial: {res.previous_mcu_serial}\n"
            f"MCU Serial: {res.mcu_serial}\n"
            f"Re-enumeration: {res.reenumeration_s:.2f} s\n\n{log_text}",
        )
        return maui_manufacturing_pb2.MauiProvisionSerialResponse(
            success=res.success,
            already_provisioned=res.already_provisioned,
            h2h_serial=res.h2h_serial,
            previous_mcu_serial=res.previous_mcu_serial,
            mcu_serial=res.mcu_serial,
            device_port=res.device_port,
            reenumeration_s=res.reenumeration_s,
            message=res.message,
            log=log_text,
        )

    def check_h2h_spi_serial(
        self,
        request: maui_manufacturing_pb2.MauiH2hSpiCheckRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiH2hSpiCheckResponse:
        """Reads the unit serial from the H2H SPI flash via the MCU console."""
        unit = h2h_check.find_unit(request.device_port)
        try:
            with device.console_busy([unit.mcu], "H2H SPI serial check"):
                res = h2h_check.run_check(unit, self._make_tester)
        except device.ConsoleBusyError as e:
            # Nothing was attempted: keep the board's previous log.
            return maui_manufacturing_pb2.MauiH2hSpiCheckResponse(
                firmware_supported=True, message=str(e), log=str(e)
            )
        write_device_log(
            request.serial_number
            or res.host_serial_before
            or res.spi_serial
            or "UNKNOWN",
            h2h_check.LOG_NAME,
            h2h_check.format_log(res),
        )
        return maui_manufacturing_pb2.MauiH2hSpiCheckResponse(
            success=res.success,
            firmware_supported=res.firmware_supported,
            host_serial_before=res.host_serial_before,
            spi_serial=res.spi_serial,
            host_serial_after=res.host_serial_after,
            jedec_id=res.jedec_id,
            flash_size=res.flash_size,
            message=res.message,
            log="\n".join(res.log),
        )

    def list_firmware(
        self,
        request: maui_manufacturing_pb2.MauiListFirmwareRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiListFirmwareResponse:
        """Lists station firmware files of a component (newest first)."""
        if request.component and request.component not in KNOWN_COMPONENTS:
            return maui_manufacturing_pb2.MauiListFirmwareResponse(
                message=(
                    f"Unknown firmware component '{request.component}' "
                    f"(expected one of {', '.join(KNOWN_COMPONENTS)})."
                )
            )
        components = (
            [request.component] if request.component else list(KNOWN_COMPONENTS)
        )
        files = []
        not_app = []
        for component in components:
            stable, stable_version = stable_firmware(component)
            for path in reversed(list_firmware_files(component)):
                name = os.path.basename(path)
                if component == profile.FW_COMPONENT_MCU_APP and app_image_error(path):
                    not_app.append(name)  # E.g. a full image: never offered.
                    continue
                files.append(
                    maui_manufacturing_pb2.MauiFirmwareFile(
                        component=component,
                        file_name=name,
                        path=path,
                        size_bytes=os.path.getsize(path),
                        expected_size_bytes=COMPONENT_SIZES.get(component, 0),
                        is_stable=path == stable,
                        version=(
                            stable_version
                            if path == stable
                            else version_from_name(name)
                        ),
                    )
                )
        msg = (
            f"{len(files)} firmware file(s) found."
            if files
            else f"No firmware found in {FIRMWARE_ROOTS[0]}/<{'|'.join(components)}>."
        )
        wrong_size = [
            f
            for f in files
            if f.expected_size_bytes and f.size_bytes != f.expected_size_bytes
        ]
        if wrong_size:
            msg += (
                f" {len(wrong_size)} file(s) have the wrong size: "
                + ", ".join(
                    f"{f.file_name} ({f.size_bytes} bytes, expected "
                    f"{f.expected_size_bytes})"
                    for f in wrong_size
                )
                + "."
            )
        if not_app:
            msg += f" Not MCU App images (skipped): {', '.join(not_app)}."
        return maui_manufacturing_pb2.MauiListFirmwareResponse(files=files, message=msg)

    async def update_mcu_app(
        self,
        request: maui_manufacturing_pb2.MauiMcuUpdateRequest,
        context: grpc.ServicerContext,
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiMcuUpdateProgress, None]:
        """Updates the MCU App via maui-flash (RW bootloader, SMP) in maui-utils."""
        unit = device.find_unit()
        port = request.device_port or (unit.mcu.dev_node if unit.mcu else "")
        # The selected console need not be on the first board: log and wait
        # for re-enumeration on its own board.
        selected = next(
            (c for c in device.scan_mcu_consoles() if c.dev_node == port), None
        )
        if selected:
            h2h = _paired_h2h(selected, unit)
        else:
            h2h = None if request.device_port else unit.h2h
        serial_no = (
            request.serial_number
            or (h2h.serial if h2h else "")
            or (selected.serial if selected else "")
        )
        if not port:
            error = "Maui MCU console (18d1:5085) not detected."
        elif selected is None:
            # Only ever hand detected Maui consoles to the privileged container.
            error = f"'{port}' is not a detected Maui MCU console (18d1:5085)."
        else:
            error = firmware_name_error(request.file_name)
        fw_file = (
            ""
            if error
            else select_firmware_file(
                profile.FW_COMPONENT_MCU_APP, request.file_name, _is_app_image
            )
        )
        if not error and not fw_file:
            error = (
                f"MCU App firmware '{request.file_name or '*.bin'}' not found in "
                f"{FIRMWARE_ROOTS[0]}/{profile.FW_COMPONENT_MCU_APP}."
            )
        if not error:
            # maui-flash --target app must never write a full image.
            error = app_image_error(fw_file)
        if error:
            yield _app_update_failed(serial_no, error)
            return
        try:
            keys = await device.claim_consoles_async([selected], "MCU App update")
        except device.ConsoleBusyError as e:
            # Nothing was attempted: keep the board's previous log.
            yield maui_manufacturing_pb2.MauiMcuUpdateProgress(
                is_complete=True,
                success=False,
                status_message=str(e),
                error_message=str(e),
            )
            return
        try:
            # aclosing: a cancelled RPC stops maui-flash right away.
            async with contextlib.aclosing(
                self._update_app(selected, fw_file, serial_no, context)
            ) as updates:
                async for update in updates:
                    yield update
        finally:
            device.release_consoles(keys)

    async def _update_app(
        self,
        console: device.McuConsole,
        fw_file: str,
        serial_no: str,
        context: grpc.ServicerContext,
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiMcuUpdateProgress, None]:
        """update_mcu_app on a validated, claimed console."""
        progress = maui_manufacturing_pb2.MauiMcuUpdateProgress
        yield progress(
            status_message=(
                f"Updating MCU App on {console.dev_node} with "
                f"{os.path.basename(fw_file)}..."
            )
        )

        if os.environ.get("DIAGNOSEME_SIMULATE_MAUI") == "1":
            for pct in range(0, 101, 25):
                yield progress(
                    is_complete=pct == 100,
                    success=pct == 100,
                    progress_percent=pct,
                    status_message=f"[DEV MODE] Updating MCU App... {pct}%",
                )
                await asyncio.sleep(0.2)
            return

        run = {
            "returncode": None,
            "output": collections.deque(maxlen=MAX_TOOL_LOG_LINES),
        }
        # aclosing: a cancelled RPC closes the inner generator right away, so
        # its cleanup stops maui-flash and removes the container.
        async with contextlib.aclosing(
            self._run_maui_flash(console.dev_node, fw_file, context, run)
        ) as updates:
            async for update in updates:
                yield update
        tool_log = "\n".join(run["output"])
        if run["returncode"] != 0:
            yield _app_update_failed(
                serial_no,
                maui_utils_runner.explain_failure(tool_log)
                or f"maui-flash failed (exit code {run['returncode']}).",
                tool_log,
            )
            return

        yield progress(
            progress_percent=99, status_message="Waiting for the new MCU App to boot..."
        )
        # Same physical USB port: unprovisioned boards all report the same
        # serial ("Uninitialized"), so the serial alone could pick another one.
        new_console = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: device.wait_for_console(
                None,
                usb_path=console.usb_path,
                initial_delay_s=profile.REENUM_INITIAL_DELAY_S,
                grace_s=APP_BOOT_GRACE_S,
            ),
        )
        if new_console is None:
            yield _app_update_failed(
                serial_no,
                f"MCU App did not enumerate on USB port "
                f"{os.path.basename(console.usb_path)} after the update.",
                tool_log,
            )
            return
        version = read_mcu_version(self._make_tester(new_console.dev_node))
        write_device_log(
            serial_no,
            "mcu_app_update",
            f"ALL_PASSED=True\nSTATUS: SUCCESS\nFile: {fw_file}\n"
            f"Console: {new_console.dev_node} ({new_console.usb_path})\n"
            f"Version: {version}\n\n{tool_log}",
        )
        yield progress(
            is_complete=True,
            success=True,
            progress_percent=100,
            status_message=f"MCU App updated (version {version or 'unknown'}).",
            mcu_version=version,
        )

    @staticmethod
    async def _run_maui_flash(
        port: str,
        fw_file: str,
        context: grpc.ServicerContext,
        run: Dict[str, Any],
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiMcuUpdateProgress, None]:
        """Runs `maui-flash mcu --target app` in maui-utils, streaming progress.

        Stores the exit code and output lines in |run|.
        """
        with maui_utils_runner.MauiUtilsRunner().session() as maui_utils:
            args = ["--port", port, "mcu", "--file", maui_utils.stage(fw_file)]
            cmd = maui_utils.command(
                maui_utils_runner.TOOL_MAUI_FLASH, args + ["--target", "app"]
            )
            logging.info("Running: %s", " ".join(cmd))
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            except OSError as e:
                run["output"].append(f"Failed to start maui-flash: {e}")
                run["returncode"] = -1
                return
            deadline = time.monotonic() + APP_UPDATE_TIMEOUT_S
            logged_bucket = -1
            try:
                async for line in _read_tool_lines(proc, context, deadline):
                    m = _PERCENT_RE.search(line)
                    percent = min(100, int(float(m.group(1)))) if m else None
                    # Upload progress may rewrite one line many times a
                    # second: keep every 10 % of it.
                    log = (
                        percent is None or percent // LOG_EVERY_PERCENT > logged_bucket
                    )
                    if percent is not None and log:
                        logged_bucket = percent // LOG_EVERY_PERCENT
                    if log:
                        run["output"].append(line)
                    yield maui_manufacturing_pb2.MauiMcuUpdateProgress(
                        progress_percent=min(99, percent or 0),
                        status_message=(
                            "Uploading MCU App..." if m else "Updating MCU App..."
                        ),
                        log_line=line if log else "",
                    )
                if proc.returncode is None and time.monotonic() >= deadline:
                    run["output"].append(
                        f"maui-flash timed out after {APP_UPDATE_TIMEOUT_S:.0f} s."
                    )
                if proc.returncode is None and (
                    context.done() or time.monotonic() >= deadline
                ):
                    # Cancelled or timed out: stop the tool right away.
                    await _stop_tool(proc, maui_utils)
                elif proc.returncode is None:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        await _stop_tool(proc, maui_utils)
            finally:
                # Also runs when the RPC is cancelled (GeneratorExit /
                # CancelledError): never leave the container flashing alone.
                if proc.returncode is None:
                    await _stop_tool(proc, maui_utils)
            run["returncode"] = proc.returncode

    def list_uart_bridges(
        self,
        unused_request: empty_pb2.Empty,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiUartBridgesResponse:
        """Lists the USB-UART bridges for full-image flashing, default first."""
        bridges = uart.scan_uart_bridges()
        default = uart.default_bridge(bridges)
        msg = (
            f"{len(bridges)} USB-UART bridge(s) detected."
            if bridges
            else (
                "No USB-UART bridge detected (supported drivers: "
                f"{', '.join(uart.BRIDGE_DRIVERS)})."
            )
        )
        return maui_manufacturing_pb2.MauiUartBridgesResponse(
            bridges=[_proto_bridge(b) for b in bridges],
            default_port=default.dev_node if default else "",
            message=msg,
        )

    async def update_mcu_full_image(
        self,
        request: maui_manufacturing_pb2.MauiMcuFullImageRequest,
        context: grpc.ServicerContext,
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiMcuFullImageProgress, None]:
        """Writes the whole 1 MiB MCU flash with itecomdbgr (USB-UART bridge)."""
        error = firmware_name_error(request.file_name)
        keys: Tuple[str, ...] = ()
        if not error:
            try:
                # The bridge may be wired to any board: hold every console.
                keys = await device.claim_consoles_async(
                    [device.ALL_CONSOLES], "MCU full image flash"
                )
            except device.ConsoleBusyError as e:
                error = str(e)
        if error:
            yield maui_manufacturing_pb2.MauiMcuFullImageProgress(
                is_complete=True,
                success=False,
                phase=ite_flash.PHASE_FAILED,
                status_message=error,
                error_message=error,
            )
            return
        try:
            fw_file = select_firmware_file(
                profile.FW_COMPONENT_MCU_FULL,
                request.file_name,
                lambda path: not ite_flash.check_image(path),
            )
            flasher = ite_flash.FullImageFlasher(
                read_version=lambda port: read_mcu_version(self._make_tester(port))
            )
            # aclosing: a cancelled RPC stops itecomdbgr right away.
            async with contextlib.aclosing(
                flasher.flash(request, fw_file, is_cancelled=context.done)
            ) as updates:
                async for update in updates:
                    yield update
        finally:
            device.release_consoles(keys)

    def list_h2h_firmware(
        self,
        request: maui_manufacturing_pb2.MauiH2hFirmwareListRequest,
        unused_context: grpc.ServicerContext,
    ) -> maui_manufacturing_pb2.MauiH2hFirmwareListResponse:
        """Lists the H2H images of the station and checks the MCU App build."""
        port, verdict = h2h_flash.read_firmware_verdict(
            request.device_port,
            lambda port: h2h_check.open_console(self._make_tester(port)),
        )
        return h2h_flash.list_response(mcu_port=port, firmware=verdict)

    async def flash_h2h_firmware(
        self,
        request: maui_manufacturing_pb2.MauiH2hFlashRequest,
        context: grpc.ServicerContext,
    ) -> AsyncGenerator[maui_manufacturing_pb2.MauiH2hFlashProgress, None]:
        """Programs the H2H bridge SPI flash with maui-flash h2h (maui-utils)."""
        job, error, log_key = await asyncio.get_running_loop().run_in_executor(
            None, h2h_flash.prepare_job, request
        )
        if job is None:
            yield h2h_flash.failed_progress(log_key, error)
            return
        try:
            keys = await device.claim_consoles_async(
                [job.console], "H2H firmware flash"
            )
        except device.ConsoleBusyError as e:
            # Nothing was attempted: keep the board's previous log.
            yield maui_manufacturing_pb2.MauiH2hFlashProgress(
                is_complete=True,
                success=False,
                phase=h2h_flash.PHASE_FAILED,
                status_message=str(e),
                error_message=str(e),
            )
            return
        try:
            flasher = h2h_flash.H2hFirmwareFlasher(
                lambda port: h2h_check.open_console(self._make_tester(port))
            )
            # aclosing: a cancelled RPC stops maui-flash right away.
            async with contextlib.aclosing(
                flasher.flash(job, is_cancelled=context.done)
            ) as updates:
                async for update in updates:
                    yield update
        finally:
            device.release_consoles(keys)
