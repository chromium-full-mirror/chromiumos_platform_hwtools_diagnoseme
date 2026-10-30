# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for Maui v1.2 H2H (PL27A1) firmware flashing."""

# Generated protobuf members are not visible to pylint; the tests drive the
# flasher's internals with fakes.
# pylint: disable=no-member,protected-access

import contextlib
import hashlib
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

from server import maui_utils_runner
from server import maui_v12_device as device
from server import maui_v12_h2h as h2h
from server import maui_v12_h2h_flash as h2h_flash
from server import maui_v12_mcu_service as mcu_service
from server import maui_v12_profile as profile
from server.generated import maui_manufacturing_pb2


SN = "MAUIV12H26090104"
NEW_SN = "MAUIV12H26090199"
APPROVED = {"MAUI_V12_APPROVED_FW": "0.69-5324f43"}
VERSION_069 = (
    "version\r\nMaui version 0.69-5324f43 (h2h-read-rpc, built 2026-10-13 "
    "08:45:17 UTC) [MCUBOOT-SLOT0 (0x80038200)]\r\nmaui$ "
)
VERSION_064 = (
    "version\r\nMaui version 0.64-86c6fbb (main, built 2026-10-15 09:12:01 UTC) "
    "[MCUBOOT-SLOT0 (0x80038200)]\r\nmaui$ "
)
# First 96 bytes of the PL27A1 image of MAUIV12H26090104 (b/567837469).
HEADER = bytes.fromhex(
    "7b06d1186d500002a032000000000000"
    "04030904160347006f006f0067006c00"
    "650020004c004c0043000e0341004400"
    "420020007600310022034d0041005500"
    "49005600310032004800320036003000"
    "39003000310030003400000000000000"
)
# A synthetic whole image: the real header, settings and "firmware" filler.
IMAGE = (
    HEADER
    + bytes(0x100 - len(HEADER))
    + bytes(range(0x80, 0x100))
    + bytes(0x80)
    + bytes((i * 7) & 0xFF for i in range(h2h.IMAGE_SIZE - 0x200))
)

# Stand-in for `docker` running maui-flash in maui-utils: prints the
# maui-flash output format (logging, not a terminal) per scenario and keeps
# the "flash" content in FAKE_MF_FLASH.
FAKE_DOCKER = r"""
import os
import sys
import time

argv = sys.argv[1:]
if argv[0] == "rm":
    sys.exit(0)
staging = next(
    a.split(":")[0] for a in argv if a.endswith(":/mnt/firmware_dir")
)
args = argv[argv.index("--entrypoint") + 3 :]
scenario = os.environ.get("FAKE_MF_SCENARIO", "ok")
flash = os.environ["FAKE_MF_FLASH"]
with open(os.environ["FAKE_MF_ARGS"], "a") as f:
    f.write(" ".join(args) + "\n")


def local(path):
    return os.path.join(staging, os.path.basename(path))


def progress(phase, total):
    for pct in range(0, 101, 25):
        print(f"INFO:   {phase} {pct:3d}% ({total * pct // 100}/{total} B)")
        sys.stdout.flush()
        if scenario == "slow":
            time.sleep(0.2)


print("INFO: Using RPC port /dev/ttyACM1")
if "--file" in args:
    if scenario == "unsupported":
        print("ERROR: Command failed: Device firmware does not support h2h_begin "
              "over RPC; update the MCU firmware first.")
        sys.exit(1)
    with open(local(args[args.index("--file") + 1]), "rb") as f:
        data = f.read()
    print(f"INFO: Flashing {args[-1]} ({len(data)} B) to the H2H SPI flash...")
    progress("erase", 524288)
    if scenario in ("hang", "slow"):
        time.sleep(30)
    with open(flash, "wb") as f:
        f.write(data)
    progress("program", len(data))
    progress("verify", len(data))
    print("INFO: H2H flash programmed and verified: JEDEC ID 0x852013, 512 KB")
elif "--read" in args:
    size = int(args[args.index("--size") + 1])
    with open(flash, "rb") as f:
        data = bytearray(f.read()[:size])
    if scenario == "corrupt_read":
        data[0x300] ^= 0xFF
    progress("read", size)
    with open(local(args[args.index("--read") + 1]), "wb") as f:
        f.write(data)
    print(f"INFO: H2H flash dumped to {args[args.index('--read') + 1]}")
"""


def gpio_line(name, level):
    """A `gpio get` / `gpio set` line."""
    return f"[{name:>25}] O = {level}\r\n"


class FakeBoard:  # pylint: disable=too-many-instance-attributes
    """MCU console and H2H bridge of one board."""

    def __init__(self, serial_after=SN):
        self.version = VERSION_069
        self.commands = []
        self.reset_level = 0
        self.bus = {"ec_mem_sel": 0, "ec_mem_oe_l": 1}
        self.resets = 0
        self.serial_before = SN
        self.serial_after = serial_after
        self.pid_after = 0x506D
        self.present_after = True

    def run(self, command):
        """Answers one console command."""
        self.commands.append(command)
        if command == "version":
            return self.version
        if command == "gpio get":
            return (
                gpio_line("h2h_reset_l", self.reset_level)
                + "".join(gpio_line(k, v) for k, v in self.bus.items())
                + "maui$ "
            )
        if command.startswith("gpio set h2h_reset_l "):
            self.reset_level = int(command[-1])
            if self.reset_level == 0:
                self.resets += 1
            return gpio_line("h2h_reset_l", self.reset_level) + "maui$ "
        return "maui$ "

    @contextlib.contextmanager
    def open_console(self, unused_port):
        """open_console_fn of the flasher."""
        yield self.run

    def scan_h2h(self):
        """The bridge as the host sees it (new serial after a reset)."""
        if not self.resets:
            return [
                device.H2hDevice(
                    "/sys/usb1/1-2/1-2.2",
                    self.serial_before,
                    "ADB v1",
                    0x506D,
                    devnum="5",
                )
            ]
        if not self.present_after:
            return []
        return [
            device.H2hDevice(
                "/sys/usb1/1-2/1-2.2",
                self.serial_after,
                "ADB v1",
                self.pid_after,
                speed="5000",
                devnum="9",
            )
        ]


class FakeClock:  # pylint: disable=too-few-public-methods
    """A clock that advances on every reading (wait loops end at once)."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.5
        return self.now


def console(stage=profile.STAGE_APP, serial=SN, port="/dev/ttyACM0"):
    """An MCU console of the board."""
    return device.McuConsole(
        dev_node=port,
        usb_path="/sys/usb1/1-2/1-2.1",
        serial=serial,
        product=profile.PRODUCT_APP,
        stage=stage,
    )


class _DirTestCase(unittest.TestCase):
    """A temporary H2H firmware directory (MAUI_H2H_FW_DIR)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.fw_dir = os.path.join(self.tmp, "diagnoseme_h2h_firmware")
        os.makedirs(self.fw_dir)
        self.environ = {h2h_flash.HOST_DIR_ENV: self.fw_dir}
        self.mtime = 1000
        patcher = mock.patch.object(
            h2h_flash, "CONTAINER_DIR", os.path.join(self.tmp, "no_mount")
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, name, data=IMAGE):
        """Creates an image file; later files are newer."""
        path = os.path.join(self.fw_dir, name)
        with open(path, "wb") as f:
            f.write(data)
        self.mtime += 10
        os.utime(path, (self.mtime, self.mtime))
        return path


class TestImage(unittest.TestCase):
    """check_image, serial_patch_error and patch_serial."""

    def test_check_image(self):
        """Whole PL27A1 images pass; wrong size, header or signature fail."""
        image, error = h2h.check_image(IMAGE)
        self.assertEqual(error, "")
        self.assertEqual((image.serial, image.pid), (SN, 0x506D))
        self.assertIn(
            "8703 bytes; expected a raw 8704-byte PL27A1 .bin from "
            "prepare_raw_flash.py",
            h2h.check_image(IMAGE[:-1])[1],
        )
        self.assertIn("raw 8704-byte", h2h.check_image(IMAGE + b"\0")[1])
        blank = h2h.check_image(b"\xff" * h2h.IMAGE_SIZE)
        self.assertIsNone(blank[0])
        self.assertIn("not a PL27A1 H2H image", blank[1])
        other = IMAGE[:2] + b"\x34\x12\x78\x56" + IMAGE[6:]
        self.assertIn("unexpected image header 1234:5678", h2h.check_image(other)[1])
        unsigned = b"\x00\x00" + IMAGE[2:]
        image, error = h2h.check_image(unsigned)
        self.assertEqual(image.serial, SN)
        self.assertIn("signature 0000", error)
        image, error = h2h.check_image(IMAGE[:4] + b"\x84\x50" + IMAGE[6:])
        self.assertEqual(image.serial, SN)
        self.assertIn("USB ID 18d1:5084 in the image (expected 18d1:506d)", error)
        self.assertIn("prepare_raw_flash.py", error)

    def test_patch_serial_changes_only_the_serial(self):
        """The new serial replaces the old one in place, nothing else moves."""
        patched = h2h.patch_serial(IMAGE, NEW_SN)
        self.assertEqual(len(patched), len(IMAGE))
        self.assertEqual(h2h.decode_image(patched).serial, NEW_SN)
        diffs = [i for i, (a, b) in enumerate(zip(IMAGE, patched)) if a != b]
        # "...0104" -> "...0199": the last two UTF-16LE characters.
        self.assertEqual(diffs, [0x56, 0x58])
        self.assertEqual(patched[0x58:0x5A], b"9\x00")
        self.assertEqual(h2h.check_image(patched)[1], "")
        self.assertEqual(h2h.patch_serial(IMAGE, SN), IMAGE)

    def test_patch_serial_round_trip(self):
        """patch -> decode gives the new serial; nothing outside the serial
        descriptor changes, whatever the length of the old serial."""
        for old in ("MAUIV11L26090007", "SHORT1", "A-LONGER-FACTORY-SERIAL-0001"):
            encoded = old.encode("utf-16-le")
            image = bytearray(IMAGE)
            image[0x38:0xF9] = bytes(0xF9 - 0x38)
            image[0x38 : 0x3A + len(encoded)] = bytes([2 + len(encoded), 3]) + encoded
            image = bytes(image)
            self.assertEqual(h2h.decode_image(image).serial, old)
            patched = h2h.patch_serial(image, NEW_SN)
            self.assertEqual(h2h.decode_image(patched).serial, NEW_SN)
            descriptor_end = 0x3A + max(len(encoded), 32)
            self.assertEqual(patched[:0x38], image[:0x38])
            self.assertEqual(patched[descriptor_end:], image[descriptor_end:])
            self.assertEqual(patched[0x38:0x3A], b"\x22\x03")
            # A shorter new serial leaves zeros where the old one was.
            self.assertEqual(patched[0x5A:descriptor_end], bytes(descriptor_end - 0x5A))
            # The hardware config registers never change.
            self.assertEqual(patched[0xF9:0x200], IMAGE[0xF9:0x200])

    def test_patch_serial_refuses_unsafe_changes(self):
        """Invalid serials and images without the expected layout fail."""
        for serial in ("MAUIV12H2609010", "MAUIV12H260901045", "TESTV11111000001"):
            with self.assertRaisesRegex(ValueError, "not a valid Maui v1.2 serial"):
                h2h.patch_serial(IMAGE, serial)
        with self.assertRaisesRegex(ValueError, "raw 8704-byte"):
            h2h.patch_serial(IMAGE[:96], NEW_SN)
        # Manufacturer string one character shorter: the serial descriptor
        # moves to 0x36.
        moved = (
            IMAGE[:0x14]
            + b"\x14\x03"
            + "Google LL".encode("utf-16-le")
            + IMAGE[0x2A:0x5A]
            + b"\x00\x00"
            + IMAGE[0x5A:]
        )
        self.assertEqual(h2h.decode_image(moved).serial, SN)
        self.assertIn("serial descriptor is at 0x36", h2h.serial_patch_error(moved))
        with self.assertRaisesRegex(ValueError, "cannot change the serial number"):
            h2h.patch_serial(moved, NEW_SN)
        broken = bytearray(IMAGE)
        broken[0x2B] = 0x00  # Product descriptor type.
        self.assertIn("do not decode", h2h.serial_patch_error(broken))
        self.assertEqual(h2h.serial_patch_error(IMAGE), "")


class TestListing(_DirTestCase):
    """list_h2h_firmware: directory, files and messages."""

    def test_missing_and_empty_directory(self):
        """A missing or empty directory says where to copy the image."""
        missing = os.path.join(self.tmp, "nope")
        resp = h2h_flash.list_response({h2h_flash.HOST_DIR_ENV: missing})
        self.assertFalse(resp.directory_exists)
        self.assertEqual(resp.directory, missing)
        self.assertIn("does not exist", resp.message)
        resp = h2h_flash.list_response(self.environ)
        self.assertTrue(resp.directory_exists)
        self.assertEqual(len(resp.files), 0)
        self.assertIn(f"No H2H image (*.bin) in {self.fw_dir}", resp.message)
        self.assertEqual(resp.expected_size_bytes, 8704)

    def test_files(self):
        """Valid images carry serial and sha256; bad files say why."""
        self.add("short.bin", IMAGE[:100])
        self.add("blank.BIN", b"\xff" * h2h.IMAGE_SIZE)
        self.add("notes.txt", b"x")
        outside = os.path.join(self.tmp, "outside.bin")
        with open(outside, "wb") as f:
            f.write(IMAGE)
        os.symlink(outside, os.path.join(self.fw_dir, "link.bin"))
        self.add("h2h_maui_v12.bin")
        resp = h2h_flash.list_response(self.environ)
        names = [f.file_name for f in resp.files]
        self.assertEqual(names, ["h2h_maui_v12.bin", "blank.BIN", "short.bin"])
        good = resp.files[0]
        self.assertTrue(good.valid)
        self.assertEqual(good.sha256, hashlib.sha256(IMAGE).hexdigest())
        self.assertEqual((good.serial_number, good.pid), (SN, 0x506D))
        self.assertTrue(good.serial_valid and good.serial_patchable)
        self.assertEqual(good.size_bytes, 8704)
        self.assertFalse(resp.files[1].valid)
        self.assertIn("not a PL27A1 H2H image", resp.files[1].error)
        self.assertIn("100 bytes", resp.files[2].error)
        self.assertIn("3 image(s)", resp.message)
        self.assertIn("1 valid", resp.message)
        self.assertIn("short.bin: 100 bytes", resp.message)

    def test_container_mount(self):
        """In the server container the mount is read, the host path shown."""
        self.add("h2h.bin")
        with mock.patch.object(h2h_flash, "CONTAINER_DIR", self.fw_dir):
            resp = h2h_flash.list_response(
                {h2h_flash.HOST_DIR_ENV: "/home/op/diagnoseme_h2h_firmware"}
            )
            self.assertEqual(resp.directory, "/home/op/diagnoseme_h2h_firmware")
            self.assertEqual([f.file_name for f in resp.files], ["h2h.bin"])
            self.assertEqual(h2h_flash.firmware_dir({})[1], h2h_flash.DEFAULT_HOST_DIR)

    def test_servicer(self):
        """The servicer lists the station directory and the MCU App verdict."""
        board = FakeBoard()
        board.version = VERSION_064
        with mock.patch.dict(
            os.environ, {**self.environ, **APPROVED}
        ), mock.patch.object(
            device, "scan_mcu_consoles", return_value=[console()]
        ), mock.patch.object(
            h2h, "open_console", return_value=board.open_console("")
        ):
            resp = mcu_service.MauiMcuServicer().list_h2h_firmware(
                maui_manufacturing_pb2.MauiH2hFirmwareListRequest(), None
            )
        self.assertEqual(resp.directory, self.fw_dir)
        self.assertEqual(resp.mcu_port, "/dev/ttyACM0")
        self.assertEqual(
            (resp.mcu_version, resp.stable_version), ("0.64-86c6fbb", "0.69-5324f43")
        )
        self.assertEqual(resp.firmware_verdict, "older")
        self.assertTrue(resp.firmware_warning)
        self.assertIn(
            "if flashing fails, update the MCU App first", resp.firmware_message
        )
        self.assertEqual(board.commands, ["version"])
        self.assertEqual(device.busy_reason(console()), "")


class TestFirmwareVerdict(unittest.TestCase):
    """compare_mcu_version / read_firmware_verdict: any build may flash."""

    @staticmethod
    def verdict(output, environ=None, roots=()):
        """The verdict of `version` |output| (MAUI_V12_APPROVED_FW 0.69)."""
        return profile.compare_mcu_version(
            output, APPROVED if environ is None else environ, roots
        )

    def test_approved_newer_older(self):
        """Equal: no warning; newer: info only; older: warning."""
        approved = self.verdict(VERSION_069)
        self.assertEqual((approved.status, approved.warning), ("approved", False))
        self.assertEqual(approved.version, "0.69-5324f43")
        newer = self.verdict(VERSION_069.replace("0.69-5324f43", "0.71-abcdef1"))
        self.assertEqual((newer.status, newer.warning), ("newer", False))
        self.assertEqual(
            newer.message, "MCU App 0.71-abcdef1 is newer than the stable 0.69-5324f43."
        )
        older = self.verdict(VERSION_064)
        self.assertEqual((older.status, older.warning), ("older", True))
        self.assertEqual(
            older.message,
            "MCU App 0.64-86c6fbb is older than the stable 0.69-5324f43; if flashing "
            "fails, update the MCU App first.",
        )
        # The newest of several approved builds is the reference.
        two = self.verdict(
            VERSION_064, {"MAUI_V12_APPROVED_FW": "0.60-1111111,0.67-17241ef"}
        )
        self.assertEqual((two.status, two.stable), ("older", "0.67-17241ef"))
        anything = self.verdict(VERSION_064, {"MAUI_V12_APPROVED_FW": "any"})
        self.assertEqual(anything.status, "approved")

    def test_unknown(self):
        """Unparsable versions, no stable info or another build: warning."""
        for output, environ, text in (
            (
                "version\r\nmaui$ ",
                None,
                "Could not parse the MCU App version (no answer)",
            ),
            (
                "Maui version 0.69-5324f43 (odd)\r\n",
                None,
                "Could not parse the MCU App version (Maui version 0.69-5324f43 (odd))",
            ),
            (VERSION_064, {}, "cannot be compared (no bundled stable MCU firmware"),
            (
                VERSION_069.replace("5324f43", "5324f43-dirty"),
                None,
                "is a dirty development build",
            ),
            (
                VERSION_069.replace("5324f43", "1234567"),
                None,
                "is another build than the stable 0.69-5324f43",
            ),
        ):
            verdict = self.verdict(output, environ)
            self.assertEqual(verdict.status, "unknown", output)
            self.assertTrue(verdict.warning)
            self.assertIn(text, verdict.message)
            self.assertTrue(verdict.message.endswith("update the MCU App first."))

    def test_read_firmware_verdict(self):
        """The console is read once and released; busy or missing consoles."""
        board = FakeBoard()
        port, verdict = h2h_flash.read_firmware_verdict(
            "", board.open_console, APPROVED, (), lambda: [console()]
        )
        self.assertEqual((port, verdict.status), ("/dev/ttyACM0", "approved"))
        port, verdict = h2h_flash.read_firmware_verdict(
            "", board.open_console, APPROVED, (), lambda: []
        )
        self.assertEqual((port, verdict.status, verdict.warning), ("", "", False))
        self.assertIn("not detected", verdict.message)
        keys = device.claim_consoles([console()], "MCU App update")
        try:
            _, verdict = h2h_flash.read_firmware_verdict(
                "", board.open_console, APPROVED, (), lambda: [console()]
            )
        finally:
            device.release_consoles(keys)
        self.assertEqual(verdict.status, "busy")
        self.assertIn("MCU console busy: MCU App update", verdict.message)
        self.assertEqual(board.commands, ["version"])


class TestPrepareJob(_DirTestCase):
    """prepare_job(): every input is validated before anything runs."""

    def prepare(self, consoles=None, **fields):
        """Runs prepare_job with the board console."""
        fields.setdefault("file_name", "h2h.bin")
        return h2h_flash.prepare_job(
            maui_manufacturing_pb2.MauiH2hFlashRequest(**fields),
            self.environ,
            lambda: [console()] if consoles is None else consoles,
        )

    def test_rejected_requests(self):
        """Bad names, files, serials and consoles are refused."""
        self.add("h2h.bin")
        self.add("short.bin", IMAGE[:10])
        odd = bytearray(IMAGE)
        odd[0x3A:0x5A] = "TESTV11111000001".encode("utf-16-le")
        self.add("test_serial.bin", bytes(odd))
        cases = (
            ({"file_name": ""}, "Invalid H2H image file name"),
            ({"file_name": "../h2h.bin"}, "Invalid H2H image file name"),
            ({"file_name": "h2h.txt"}, "Invalid H2H image file name"),
            ({"file_name": "gone.bin"}, "not found in"),
            ({"file_name": "short.bin"}, "10 bytes; expected a raw 8704-byte"),
            ({"expected_sha256": "0" * 64}, "changed since it was listed"),
            ({"new_serial": "MAUIV12H123"}, "not a valid Maui v1.2 serial"),
            ({"file_name": "test_serial.bin"}, "use 'Change serial number'"),
            ({"device_port": "/dev/ttyACM7"}, "not a detected Maui MCU console"),
        )
        for fields, error in cases:
            job, message, _ = self.prepare(**fields)
            self.assertIsNone(job, fields)
            self.assertIn(error, message)
        job, message, _ = self.prepare(consoles=[])
        self.assertIn("not detected", message)
        job, message, _ = self.prepare(
            consoles=[console(stage=profile.STAGE_RW_BOOTLOADER)],
            device_port="/dev/ttyACM0",
        )
        self.assertIn("needs the MCU App", message)

    def test_job_without_and_with_serial_change(self):
        """The file is flashed as is, or a patched copy with the new serial."""
        path = self.add("h2h.bin")
        job, error, key = self.prepare(
            expected_sha256=hashlib.sha256(IMAGE).hexdigest(), read_back=True
        )
        self.assertEqual(error, "")
        self.assertEqual((key, job.serial, job.serial_changed), (SN, SN, False))
        self.assertEqual((job.data, job.staged_name), (IMAGE, "h2h.bin"))
        self.assertTrue(job.read_back)
        job, error, key = self.prepare(new_serial=f" {NEW_SN} ")
        self.assertEqual((key, job.serial, job.serial_changed), (NEW_SN, NEW_SN, True))
        self.assertEqual(job.data, h2h.patch_serial(IMAGE, NEW_SN))
        self.assertEqual(job.staged_name, f"h2h_{NEW_SN}.bin")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), IMAGE)  # The operator's file is untouched.
        odd = bytearray(IMAGE)
        odd[0x3A:0x5A] = "TESTV11111000001".encode("utf-16-le")
        self.add("test_serial.bin", bytes(odd))
        job, error, _ = self.prepare(file_name="test_serial.bin", new_serial=NEW_SN)
        self.assertEqual(error, "")
        self.assertEqual(h2h.decode_image(job.data).serial, NEW_SN)


class _FlashTestCase(_DirTestCase, unittest.IsolatedAsyncioTestCase):
    """The flasher with a fake board and a fake docker running maui-flash."""

    def setUp(self):
        super().setUp()
        self.add("h2h.bin")
        self.board = FakeBoard()
        docker = os.path.join(self.tmp, "docker")
        with open(docker, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable}\n{FAKE_DOCKER}")
        os.chmod(docker, os.stat(docker).st_mode | stat.S_IEXEC)
        self.flash_file = os.path.join(self.tmp, "flash.bin")
        self.args_file = os.path.join(self.tmp, "args.txt")
        self.runner = maui_utils_runner.MauiUtilsRunner(
            image="maui-utils:test",
            staging_root=os.path.join(self.tmp, "staging"),
            docker_bin=docker,
        )
        patches = [
            mock.patch.dict(
                os.environ,
                {
                    "FAKE_MF_FLASH": self.flash_file,
                    "FAKE_MF_ARGS": self.args_file,
                    "FAKE_MF_SCENARIO": "ok",
                },
            ),
            mock.patch.object(h2h_flash, "write_device_log"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.mock_log = h2h_flash.write_device_log

    def scenario(self, name):
        """Selects the behavior of the fake maui-flash."""
        os.environ["FAKE_MF_SCENARIO"] = name

    def tool_args(self):
        """The maui-flash command lines run so far."""
        if not os.path.exists(self.args_file):
            return []
        with open(self.args_file, encoding="utf-8") as f:
            return f.read().splitlines()

    def job(self, **fields):
        """A validated job for h2h.bin."""
        fields.setdefault("file_name", "h2h.bin")
        job, error, _ = h2h_flash.prepare_job(
            maui_manufacturing_pb2.MauiH2hFlashRequest(**fields),
            self.environ,
            lambda: [console()],
        )
        self.assertEqual(error, "")
        return job

    def flasher(self, environ=None):
        """A flasher on the fake board."""
        return h2h_flash.H2hFirmwareFlasher(
            self.board.open_console,
            runner=self.runner,
            environ=APPROVED if environ is None else environ,
            firmware_roots=(),
            scan_h2h=self.board.scan_h2h,
            sleep=lambda s: None,
            clock=FakeClock(),
        )

    async def flash(self, job=None, flasher=None, is_cancelled=lambda: False):
        """Runs one flash; returns all progress messages."""
        flasher = flasher or self.flasher()
        return [p async for p in flasher.flash(job or self.job(), is_cancelled)]

    def log_content(self):
        """(log key, log name, content) of h2h_fw_flashing.log."""
        self.mock_log.assert_called_once()
        return self.mock_log.call_args[0]

    def assert_failed(self, out, error):
        """The last message is a failure containing |error|."""
        final = out[-1]
        self.assertTrue(final.is_complete)
        self.assertFalse(final.success)
        self.assertEqual(final.phase, h2h_flash.PHASE_FAILED)
        self.assertIn(error, final.error_message)


class TestFlash(_FlashTestCase):
    """End-to-end flashes."""

    async def test_flash_keeps_the_serial(self):
        """Flash, read back, reset, iSerial check, log."""
        out = await self.flash(self.job(read_back=True))
        final = out[-1]
        self.assertTrue(final.success, final.error_message)
        self.assertEqual(final.progress_percent, 100)
        self.assertEqual((final.host_serial, final.image_serial), (SN, SN))
        self.assertFalse(final.serial_changed or final.mcu_serial_mismatch)
        with open(self.flash_file, "rb") as f:
            self.assertEqual(f.read(), IMAGE)
        args = self.tool_args()
        self.assertEqual(
            args,
            [
                "--port /dev/ttyACM0 h2h --file /mnt/firmware_dir/h2h.bin",
                "--port /dev/ttyACM0 h2h --read /mnt/firmware_dir/h2h_readback.bin "
                "--size 8704",
            ],
        )
        phases = [p.phase for p in out]
        for phase in ("checking", "erasing", "programming", "verifying", "reading"):
            self.assertIn(phase, phases)
        percents = [p.progress_percent for p in out]
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(
            self.board.commands,
            [
                "version",
                "gpio get",
                "gpio get",
                "gpio set h2h_reset_l 1",
                "gpio set h2h_reset_l 0",
            ],
        )
        key, name, content = self.log_content()
        self.assertEqual((key, name), (SN, "h2h_fw_flashing"))
        self.assertIn("ALL_PASSED=True", content)
        self.assertIn(hashlib.sha256(IMAGE).hexdigest(), content)
        self.assertIn("Read-back matches the image.", content)
        self.assertIn("program 100% (8704/8704 B)", content)
        self.assertIn("iSerial 'MAUIV12H26090104'", content)
        self.assertEqual(os.listdir(self.runner.staging_root), [])

    async def test_flash_with_new_serial(self):
        """The patched copy is flashed; the MCU serial has to follow."""
        self.board.serial_after = NEW_SN
        out = await self.flash(self.job(new_serial=NEW_SN))
        final = out[-1]
        self.assertTrue(final.success, final.error_message)
        self.assertTrue(final.serial_changed and final.mcu_serial_mismatch)
        self.assertEqual(final.host_serial, NEW_SN)
        self.assertIn("provision it", final.status_message)
        with open(self.flash_file, "rb") as f:
            self.assertEqual(f.read(), h2h.patch_serial(IMAGE, NEW_SN))
        self.assertEqual(len(self.tool_args()), 1)  # No read-back requested.
        self.assertIn(f"h2h_{NEW_SN}.bin", self.tool_args()[0])
        key, _, content = self.log_content()
        self.assertEqual(key, NEW_SN)
        self.assertIn(f"(changed from {SN})", content)

    async def test_wrong_serial_after_reset(self):
        """The bridge has to come back with the serial of the image."""
        self.board.serial_after = "MAUIV12H00000000"
        self.assert_failed(await self.flash(), "with iSerial 'MAUIV12H00000000'")
        self.board.serial_after, self.board.pid_after = SN, 0x5084
        self.mock_log.reset_mock()
        self.board.resets = 0
        self.assert_failed(await self.flash(), "enumerated as 18d1:5084")

    async def test_bridge_does_not_come_back(self):
        """No bridge after the reset: power-cycle hint."""
        self.board.present_after = False
        self.assert_failed(await self.flash(), "did not enumerate")

    async def test_older_or_unknown_firmware_flashes_with_a_warning(self):
        """Any MCU App may flash; older / unknown ones are warned of."""
        self.board.version = VERSION_064
        out = await self.flash()
        self.assertTrue(out[-1].success, out[-1].error_message)
        warning = (
            "WARNING: MCU App 0.64-86c6fbb is older than the stable 0.69-5324f43; "
            "if flashing fails, update the MCU App first."
        )
        self.assertIn(warning, [p.log_line for p in out])
        self.assertIn(warning, self.log_content()[2])
        self.mock_log.reset_mock()
        self.board.resets = 0
        out = await self.flash(flasher=self.flasher(environ={}))
        self.assertTrue(out[-1].success, out[-1].error_message)
        self.assertIn(
            "WARNING: MCU App 0.64-86c6fbb cannot be compared", self.log_content()[2]
        )
        self.assertEqual(len(self.tool_args()), 2)

    async def test_newer_firmware_is_only_logged(self):
        """A newer MCU App gets an info line, no warning."""
        self.board.version = VERSION_069.replace("0.69-5324f43", "0.71-abcdef1")
        out = await self.flash()
        self.assertTrue(out[-1].success, out[-1].error_message)
        content = self.log_content()[2]
        self.assertIn(
            "MCU App 0.71-abcdef1 is newer than the stable 0.69-5324f43.", content
        )
        self.assertNotIn("WARNING", content)

    async def test_busy_bus_is_not_touched(self):
        """A bus in use by another session is never disturbed."""
        self.board.bus["ec_mem_sel"] = 1
        self.assert_failed(await self.flash(), "H2H SPI bus is not idle")
        self.assertEqual(self.tool_args(), [])
        self.assertEqual(self.board.resets, 0)

    async def test_bus_not_handed_back(self):
        """No reset when the MCU still holds the bus after the flash."""
        flasher = self.flasher()
        board = self.board
        original = board.run

        def run(command):
            if command == "gpio get" and board.commands.count("gpio get") >= 1:
                board.bus["ec_mem_oe_l"] = 0
            return original(command)

        board.run = run
        out = await self.flash(flasher=flasher)
        self.assert_failed(out, "not handed back")
        self.assertEqual(board.resets, 0)

    async def test_tool_failure(self):
        """maui-flash errors fail with a hint and skip the reset."""
        self.scenario("unsupported")
        out = await self.flash()
        self.assert_failed(out, "maui-flash h2h failed (exit code 1)")
        self.assertIn("update the MCU App", out[-1].error_message)
        self.assertNotIn("may be erased", out[-1].error_message)
        self.assertNotIn("probably too old", out[-1].error_message)
        self.assertEqual(self.board.resets, 0)

    async def test_tool_failure_with_old_firmware(self):
        """With an older MCU App the failure points to the MCU App update."""
        self.scenario("unsupported")
        self.board.version = VERSION_064
        out = await self.flash()
        self.assert_failed(out, "The MCU App (0.64-86c6fbb) is probably too old")
        self.assertIn(
            "'MCU App update over USB (RW bootloader)'", out[-1].error_message
        )

    async def test_read_back_mismatch(self):
        """A dump that differs from the image fails."""
        self.scenario("corrupt_read")
        out = await self.flash(self.job(read_back=True))
        self.assert_failed(out, "1 byte(s) differ from the image (first at 0x0300")
        self.assertEqual(self.board.resets, 0)

    async def test_timeout_stops_the_tool(self):
        """A hung maui-flash is killed; the flash may be incomplete."""
        self.scenario("hang")
        with mock.patch.object(h2h_flash, "FLASH_TIMEOUT_S", 1.0), mock.patch.object(
            h2h_flash, "TOOL_POLL_S", 0.1
        ):
            out = await self.flash()
        self.assert_failed(out, "did not finish within 1 s")
        self.assertIn("may be erased or incomplete", out[-1].error_message)

    async def test_cancel_stops_the_tool(self):
        """Leaving the page stops maui-flash."""
        self.scenario("slow")
        state = {"erasing": False}
        flasher = self.flasher()
        out = []
        with mock.patch.object(h2h_flash, "TOOL_POLL_S", 0.1):
            async for update in flasher.flash(self.job(), lambda: state["erasing"]):
                out.append(update)
                state["erasing"] = state["erasing"] or update.phase == "erasing"
        self.assert_failed(out, "Cancelled.")


class TestServicerFlash(_FlashTestCase):
    """flash_h2h_firmware: validation, console claim and the flasher."""

    async def run_rpc(self, **fields):
        """Runs the RPC with the fake board; returns the progress messages."""
        context = mock.MagicMock()
        context.done.return_value = False
        fields.setdefault("file_name", "h2h.bin")
        flasher = self.flasher()
        with mock.patch.dict(os.environ, self.environ), mock.patch.object(
            device, "scan_mcu_consoles", return_value=[console()]
        ), mock.patch.object(h2h_flash, "H2hFirmwareFlasher", return_value=flasher):
            return [
                p
                async for p in mcu_service.MauiMcuServicer().flash_h2h_firmware(
                    maui_manufacturing_pb2.MauiH2hFlashRequest(**fields), context
                )
            ]

    async def test_flash(self):
        """A valid request flashes and releases the console."""
        out = await self.run_rpc()
        self.assertTrue(out[-1].success, out[-1].error_message)
        self.assertEqual(device.busy_reason(console()), "")

    async def test_rejected_request_writes_the_log(self):
        """Validation errors are logged under the serial of the image."""
        out = await self.run_rpc(new_serial="bad")
        self.assert_failed(out, "not a valid Maui v1.2 serial")
        key, name, content = self.log_content()
        self.assertEqual((key, name), (SN, "h2h_fw_flashing"))
        self.assertIn("Nothing was flashed", content)
        self.assertEqual(self.tool_args(), [])

    async def test_busy_console(self):
        """Another console operation blocks the flash; nothing is logged."""
        keys = device.claim_consoles([console()], "MCU App update")
        try:
            out = await self.run_rpc()
        finally:
            device.release_consoles(keys)
        self.assert_failed(out, "MCU console busy: MCU App update")
        self.mock_log.assert_not_called()
        self.assertEqual(self.tool_args(), [])

    async def test_status_poll_waits(self):
        """The unit status query never runs while the flash holds the console."""
        seen = []
        board_run = self.board.run

        def run(command):
            seen.append(device.busy_reason(console()))
            return board_run(command)

        self.board.run = run
        out = await self.run_rpc()
        self.assertTrue(out[-1].success, out[-1].error_message)
        self.assertEqual(set(seen), {"H2H firmware flash"})


if __name__ == "__main__":
    unittest.main()
