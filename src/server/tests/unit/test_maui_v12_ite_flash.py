# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for whole-image MCU flashing with itecomdbgr (Maui v1.2)."""

# Generated protobuf members are not visible to pylint; the tests check the
# module's bridge registry directly.
# pylint: disable=no-member,protected-access

import os
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

from google.protobuf import empty_pb2

from server import maui_v12_device as device
from server import maui_v12_ite_flash as ite_flash
from server import maui_v12_mcu_service as mcu_service
from server import maui_v12_profile as profile
from server import maui_v12_uart as uart
from server.generated import maui_manufacturing_pb2


SN = "MAUIV12H26010001"

# Stand-in for itecomdbgr: prints its exact output format (progress with
# '\r' and no newline, header lines block-buffered on a pipe) per scenario.
FAKE_TOOL = r"""
import os
import signal
import sys
import time

scenario = os.environ.get("FAKE_ITE_SCENARIO", "ok")
verify = "-n" not in sys.argv
with open(os.environ["FAKE_ITE_ARGV"], "w") as f:
    f.write("\n".join(sys.argv[1:]))


def progress(phase, step=5, delay=0.0):
    for pct in range(0, 101, step):
        sys.stdout.write("\r%-17s: %3d%%" % (phase + "...", pct))
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")
    sys.stdout.flush()


if scenario == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if scenario in ("silent", "ignore_term"):
    time.sleep(30)
    sys.exit(1)
if scenario == "bad_baud":
    print("UART Baudrate only support 115200  or 3M")
    sys.exit(0)
if scenario == "no_dbgr":
    print("Disabling protect path...")
    print("Invalid EFLASH TYPE!")
    sys.exit(1)
print("Chip ID = 08 20 02, Chip Ver = c2, eflash size = 1024 KB, file size = 1024 KB")
print("Flash ID = c8 40 14")
print("FLASH TYPE = KGD")
print("Disabling protect path...")
progress("Erasing")
if verify:
    progress("Checking")
if scenario == "hang":
    sys.stdout.write("\rProgramming...   :  42%")
    sys.stdout.flush()
    time.sleep(30)
    sys.exit(1)
if scenario == "endless":
    while True:
        progress("Programming", 1, 0.02)
progress("Programming")
if scenario == "wip_err":
    print("program_page: check_status WIP err")
    print("write_flash : error")
    sys.exit(1)
if verify and scenario != "skip_verify":
    progress("Verifying")
sys.exit(0)
"""


def _console(serial="Uninitialized", node="/dev/ttyACM0", usb="/sys/usb1/1-2"):
    return device.McuConsole(
        dev_node=node,
        usb_path=usb,
        serial=serial,
        product=profile.PRODUCT_APP,
        stage=profile.STAGE_APP,
    )


def _bridge(node="/dev/ttyUSB0"):
    return uart.UartBridge(
        dev_node=node,
        by_id="/dev/serial/by-id/usb-FTDI_TTL232R-3V3_FT9XYZ-if00-port0",
        driver="ftdi_sio",
        vid=0x0403,
        pid=0x6001,
        manufacturer="FTDI",
        product="TTL232R-3V3",
        serial="FT9XYZ",
        usb_path="1-7",
        interface=0,
    )


class _ToolTestCase(unittest.IsolatedAsyncioTestCase):
    """Provides the fake itecomdbgr and short timeouts."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.tool = os.path.join(self.tmp, "itecomdbgr")
        with open(self.tool, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable}\n{FAKE_TOOL}")
        os.chmod(self.tool, stat.S_IRWXU)
        self.argv_file = os.path.join(self.tmp, "argv")
        self.env = mock.patch.dict(
            os.environ, {"FAKE_ITE_SCENARIO": "ok", "FAKE_ITE_ARGV": self.argv_file}
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        for name, value in (
            ("ITECOMDBGR", self.tool),
            ("NO_OUTPUT_TIMEOUT_S", 1.0),
            ("TERMINATE_GRACE_S", 0.5),
        ):
            patcher = mock.patch.object(ite_flash, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def scenario(self, name):
        """Selects the behaviour of the fake itecomdbgr."""
        os.environ["FAKE_ITE_SCENARIO"] = name

    def tool_argv(self):
        """Arguments the fake itecomdbgr was started with."""
        with open(self.argv_file, encoding="utf-8") as f:
            return f.read().split("\n")

    async def run_tool(self, verify=False, timeout_s=60.0, is_cancelled=None):
        """Runs the fake tool; returns (ToolRun, updates)."""
        run = ite_flash.ToolRun()
        cmd = ite_flash.build_command("/dev/ttyUSB0", "/fw/full.bin", 115200, verify)
        updates = [
            u
            async for u in ite_flash.run_itecomdbgr(
                cmd, run, timeout_s, is_cancelled or (lambda: False)
            )
        ]
        return run, updates


class TestOutputParser(unittest.TestCase):
    """Raw itecomdbgr output -> lines and progress."""

    def test_progress_without_newline_is_parsed_at_once(self):
        """'\\r<Phase>...: NN%' needs no terminator; header lines need one."""
        parser = ite_flash.OutputParser()
        updates = parser.feed(
            b"Chip ID = 08 20 02, Chip Ver = c2, eflash size = 1024 KB\n"
            b"FLASH TYPE = KGD\nDisabling protect path...\n"
            b"\rErasing...       :   0%\rErasing...       :   1%"
        )
        self.assertEqual(
            [(u.phase, u.percent) for u in updates],
            [("", 0), ("", 0), ("", 0), ("Erasing", 0), ("Erasing", 1)],
        )
        self.assertTrue(updates[0].line.startswith("Chip ID = 08 20 02"))

    def test_progress_split_across_chunks(self):
        """A chunk may end in the middle of a percentage."""
        parser = ite_flash.OutputParser()
        self.assertEqual(parser.feed(b"\rProgramming...   :  1"), [])
        updates = parser.feed(b"0%\rProgramming...   :  11%")
        self.assertEqual([u.percent for u in updates], [10, 11])
        self.assertEqual(parser.feed(b"\n"), [])

    def test_pty_line_endings_and_flush(self):
        """CRLF line endings (pty) and an unterminated last line."""
        parser = ite_flash.OutputParser()
        updates = parser.feed(b"Flash ID = c8 40 14\r\nInvalid EFLAS")
        self.assertEqual([u.line for u in updates], ["Flash ID = c8 40 14"])
        self.assertEqual(
            [u.line for u in parser.feed(b"H TYPE!")], []
        )  # Still pending.
        self.assertEqual([u.line for u in parser.flush()], ["Invalid EFLASH TYPE!"])

    def test_log_lines_are_throttled(self):
        """Every progress update is reported, every 10 % is logged."""
        parser = ite_flash.OutputParser()
        data = b"".join(b"\rVerifying...     : %3d%%" % p for p in range(0, 101))
        updates = parser.feed(data)
        self.assertEqual(len(updates), 101)
        self.assertEqual([u.percent for u in updates if u.log], list(range(0, 101, 10)))

    def test_percent_is_clamped(self):
        """Programming can exceed 100 % for odd file sizes."""
        updates = ite_flash.OutputParser().feed(b"\rProgramming...   : 101%")
        self.assertEqual(updates[0].percent, 100)


class TestHelpers(unittest.TestCase):
    """Command line, progress mapping, image check, failure reasons."""

    def test_no_output_timeout_default_and_override(self):
        """40 s by default; MAUI_ITE_NO_OUTPUT_TIMEOUT_S overrides it."""
        env = ite_flash.NO_OUTPUT_TIMEOUT_ENV
        timeout = ite_flash.no_output_timeout_s
        self.assertEqual(ite_flash.NO_OUTPUT_TIMEOUT_S, 40.0)
        self.assertEqual(timeout({}), 40.0)
        self.assertEqual(timeout({env: " 75 "}), 75.0)
        for bad in ("abc", "1", "nan", "-5", "100000"):
            self.assertEqual(timeout({env: bad}), 40.0, bad)

    def test_line_splitter(self):
        """CR or LF ends a line; endless output without them is cut."""
        lines = ite_flash.LineSplitter()
        self.assertEqual(lines.feed(b"a\r\nb\rc"), ["a", "b"])
        self.assertEqual(lines.feed(b"\xc3"), [])  # Half a UTF-8 character.
        self.assertEqual(lines.feed(b"\xa4\n"), ["c\u00e4"])
        self.assertEqual(
            lines.feed(b"x" * (ite_flash.MAX_PENDING_CHARS + 1)),
            ["x" * (ite_flash.MAX_PENDING_CHARS + 1)],
        )
        self.assertEqual(lines.feed(b"tail"), [])
        self.assertEqual(lines.flush(), ["tail"])
        self.assertEqual(lines.flush(), [])

    def test_build_command(self):
        """-n unless verifying; line-buffered through stdbuf if available."""
        self.assertEqual(
            ite_flash.build_command("/dev/ttyUSB0", "/fw/a.bin", 115200, False, ""),
            [ite_flash.ITECOMDBGR, "-b", "115200", "-d", "/dev/ttyUSB0"]
            + ["-f", "/fw/a.bin", "-n"],
        )
        cmd = ite_flash.build_command("/dev/ttyACM1", "/a", 3000000, True, "/bin/sb")
        self.assertEqual(cmd[:3], ["/bin/sb", "-oL", ite_flash.ITECOMDBGR])
        self.assertNotIn("-n", cmd)

    def test_overall_percent(self):
        """Phases map to increasing slices of 0-95 %."""
        pct = ite_flash.overall_percent
        self.assertEqual(pct("Erasing", 0, False), 0)
        self.assertEqual(pct("Erasing", 100, False), 10)
        self.assertEqual(pct("Programming", 100, False), 95)
        self.assertEqual(pct("Checking", 50, True), 20)
        self.assertEqual(pct("Verifying", 100, True), 95)
        self.assertEqual(pct("Programming", 150, True), 65)

    def test_check_image(self):
        """Only exactly 1 MiB passes."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "full.bin")
            with open(path, "wb") as f:
                f.write(b"\xff" * profile.MCU_FULL_IMAGE_SIZE)
            self.assertEqual(ite_flash.check_image(path), "")
            with open(path, "ab") as f:
                f.write(b"\xff")
            self.assertIn("exactly 1048576 bytes", ite_flash.check_image(path))
            self.assertIn("Cannot read", ite_flash.check_image(path + ".x"))

    def test_exit_code_zero_without_programming_fails(self):
        """The exit code alone is not trusted."""
        run = ite_flash.ToolRun(returncode=0)
        run.output.append("UART Baudrate only support 115200  or 3M")
        self.assertIn(
            "without completing Programming", ite_flash.failure_reason(run, False)
        )
        run.max_percent.update(Erasing=100, Programming=100)
        self.assertEqual(ite_flash.failure_reason(run, False), "")
        self.assertIn("Verifying", ite_flash.failure_reason(run, True))
        run.max_percent["Verifying"] = 100
        self.assertEqual(ite_flash.failure_reason(run, True), "")

    def test_failure_hints(self):
        """Known messages of failed runs are explained."""
        run = ite_flash.ToolRun(returncode=1)
        run.output.extend(["Disabling protect path...", "Invalid EFLASH TYPE!"])
        reason = ite_flash.failure_reason(run, False)
        self.assertIn("exit code 1", reason)
        self.assertIn("even parity", reason)
        run = ite_flash.ToolRun(returncode=1)
        run.output.append("open: Device or resource busy")
        self.assertIn("Could not open", ite_flash.failure_reason(run, False))

    def test_wait_for_new_app(self):
        """Only an App enumeration that is new after the flash counts."""
        old = _console(serial=SN, usb="/sys/usb1/1-2")
        new = _console(usb="/sys/usb1/1-3")
        boot = device.McuConsole(
            "/dev/ttyACM1", "/sys/usb1/1-4", stage=profile.STAGE_RW_BOOTLOADER
        )
        scans = [[old], [old, boot], [old, boot, new]]
        clock = iter(range(100)).__next__
        with mock.patch.object(
            device, "scan_mcu_consoles", side_effect=lambda: scans.pop(0)
        ), mock.patch.object(
            ite_flash, "enumeration_key", side_effect=lambda c: (c.usb_path, "1")
        ):
            found = ite_flash.wait_for_new_app(
                frozenset({(old.usb_path, "1")}),
                10,
                sleep=lambda _s: None,
                clock=clock,
            )
        self.assertIs(found, new)

    def test_wait_for_new_app_times_out(self):
        """The old enumeration alone never counts."""
        old = _console()
        clock = iter(range(100)).__next__
        with mock.patch.object(
            device, "scan_mcu_consoles", return_value=[old]
        ), mock.patch.object(
            ite_flash, "enumeration_key", return_value=(old.usb_path, "7")
        ):
            self.assertIsNone(
                ite_flash.wait_for_new_app(
                    frozenset({(old.usb_path, "7")}),
                    5,
                    sleep=lambda _s: None,
                    clock=clock,
                )
            )


class TestRunItecomdbgr(_ToolTestCase):
    """run_itecomdbgr() with a fake itecomdbgr process."""

    async def test_station_override_of_the_watchdog(self):
        """The run uses MAUI_ITE_NO_OUTPUT_TIMEOUT_S when it is set."""
        os.environ[ite_flash.NO_OUTPUT_TIMEOUT_ENV] = "7"
        run, _ = await self.run_tool()
        self.assertEqual(run.no_output_timeout_s, 7.0)
        self.assertEqual(ite_flash.failure_reason(run, False), "")

    async def test_success_without_verify(self):
        """Phases and percentages are parsed; -n is passed."""
        run, updates = await self.run_tool()
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.error, "")
        self.assertEqual(run.max_percent, {"Erasing": 100, "Programming": 100})
        self.assertEqual(ite_flash.failure_reason(run, False), "")
        self.assertTrue(updates[0].line.startswith("Chip ID"))
        self.assertEqual(self.tool_argv()[-1], "-n")
        self.assertEqual(self.tool_argv()[:4], ["-b", "115200", "-d", "/dev/ttyUSB0"])

    async def test_verify_needs_verifying_100(self):
        """With verify, Checking and Verifying must run too."""
        run, _ = await self.run_tool(verify=True)
        self.assertNotIn("-n", self.tool_argv())
        self.assertEqual(run.max_percent["Checking"], 100)
        self.assertEqual(ite_flash.failure_reason(run, True), "")
        self.scenario("skip_verify")
        run, _ = await self.run_tool(verify=True)
        self.assertEqual(run.returncode, 0)
        self.assertIn("Verifying", ite_flash.failure_reason(run, True))

    async def test_exit_zero_without_100_percent_fails(self):
        """E.g. an invalid -b value: exit code 0 but nothing programmed."""
        self.scenario("bad_baud")
        run, updates = await self.run_tool()
        self.assertEqual(run.returncode, 0)
        self.assertEqual(updates[0].line, "UART Baudrate only support 115200  or 3M")
        self.assertIn("without completing", ite_flash.failure_reason(run, False))

    async def test_no_dbgr_answer(self):
        """'Invalid EFLASH TYPE!' with exit code 1 explains the wiring."""
        self.scenario("no_dbgr")
        run, _ = await self.run_tool()
        self.assertEqual(run.returncode, 1)
        self.assertIn(
            "did not answer in debugger mode", ite_flash.failure_reason(run, False)
        )

    async def test_program_error(self):
        """A write error mid-flash says the flash is incomplete."""
        self.scenario("wip_err")
        run, _ = await self.run_tool()
        self.assertIn("incomplete", ite_flash.failure_reason(run, False))

    async def test_watchdog_without_any_output(self):
        """No output at all: stopped after NO_OUTPUT_TIMEOUT_S, not ~125 s."""
        self.scenario("silent")
        start = time.monotonic()
        run, updates = await self.run_tool()
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(updates, [])
        self.assertIn("No answer from the MCU", run.error)
        self.assertIsNotNone(run.returncode)
        self.assertNotEqual(run.returncode, 0)

    async def test_watchdog_mid_flash(self):
        """Output stops mid-flash: the MCU or bridge stopped answering."""
        self.scenario("hang")
        run, _ = await self.run_tool()
        self.assertEqual(run.max_percent["Programming"], 42)
        self.assertIn("stopped answering", run.error)
        self.assertIsNotNone(run.returncode)

    async def test_sigkill_if_sigterm_is_ignored(self):
        """A tool that ignores SIGTERM is killed."""
        self.scenario("ignore_term")
        run, _ = await self.run_tool()
        self.assertEqual(run.returncode, -9)

    async def test_overall_timeout(self):
        """A tool that keeps printing is stopped at the overall timeout."""
        self.scenario("endless")
        start = time.monotonic()
        run, _ = await self.run_tool(timeout_s=1.5)
        self.assertLess(time.monotonic() - start, 10)
        self.assertIn("did not finish within", run.error)
        self.assertIsNotNone(run.returncode)

    async def test_cancellation_stops_the_tool(self):
        """is_cancelled() (RPC done) terminates the tool."""
        self.scenario("endless")
        calls = []

        def cancelled():
            calls.append(1)
            return len(calls) > 3

        run, _ = await self.run_tool(is_cancelled=cancelled)
        self.assertIn("Cancelled", run.error)
        self.assertIsNotNone(run.returncode)

    async def test_closing_the_generator_stops_the_tool(self):
        """A cancelled RPC closes the generator: the tool must not survive."""
        self.scenario("endless")
        run = ite_flash.ToolRun()
        cmd = ite_flash.build_command("/dev/ttyUSB0", "/fw/full.bin", 115200, False)
        gen = ite_flash.run_itecomdbgr(cmd, run, 60.0)
        async for update in gen:
            if update.phase == "Programming":
                break
        await gen.aclose()
        self.assertIsNotNone(run.returncode)
        self.assertIn("stopped", run.error)

    async def test_missing_tool(self):
        """A tool that cannot be started is reported."""
        run = ite_flash.ToolRun()
        _ = [u async for u in ite_flash.run_itecomdbgr(["/nonexistent/tool"], run)]
        self.assertIn("Could not start itecomdbgr", run.error)


# pylint: disable-next=too-many-instance-attributes
class TestFullImageFlasher(_ToolTestCase):
    """update_mcu_full_image end to end with the fake tool."""

    def setUp(self):
        super().setUp()
        self.fw_root = os.path.join(self.tmp, "firmware")
        self.fw_dir = os.path.join(self.fw_root, profile.FW_COMPONENT_MCU_FULL)
        os.makedirs(self.fw_dir)
        self.image = self.add_image("maui_full_flash_1mb_0.69-5324f43.bin")
        self.old = _console(serial=SN, usb="/sys/usb1/1-2")
        self.new = _console(usb="/sys/usb1/1-2")
        self.scans = [[self.old], [self.new]]
        self.versions = []
        self.read_version = mock.MagicMock(side_effect=self._read_version)
        self.prepare = mock.MagicMock(return_value=["latency_timer set to 1 ms"])
        self.devnums = {}
        patches = [
            mock.patch.object(mcu_service, "FIRMWARE_ROOTS", (self.fw_root,)),
            mock.patch.object(ite_flash, "write_device_log"),
            mock.patch.object(
                device,
                "find_unit",
                return_value=device.UnitInfo(
                    mcu=self.old, h2h=device.H2hDevice("/sys/usb2/2-2", serial=SN)
                ),
            ),
            mock.patch.object(device, "scan_mcu_consoles", side_effect=self._scan),
            mock.patch.object(
                ite_flash, "enumeration_key", side_effect=self._enumeration_key
            ),
            mock.patch.object(uart, "scan_uart_bridges", return_value=[_bridge()]),
            mock.patch.object(uart, "prepare_bridge", self.prepare),
            mock.patch.object(ite_flash, "REENUM_TIMEOUT_S", 0.0),
            mock.patch.object(ite_flash, "VERSION_RETRY_DELAY_S", 0.0),
        ]
        for patcher in patches:
            started = patcher.start()
            self.addCleanup(patcher.stop)
            if getattr(patcher, "attribute", "") == "write_device_log":
                self.mock_log = started

    def add_image(self, name, size=profile.MCU_FULL_IMAGE_SIZE):
        """Creates an image in the mcu_full firmware directory."""
        path = os.path.join(self.fw_dir, name)
        with open(path, "wb") as f:
            f.write(b"\xa5" * size)
        return path

    def _scan(self):
        return self.scans.pop(0) if len(self.scans) > 1 else self.scans[0]

    def _enumeration_key(self, console):
        # The flash re-enumerates the MCU: same port, new devnum.
        return (console.usb_path, "6" if console is self.new else "5")

    def _read_version(self, unused_port):
        return self.versions.pop(0) if self.versions else "0.69-5324f43"

    async def flash(self, **request):
        """Runs update_mcu_full_image; returns all progress messages."""
        servicer = mcu_service.MauiMcuServicer()
        context = mock.MagicMock()
        context.done.return_value = False
        with mock.patch.object(
            mcu_service, "read_mcu_version", side_effect=self.read_version
        ):
            return [
                p
                async for p in servicer.update_mcu_full_image(
                    maui_manufacturing_pb2.MauiMcuFullImageRequest(**request), context
                )
            ]

    def assert_failed(self, out, text):
        """The last message is a failure containing |text|."""
        self.assertTrue(out[-1].is_complete)
        self.assertFalse(out[-1].success)
        self.assertEqual(out[-1].phase, ite_flash.PHASE_FAILED)
        self.assertIn(text, out[-1].error_message)
        log_key, log_name, content = self.mock_log.call_args[0]
        self.assertEqual((log_key, log_name), (SN, "mcu_full_flash"))
        self.assertIn("ALL_PASSED=False", content)

    async def test_success(self):
        """Flash, re-enumeration and version: success with serial note."""
        self.versions = ["", "0.69-5324f43"]  # The console needs a moment.
        out = await self.flash(uart_port="/dev/ttyUSB0")
        final = out[-1]
        self.assertTrue(final.success, final.error_message)
        self.assertEqual(final.progress_percent, 100)
        self.assertEqual(final.phase, ite_flash.PHASE_DONE)
        self.assertEqual(final.mcu_version, "0.69-5324f43")
        self.assertEqual(final.mcu_serial, "Uninitialized")
        self.assertIn("serial number", final.status_message)
        phases = [p.phase for p in out]
        self.assertLess(phases.index("erasing"), phases.index("programming"))
        self.assertLess(phases.index("programming"), phases.index("rebooting"))
        percents = [p.progress_percent for p in out[:-1]]
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(max(percents), ite_flash.REBOOT_PERCENT)
        self.assertEqual(self.tool_argv()[-3:], ["-f", self.image, "-n"])
        self.prepare.assert_called_once()
        log_key, log_name, content = self.mock_log.call_args[0]
        self.assertEqual((log_key, log_name), (SN, "mcu_full_flash"))
        self.assertIn("ALL_PASSED=True", content)
        self.assertIn("sha256 ", content)
        self.assertIn("Programming...   : 100%", content)
        self.assertEqual(ite_flash._busy_ports, set())

    async def test_default_bridge_newest_image_and_verify(self):
        """Empty port and file: default bridge, newest image; verify at 3M."""
        os.utime(self.image, (1, 1))
        newest = self.add_image("maui_full_flash_1mb_0.70-aaaaaaa.bin")
        out = await self.flash(verify=True, baud=3000000)
        self.assertTrue(out[-1].success, out[-1].error_message)
        argv = self.tool_argv()
        self.assertEqual(argv[:4], ["-b", "3000000", "-d", "/dev/ttyUSB0"])
        self.assertEqual(argv[-2:], ["-f", newest])
        self.assertIn("verifying", [p.phase for p in out])

    async def test_rejects_invalid_requests(self):
        """Inputs are validated before itecomdbgr ever runs."""
        os.utime(self.add_image("short.bin", size=4096), (1, 1))
        cases = (
            ({"baud": 9600}, "Unsupported baud rate 9600"),
            ({"file_name": "nope.bin"}, "'nope.bin' not found"),
            ({"file_name": "short.bin"}, "exactly 1048576 bytes"),
            ({"uart_port": "/dev/ttyACM0"}, "not a detected USB-UART bridge"),
        )
        with mock.patch.object(
            ite_flash.asyncio, "create_subprocess_exec"
        ) as create_subprocess:
            for request, error in cases:
                self.assert_failed(await self.flash(**request), error)
            with mock.patch.object(uart, "scan_uart_bridges", return_value=[]):
                self.assert_failed(await self.flash(), "No USB-UART bridge detected")
            with mock.patch.object(ite_flash, "ITECOMDBGR", "/nonexistent"):
                self.assert_failed(await self.flash(), "is not installed")
        create_subprocess.assert_not_called()

    async def test_tool_failure_is_not_followed_by_reboot_wait(self):
        """exit 0 without 100 % fails right away."""
        self.scenario("bad_baud")
        out = await self.flash()
        self.assert_failed(out, "without completing Programming")
        self.assertNotIn(ite_flash.PHASE_REBOOTING, [p.phase for p in out])

    async def test_reenumeration_timeout(self):
        """The new App has to enumerate after the flash."""
        self.scans = [[self.old]]
        out = await self.flash()
        self.assert_failed(out, "did not enumerate")
        self.assertIn("Power-cycle", out[-1].error_message)
        self.read_version.assert_not_called()

    async def test_busy_bridge_is_rejected(self):
        """Two flashes never share a bridge."""
        ite_flash._busy_ports.add("/dev/ttyUSB0")
        try:
            self.assert_failed(await self.flash(), "already running")
        finally:
            ite_flash._busy_ports.discard("/dev/ttyUSB0")

    async def test_cancelled_rpc_stops_the_tool(self):
        """Leaving the page closes the stream: itecomdbgr is stopped."""
        self.scenario("endless")
        servicer = mcu_service.MauiMcuServicer()
        context = mock.MagicMock()
        context.done.return_value = False
        gen = servicer.update_mcu_full_image(
            maui_manufacturing_pb2.MauiMcuFullImageRequest(), context
        )
        async for update in gen:
            if update.phase == "programming":
                break
        await gen.aclose()
        self.assertEqual(ite_flash._busy_ports, set())


class TestServicerListings(unittest.TestCase):
    """list_uart_bridges and the mcu_full firmware listing."""

    def test_list_uart_bridges(self):
        """Bridges in scan order, the first one is the default."""
        servicer = mcu_service.MauiMcuServicer()
        bridges = [_bridge("/dev/ttyUSB0"), _bridge("/dev/ttyUSB1")]
        with mock.patch.object(uart, "scan_uart_bridges", return_value=bridges):
            resp = servicer.list_uart_bridges(empty_pb2.Empty(), None)
        self.assertEqual(resp.default_port, "/dev/ttyUSB0")
        self.assertEqual(
            [b.device_port for b in resp.bridges], ["/dev/ttyUSB0", "/dev/ttyUSB1"]
        )
        first = resp.bridges[0]
        self.assertEqual(
            (first.vid, first.pid, first.driver), (0x0403, 0x6001, "ftdi_sio")
        )
        self.assertTrue(first.by_id.endswith("FT9XYZ-if00-port0"))
        self.assertEqual(first.label, bridges[0].label)
        with mock.patch.object(uart, "scan_uart_bridges", return_value=[]):
            empty = servicer.list_uart_bridges(empty_pb2.Empty(), None)
        self.assertEqual(empty.default_port, "")
        self.assertIn("No USB-UART bridge", empty.message)

    def test_list_mcu_full_firmware_reports_sizes(self):
        """mcu_full files carry the required size; wrong sizes are named."""
        with tempfile.TemporaryDirectory() as root:
            fw_dir = os.path.join(root, profile.FW_COMPONENT_MCU_FULL)
            os.makedirs(fw_dir)
            for name, size in (
                ("good.bin", profile.MCU_FULL_IMAGE_SIZE),
                ("bad.bin", 10),
            ):
                with open(os.path.join(fw_dir, name), "wb") as f:
                    f.write(b"\xff" * size)
            with mock.patch.object(mcu_service, "FIRMWARE_ROOTS", (root,)):
                resp = mcu_service.MauiMcuServicer().list_firmware(
                    maui_manufacturing_pb2.MauiListFirmwareRequest(
                        component=profile.FW_COMPONENT_MCU_FULL
                    ),
                    None,
                )
        self.assertEqual(
            {f.file_name: f.expected_size_bytes for f in resp.files},
            {"good.bin": 0x100000, "bad.bin": 0x100000},
        )
        self.assertIn("bad.bin (10 bytes, expected 1048576)", resp.message)
        self.assertNotIn("good.bin", resp.message)


if __name__ == "__main__":
    unittest.main()
