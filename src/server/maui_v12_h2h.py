# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Maui v1.2 H2H SPI flash access check.

The PL27A1 host-to-host bridge boots from its own SPI flash, which also holds
the unit serial number as a USB string descriptor. The MCU App can take the
flash over (`h2h_flash acquire`, which holds the bridge in reset), read it and
hand it back (`h2h_flash release`, which lets the bridge boot again).

This check reads the first 96 bytes of the image through the MCU console,
decodes the serial and compares it with the iSerial the host sees before the
check and after an explicit reset of the bridge (`gpio set h2h_reset_l 1`,
then `0`; h2h_reset_l is active low), which makes it boot from its flash again.

It only touches the SPI bus on approved MCU App builds (bundled stable
firmware or MAUI_V12_APPROVED_FW; nothing to compare with is not enough) that
print a `Size:` line in `h2h_flash id`: older builds disable the SPI mux
buffer on release, which leaves the bridge unable to boot until a power-cycle.
The bus is only released (and the bridge reset) after a confirmed acquire.
"""

import contextlib
import dataclasses
import re
import struct
import time
from typing import Callable
from typing import ContextManager
from typing import Dict
from typing import Iterator
from typing import List
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple

from server import maui_v12_device as device
from server import maui_v12_profile as profile
from server.maui_console_tester import open_serial_port


LOG_NAME = "h2h_spi_check"
UPDATE_HINT = "update the MCU App"

# A hex offset with "0x" and a decimal length parse on all firmware versions.
READ_LENGTH = 96
READ_COMMAND = f"h2h_flash read 0x0 {READ_LENGTH}"
READ_ATTEMPTS = 3
ID_ATTEMPTS = 2
COMMAND_TIMEOUT_S = 2.0
# PL27A1 reset pulse after the release (also used by the recovery).
RESET_PULSE_S = 0.15
# Observed: the bridge is back about 0.3 s after its reset. The host is not
# polled during the first REENUM_SETTLE_S, so that the enumeration from before
# the reset cannot be mistaken for the bridge; it has REENUM_TIMEOUT_S (from
# the end of the reset) to show up.
REENUM_SETTLE_S = 1.0
REENUM_TIMEOUT_S = 5.0
REENUM_POLL_S = 0.1

# Bus GPIOs while the PL27A1 runs from its flash (logical levels).
IDLE_BUS_GPIOS = {"h2h_reset_l": 0, "ec_mem_sel": 0, "ec_mem_oe_l": 1}

# Size of a whole PL27A1 SPI image (0x000-0x1FF descriptors and settings,
# 0x200-0x21FF Prolific firmware); `maui-flash h2h --read` dumps this much.
IMAGE_SIZE = 8704
RAW_IMAGE_HINT = (
    f"expected a raw {IMAGE_SIZE}-byte PL27A1 .bin from prepare_raw_flash.py"
)
# PL27A1 image layout (header, then packed USB string descriptors from 0x10:
# LANGID, manufacturer, product, serial).
IMAGE_SIGNATURE = b"\x7b\x06"
# USB ID of the bridge in a raw image (prepare_raw_flash.py).
IMAGE_PID = 0x506D
IMAGE_BCD_DEVICE = 0x0200
STRING_TABLE_OFFSET = 0x10
SERIAL_DESCRIPTOR_OFFSET = 0x38
SERIAL_DESCRIPTOR_HEADER = b"\x22\x03"
# The string region (zero-filled after the strings) ends here; the hardware
# config registers follow up to 0x1FF and must never change.
STRING_REGION_END = 0xF9
USB_DT_STRING = 0x03
LANGID_EN_US = 0x0409
EXPECTED_MANUFACTURER = "Google LLC"
EXPECTED_PRODUCT = "ADB v1"
# JEDEC capacity codes the firmware turns into a `Size:` line (64 KB..16 MB).
SIZED_CAPACITY_CODES = range(16, 25)

_HEXDUMP_RE = re.compile(
    r"^([0-9A-F]{8}): ((?:[0-9a-f]{2} |   ){8}) ((?:[0-9a-f]{2} |   ){8})\|"
)
_LOG_LINE_RE = re.compile(r"^\[\d\d:\d\d:\d\d\.\d{3},\d{3}\] <(?:inf|wrn|err|dbg)> ")
_PROMPT_PREFIX_RE = re.compile(r"^(?:\S*\$ )+")
_JEDEC_RE = re.compile(
    r"JEDEC ID: 0x([0-9A-Fa-f]{2}) 0x([0-9A-Fa-f]{2}) 0x([0-9A-Fa-f]{2})"
)
_SIZE_RE = re.compile(r"^\s*Size:\s*(\d+)\s*KB", re.MULTILINE)
_GPIO_RE = re.compile(r"\[\s*([a-zA-Z0-9_]+)\s*\]\s*[IOio]\s*=\s*([01])")
_READ_ERROR_RE = re.compile(r"Flash read failed \((-?\d+)\)")

ConsoleFn = Callable[[str], str]


class CheckError(Exception):
    """A step of the check failed; the message is shown to the operator."""


@dataclasses.dataclass
class H2hImage:
    """The header and USB strings of a PL27A1 SPI image."""

    vid: int
    pid: int
    bcd_device: int
    manufacturer: str
    product: str
    serial: str
    warnings: List[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class SpiCheckResult:  # pylint: disable=too-many-instance-attributes
    """Outcome of the H2H SPI serial check."""

    success: bool = False
    firmware_supported: bool = True
    host_serial_before: str = ""
    spi_serial: str = ""
    host_serial_after: str = ""
    jedec_id: str = ""
    flash_size: str = ""
    message: str = ""
    mcu_version: str = ""
    log: List[str] = dataclasses.field(default_factory=list)


def _lines(output: str) -> List[str]:
    """Splits console output into lines without ANSI codes and prompts."""
    text = device.strip_ansi(output).replace("\r\n", "\n").replace("\r", "\n")
    return [_PROMPT_PREFIX_RE.sub("", line) for line in text.split("\n")]


def _summary(output: str, command: str, limit: int = 160) -> str:
    """Returns the answer to |command| in |output| on one line."""
    lines = [
        line.strip()
        for line in _lines(output)
        if line.strip()
        and command not in line  # The echo of the command.
        and not _LOG_LINE_RE.match(line.strip())
    ]
    text = " | ".join(lines) or "(no output)"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def parse_hexdump(output: str, length: int = READ_LENGTH) -> bytes:
    """Returns the bytes of a Zephyr shell hexdump (offsets relative to 0).

    Ignores the echo of the command, prompts and log lines. Raises ValueError
    if the dump does not run contiguously from offset 0 or does not hold
    exactly |length| bytes.
    """
    data = bytearray()
    for line in _lines(output):
        if _LOG_LINE_RE.match(line):
            continue
        m = _HEXDUMP_RE.match(line)
        if not m:
            continue
        offset = int(m.group(1), 16)
        if offset != len(data):
            raise ValueError(
                f"hexdump line at 0x{offset:x} where 0x{len(data):x} was expected"
            )
        data += bytes(int(b, 16) for b in (m.group(2) + m.group(3)).split())
    if len(data) != length:
        raise ValueError(f"hexdump holds {len(data)} of {length} bytes")
    return bytes(data)


def _string_descriptors(data: bytes, count: int = 4) -> List[bytes]:
    """Returns the bodies of the first |count| packed USB string descriptors."""
    bodies = []
    offset = STRING_TABLE_OFFSET
    for index in range(count):
        if offset + 2 > len(data):
            raise ValueError(f"string descriptor #{index} at 0x{offset:02x} not read")
        length, dtype = data[offset], data[offset + 1]
        if (
            dtype != USB_DT_STRING
            or length < 2
            or length % 2
            or offset + length > len(data)
        ):
            raise ValueError(
                f"bad string descriptor #{index} at 0x{offset:02x} "
                f"(bLength 0x{length:02x}, bDescriptorType 0x{dtype:02x})"
            )
        bodies.append(data[offset + 2 : offset + length])
        offset += length
    return bodies


def decode_image(data: bytes) -> H2hImage:
    """Decodes the PL27A1 image header and USB string descriptors.

    Raises ValueError for a blank, unreadable or corrupt image.
    """
    if len(data) < SERIAL_DESCRIPTOR_OFFSET + len(SERIAL_DESCRIPTOR_HEADER):
        raise ValueError(f"only {len(data)} bytes of the image were read")
    if all(b == 0xFF for b in data):
        raise ValueError("the SPI flash reads all 0xFF (blank or not driven)")
    if all(b == 0x00 for b in data):
        raise ValueError("the SPI flash reads all 0x00 (MISO stuck low?)")
    signature, vid, pid, bcd = struct.unpack_from("<2sHHH", data, 0)
    if vid != profile.GOOGLE_VID or pid not in profile.H2H_PIDS:
        raise ValueError(
            f"unexpected image header {vid:04x}:{pid:04x} "
            "(expected 18d1:506d or 18d1:5084)"
        )
    image = H2hImage(
        vid=vid, pid=pid, bcd_device=bcd, manufacturer="", product="", serial=""
    )
    if signature != IMAGE_SIGNATURE:
        image.warnings.append(f"image signature {signature.hex()} (expected 7b06)")
    if bcd != IMAGE_BCD_DEVICE:
        image.warnings.append(f"bcdDevice {bcd:04x} (expected 0200)")
    try:
        strings = _string_descriptors(data)
        image.manufacturer = strings[1].decode("utf-16-le")
        image.product = strings[2].decode("utf-16-le")
        image.serial = strings[3].decode("utf-16-le")
        if strings[0][:2] != struct.pack("<H", LANGID_EN_US):
            image.warnings.append(f"LANGID {strings[0].hex()} (expected 0904)")
    except ValueError as e:
        start = SERIAL_DESCRIPTOR_OFFSET
        if data[start : start + 2] != SERIAL_DESCRIPTOR_HEADER:
            raise ValueError(f"bad USB string descriptors ({e})") from e
        image.warnings.append(f"{e}; used the serial descriptor at 0x{start:02x}")
        image.serial = data[start + 2 : start + SERIAL_DESCRIPTOR_HEADER[0]].decode(
            "utf-16-le", errors="replace"
        )
    for name, value, expected in (
        ("manufacturer", image.manufacturer, EXPECTED_MANUFACTURER),
        ("product", image.product, EXPECTED_PRODUCT),
    ):
        if value and value != expected:
            image.warnings.append(f"{name} '{value}' (expected '{expected}')")
    return image


def check_image(data: bytes) -> Tuple[Optional[H2hImage], str]:
    """Checks that |data| is a raw PL27A1 image that can be flashed.

    That is the 8704-byte .bin of prepare_raw_flash.py ("[Huaqin] PL27A1
    pre-programming", b/563317155): Prolific signature, 18d1:506d and
    decodable USB string descriptors. Returns the decoded image (None if it
    cannot be decoded) and '' if the image is fine, else why not. Other
    image warnings (see decode_image) are no errors.
    """
    if len(data) != IMAGE_SIZE:
        return None, f"{len(data)} bytes; {RAW_IMAGE_HINT}"
    try:
        image = decode_image(data)
    except ValueError as e:
        return None, f"not a PL27A1 H2H image ({e}); {RAW_IMAGE_HINT}"
    if data[: len(IMAGE_SIGNATURE)] != IMAGE_SIGNATURE:
        return image, (
            f"not a PL27A1 H2H image (signature {data[:2].hex()}, expected "
            f"{IMAGE_SIGNATURE.hex()}); {RAW_IMAGE_HINT}"
        )
    if image.pid != IMAGE_PID:
        return image, (
            f"USB ID {image.vid:04x}:{image.pid:04x} in the image (expected "
            f"{profile.GOOGLE_VID:04x}:{IMAGE_PID:04x}); {RAW_IMAGE_HINT}"
        )
    return image, ""


def _serial_descriptor_end(data: bytes) -> Tuple[int, str]:
    """Returns (end offset of the serial descriptor, '') or (0, why not).

    The strict table walk (LANGID, manufacturer, product, serial) has to put
    the serial descriptor at SERIAL_DESCRIPTOR_OFFSET and end it inside the
    string region.
    """
    try:
        bodies = _string_descriptors(data)
    except ValueError as e:
        return 0, f"the USB string descriptors do not decode ({e})"
    serial_offset = STRING_TABLE_OFFSET + sum(len(b) + 2 for b in bodies[:3])
    if serial_offset != SERIAL_DESCRIPTOR_OFFSET:
        return 0, (
            f"the serial descriptor is at 0x{serial_offset:02x} (expected "
            f"0x{SERIAL_DESCRIPTOR_OFFSET:02x})"
        )
    end = serial_offset + 2 + len(bodies[3])
    if end > STRING_REGION_END:
        return 0, f"the serial descriptor ends at 0x{end:02x}, in the config block"
    return end, ""


def serial_patch_error(data: bytes) -> str:
    """Returns '' if the serial of image |data| can be replaced, else why not.

    The serial is the last string descriptor, at the fixed offset
    SERIAL_DESCRIPTOR_OFFSET; the string region is zero-filled up to
    STRING_REGION_END, where the hardware config registers start. The image
    has no checksum (b/563317155; the patched image of b/567837469 booted
    with the new serial), so only the serial descriptor changes.
    """
    return _serial_descriptor_end(data)[1]


def patch_serial(data: bytes, serial: str) -> bytes:
    """Returns a copy of image |data| with the serial number |serial|.

    Clears the old serial descriptor and writes the new one at
    SERIAL_DESCRIPTOR_OFFSET (bLength, USB_DT_STRING, UTF-16LE); every byte
    outside the old and the new descriptor stays as it is. Raises ValueError
    if |serial| is not a v1.2 serial or the image cannot be patched (see
    check_image and serial_patch_error).
    """
    if not profile.is_valid_serial(serial):
        raise ValueError(f"'{serial}' is not a valid Maui v1.2 serial number")
    error = check_image(data)[1]
    old_end, patch_error = _serial_descriptor_end(data)
    if error or patch_error:
        raise ValueError(f"cannot change the serial number: {error or patch_error}")
    encoded = serial.encode("utf-16-le")
    descriptor = bytes([2 + len(encoded), USB_DT_STRING]) + encoded
    new_end = SERIAL_DESCRIPTOR_OFFSET + len(descriptor)
    out = bytearray(data)
    out[SERIAL_DESCRIPTOR_OFFSET:old_end] = bytes(old_end - SERIAL_DESCRIPTOR_OFFSET)
    out[SERIAL_DESCRIPTOR_OFFSET:new_end] = descriptor
    patched = bytes(out)
    # Never flash anything but the intended change.
    if len(patched) != len(data) or decode_image(patched).serial != serial:
        raise ValueError("the patched image does not decode to the new serial")
    return patched


def parse_jedec_id(output: str) -> Optional[Tuple[int, int, int]]:
    """Returns the JEDEC ID printed by `h2h_flash id`, None if missing."""
    m = _JEDEC_RE.search(device.strip_ansi(output))
    return (
        (int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16)) if m else None
    )


def parse_flash_size(output: str) -> str:
    """Returns the `Size:` of `h2h_flash id` (e.g. '512 KB'), '' if missing."""
    m = _SIZE_RE.search(device.strip_ansi(output))
    return f"{m.group(1)} KB" if m else ""


def parse_gpios(output: str) -> Dict[str, int]:
    """Returns the levels listed by `gpio get`."""
    return {
        m.group(1): int(m.group(2))
        for m in _GPIO_RE.finditer(device.strip_ansi(output))
    }


def bus_gpio_mismatches(gpios: Mapping[str, int]) -> List[str]:
    """Returns the bus GPIOs that are not in the idle (bridge running) state."""
    return [
        f"{name}: expected {level}, got {gpios.get(name, 'nothing')}"
        for name, level in IDLE_BUS_GPIOS.items()
        if gpios.get(name) != level
    ]


@contextlib.contextmanager
def open_console(
    tester: device.MauiV12ConsoleTester,
) -> Iterator[ConsoleFn]:
    """Opens the MCU console; yields a function that runs one command."""
    with open_serial_port(
        tester.port_node, tester.baudrate, timeout=tester.timeout
    ) as ser:
        yield lambda command: tester.send_command(
            ser, command, timeout=COMMAND_TIMEOUT_S, first_byte_timeout=0.8
        )


def find_unit(
    device_port: str = "",
    scan_consoles: Callable[[], List[device.McuConsole]] = device.scan_mcu_consoles,
    scan_h2h: Callable[[], List[device.H2hDevice]] = device.scan_h2h_devices,
) -> device.UnitInfo:
    """Returns the board of MCU console |device_port| (default: the first one)."""
    if not device_port:
        return device.find_unit(scan_consoles, scan_h2h)
    consoles = scan_consoles()
    mcu = next((c for c in consoles if c.dev_node == device_port), None)
    h2h = device.pair_h2h(mcu, scan_h2h(), consoles) if mcu else None
    return device.UnitInfo(mcu=mcu, h2h=h2h, consoles=consoles)


class H2hSpiCheck:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """One run of the H2H SPI serial check (see the module docstring)."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        unit: device.UnitInfo,
        open_console_fn: Callable[[str], ContextManager[ConsoleFn]],
        *,
        scan_h2h: Callable[[], List[device.H2hDevice]] = device.scan_h2h_devices,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        environ: Optional[Mapping[str, str]] = None,
        firmware_roots: Optional[Sequence[str]] = None,
    ) -> None:
        self._unit = unit
        self._open_console = open_console_fn
        self._scan_h2h = scan_h2h
        self._sleep = sleep
        self._clock = clock
        self._environ = environ
        self._firmware_roots = firmware_roots
        self._run: Optional[ConsoleFn] = None
        # `h2h_flash acquire` was answered / confirmed by the shell.
        self._acquire_sent = False
        self._acquired = False
        # The PL27A1 reset was pulsed after the access.
        self._reset_sent = False
        # Another owner (an RPC flashing session) holds the bus.
        self._bus_busy = False
        self.result = SpiCheckResult()

    def _log(self, text: str) -> None:
        self.result.log.append(text)

    def _cmd(self, command: str) -> str:
        assert self._run is not None
        output = self._run(command)
        cleaned = "\n".join(line.rstrip() for line in _lines(output)).strip()
        self._log(f"$ {command}\n{cleaned}")
        return output

    def _fail(self, message: str) -> None:
        """Records the first failure (later ones only go to the log)."""
        self._log(f"FAILED: {message}")
        if not self.result.message:
            self.result.message = message

    def run(self) -> SpiCheckResult:
        """Runs the check; never raises."""
        res = self.result
        before = self._host_precheck()
        if before is None:
            return res
        image = None
        try:
            with self._open_console(self._unit.mcu.dev_node) as run:
                self._run = run
                if self._check_firmware():
                    image = self._access_flash()
        except Exception as e:  # pylint: disable=broad-except
            self._fail(f"MCU console error on {self._unit.mcu.dev_node}: {e}")
        after = self._wait_for_h2h()
        if after is None:
            if self._reset_sent:
                problem = "did not come back after the reset"
            elif self._acquired:
                problem = "did not come back after the SPI access"
            else:
                problem = ""
            self._fail(
                f"The H2H bridge {problem} - power-cycle the board."
                if problem
                else "The H2H bridge is no longer detected."
            )
            return res
        res.host_serial_after = after.serial
        self._log(
            f"Host H2H after{' the reset' if self._acquired else ''}: iSerial "
            f"'{after.serial}', {after.speed or '?'} Mb/s, {after.usb_path} "
            f"(device {after.devnum or '?'})"
        )
        if image is None or res.message:
            return res
        if res.spi_serial != before.serial or res.spi_serial != after.serial:
            self._fail(
                f"SPI serial '{res.spi_serial}' does not match the host iSerial "
                f"'{before.serial}' (before) / '{after.serial}' (after the reset)"
            )
            return res
        res.success = True
        res.message = (
            f"H2H SPI flash serial {res.spi_serial} matches the host iSerial before "
            f"the check and after the reset (JEDEC ID {res.jedec_id}, "
            f"{res.flash_size})."
        )
        self._log(res.message)
        return res

    def _host_precheck(self) -> Optional[device.H2hDevice]:
        mcu, h2h = self._unit.mcu, self._unit.h2h
        if not mcu or not mcu.is_app:
            self._fail("Maui MCU App console (18d1:5085) not detected.")
            return None
        if not h2h:
            self._fail(
                "H2H bridge (18d1:506d) not detected before the check: the PL27A1 "
                "does not boot or is not on the same hub as the MCU."
            )
            return None
        self.result.host_serial_before = h2h.serial
        self._log(
            f"MCU console {mcu.dev_node}; host H2H before: iSerial '{h2h.serial}', "
            f"product '{h2h.product}', {h2h.speed or '?'} Mb/s, {h2h.usb_path} "
            f"(device {h2h.devnum or '?'})"
        )
        if not profile.is_valid_serial(h2h.serial):
            self._fail(f"H2H iSerial '{h2h.serial}' is not a valid Maui v1.2 serial.")
            return None
        return h2h

    def _check_firmware(self) -> bool:
        """Gates the check on an approved MCU App and an idle SPI bus."""
        out = self._cmd("version")
        if "Maui version" not in device.strip_ansi(out):
            answer = _summary(out, "version")
            self._fail(f"The MCU console did not answer 'version' ({answer}).")
            return False
        check = profile.check_mcu_version(
            out, self._environ, self._firmware_roots, require_approval=True
        )
        self.result.mcu_version = check.build_id
        self._log(check.message)
        if not check.ok:
            if profile.NO_STABLE_FIRMWARE in check.message:
                # Nothing to compare with: not known to be unsupported.
                self._fail(f"{check.message} The SPI flash was not touched.")
                return False
            self.result.firmware_supported = False
            self._fail(
                f"{check.message} The H2H SPI check needs an approved MCU App: "
                f"{UPDATE_HINT}. The SPI flash was not touched."
            )
            return False
        mismatches = bus_gpio_mismatches(parse_gpios(self._cmd("gpio get")))
        if mismatches:
            self._fail(
                f"The H2H SPI bus is not idle ({'; '.join(mismatches)}): wait 10 s "
                "for a running flashing session or power-cycle the board. The SPI "
                "flash was not touched."
            )
            return False
        return True

    def _access_flash(self) -> Optional[H2hImage]:
        """Acquires the bus, identifies and reads the flash, then releases."""
        image = None
        try:
            out = self._cmd("h2h_flash acquire")
            self._acquire_sent = True
            if "acquired" not in out.lower():
                if "busy" in out.lower():
                    self._bus_busy = True
                    raise CheckError(
                        "The H2H flash is busy (RPC flashing session): retry in 10 s."
                    )
                raise CheckError(
                    f"'h2h_flash acquire' failed: {_summary(out, 'h2h_flash acquire')}"
                )
            self._acquired = True
            self._identify()
            image = self._read_image()
        except CheckError as e:
            self._fail(str(e))
        finally:
            self._release()
        return image if not self.result.message else None

    def _identify(self) -> None:
        """Reads the JEDEC ID; the `Size:` line marks a supported MCU App."""
        out = ""
        jedec = None
        for _ in range(ID_ATTEMPTS):
            out = self._cmd("h2h_flash id")
            jedec = parse_jedec_id(out)
            if jedec or "Failed to read JEDEC ID" in out:
                break
        if jedec is None:
            answer = _summary(out, "h2h_flash id")
            if "Failed to read JEDEC ID" in out or answer == "(no output)":
                raise CheckError(f"Could not read the JEDEC ID: {answer}")
            self.result.firmware_supported = False
            raise CheckError(
                f"'h2h_flash id' did not report a JEDEC ID ({answer}): this "
                f"MCU App cannot access the H2H SPI flash, {UPDATE_HINT}."
            )
        res = self.result
        res.jedec_id = "0x" + "".join(f"{b:02X}" for b in jedec)
        if jedec in ((0xFF, 0xFF, 0xFF), (0x00, 0x00, 0x00)):
            raise CheckError(
                f"No answer from the H2H SPI flash (JEDEC ID {res.jedec_id}): check "
                "the SPI mux, chip select and flash power."
            )
        res.flash_size = parse_flash_size(out)
        if not res.flash_size:
            if jedec[2] not in SIZED_CAPACITY_CODES:
                raise CheckError(
                    f"Unsupported H2H SPI flash (JEDEC ID {res.jedec_id}, "
                    f"capacity code 0x{jedec[2]:02X})."
                )
            res.firmware_supported = False
            raise CheckError(
                "'h2h_flash id' printed no 'Size:' line: this MCU App lacks the H2H "
                f"flash fixes, {UPDATE_HINT}. The SPI flash was not read."
            )

    def _read_image(self) -> H2hImage:
        """Reads and decodes the image header (retried on bad console output)."""
        error = ""
        for attempt in range(1, READ_ATTEMPTS + 1):
            out = self._cmd(READ_COMMAND)
            try:
                data = parse_hexdump(out)
                self._log(f"Image header: {data.hex(' ')}")
                image = decode_image(data)
            except ValueError as e:
                failed = _READ_ERROR_RE.search(out)
                error = f"Flash read failed ({failed.group(1)})" if failed else str(e)
                self._log(f"Read attempt {attempt}/{READ_ATTEMPTS}: {error}")
                continue
            for warning in image.warnings:
                self._log(f"WARNING: {warning}")
            self.result.spi_serial = image.serial
            self._log(
                f"SPI image: {image.vid:04x}:{image.pid:04x}, manufacturer "
                f"'{image.manufacturer}', product '{image.product}', serial "
                f"'{image.serial}'"
            )
            if not profile.is_valid_serial(image.serial):
                raise CheckError(
                    f"SPI serial '{image.serial}' is not a valid Maui v1.2 serial."
                )
            return image
        raise CheckError(f"Could not read the H2H SPI flash: {error}.")

    def _release(self) -> None:
        """Hands the bus back to the PL27A1 and resets the bridge.

        Only runs after a confirmed `h2h_flash acquire`: a busy bus belongs to
        an RPC flashing session, which must never be disturbed, and an
        unconfirmed acquire is not answered with a release either. If the
        release left the bus GPIOs wrong, restores them (best effort, not
        verified on hardware; includes the reset pulse), else pulses the reset.
        """
        if not self._acquired:
            if self._bus_busy:
                self._log("Bus not released: it belongs to an RPC flashing session.")
            elif self._acquire_sent:
                self._log("Bus not released: 'h2h_flash acquire' was not confirmed.")
            return
        try:
            out = self._cmd("h2h_flash release")
            if "released" not in out.lower():
                answer = _summary(out, "h2h_flash release")
                self._log(f"WARNING: release not confirmed: {answer}")
            mismatches = bus_gpio_mismatches(parse_gpios(self._cmd("gpio get")))
            if not mismatches:
                self._reset_bridge()
                return
            self._log(
                f"Bus GPIOs after the release: {'; '.join(mismatches)}. Trying the "
                "best-effort recovery (unverified): restore the SPI mux and pulse "
                "the PL27A1 reset."
            )
            self._cmd("gpio set ec_mem_sel 0")
            self._cmd("gpio set ec_mem_oe_l 1")
            self._reset_sent = True
            self._cmd("gpio set h2h_reset_l 1")
            self._sleep(RESET_PULSE_S)
            self._cmd("gpio set h2h_reset_l 0")
            left = bus_gpio_mismatches(parse_gpios(self._cmd("gpio get")))
            problem = "; ".join(mismatches)
            self._fail(
                f"The H2H SPI bus was not released correctly ({problem}); "
                + (
                    f"the recovery did not help ({'; '.join(left)}), power-cycle "
                    "the board."
                    if left
                    else "the best-effort recovery restored the bus GPIOs "
                    "(unverified)."
                )
            )
        except CheckError as e:
            self._fail(str(e))
        except Exception as e:  # pylint: disable=broad-except
            self._fail(f"Could not release the H2H SPI bus: {e}")

    def _reset_bridge(self) -> None:
        """Pulses the PL27A1 reset so that it boots from its flash again."""
        self._reset_sent = True
        reset_bridge(self._cmd, self._sleep, self._log)

    def _wait_for_h2h(self) -> Optional[device.H2hDevice]:
        """Returns the bridge as the host sees it, None if it does not show up.

        After a bus access the host is only polled after REENUM_SETTLE_S, so
        that the enumeration from before the reset is gone, until
        REENUM_TIMEOUT_S. Without a bus access the current enumeration counts.
        """
        return wait_for_h2h(
            self._unit.mcu,
            self._unit.consoles,
            settle=self._acquired,
            scan_h2h=self._scan_h2h,
            sleep=self._sleep,
            clock=self._clock,
        )


def reset_bridge(
    cmd: ConsoleFn,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda text: None,
) -> None:
    """Pulses the PL27A1 reset so that it boots from its flash again.

    |cmd| runs one MCU console command. h2h_reset_l is active low and
    `gpio set` takes logical levels: 1 holds the bridge in reset, 0 lets it
    run. `gpio set` prints the new level ("[h2h_reset_l] O = 0"). The reset
    is always released; raises CheckError if the bridge stays in reset.
    """
    try:
        out = cmd("gpio set h2h_reset_l 1")
        if parse_gpios(out).get("h2h_reset_l") != 1:
            log(
                "WARNING: 'gpio set h2h_reset_l 1' was not confirmed: the bridge "
                "may not have been reset."
            )
        sleep(RESET_PULSE_S)
    finally:
        out = cmd("gpio set h2h_reset_l 0")
    if parse_gpios(out).get("h2h_reset_l") == 0:
        return
    level = parse_gpios(cmd("gpio get")).get("h2h_reset_l", "nothing")
    if level != 0:
        raise CheckError(
            "Could not take the H2H bridge out of reset (h2h_reset_l: expected "
            f"0, got {level}) - power-cycle the board."
        )


def wait_for_h2h(  # pylint: disable=too-many-arguments
    mcu: Optional[device.McuConsole],
    consoles: Sequence[device.McuConsole],
    *,
    settle: bool = True,
    timeout_s: float = REENUM_TIMEOUT_S,
    scan_h2h: Callable[[], List[device.H2hDevice]] = device.scan_h2h_devices,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[device.H2hDevice]:
    """Returns the H2H bridge of the board of |mcu|, None if it does not show up.

    With |settle| (after a reset of the bridge) the host is only polled after
    REENUM_SETTLE_S, so that the enumeration from before the reset is gone;
    the bridge has |timeout_s| from the call to show up.
    """
    deadline = clock() + timeout_s
    if settle:
        sleep(REENUM_SETTLE_S)
    while True:
        h2h = device.pair_h2h(mcu, scan_h2h(), list(consoles)) if mcu else None
        if h2h is not None or clock() >= deadline:
            return h2h
        sleep(REENUM_POLL_S)


def run_check(
    unit: device.UnitInfo,
    make_tester: Callable[[str], device.MauiV12ConsoleTester],
    **kwargs,
) -> SpiCheckResult:
    """Runs the check on the MCU console of |unit| (see H2hSpiCheck)."""
    return H2hSpiCheck(
        unit, lambda port: open_console(make_tester(port)), **kwargs
    ).run()


def format_log(res: SpiCheckResult) -> str:
    """Returns the content of h2h_spi_check.log."""
    return (
        f"ALL_PASSED={res.success}\n"
        f"RESULT: {'PASSED' if res.success else 'FAILED'}\n"
        f"{res.message}\n"
        f"Firmware Supported: {res.firmware_supported}\n"
        f"MCU App: {res.mcu_version or 'unknown'}\n"
        f"Host Serial Before: {res.host_serial_before}\n"
        f"SPI Serial: {res.spi_serial}\n"
        f"Host Serial After: {res.host_serial_after}\n"
        f"JEDEC ID: {res.jedec_id}\n"
        f"Flash Size: {res.flash_size}\n\n" + "\n".join(res.log)
    )
