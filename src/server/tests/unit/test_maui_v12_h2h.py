# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for the Maui v1.2 H2H SPI flash access check."""

import contextlib
import json
import os
import tempfile
import unittest
from unittest import mock

import serial

from server import maui_v12_device as device
from server import maui_v12_h2h as h2h
from server import maui_v12_mcu_service as mcu_service
from server import maui_v12_profile as profile
from server import maui_v12_services
from server.generated import maui_manufacturing_pb2


SN = "MAUIV12H26090104"
# The H2H check needs an approved MCU App (no bundled STABLE.json in the tests).
APPROVED = {"MAUI_V12_APPROVED_FW": "0.69-5324f43"}
# The end of every bus access: release, bus GPIO check, PL27A1 reset pulse.
RELEASE_AND_RESET = [
    "h2h_flash release",
    "gpio get",
    "gpio set h2h_reset_l 1",
    "gpio set h2h_reset_l 0",
]
VERSION_069 = (
    "Maui version 0.69-5324f43 (h2h-read-rpc, built 2026-10-13 08:45:17 UTC) "
    "[MCUBOOT-SLOT0 (0x80038200)]"
)
VERSION_064 = (
    "Maui version 0.64-86c6fbb (main, built 2026-10-15 09:12:01 UTC) "
    "[MCUBOOT-SLOT0 (0x80038200)]"
)
VERSION_059 = (
    "Maui version 0.59-838d947 (ite_freeze, built 2026-09-19 21:06:53 UTC) "
    "[MCUBOOT-SLOT0 (0x80038200)]"
)
# First 96 bytes of the PL27A1 image of MAUIV12H26090104 (b/567837469).
IMAGE = bytes.fromhex(
    "7b06d1186d500002a032000000000000"
    "04030904160347006f006f0067006c00"
    "650020004c004c0043000e0341004400"
    "420020007600310022034d0041005500"
    "49005600310032004800320036003000"
    "39003000310030003400000000000000"
)
ID_REWORK = (
    "h2h_flash id\r\n"
    "JEDEC ID: 0x85 0x20 0x13 (Manufacturer: 0x85, Type: 0x20, Capacity: 0x13)\r\n"
    "Size: 512 KB\r\nmaui$ "
)
ID_MAIN = (
    "h2h_flash id\r\n"
    "JEDEC ID: 0x85 0x20 0x13 (Manufacturer: 0x85, Type: 0x20, Capacity: 0x13)\r\n"
    "maui$ "
)
ID_NO_FLASH = (
    "h2h_flash id\r\n"
    "JEDEC ID: 0xFF 0xFF 0xFF (Manufacturer: 0xFF, Type: 0xFF, Capacity: 0xFF)\r\n"
    "maui$ "
)
OTHER_GPIOS = (
    "[              hub_reset_l] O = 0\r\n"
    "[            pp5000_cutoff] O = 0\r\n"
    "[        spi_ec_wp_mem_r_l] O = 0\r\n"
    "[      spi_ec_hold_mem_r_l] O = 0\r\n"
    "[    h2h_host_access_led_l] I = 1\r\n"
)


def zephyr_hexdump(data):
    """Formats |data| like the Zephyr shell_hexdump() (relative offsets)."""
    lines = []
    for base in range(0, len(data), 16):
        chunk = data[base : base + 16]
        line = f"{base:08X}: "
        for i in range(16):
            if i and i % 8 == 0:
                line += " "
            line += f"{chunk[i]:02x} " if i < len(chunk) else "   "
        line += "|"
        for i in range(16):
            if i and i % 8 == 0:
                line += " "
            if i < len(chunk):
                line += chr(chunk[i]) if 0x20 <= chunk[i] < 0x7F else "."
            else:
                line += " "
        lines.append(line + "|")
    return "\r\n".join(lines)


def read_output(data=IMAGE, noise=True):
    """`h2h_flash read 0x0 96` output with echo, prompt and deferred logs."""
    out = f"h2h_flash read 0x0 96\r\n{zephyr_hexdump(data)}\r\n"
    if noise:
        out += (
            "\x1b[1;32mmaui$ \x1b[m[00:01:18.037,536] <inf> h2h_flash: H2H SPI "
            "flash bus acquired by EC\r\n"
        )
    return out + "maui$ "


def image_with_serial(serial_number):
    """IMAGE with another 16-character serial patched in at 0x3a."""
    encoded = serial_number.encode("utf-16-le")
    return IMAGE[:0x3A] + encoded + IMAGE[0x3A + len(encoded) :]


# pylint: disable-next=too-many-instance-attributes
class SimBoard:
    """Simulated MCU console and PL27A1 bridge.

    The bridge drops off USB while it is held in reset and re-enumerates
    0.3 s after it was released with the SPI mux enabled. With |release_bug|
    the release disables the mux buffer (old firmware), so the bridge only
    comes back after the recovery pulse. |resets| lists the (start, end)
    times of every reset of the bridge.
    """

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        version=VERSION_069,
        id_outputs=(ID_REWORK,),
        reads=None,
        release_bug=False,
        never_back=False,
        acquire_output=None,
    ):
        self.version = version
        self.id_outputs = list(id_outputs)
        self.reads = list(reads) if reads is not None else [read_output()]
        self.release_bug = release_bug
        self.never_back = never_back
        self.acquire_output = acquire_output
        self.gpios = {"h2h_reset_l": 0, "ec_mem_sel": 0, "ec_mem_oe_l": 1}
        self.commands = []
        self.now = 0.0
        self.devnum = 31
        self.serial = SN
        self.back_at = 0.0
        self.raise_on = None
        self.reset_at = None
        self.resets = []

    # Console ---------------------------------------------------------------
    def run(self, command):  # pylint: disable=too-many-return-statements
        """Answers one console command."""
        self.commands.append(command)
        if self.raise_on and command.startswith(self.raise_on):
            raise serial.SerialException("read failed: device disconnected?")
        if command == "version":
            return f"version\r\n{self.version}\r\nmaui$ "
        if command == "gpio get":
            listing = "".join(
                f"[{name:>25}] O = {level}\r\n" for name, level in self.gpios.items()
            )
            return f"gpio get\r\n{listing}{OTHER_GPIOS}maui$ "
        if command == "h2h_flash acquire":
            if self.acquire_output is not None:
                return self.acquire_output
            self._set(h2h_reset_l=1, ec_mem_sel=1, ec_mem_oe_l=1)
            return (
                "h2h_flash acquire\r\nH2H SPI bus acquired by EC (PL27A1 held in "
                "reset)\r\nmaui$ "
            )
        if command == "h2h_flash id":
            return (
                self.id_outputs.pop(0)
                if len(self.id_outputs) > 1
                else self.id_outputs[0]
            )
        if command.startswith("h2h_flash read"):
            return self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
        if command == "h2h_flash release":
            if self.release_bug:
                self._set(ec_mem_oe_l=0, ec_mem_sel=0, h2h_reset_l=0)
            else:
                self._set(ec_mem_sel=0, ec_mem_oe_l=1, h2h_reset_l=0)
            return (
                "h2h_flash release\r\nH2H SPI bus released (PL27A1 running)\r\nmaui$ "
            )
        if command.startswith("gpio set "):
            _, _, name, level = command.split()
            self._set(**{name: int(level)})
            return f"{command}\r\n[{name:>25}] O = {int(level)}\r\nmaui$ "
        return f"{command}\r\nmaui$ "

    @contextlib.contextmanager
    def open_console(self, unused_port):
        """Stands in for h2h.open_console()."""
        yield self.run

    # Bridge ----------------------------------------------------------------
    def _set(self, **levels):
        was_in_reset = self.gpios["h2h_reset_l"] == 1
        self.gpios.update(levels)
        in_reset = self.gpios["h2h_reset_l"] == 1
        if in_reset:
            if not was_in_reset:
                self.reset_at = self.now
            self.back_at = None
        elif was_in_reset:
            self.resets.append((self.reset_at, self.now))
            boots = self.gpios["ec_mem_oe_l"] == 1 and self.gpios["ec_mem_sel"] == 0
            if boots and not self.never_back:
                self.devnum += 1
                self.back_at = self.now + 0.3
            else:
                self.back_at = None

    def scan(self):
        """Stands in for device.scan_h2h_devices()."""
        if self.back_at is None or self.now < self.back_at:
            return []
        return [
            device.H2hDevice(
                usb_path="/sys/devices/usb2/2-3/2-3.3",
                serial=self.serial,
                product="ADB v1",
                pid=0x506D,
                speed="5000",
                devnum=str(self.devnum),
            )
        ]

    def sleep(self, seconds):
        """Advances the simulated clock."""
        self.now += seconds


def _unit(h2h_serial=SN, product=profile.PRODUCT_APP):
    mcu = device.McuConsole(
        dev_node="/dev/ttyACM0",
        usb_path="/sys/devices/usb1/1-3/1-3.1",
        serial="Uninitialized",
        product=product,
        stage=profile.stage_for_product(product),
    )
    bridge = device.H2hDevice(
        usb_path="/sys/devices/usb1/1-3/1-3.3",
        serial=h2h_serial,
        product="ADB v1",
        pid=0x506D,
        speed="480",
        devnum="31",
    )
    return device.UnitInfo(mcu=mcu, h2h=bridge, consoles=[mcu])


def _check(board, unit=None, environ=None, firmware_roots=()):
    return h2h.H2hSpiCheck(
        unit or _unit(),
        board.open_console,
        scan_h2h=board.scan,
        sleep=board.sleep,
        clock=lambda: board.now,
        environ=environ if environ is not None else APPROVED,
        firmware_roots=firmware_roots,
    ).run()


class TestHexdump(unittest.TestCase):
    """parse_hexdump() of the Zephyr shell_hexdump() format."""

    def test_round_trip_with_console_noise(self):
        self.assertEqual(h2h.parse_hexdump(read_output()), IMAGE)

    def test_prompt_before_the_first_line(self):
        out = "maui$ " + zephyr_hexdump(IMAGE)
        self.assertEqual(h2h.parse_hexdump(out), IMAGE)

    def test_short_or_empty_dump(self):
        with self.assertRaisesRegex(ValueError, "holds 48 of 96 bytes"):
            h2h.parse_hexdump(read_output(IMAGE[:48]))
        with self.assertRaisesRegex(ValueError, "holds 0 of 96 bytes"):
            h2h.parse_hexdump("h2h_flash read 0x0 96\r\nmaui$ ")

    def test_partial_last_line(self):
        self.assertEqual(h2h.parse_hexdump(zephyr_hexdump(IMAGE[:20]), 20), IMAGE[:20])

    def test_gap_is_rejected(self):
        lines = zephyr_hexdump(IMAGE).split("\r\n")
        del lines[2]
        with self.assertRaisesRegex(ValueError, "0x30 where 0x20 was expected"):
            h2h.parse_hexdump("\r\n".join(lines))


class TestDecodeImage(unittest.TestCase):
    """decode_image() of the PL27A1 image header."""

    def test_observed_image(self):
        image = h2h.decode_image(IMAGE)
        self.assertEqual(
            (image.vid, image.pid, image.bcd_device), (0x18D1, 0x506D, 0x200)
        )
        self.assertEqual(image.manufacturer, "Google LLC")
        self.assertEqual(image.product, "ADB v1")
        self.assertEqual(image.serial, SN)
        self.assertEqual(image.warnings, [])

    def test_blank_and_stuck_flash(self):
        with self.assertRaisesRegex(ValueError, "all 0xFF"):
            h2h.decode_image(b"\xff" * 96)
        with self.assertRaisesRegex(ValueError, "all 0x00"):
            h2h.decode_image(bytes(96))

    def test_bad_header(self):
        with self.assertRaisesRegex(ValueError, "unexpected image header 18d1:5066"):
            h2h.decode_image(IMAGE[:4] + b"\x66\x50" + IMAGE[6:])

    def test_broken_descriptor_chain_falls_back_to_0x38(self):
        broken = IMAGE[:0x14] + b"\x15" + IMAGE[0x15:]  # Odd bLength.
        image = h2h.decode_image(broken)
        self.assertEqual(image.serial, SN)
        self.assertIn("bad string descriptor #1 at 0x14", image.warnings[0])
        with self.assertRaisesRegex(ValueError, "bad USB string descriptors"):
            h2h.decode_image(broken[:0x38] + b"\x00\x00" + broken[0x3A:])

    def test_warnings(self):
        image = h2h.decode_image(b"\x00\x00" + IMAGE[2:6] + b"\x01\x02" + IMAGE[8:])
        self.assertEqual(
            image.warnings,
            ["image signature 0000 (expected 7b06)", "bcdDevice 0201 (expected 0200)"],
        )


class TestParsers(unittest.TestCase):
    """JEDEC ID, size and GPIO parsing."""

    def test_jedec_and_size(self):
        self.assertEqual(h2h.parse_jedec_id(ID_REWORK), (0x85, 0x20, 0x13))
        self.assertEqual(h2h.parse_flash_size(ID_REWORK), "512 KB")
        self.assertEqual(h2h.parse_flash_size(ID_MAIN), "")
        self.assertIsNone(h2h.parse_jedec_id("h2h_flash id\r\nmaui$ "))

    def test_bus_gpios(self):
        gpios = h2h.parse_gpios(SimBoard().run("gpio get"))
        self.assertEqual(h2h.bus_gpio_mismatches(gpios), [])
        gpios["ec_mem_oe_l"] = 0
        del gpios["h2h_reset_l"]
        self.assertEqual(
            h2h.bus_gpio_mismatches(gpios),
            ["h2h_reset_l: expected 0, got nothing", "ec_mem_oe_l: expected 1, got 0"],
        )


class TestH2hSpiCheck(unittest.TestCase):
    """The check procedure against a simulated board."""

    def test_passes_on_an_approved_build(self):
        board = SimBoard()
        res = _check(board)
        self.assertTrue(res.success, res.message)
        self.assertTrue(res.firmware_supported)
        self.assertEqual(
            (res.host_serial_before, res.spi_serial, res.host_serial_after),
            (SN, SN, SN),
        )
        self.assertEqual((res.jedec_id, res.flash_size), ("0x852013", "512 KB"))
        self.assertEqual(res.mcu_version, "0.69-5324f43")
        self.assertEqual(
            board.commands,
            [
                "version",
                "gpio get",
                "h2h_flash acquire",
                "h2h_flash id",
                "h2h_flash read 0x0 96",
            ]
            + RELEASE_AND_RESET,
        )
        log = "\n".join(res.log)
        self.assertIn(f"Host H2H after the reset: iSerial '{SN}'", log)
        self.assertIn("(device 33)", log)
        self.assertIn(
            "matches the host iSerial before the check and after the reset",
            res.message,
        )

    def test_silent_console_is_not_a_firmware_problem(self):
        board = SimBoard(version="")
        board.run = lambda command: (
            f"{command}\r\nmaui$ "
            if command == "version"
            else SimBoard.run(board, command)
        )
        res = _check(board)
        self.assertFalse(res.success)
        self.assertTrue(res.firmware_supported)
        self.assertIn("did not answer 'version' ((no output))", res.message)
        self.assertEqual(res.host_serial_after, SN)

    def test_old_firmware_without_size_line_is_not_read(self):
        board = SimBoard(version=VERSION_064, id_outputs=[ID_MAIN], release_bug=True)
        res = _check(board, environ={"MAUI_V12_APPROVED_FW": "any"})
        self.assertFalse(res.success)
        self.assertFalse(res.firmware_supported)
        self.assertIn("no 'Size:' line", res.message)
        self.assertIn("update the MCU App", res.message)
        self.assertNotIn("h2h_flash read 0x0 96", board.commands)
        self.assertIn("h2h_flash release", board.commands)
        # The release disabled the SPI mux buffer: the recovery restored it.
        self.assertEqual(
            board.commands[board.commands.index("h2h_flash release") + 2 :],
            [
                "gpio set ec_mem_sel 0",
                "gpio set ec_mem_oe_l 1",
                "gpio set h2h_reset_l 1",
                "gpio set h2h_reset_l 0",
                "gpio get",
            ],
        )
        log = "\n".join(res.log)
        self.assertIn("best-effort recovery (unverified)", log)
        self.assertIn("restored the bus GPIOs (unverified)", log)
        self.assertEqual(res.host_serial_after, SN)

    def test_factory_firmware_without_id_command(self):
        board = SimBoard(
            version=VERSION_059,
            id_outputs=["h2h_flash id\r\nh2h_flash: unknown parameter: id\r\nmaui$ "],
        )
        res = _check(board, environ={"MAUI_V12_APPROVED_FW": "any"})
        self.assertFalse(res.firmware_supported)
        self.assertIn("did not report a JEDEC ID", res.message)
        self.assertNotIn("h2h_flash read 0x0 96", board.commands)
        self.assertIn("h2h_flash release", board.commands)

    def test_no_flash_answer(self):
        board = SimBoard(id_outputs=[ID_NO_FLASH])
        res = _check(board)
        self.assertFalse(res.success)
        self.assertTrue(res.firmware_supported)
        self.assertEqual(res.jedec_id, "0xFFFFFF")
        self.assertIn(
            "No answer from the H2H SPI flash (JEDEC ID 0xFFFFFF)", res.message
        )
        self.assertNotIn("h2h_flash read 0x0 96", board.commands)
        self.assertEqual(board.commands[-4:], RELEASE_AND_RESET)

    def test_empty_id_answer_is_retried(self):
        board = SimBoard(id_outputs=["", ID_REWORK])
        res = _check(board)
        self.assertTrue(res.success, res.message)
        self.assertEqual(board.commands.count("h2h_flash id"), 2)

    def test_serial_mismatch(self):
        board = SimBoard(reads=[read_output(image_with_serial("MAUIV12H26090100"))])
        res = _check(board)
        self.assertFalse(res.success)
        self.assertEqual(res.spi_serial, "MAUIV12H26090100")
        self.assertIn(
            "SPI serial 'MAUIV12H26090100' does not match the host iSerial "
            f"'{SN}' (before) / '{SN}' (after the reset)",
            res.message,
        )
        self.assertIn("h2h_flash release", board.commands)

    def test_invalid_spi_serial(self):
        board = SimBoard(reads=[read_output(image_with_serial("TESTV11111000001"))])
        res = _check(board)
        self.assertFalse(res.success)
        self.assertIn("'TESTV11111000001' is not a valid Maui v1.2 serial", res.message)

    def test_read_is_retried(self):
        board = SimBoard(
            reads=[
                "h2h_flash read 0x0 96\r\nmaui$ ",
                "h2h_flash read 0x0 96\r\n\x1b[1;31mFlash read failed (-5)\x1b[0m\r\n",
                read_output(),
            ]
        )
        res = _check(board)
        self.assertTrue(res.success, res.message)
        self.assertEqual(board.commands.count("h2h_flash read 0x0 96"), 3)
        self.assertIn("Read attempt 2/3: Flash read failed (-5)", "\n".join(res.log))

    def test_read_fails_after_the_retries(self):
        board = SimBoard(reads=[read_output(IMAGE[:48])])
        res = _check(board)
        self.assertFalse(res.success)
        self.assertIn(
            "Could not read the H2H SPI flash: hexdump holds 48 of 96 bytes",
            res.message,
        )
        self.assertEqual(board.commands.count("h2h_flash read 0x0 96"), 3)
        self.assertIn("h2h_flash release", board.commands)

    def test_blank_flash(self):
        board = SimBoard(reads=[read_output(b"\xff" * 96)])
        res = _check(board)
        self.assertFalse(res.success)
        self.assertIn("all 0xFF", res.message)

    def test_release_is_called_when_the_console_fails(self):
        board = SimBoard()
        board.raise_on = "h2h_flash read"
        res = _check(board)
        self.assertFalse(res.success)
        self.assertIn("MCU console error on /dev/ttyACM0", res.message)
        self.assertEqual(board.commands[-4:], RELEASE_AND_RESET)
        self.assertEqual(res.host_serial_after, SN)

    def test_host_prechecks(self):
        board = SimBoard()
        res = _check(board, unit=device.UnitInfo())
        self.assertIn("MCU App console (18d1:5085) not detected", res.message)
        unit = _unit()
        unit.h2h = None
        self.assertIn(
            "H2H bridge (18d1:506d) not detected", _check(board, unit).message
        )
        res = _check(board, unit=_unit(h2h_serial="Uninitialized"))
        self.assertIn("'Uninitialized' is not a valid Maui v1.2 serial", res.message)
        boot = _unit(product=profile.PRODUCT_RW_BOOTLOADER)
        self.assertIn("not detected", _check(board, boot).message)
        self.assertEqual(board.commands, [])

    def test_format_log(self):
        res = _check(SimBoard())
        text = h2h.format_log(res)
        self.assertTrue(text.startswith("ALL_PASSED=True\nRESULT: PASSED\n"))
        self.assertIn(f"SPI Serial: {SN}", text)
        self.assertIn("$ h2h_flash read 0x0 96", text)
        self.assertNotIn("bypass", text.lower())


class TestH2hReset(unittest.TestCase):
    """The explicit PL27A1 reset after the release and the host check."""

    def test_reset_pulse_after_the_release(self):
        board = SimBoard()
        res = _check(board)
        self.assertTrue(res.success, res.message)
        self.assertEqual(board.commands[-4:], RELEASE_AND_RESET)
        start, end = board.resets[-1]
        self.assertGreaterEqual(end - start, 0.1)
        self.assertLessEqual(end - start, 0.2)
        self.assertEqual(board.gpios["h2h_reset_l"], 0)

    def test_host_is_polled_only_after_the_reset_settled(self):
        board = SimBoard()
        scans = []
        real_scan = board.scan

        def scan():
            scans.append(board.now)
            return real_scan()

        board.scan = scan
        res = _check(board)
        self.assertTrue(res.success, res.message)
        self.assertAlmostEqual(scans[0] - board.resets[-1][1], h2h.REENUM_SETTLE_S)

    def test_device_number_does_not_matter(self):
        board = SimBoard()
        unit = _unit()
        unit.h2h.usb_path = "/sys/devices/usb2/2-3/2-3.3"
        unit.h2h.devnum = "33"  # The simulated bridge is device 33 after the reset.
        res = _check(board, unit=unit)
        self.assertTrue(res.success, res.message)
        self.assertNotIn("enumeration", "\n".join(res.log))
        self.assertLess(board.now, h2h.REENUM_SETTLE_S + 1.0)

    def test_bridge_not_back_after_the_reset(self):
        board = SimBoard(never_back=True)
        res = _check(board)
        self.assertFalse(res.success)
        self.assertEqual(
            res.message,
            "The H2H bridge did not come back after the reset - power-cycle the "
            "board.",
        )
        self.assertEqual(res.host_serial_after, "")
        self.assertGreaterEqual(board.now, h2h.REENUM_TIMEOUT_S)

    def test_serial_after_the_reset_must_match(self):
        board = SimBoard()
        board.serial = "MAUIV12H26090100"  # Only seen after the reset.
        res = _check(board)
        self.assertFalse(res.success)
        self.assertEqual(res.host_serial_after, "MAUIV12H26090100")
        self.assertIn(
            f"SPI serial '{SN}' does not match the host iSerial '{SN}' (before) / "
            "'MAUIV12H26090100' (after the reset)",
            res.message,
        )

    def test_gpio_set_without_level_output_falls_back_to_gpio_get(self):
        board = SimBoard()
        real_run = board.run

        def run(command):
            out = real_run(command)
            return f"{command}\r\nmaui$ " if command.startswith("gpio set") else out

        board.run = run
        res = _check(board)
        self.assertTrue(res.success, res.message)
        self.assertEqual(board.commands[-5:], RELEASE_AND_RESET + ["gpio get"])
        self.assertIn(
            "WARNING: 'gpio set h2h_reset_l 1' was not confirmed", "\n".join(res.log)
        )

    def test_reset_that_stays_asserted_fails(self):
        board = SimBoard()
        real_run = board.run

        def run(command):
            if command == "gpio set h2h_reset_l 0":
                board.commands.append(command)
                return f"{command}\r\nmaui$ "  # -EINVAL: nothing changes.
            return real_run(command)

        board.run = run
        res = _check(board)
        self.assertFalse(res.success)
        self.assertEqual(
            res.message,
            "Could not take the H2H bridge out of reset (h2h_reset_l: expected 0, "
            "got 1) - power-cycle the board.",
        )
        self.assertEqual(board.commands[-1], "gpio get")


class TestH2hBusOwnership(unittest.TestCase):
    """The bus is only released after a confirmed acquire."""

    def test_busy_bus(self):
        board = SimBoard(
            acquire_output=(
                "h2h_flash acquire\r\n\x1b[1;31mH2H flash is busy (RPC flashing "
                "session)\x1b[0m\r\nmaui$ "
            )
        )
        res = _check(board)
        self.assertFalse(res.success)
        self.assertIn("busy (RPC flashing session)", res.message)
        self.assertNotIn("h2h_flash id", board.commands)
        # The shell must not release a bus that an RPC session owns.
        self.assertNotIn("h2h_flash release", board.commands)
        self.assertEqual(board.commands[-1], "h2h_flash acquire")
        # Never acquired: the current enumeration is accepted at once.
        self.assertEqual(board.now, 0.0)

    def test_bus_taken_by_rpc_is_not_recovered(self):
        board = SimBoard(
            acquire_output=(
                "h2h_flash acquire\r\nH2H flash is busy (RPC flashing session)\r\n"
            )
        )
        real_run = board.run

        def run(command):
            if command == "h2h_flash acquire":
                # An RPC flashing session took the bus after the pre-check.
                board.gpios.update(h2h_reset_l=1, ec_mem_sel=1)
            return real_run(command)

        board.run = run
        res = _check(board)
        self.assertIn("busy (RPC flashing session)", res.message)
        self.assertNotIn("h2h_flash release", board.commands)
        self.assertFalse([c for c in board.commands if c.startswith("gpio set")])
        self.assertIn(
            "Bus not released: it belongs to an RPC flashing session",
            "\n".join(res.log),
        )
        self.assertEqual(board.gpios["h2h_reset_l"], 1)  # Untouched.

    def test_unconfirmed_acquire_is_not_released(self):
        board = SimBoard(
            acquire_output="h2h_flash acquire\r\nFailed to acquire H2H SPI bus (-5)\r\n"
        )
        res = _check(board)
        self.assertIn("'h2h_flash acquire' failed: Failed to acquire", res.message)
        self.assertEqual(board.commands[-1], "h2h_flash acquire")
        self.assertIn(
            "Bus not released: 'h2h_flash acquire' was not confirmed",
            "\n".join(res.log),
        )
        self.assertEqual(res.host_serial_after, SN)

    def test_no_release_without_an_answered_acquire(self):
        board = SimBoard()
        board.raise_on = "h2h_flash acquire"
        res = _check(board)
        self.assertIn("MCU console error on /dev/ttyACM0", res.message)
        self.assertNotIn("h2h_flash release", board.commands)

    def test_bus_not_idle_before(self):
        board = SimBoard()
        board.gpios["h2h_reset_l"] = 1
        board.back_at = 0.0
        res = _check(board)
        self.assertFalse(res.success)
        self.assertIn("not idle (h2h_reset_l: expected 0, got 1)", res.message)
        self.assertEqual(board.commands, ["version", "gpio get"])


class TestH2hFirmwareGate(unittest.TestCase):
    """The SPI bus is only touched on an approved MCU App build."""

    def test_unapproved_build_never_touches_the_bus(self):
        board = SimBoard(version=VERSION_064)
        res = _check(board)
        self.assertFalse(res.success)
        self.assertFalse(res.firmware_supported)
        self.assertIn("0.64-86c6fbb is not an approved build", res.message)
        self.assertIn("update the MCU App", res.message)
        self.assertEqual(board.commands, ["version"])
        self.assertEqual(res.host_serial_after, SN)

    def test_no_bundled_stable_firmware_never_touches_the_bus(self):
        board = SimBoard()
        res = _check(board, environ={})
        self.assertFalse(res.success)
        # Not known to be unsupported: no "update the MCU App" advice.
        self.assertTrue(res.firmware_supported)
        self.assertIn(profile.NO_STABLE_FIRMWARE, res.message)
        self.assertIn("MAUI_V12_APPROVED_FW", res.message)
        self.assertIn("The SPI flash was not touched.", res.message)
        self.assertNotIn("update the MCU App", res.message)
        self.assertEqual(board.commands, ["version"])

    def test_bundled_stable_firmware_approves_the_build(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "mcu_app"))
            with open(
                os.path.join(root, "mcu_app", "STABLE.json"), "w", encoding="utf-8"
            ) as f:
                json.dump({"version": "0.69-5324f43", "file": "maui_app.bin"}, f)
            res = _check(SimBoard(), environ={}, firmware_roots=[root])
        self.assertTrue(res.success, res.message)
        self.assertIn("MCU App 0.69-5324f43 (approved build)", "\n".join(res.log))


class TestFindUnit(unittest.TestCase):
    """find_unit() picks the MCU console of the request."""

    def test_by_port(self):
        first = _unit().mcu
        second = device.McuConsole(
            dev_node="/dev/ttyACM2",
            usb_path="/sys/devices/usb1/1-4/1-4.1",
            product=profile.PRODUCT_APP,
            stage=profile.STAGE_APP,
        )
        bridges = [
            device.H2hDevice(usb_path="/sys/devices/usb1/1-3/1-3.3", serial="A"),
            device.H2hDevice(usb_path="/sys/devices/usb1/1-4/1-4.3", serial="B"),
        ]
        unit = h2h.find_unit("/dev/ttyACM2", lambda: [first, second], lambda: bridges)
        self.assertIs(unit.mcu, second)
        self.assertEqual(unit.h2h.serial, "B")
        self.assertEqual(
            h2h.find_unit("", lambda: [first, second], lambda: bridges).h2h.serial, "A"
        )
        missing = h2h.find_unit("/dev/ttyACM9", lambda: [first], lambda: bridges)
        self.assertIsNone(missing.mcu)
        self.assertIsNone(missing.h2h)


class TestServicer(unittest.TestCase):
    """MauiMcuServicer.check_h2h_spi_serial."""

    @mock.patch.object(mcu_service, "write_device_log")
    def test_response_and_log(self, mock_log):
        res = h2h.SpiCheckResult(
            success=True,
            host_serial_before=SN,
            spi_serial=SN,
            host_serial_after=SN,
            jedec_id="0x852013",
            flash_size="512 KB",
            message="ok",
            log=["line 1", "line 2"],
        )
        unit = _unit()
        with mock.patch.object(
            h2h, "find_unit", return_value=unit
        ) as find, mock.patch.object(h2h, "run_check", return_value=res) as run:
            resp = mcu_service.MauiMcuServicer().check_h2h_spi_serial(
                maui_manufacturing_pb2.MauiH2hSpiCheckRequest(
                    device_port="/dev/ttyACM0"
                ),
                None,
            )
        find.assert_called_once_with("/dev/ttyACM0")
        self.assertIs(run.call_args[0][0], unit)
        self.assertTrue(resp.success)
        self.assertTrue(resp.firmware_supported)
        self.assertEqual(resp.spi_serial, SN)
        self.assertEqual(resp.jedec_id, "0x852013")
        self.assertEqual(resp.flash_size, "512 KB")
        self.assertEqual(resp.log, "line 1\nline 2")
        self.assertEqual(mock_log.call_args[0][:2], (SN, "h2h_spi_check"))
        self.assertIn("ALL_PASSED=True", mock_log.call_args[0][2])

    @mock.patch.object(mcu_service, "write_device_log")
    def test_log_directory_follows_the_request_serial(self, mock_log):
        with mock.patch.object(h2h, "find_unit", return_value=device.UnitInfo()):
            resp = mcu_service.MauiMcuServicer().check_h2h_spi_serial(
                maui_manufacturing_pb2.MauiH2hSpiCheckRequest(serial_number=SN), None
            )
        self.assertFalse(resp.success)
        self.assertEqual(mock_log.call_args[0][0], SN)
        self.assertIn("ALL_PASSED=False", mock_log.call_args[0][2])

    def test_stage_log_mapping(self):
        self.assertIn(
            ("h2h_spi_check", "h2h_spi_check.log", "H2H SPI Flash Access"),
            maui_v12_services.V12_STAGE_LOG_MAPPING,
        )


if __name__ == "__main__":
    unittest.main()
