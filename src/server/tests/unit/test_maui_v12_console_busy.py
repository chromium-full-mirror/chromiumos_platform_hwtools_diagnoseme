# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for the Maui v1.2 console busy registry and the MCU service
guards (busy consoles, same-port re-enumeration, firmware file names,
streaming maui-flash output)."""

# Generated protobuf members are not visible to pylint; the tests check the
# module's registry directly.
# pylint: disable=no-member,protected-access

import asyncio
import os
import tempfile
import threading
import unittest
from unittest import mock

from server import maui_common
from server import maui_v12_device as device
from server import maui_v12_h2h as h2h_check
from server import maui_v12_mcu_service as mcu_service
from server import maui_v12_profile as profile
from server.generated import maui_manufacturing_pb2


SN = "MAUIV12H26010001"


def _console(node="/dev/ttyACM0", usb="/sys/usb1/1-2/1-2.1", serial=SN):
    return device.McuConsole(
        dev_node=node,
        usb_path=usb,
        serial=serial,
        product=profile.PRODUCT_APP,
        stage=profile.STAGE_APP,
    )


class _RegistryTestCase(unittest.TestCase):
    """Every test must leave the process-wide registry empty."""

    def tearDown(self):
        leftover = dict(device._busy)
        device._busy.clear()
        self.assertEqual(leftover, {})


class TestConsoleBusy(_RegistryTestCase):
    """device.console_busy() and busy_reason()."""

    def test_exclusive_claim(self):
        """A second exclusive operation on the console fails at once."""
        console = _console()
        with device.console_busy([console], "MCU App update"):
            self.assertEqual(device.busy_reason(console), "MCU App update")
            self.assertEqual(device.busy_reason(console.dev_node), "MCU App update")
            # Re-enumerated under another node: the USB port is still busy.
            self.assertEqual(
                device.busy_reason("/dev/ttyACM5", console.usb_path), "MCU App update"
            )
            with self.assertRaisesRegex(
                device.ConsoleBusyError, "busy: MCU App update"
            ):
                with device.console_busy([console.dev_node], "H2H SPI serial check"):
                    pass
            # Another board stays free.
            other = _console("/dev/ttyACM2", "/sys/usb1/1-3/1-3.1")
            with device.console_busy([other], "MCU serial provisioning"):
                self.assertEqual(device.busy_reason(other), "MCU serial provisioning")
        self.assertEqual(device.busy_reason(console), "")

    def test_released_on_errors(self):
        """The claim ends with the block, also on exceptions."""
        with self.assertRaises(ValueError):
            with device.console_busy([_console()], "MCU App update"):
                raise ValueError("boom")
        self.assertEqual(device.busy_reason(_console()), "")

    def test_all_consoles(self):
        """The full image flash holds every console, and needs them all free."""
        with device.console_busy([device.ALL_CONSOLES], "MCU full image flash"):
            self.assertEqual(device.busy_reason(_console()), "MCU full image flash")
            with self.assertRaisesRegex(device.ConsoleBusyError, "full image"):
                # Even without a detected console.
                with device.console_busy([None], "MCU serial provisioning"):
                    pass
        with device.console_busy([_console()], "MCU App update"):
            with self.assertRaisesRegex(device.ConsoleBusyError, "MCU App update"):
                with device.console_busy([device.ALL_CONSOLES], "full image"):
                    pass

    def test_exclusive_operation_waits_for_a_status_query(self):
        """A running status query is waited for; it never waits itself."""
        console = _console()
        keys = device.claim_consoles([console], device.STATUS_QUERY, wait_s=0)
        timer = threading.Timer(0.1, device.release_consoles, args=(keys,))
        timer.start()
        try:
            with device.console_busy([console], "MCU App update", wait_s=10):
                self.assertEqual(device.busy_reason(console), "MCU App update")
                with self.assertRaises(device.ConsoleBusyError):
                    device.claim_consoles([console], device.STATUS_QUERY, wait_s=0)
        finally:
            timer.join()

    def test_status_query_wait_is_bounded(self):
        """A query that does not end in time makes the operation fail."""
        console = _console()
        with device.console_busy([console], device.STATUS_QUERY, wait_s=0):
            with self.assertRaisesRegex(device.ConsoleBusyError, "status query"):
                device.claim_consoles([console], "MCU App update", wait_s=0.05)

    def test_wait_for_console_on_the_same_usb_port(self):
        """Two unprovisioned boards: only the updated port counts."""
        first = _console(serial="Uninitialized")
        second = _console("/dev/ttyACM2", "/sys/usb1/1-3/1-3.1", "Uninitialized")
        kwargs = {
            "initial_delay_s": 0,
            "grace_s": 0,
            "scan": lambda: [first, second],
            "sleep": lambda _s: None,
        }
        self.assertIs(device.wait_for_console("Uninitialized", **kwargs), first)
        self.assertIs(
            device.wait_for_console(None, usb_path=second.usb_path, **kwargs), second
        )
        self.assertIsNone(
            device.wait_for_console(None, usb_path="/sys/usb1/1-9", **kwargs)
        )


class TestConsoleBusyAsync(unittest.IsolatedAsyncioTestCase):
    """device.claim_consoles_async() for the streaming RPCs."""

    def tearDown(self):
        leftover = dict(device._busy)
        device._busy.clear()
        self.assertEqual(leftover, {})

    async def test_claim_and_busy(self):
        """Claims like claim_consoles() without blocking the event loop."""
        keys = await device.claim_consoles_async([_console()], "MCU App update")
        try:
            self.assertEqual(device.busy_reason(_console()), "MCU App update")
            with self.assertRaises(device.ConsoleBusyError):
                await device.claim_consoles_async([_console()], "H2H SPI serial check")
        finally:
            device.release_consoles(keys)

    async def test_cancelled_claim_is_released(self):
        """A claim that completes after the RPC was cancelled is dropped."""
        query = device.claim_consoles([_console()], device.STATUS_QUERY, wait_s=0)
        task = asyncio.create_task(
            device.claim_consoles_async([_console()], "MCU App update", wait_s=10)
        )
        await asyncio.sleep(0.05)  # The executor thread waits for the query.
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        device.release_consoles(query)  # Now the thread claims the console...
        for _ in range(200):  # ...and the done-callback releases it again.
            if not device._busy:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(device.busy_reason(_console()), "")


class TestFirmwareNames(unittest.TestCase):
    """firmware_name_error() and the component directory confinement."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "firmware")
        self.app_dir = os.path.join(self.root, profile.FW_COMPONENT_MCU_APP)
        os.makedirs(self.app_dir)
        self.outside = os.path.join(tmp.name, "secret.bin")
        with open(self.outside, "wb") as f:
            f.write(b"x")
        patcher = mock.patch.object(mcu_service, "FIRMWARE_ROOTS", (self.root,))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_plain_file_names(self):
        """Path separators, '..' and NUL are rejected."""
        for bad in (
            "../secret.bin",
            "mcu_full/a.bin",
            "/etc/passwd",
            "..",
            "a\\b.bin",
            "a\x00.bin",
        ):
            self.assertIn(
                "Invalid firmware file name", mcu_service.firmware_name_error(bad)
            )
            self.assertEqual(
                mcu_service.select_firmware_file(profile.FW_COMPONENT_MCU_APP, bad), ""
            )
        self.assertEqual(mcu_service.firmware_name_error(""), "")
        self.assertEqual(mcu_service.firmware_name_error("maui_app_0.69.bin"), "")

    def test_symlinks_out_of_the_component_directory_are_ignored(self):
        """A link to a file outside the directory is neither listed nor used."""
        good = os.path.join(self.app_dir, "maui_app.bin")
        with open(good, "wb") as f:
            f.write(b"x")
        os.symlink(self.outside, os.path.join(self.app_dir, "evil.bin"))
        os.symlink(good, os.path.join(self.app_dir, "alias.bin"))
        os.symlink("/nonexistent", os.path.join(self.app_dir, "dangling.bin"))
        self.assertEqual(
            sorted(
                os.path.basename(f)
                for f in mcu_service.list_firmware_files(profile.FW_COMPONENT_MCU_APP)
            ),
            ["alias.bin", "maui_app.bin"],
        )
        self.assertEqual(
            mcu_service.select_firmware_file(profile.FW_COMPONENT_MCU_APP, "evil.bin"),
            "",
        )


class _FakeContext:  # pylint: disable=too-few-public-methods
    """grpc.ServicerContext stand-in."""

    def __init__(self):
        self.cancelled = False

    def done(self):
        """True once the RPC was cancelled."""
        return self.cancelled


class TestReadToolLines(unittest.IsolatedAsyncioTestCase):
    """_read_tool_lines(): maui-flash output split on CR and LF."""

    async def test_cr_only_progress_streams(self):
        """Progress rewritten with bare CRs arrives before the tool ends."""
        more = asyncio.Event()
        chunks = [b"Uploading  10%\rUploading  20%\r", b"Uploading 100%\nDone", b""]

        async def read(unused_size=-1):
            if len(chunks) < 3:
                await more.wait()
            return chunks.pop(0)

        proc = mock.MagicMock()
        proc.stdout.read = read
        deadline = mcu_service.time.monotonic() + 60
        gen = mcu_service._read_tool_lines(proc, _FakeContext(), deadline)
        self.assertEqual(await gen.__anext__(), "Uploading  10%")
        self.assertEqual(await gen.__anext__(), "Uploading  20%")
        more.set()
        self.assertEqual([line async for line in gen], ["Uploading 100%", "Done"])

    async def test_cancellation_is_noticed_without_output(self):
        """A silent tool does not delay the cancellation check."""

        async def read(unused_size=-1):
            await asyncio.sleep(3600)

        proc = mock.MagicMock()
        proc.stdout.read = read
        context = _FakeContext()
        asyncio.get_running_loop().call_later(0.1, setattr, context, "cancelled", True)
        deadline = mcu_service.time.monotonic() + 60
        with mock.patch.object(mcu_service, "TOOL_POLL_S", 0.05):
            lines = [
                line
                async for line in mcu_service._read_tool_lines(proc, context, deadline)
            ]
        self.assertEqual(lines, [])


class TestServicerBusy(unittest.IsolatedAsyncioTestCase):
    """A second exclusive RPC on a busy console gets a clear error."""

    def setUp(self):
        self.console = _console()
        self.unit = device.UnitInfo(
            mcu=self.console,
            h2h=device.H2hDevice("/sys/usb1/1-2/1-2.2", serial=SN),
            consoles=[self.console],
        )
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        app_dir = os.path.join(tmp.name, profile.FW_COMPONENT_MCU_APP)
        os.makedirs(app_dir)
        with open(os.path.join(app_dir, "maui_app.bin"), "wb") as f:
            f.write(b"x")
        patches = [
            mock.patch.object(mcu_service, "FIRMWARE_ROOTS", (tmp.name,)),
            mock.patch.object(device, "find_unit", return_value=self.unit),
            mock.patch.object(device, "scan_mcu_consoles", return_value=[self.console]),
            mock.patch.object(h2h_check, "find_unit", return_value=self.unit),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        log_patcher = mock.patch.object(mcu_service, "write_device_log")
        self.mock_log = log_patcher.start()
        self.addCleanup(log_patcher.stop)
        self.servicer = mcu_service.MauiMcuServicer()

    def tearDown(self):
        leftover = dict(device._busy)
        device._busy.clear()
        self.assertEqual(leftover, {})

    def test_provisioning_on_a_busy_console(self):
        """Provisioning is refused and the board's log is kept."""
        with device.console_busy([self.console], "MCU App update"), mock.patch.object(
            device, "provision_serial"
        ) as provision:
            resp = self.servicer.provision_mcu_serial(
                maui_manufacturing_pb2.MauiProvisionSerialRequest(), None
            )
        provision.assert_not_called()
        self.assertFalse(resp.success)
        self.assertIn("busy: MCU App update", resp.message)
        self.mock_log.assert_not_called()

    def test_provisioning_holds_the_console(self):
        """While provisioning runs, the console is busy."""
        seen = []

        def provision(unit, **unused_kwargs):
            seen.append(device.busy_reason(unit.mcu))
            return device.ProvisionResult(success=True, message="ok")

        with mock.patch.object(device, "provision_serial", side_effect=provision):
            resp = self.servicer.provision_mcu_serial(
                maui_manufacturing_pb2.MauiProvisionSerialRequest(), None
            )
        self.assertTrue(resp.success)
        self.assertEqual(seen, ["MCU serial provisioning"])
        self.assertEqual(device.busy_reason(self.console), "")

    def test_h2h_check_on_a_busy_console(self):
        """The H2H SPI check never touches a busy console."""
        with device.console_busy(
            [self.console], "MCU serial provisioning"
        ), mock.patch.object(h2h_check, "run_check") as run_check:
            resp = self.servicer.check_h2h_spi_serial(
                maui_manufacturing_pb2.MauiH2hSpiCheckRequest(), None
            )
        run_check.assert_not_called()
        self.assertFalse(resp.success)
        self.assertTrue(resp.firmware_supported)  # No "update the App" hint.
        self.assertIn("busy: MCU serial provisioning", resp.message)
        self.mock_log.assert_not_called()

    async def test_app_update_on_a_busy_console(self):
        """update_mcu_app is refused before maui-flash starts."""
        with device.console_busy(
            [self.console], "H2H SPI serial check"
        ), mock.patch.object(mcu_service.asyncio, "create_subprocess_exec") as run:
            out = [
                r
                async for r in self.servicer.update_mcu_app(
                    maui_manufacturing_pb2.MauiMcuUpdateRequest(), _FakeContext()
                )
            ]
        run.assert_not_called()
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0].success)
        self.assertIn("busy: H2H SPI serial check", out[0].error_message)
        self.mock_log.assert_not_called()

    async def test_full_flash_blocks_every_console(self):
        """A running full image flash refuses App updates and itself."""
        with device.console_busy([device.ALL_CONSOLES], "MCU full image flash"):
            app = [
                r
                async for r in self.servicer.update_mcu_app(
                    maui_manufacturing_pb2.MauiMcuUpdateRequest(), _FakeContext()
                )
            ]
            full = [
                r
                async for r in self.servicer.update_mcu_full_image(
                    maui_manufacturing_pb2.MauiMcuFullImageRequest(), _FakeContext()
                )
            ]
        self.assertIn("busy: MCU full image flash", app[-1].error_message)
        self.assertIn("busy: MCU full image flash", full[-1].error_message)
        self.assertEqual(full[-1].phase, "failed")

    async def test_full_flash_refused_while_a_console_is_busy(self):
        """The full image flash needs every console free."""
        with device.console_busy([self.console], "MCU App update"):
            out = [
                r
                async for r in self.servicer.update_mcu_full_image(
                    maui_manufacturing_pb2.MauiMcuFullImageRequest(), _FakeContext()
                )
            ]
        self.assertFalse(out[-1].success)
        self.assertIn("busy: MCU App update", out[-1].error_message)

    async def test_invalid_file_names_are_rejected(self):
        """Both update RPCs only take plain file names."""
        with mock.patch.object(mcu_service.asyncio, "create_subprocess_exec") as run:
            app = [
                r
                async for r in self.servicer.update_mcu_app(
                    maui_manufacturing_pb2.MauiMcuUpdateRequest(
                        file_name="../mcu_full/x.bin"
                    ),
                    _FakeContext(),
                )
            ]
            full = [
                r
                async for r in self.servicer.update_mcu_full_image(
                    maui_manufacturing_pb2.MauiMcuFullImageRequest(
                        file_name="/etc/passwd"
                    ),
                    _FakeContext(),
                )
            ]
        run.assert_not_called()
        self.assertIn("Invalid firmware file name", app[-1].error_message)
        self.assertIn("Invalid firmware file name", full[-1].error_message)


class _InlineThread:  # pylint: disable=too-few-public-methods
    """threading.Thread stand-in running the target on start()."""

    def __init__(self, target, args=(), **unused_kwargs):
        self._target = target
        self._args = args

    def start(self):
        """Runs the target right away."""
        self._target(*self._args)


class TestStatusQueryWhileBusy(_RegistryTestCase):
    """get_unit_status skips the console query while the console is busy."""

    def setUp(self):
        self.console = _console()
        patches = [
            mock.patch.object(
                device,
                "find_unit",
                return_value=device.UnitInfo(mcu=self.console, consoles=[self.console]),
            ),
            mock.patch.object(maui_common, "scan_usb_devices", return_value=[]),
            mock.patch.object(mcu_service.os.path, "exists", return_value=True),
            mock.patch.object(device, "read_sysfs", return_value="7"),
            mock.patch.object(mcu_service.threading, "Thread", _InlineThread),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.servicer = mcu_service.MauiMcuServicer()

    def _status(self):
        return self.servicer.get_unit_status(None, None)

    def test_no_query_while_busy(self):
        """The serial page never opens a console that another RPC uses."""
        info = mcu_service.ConsoleInfo(version="0.69-5324f43")
        with mock.patch.object(
            self.servicer, "_query_console", return_value=info
        ) as query:
            with device.console_busy([self.console], "MCU App update"):
                self.assertTrue(self._status().console_pending)
            query.assert_not_called()
            self.assertTrue(self._status().console_pending)  # Queried now.
            self.assertEqual(self._status().mcu_version, "0.69-5324f43")
        query.assert_called_once()

    def test_query_holds_the_console(self):
        """Exclusive RPCs see the running query (and wait for it)."""
        seen = []

        def query(unused_node):
            seen.append(device.busy_reason(self.console))
            return mcu_service.ConsoleInfo(version="0.69-5324f43")

        with mock.patch.object(self.servicer, "_query_console", side_effect=query):
            self._status()
        self.assertEqual(seen, [device.STATUS_QUERY])
        self.assertEqual(device.busy_reason(self.console), "")


if __name__ == "__main__":
    unittest.main()
