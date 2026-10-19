# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for the Maui v1.2 (ITE IT82002) backend."""

import os
import tempfile
import unittest
from unittest import mock

from google.protobuf import empty_pb2

from server import editions
from server import maui_common
from server import maui_v12_device as device
from server import maui_v12_mcu_service as mcu_service
from server import maui_v12_profile as profile
from server import maui_v12_services
from server.generated import maui_manufacturing_pb2


SN = "MAUIV12H26010001"


def _console(
    serial=SN,
    product=profile.PRODUCT_APP,
    node="/dev/ttyACM0",
    path="/sys/usb1/1-2/1-2.1",
):
    return device.McuConsole(
        dev_node=node,
        usb_path=path,
        serial=serial,
        product=product,
        stage=profile.stage_for_product(product),
        interface=os.path.basename(path) + ":1.0",
    )


def _h2h(serial=SN, path="/sys/usb2/2-2/2-2.3"):
    return device.H2hDevice(usb_path=path, serial=serial, product="H2H", pid=0x506D)


class FakeTester:
    """Stand-in for MauiV12ConsoleTester used by provision_serial."""

    def __init__(self, set_ok=True, get_serial=SN):
        self.set_ok = set_ok
        self.get_serial = get_serial
        self.calls = []

    def serial_set(self, sn):
        self.calls.append(("set", sn))
        return self.set_ok, f"Serial Number updated to: {sn}" if self.set_ok else "err"

    def reboot(self):
        self.calls.append(("reboot",))
        return ""

    def serial_get(self):
        self.calls.append(("get",))
        return self.get_serial, f"Serial Number: {self.get_serial}"


class TestProfile(unittest.TestCase):
    """Tests for maui_v12_profile."""

    def test_stage_for_product(self):
        self.assertEqual(
            profile.stage_for_product("Maui Diagnostic Controller"), profile.STAGE_APP
        )
        self.assertEqual(
            profile.stage_for_product("Maui RW Bootloader"), profile.STAGE_RW_BOOTLOADER
        )
        self.assertEqual(profile.stage_for_product("?"), profile.STAGE_UNKNOWN)

    def test_is_valid_serial(self):
        self.assertTrue(profile.is_valid_serial(SN))
        for bad in (
            "",
            "Uninitialized",
            "MAUIV12H2601",
            "maui v12",
            "MAUIV12H26010001X",
            "XMAUIV12H26010001",
        ):
            self.assertFalse(profile.is_valid_serial(bad), bad)


class TestDiscovery(unittest.TestCase):
    """Tests for the sysfs CDC-ACM discovery and H2H pairing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tty = os.path.join(self.tmp.name, "class", "tty")
        os.makedirs(self.tty)

    def _add(self, tty, vid, pid, product, serial, iface="1-2.1:1.0", usb="1-2.1"):
        usb_dir = os.path.join(self.tmp.name, "devices", usb)
        iface_dir = os.path.join(usb_dir, iface)
        os.makedirs(iface_dir, exist_ok=True)
        for name, value in (
            ("idVendor", vid),
            ("idProduct", pid),
            ("product", product),
            ("serial", serial),
        ):
            with open(os.path.join(usb_dir, name), "w", encoding="utf-8") as f:
                f.write(value + "\n")
        os.makedirs(os.path.join(self.tty, tty))
        os.symlink(iface_dir, os.path.join(self.tty, tty, "device"))

    def test_scan_mcu_consoles(self):
        self._add("ttyACM0", "18d1", "5085", profile.PRODUCT_APP, SN)
        self._add("ttyACM1", "18d1", "5085", profile.PRODUCT_APP, SN, iface="1-2.1:1.2")
        self._add("ttyACM2", "18d1", "5066", "Ti50", "X", usb="1-3")
        self._add(
            "ttyACM3",
            "18d1",
            "5085",
            profile.PRODUCT_RW_BOOTLOADER,
            "B",
            usb="1-4",
            iface="1-4:1.0",
        )
        consoles = device.scan_mcu_consoles(self.tty)
        self.assertEqual(
            [c.dev_node for c in consoles], ["/dev/ttyACM0", "/dev/ttyACM3"]
        )
        self.assertTrue(consoles[0].is_app)
        self.assertEqual(consoles[0].serial, SN)
        self.assertEqual(consoles[1].stage, profile.STAGE_RW_BOOTLOADER)

    def test_pair_h2h(self):
        mcu = _console(path="/sys/usb1/1-2/1-2.1")
        same_parent = _h2h(serial="A", path="/sys/usb1/1-2/1-2.3")
        other_bus = _h2h(serial="B", path="/sys/usb2/2-2/2-2.3")
        unrelated = _h2h(serial="C", path="/sys/usb2/2-5/2-5.3")
        self.assertIsNone(device.pair_h2h(mcu, [], [mcu]))
        self.assertEqual(device.pair_h2h(mcu, [unrelated], [mcu]).serial, "C")
        self.assertEqual(
            device.pair_h2h(mcu, [unrelated, same_parent], [mcu]).serial, "A"
        )
        self.assertEqual(
            device.pair_h2h(mcu, [unrelated, other_bus], [mcu]).serial, "B"
        )

    def test_lone_h2h_needs_a_hub_match_with_several_boards(self):
        mcu = _console(path="/sys/usb1/1-2/1-2.1")
        other_mcu = _console(node="/dev/ttyACM2", path="/sys/usb1/1-5/1-5.1")
        other_board_h2h = _h2h(serial="C", path="/sys/usb2/2-5/2-5.3")
        own_h2h = _h2h(serial="B", path="/sys/usb2/2-2/2-2.3")
        consoles = [mcu, other_mcu]
        self.assertIsNone(device.pair_h2h(mcu, [other_board_h2h], consoles))
        self.assertEqual(device.pair_h2h(mcu, [own_h2h], consoles).serial, "B")
        self.assertEqual(
            device.pair_h2h(other_mcu, [other_board_h2h], consoles).serial, "C"
        )
        # Without a console list the consoles are scanned.
        with mock.patch.object(device, "scan_mcu_consoles", return_value=consoles):
            self.assertIsNone(device.pair_h2h(mcu, [other_board_h2h]))
        with mock.patch.object(device, "scan_mcu_consoles", return_value=[mcu]):
            self.assertEqual(device.pair_h2h(mcu, [other_board_h2h]).serial, "C")

    def test_find_unit_prefers_app(self):
        boot = _console(
            serial="B", product=profile.PRODUCT_RW_BOOTLOADER, node="/dev/ttyACM1"
        )
        app = _console()
        unit = device.find_unit(lambda: [boot, app], lambda: [_h2h()])
        self.assertIs(unit.mcu, app)
        self.assertEqual(unit.h2h.serial, SN)

    def test_wait_for_console(self):
        now = [0.0]
        sleeps = []

        def sleep(s):
            sleeps.append(s)
            now[0] += s

        scans = iter([[], [_console(serial="Uninitialized")], [_console()]])
        found = device.wait_for_console(
            SN, scan=lambda: next(scans), sleep=sleep, clock=lambda: now[0]
        )
        self.assertEqual(found.serial, SN)
        self.assertEqual(sleeps[0], profile.REENUM_INITIAL_DELAY_S)

    def test_wait_for_console_times_out(self):
        now = [0.0]

        def sleep(s):
            now[0] += s

        found = device.wait_for_console(
            SN, scan=lambda: [], sleep=sleep, clock=lambda: now[0]
        )
        self.assertIsNone(found)
        self.assertGreaterEqual(
            now[0], profile.REENUM_INITIAL_DELAY_S + profile.REENUM_GRACE_S
        )


class TestProvisionSerial(unittest.TestCase):
    """Tests for maui_v12_device.provision_serial."""

    def test_already_matched_passes_without_reboot(self):
        tester = FakeTester()
        res = device.provision_serial(
            device.UnitInfo(mcu=_console(), h2h=_h2h()), make_tester=lambda p: tester
        )
        self.assertTrue(res.success)
        self.assertTrue(res.already_provisioned)
        self.assertEqual(tester.calls, [])

    def test_sets_reboots_and_verifies(self):
        tester = FakeTester()
        new_console = _console(node="/dev/ttyACM1")
        res = device.provision_serial(
            device.UnitInfo(mcu=_console(serial="Uninitialized"), h2h=_h2h()),
            make_tester=lambda p: tester,
            wait=lambda sn: new_console if sn == SN else None,
        )
        self.assertTrue(res.success, res.message)
        self.assertEqual(tester.calls, [("set", SN), ("reboot",), ("get",)])
        self.assertEqual(res.device_port, "/dev/ttyACM1")
        self.assertEqual(res.previous_mcu_serial, "Uninitialized")

    def test_overwrites_different_serial(self):
        tester = FakeTester()
        res = device.provision_serial(
            device.UnitInfo(mcu=_console(serial="MAUIV12H99999999"), h2h=_h2h()),
            make_tester=lambda p: tester,
            wait=lambda sn: _console(),
        )
        self.assertTrue(res.success)
        self.assertIn(("set", SN), tester.calls)

    def test_invalid_h2h_serial_fails(self):
        res = device.provision_serial(
            device.UnitInfo(mcu=_console(), h2h=_h2h(serial="BOGUS"))
        )
        self.assertFalse(res.success)
        self.assertIn("not a valid", res.message)

    def test_expected_serial_mismatch_fails(self):
        res = device.provision_serial(
            device.UnitInfo(mcu=_console(), h2h=_h2h()),
            expected_serial="MAUIV12H00000000",
        )
        self.assertFalse(res.success)

    def test_missing_mcu_or_bootloader_fails(self):
        self.assertFalse(device.provision_serial(device.UnitInfo(h2h=_h2h())).success)
        boot = _console(product=profile.PRODUCT_RW_BOOTLOADER, serial="x")
        self.assertFalse(
            device.provision_serial(device.UnitInfo(mcu=boot, h2h=_h2h())).success
        )

    def test_serial_set_not_confirmed_fails(self):
        tester = FakeTester(set_ok=False)
        res = device.provision_serial(
            device.UnitInfo(mcu=_console(serial="Uninitialized"), h2h=_h2h()),
            make_tester=lambda p: tester,
        )
        self.assertFalse(res.success)
        self.assertNotIn(("reboot",), tester.calls)

    def test_no_reenumeration_fails(self):
        res = device.provision_serial(
            device.UnitInfo(mcu=_console(serial="Uninitialized"), h2h=_h2h()),
            make_tester=lambda p: FakeTester(),
            wait=lambda sn: None,
        )
        self.assertFalse(res.success)
        self.assertIn("did not re-enumerate", res.message)

    def test_serial_get_mismatch_fails(self):
        res = device.provision_serial(
            device.UnitInfo(mcu=_console(serial="Uninitialized"), h2h=_h2h()),
            make_tester=lambda p: FakeTester(get_serial="MAUIV12H00000000"),
            wait=lambda sn: _console(),
        )
        self.assertFalse(res.success)
        self.assertIn("expected", res.message)

    def test_serial_get_without_answer_fails(self):
        res = device.provision_serial(
            device.UnitInfo(mcu=_console(serial="Uninitialized"), h2h=_h2h()),
            make_tester=lambda p: FakeTester(get_serial=""),
            wait=lambda sn: _console(),
        )
        self.assertFalse(res.success)
        self.assertIn("did not return", res.message)


class TestMauiV12ConsoleTester(unittest.TestCase):
    """Tests for MauiV12ConsoleTester parsing."""

    def setUp(self):
        self.tester = device.MauiV12ConsoleTester("/dev/ttyACM0")
        self.ser = mock.MagicMock()

    def _reply(self, text):
        return mock.patch.object(self.tester, "send_command", return_value=text)

    def test_version_pattern(self):
        version_case = self.tester.test_cases[0]
        self.assertEqual(version_case.name, "MCU Version Check")
        self.assertTrue(
            version_case.evaluate(
                "Maui version 0.69-5324f43 (h2h-read-rpc, built 2026-10-13 08:45:17 "
                "UTC) [MCUBOOT-SLOT0 (0x80038200)]"
            )[0]
        )
        self.assertFalse(version_case.evaluate("Maui version 0.59.1-abc")[0])

    def test_serial_get_set(self):
        with self._reply("serial get\r\nSerial Number: MAUIV12H26010001\r\nmaui$ "):
            self.assertEqual(self.tester.serial_get(ser=self.ser)[0], SN)
        with self._reply(
            f"Serial Number updated to: {SN} (reboot required for USB descriptor update)"
        ):
            self.assertTrue(self.tester.serial_set(SN, ser=self.ser)[0])
        with self._reply("serial: wrong parameter count"):
            self.assertFalse(self.tester.serial_set(SN, ser=self.ser)[0])

    def test_hwrev_range(self):
        with self._reply("Hardware revision: 3"):
            self.assertEqual(self.tester.read_hwrev(ser=self.ser)[:2], (True, 3))
        with self._reply("Hardware revision: 9"):
            self.assertFalse(self.tester.read_hwrev(ser=self.ser)[0])

    def test_gpio_table_by_default(self):
        with self._reply("[ h2h_reset_l ] O = 0"):
            ok, _, mismatches, _ = self.tester.read_and_verify_gpios(ser=self.ser)
            self.assertFalse(ok)
            self.assertIn("ec_mem_oe_l: missing in 'gpio get'", mismatches)

    def test_read_gpio(self):
        with self._reply("[ ec_bsl_invoke ] I = 1\r\nmaui$ "):
            self.assertEqual(self.tester.read_gpio("ec_bsl_invoke", ser=self.ser)[0], 1)
        with self._reply("ec_bsl_invoke: 0"):
            self.assertEqual(self.tester.read_gpio("ec_bsl_invoke", ser=self.ser)[0], 0)
        with self._reply("garbage"):
            self.assertEqual(
                self.tester.read_gpio("ec_bsl_invoke", ser=self.ser)[0], -1
            )


class TestV12Servicers(unittest.TestCase):
    """Tests for the Maui v1.2 servicer overrides."""

    def test_station2_topology_uses_acm_console(self):
        usb = [
            maui_common.UsbDeviceInfo(sys_path="a", vid=0x0451, pid=0x8142),
            maui_common.UsbDeviceInfo(sys_path="b", vid=0x0451, pid=0x8140),
            maui_common.UsbDeviceInfo(sys_path="c", vid=0x18D1, pid=0x506D, serial=SN),
        ]
        servicer = maui_v12_services.MauiV12Station2Servicer()
        now = [0.0]
        servicer._clock = lambda: now[0]
        servicer._sleep = lambda s: now.__setitem__(0, now[0] + s)
        with mock.patch.object(
            maui_common, "scan_usb_devices", return_value=usb
        ), mock.patch.object(device, "scan_mcu_consoles", return_value=[_console()]):
            self.assertEqual(
                servicer._check_host_usb_topology(), (True, True, True, True)
            )
        with mock.patch.object(
            maui_common, "scan_usb_devices", return_value=usb
        ), mock.patch.object(device, "scan_mcu_consoles", return_value=[]):
            self.assertEqual(
                servicer._check_host_usb_topology(), (True, True, False, True)
            )

    def test_console_port_resolution(self):
        servicer = maui_v12_services.MauiV12Station2Servicer()
        with mock.patch.object(
            device, "find_app_console_port", return_value="/dev/ttyACM5"
        ) as find:
            self.assertEqual(
                servicer._resolve_console_port("/dev/does-not-exist"), "/dev/ttyACM5"
            )
            find.assert_called_with(maui_v12_services.DEFAULT_ACM_PORT)
        self.assertEqual(servicer._resolve_console_port("/dev/null"), "/dev/null")

    def test_pdc_discovery_lists_app_consoles(self):
        consoles = [_console(node=f"/dev/ttyACM{i}", serial=f"S{i}") for i in range(7)]
        consoles.append(
            _console(product=profile.PRODUCT_RW_BOOTLOADER, node="/dev/ttyACM9")
        )
        with mock.patch.object(
            device, "scan_mcu_consoles", return_value=consoles
        ), mock.patch.object(device, "scan_h2h_devices", return_value=[]):
            resp = (
                maui_v12_services.MauiV12PdcServicer().discover_connected_pdc_devices(
                    empty_pb2.Empty(), None
                )
            )
        self.assertEqual(len(resp.devices), profile.PDC_MAX_SLOTS)
        self.assertEqual(resp.devices[0].device_port, "/dev/ttyACM0")
        self.assertEqual(resp.devices[4].slot_index, 5)

    @mock.patch("server.maui_station1_service.write_device_log")
    def test_label_verification_against_h2h_and_mcu(self, unused_log):
        servicer = maui_v12_services.MauiV12DeviceManagementServicer()
        unit = device.UnitInfo(mcu=_console(), h2h=_h2h())
        with mock.patch.object(device, "find_unit", return_value=unit):
            status = servicer.read_connected_device_serial(empty_pb2.Empty(), None)
            self.assertTrue(status.dual_endpoints_match)
            self.assertTrue(status.ftdi_is_programmed)
            self.assertEqual(status.ftdi_pid, profile.MCU_PID)
            ok = servicer.verify_scanned_label(
                maui_manufacturing_pb2.MauiVerifyLabelRequest(scanned_barcode=SN), None
            )
            self.assertTrue(ok.matches)
            self.assertIn("MCU Endpoint", ok.message)
            bad = servicer.verify_scanned_label(
                maui_manufacturing_pb2.MauiVerifyLabelRequest(
                    scanned_barcode="MAUIV12H00000000"
                ),
                None,
            )
            self.assertFalse(bad.matches)

        unprovisioned = device.UnitInfo(
            mcu=_console(serial="Uninitialized"), h2h=_h2h()
        )
        with mock.patch.object(device, "find_unit", return_value=unprovisioned):
            status = servicer.read_connected_device_serial(empty_pb2.Empty(), None)
        self.assertFalse(status.dual_endpoints_match)
        self.assertIn("not provisioned", status.message)


class TestMauiMcuServicer(unittest.IsolatedAsyncioTestCase):
    """Tests for MauiMcuServicer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fw_dir = os.path.join(self.tmp.name, profile.FW_COMPONENT_MCU_APP)
        os.makedirs(self.fw_dir)
        patcher = mock.patch.object(mcu_service, "FIRMWARE_ROOTS", (self.tmp.name,))
        patcher.start()
        self.addCleanup(patcher.stop)
        log_patcher = mock.patch.object(mcu_service, "write_device_log")
        self.mock_log = log_patcher.start()
        self.addCleanup(log_patcher.stop)
        scan_patcher = mock.patch.object(
            device, "scan_mcu_consoles", return_value=[_console()]
        )
        scan_patcher.start()
        self.addCleanup(scan_patcher.stop)
        # maui-utils container cleanup (docker rm -f) must not run for real.
        run_patcher = mock.patch("subprocess.run")
        self.mock_run = run_patcher.start()
        self.addCleanup(run_patcher.stop)
        self.servicer = mcu_service.MauiMcuServicer()

    def test_list_firmware_rejects_unknown_component(self):
        resp = self.servicer.list_firmware(
            maui_manufacturing_pb2.MauiListFirmwareRequest(component="../../etc"),
            None,
        )
        self.assertEqual(len(resp.files), 0)
        self.assertIn("Unknown firmware component", resp.message)
        self.assertEqual(mcu_service.list_firmware_files("../../etc"), [])

    async def test_update_mcu_app_rejects_undetected_port(self):
        with open(os.path.join(self.fw_dir, "maui_app_signed.bin"), "wb") as f:
            f.write(b"x")
        with mock.patch.object(
            device,
            "find_unit",
            return_value=device.UnitInfo(mcu=_console(), h2h=_h2h()),
        ), mock.patch.object(
            mcu_service.asyncio, "create_subprocess_exec"
        ) as mock_exec:
            out = [
                r
                async for r in self.servicer.update_mcu_app(
                    maui_manufacturing_pb2.MauiMcuUpdateRequest(device_port="/dev/sda"),
                    None,
                )
            ]
        self.assertFalse(out[-1].success)
        self.assertIn("not a detected Maui MCU console", out[-1].error_message)
        mock_exec.assert_not_called()

    def test_list_firmware(self):
        with open(os.path.join(self.fw_dir, "maui_app_signed.bin"), "wb") as f:
            f.write(b"x" * 10)
        resp = self.servicer.list_firmware(
            maui_manufacturing_pb2.MauiListFirmwareRequest(
                component=profile.FW_COMPONENT_MCU_APP
            ),
            None,
        )
        self.assertEqual([f.file_name for f in resp.files], ["maui_app_signed.bin"])
        self.assertEqual(resp.files[0].size_bytes, 10)
        empty = self.servicer.list_firmware(
            maui_manufacturing_pb2.MauiListFirmwareRequest(component="pdc"), None
        )
        self.assertEqual(len(empty.files), 0)

    def test_provision_mcu_serial_logs_result(self):
        unit = device.UnitInfo(mcu=_console(), h2h=_h2h())
        with mock.patch.object(device, "find_unit", return_value=unit):
            resp = self.servicer.provision_mcu_serial(
                maui_manufacturing_pb2.MauiProvisionSerialRequest(), None
            )
        self.assertTrue(resp.success)
        self.assertTrue(resp.already_provisioned)
        args = self.mock_log.call_args[0]
        self.assertEqual(args[:2], (SN, "mcu_serial_provisioning"))
        self.assertIn("ALL_PASSED=True", args[2])

    async def test_update_mcu_app_without_firmware_fails(self):
        with mock.patch.object(
            device,
            "find_unit",
            return_value=device.UnitInfo(mcu=_console(), h2h=_h2h()),
        ):
            out = [
                r
                async for r in self.servicer.update_mcu_app(
                    maui_manufacturing_pb2.MauiMcuUpdateRequest(), None
                )
            ]
        self.assertTrue(out[-1].is_complete)
        self.assertFalse(out[-1].success)
        self.assertIn("not found", out[-1].error_message)

    async def test_update_mcu_app_runs_maui_flash(self):
        fw = os.path.join(self.fw_dir, "maui_app_signed.bin")
        with open(fw, "wb") as f:
            f.write(b"x")

        proc = mock.MagicMock()
        proc.returncode = None
        # Upload progress rewrites its line with a bare CR.
        chunks = [b"Uploading 50%\rUploading 100%\n", b""]

        async def read(unused_size=-1):
            chunk = chunks.pop(0)
            if not chunk:
                proc.returncode = 0
            return chunk

        async def wait():
            proc.returncode = 0
            return 0

        proc.stdout.read = read
        proc.wait = wait
        context = mock.MagicMock()
        context.done.return_value = False

        with mock.patch.object(
            device,
            "find_unit",
            return_value=device.UnitInfo(mcu=_console(), h2h=_h2h()),
        ), mock.patch.object(
            mcu_service.asyncio, "create_subprocess_exec", return_value=proc
        ) as mock_exec, mock.patch.object(
            device, "wait_for_console", return_value=_console()
        ), mock.patch.object(
            mcu_service, "read_mcu_version", return_value="0.59.2"
        ), mock.patch(
            "server.maui_utils_runner.MauiUtilsSession.stage",
            return_value="/mnt/firmware_dir/maui_app_signed.bin",
        ):
            out = [
                r
                async for r in self.servicer.update_mcu_app(
                    maui_manufacturing_pb2.MauiMcuUpdateRequest(), context
                )
            ]

        argv = mock_exec.call_args[0]
        self.assertIn("--entrypoint", argv)
        self.assertEqual(
            list(argv[-7:]),
            [
                "--port",
                "/dev/ttyACM0",
                "mcu",
                "--file",
                "/mnt/firmware_dir/maui_app_signed.bin",
                "--target",
                "app",
            ],
        )
        self.assertEqual(max(r.progress_percent for r in out[:-1]), 99)
        self.assertTrue(out[-1].success, out[-1].error_message)
        self.assertEqual(out[-1].mcu_version, "0.59.2")

    async def test_cancelled_update_stops_maui_utils(self):
        fw = os.path.join(self.fw_dir, "maui_app_signed.bin")
        with open(fw, "wb") as f:
            f.write(b"x")
        proc = mock.MagicMock()
        proc.returncode = None

        async def read(unused_size=-1):
            return b"Uploading 10%\n"

        async def wait():
            proc.returncode = -9
            return -9

        proc.stdout.read = read
        proc.wait = wait
        context = mock.MagicMock()
        context.done.return_value = False

        with mock.patch.object(
            device,
            "find_unit",
            return_value=device.UnitInfo(mcu=_console(), h2h=_h2h()),
        ), mock.patch.object(
            mcu_service.asyncio, "create_subprocess_exec", return_value=proc
        ), mock.patch(
            "server.maui_utils_runner.MauiUtilsSession.stage",
            return_value="/mnt/firmware_dir/maui_app_signed.bin",
        ):
            gen = self.servicer.update_mcu_app(
                maui_manufacturing_pb2.MauiMcuUpdateRequest(), context
            )
            async for update in gen:
                if update.progress_percent == 10:
                    break  # The client went away mid-upload.
            await gen.aclose()

        proc.kill.assert_called()
        rm_calls = [
            c[0][0]
            for c in self.mock_run.call_args_list
            if c[0][0][1:3] == ["rm", "-f"]
        ]
        self.assertTrue(rm_calls, "maui-utils container was not removed")

    async def test_update_mcu_app_uses_the_selected_board(self):
        """Logs under the selected board's serial and waits on its USB port."""
        with open(os.path.join(self.fw_dir, "maui_app_signed.bin"), "wb") as f:
            f.write(b"x")
        sn2 = "MAUIV12H26010002"
        first = _console()
        second = _console(serial=sn2, node="/dev/ttyACM2", path="/sys/usb1/1-3/1-3.1")
        proc = mock.MagicMock()
        proc.returncode = None
        chunks = [b"Uploading 100%\n", b""]

        async def read(unused_size=-1):
            return chunks.pop(0)

        async def wait():
            proc.returncode = 0
            return 0

        proc.stdout.read = read
        proc.wait = wait
        context = mock.MagicMock()
        context.done.return_value = False
        h2hs = [_h2h(), _h2h(serial=sn2, path="/sys/usb1/1-3/1-3.2")]
        with mock.patch.object(
            device, "find_unit", return_value=device.UnitInfo(mcu=first, h2h=h2hs[0])
        ), mock.patch.object(
            device, "scan_mcu_consoles", return_value=[first, second]
        ), mock.patch.object(
            device, "scan_h2h_devices", return_value=h2hs
        ), mock.patch.object(
            mcu_service.asyncio, "create_subprocess_exec", return_value=proc
        ), mock.patch.object(
            device, "wait_for_console", return_value=second
        ) as wait_for_console, mock.patch.object(
            mcu_service, "read_mcu_version", return_value="0.69.1"
        ), mock.patch(
            "server.maui_utils_runner.MauiUtilsSession.stage",
            return_value="/mnt/firmware_dir/maui_app_signed.bin",
        ):
            out = [
                r
                async for r in self.servicer.update_mcu_app(
                    maui_manufacturing_pb2.MauiMcuUpdateRequest(
                        device_port="/dev/ttyACM2"
                    ),
                    context,
                )
            ]
        self.assertTrue(out[-1].success, out[-1].error_message)
        # Same physical port: unprovisioned boards share their serial.
        self.assertIsNone(wait_for_console.call_args[0][0])
        self.assertEqual(wait_for_console.call_args[1]["usb_path"], second.usb_path)
        self.assertEqual(self.mock_log.call_args[0][:2], (sn2, "mcu_app_update"))


class _InlineThread:
    """threading.Thread stand-in running the target on start()."""

    def __init__(self, target, args=(), **unused_kwargs):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


class TestUnitStatus(unittest.TestCase):
    """get_unit_status queries the MCU console once per USB enumeration."""

    def setUp(self):
        self.servicer = mcu_service.MauiMcuServicer()
        self.devnum = "7"
        patches = [
            mock.patch.object(
                device,
                "find_unit",
                side_effect=lambda: device.UnitInfo(mcu=_console(), h2h=_h2h()),
            ),
            mock.patch.object(maui_common, "scan_usb_devices", return_value=[]),
            mock.patch.object(mcu_service.os.path, "exists", return_value=True),
            mock.patch.object(
                device, "read_sysfs", side_effect=lambda _path: self.devnum
            ),
            # Run the background console query inline.
            mock.patch.object(mcu_service.threading, "Thread", _InlineThread),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _status(self):
        return self.servicer.get_unit_status(empty_pb2.Empty(), mock.MagicMock())

    def test_console_queried_once_per_enumeration(self):
        info = mcu_service.ConsoleInfo(version="0.59.1", hwrev=3, nvs_serial=SN)
        with mock.patch.object(
            self.servicer, "_query_console", return_value=info
        ) as query:
            pending = self._status()
            first = self._status()
            second = self._status()
            self.assertEqual(query.call_count, 1)
            self.devnum = "8"  # Re-enumerated, e.g. after `reboot`.
            self.assertTrue(self._status().console_pending)
            self.assertEqual(query.call_count, 2)
        # USB info is reported at once, console info on the next poll.
        self.assertTrue(pending.console_pending)
        self.assertTrue(pending.serials_match)
        self.assertEqual(pending.mcu_version, "")
        self.assertFalse(first.console_pending)
        self.assertEqual(first, second)
        self.assertEqual(first.mcu_version, "0.59.1")
        self.assertEqual(first.hwrev, 3)
        self.assertEqual(first.mcu_nvs_serial, SN)
        self.assertTrue(first.serials_match)

    def test_console_without_answer_is_retried(self):
        with mock.patch.object(
            self.servicer,
            "_query_console",
            side_effect=[
                mcu_service.ConsoleInfo(),
                mcu_service.ConsoleInfo(version="0.59.1"),
            ],
        ) as query:
            self.assertTrue(self._status().console_pending)  # No answer.
            self.assertTrue(self._status().console_pending)  # Retried.
            self.assertEqual(self._status().mcu_version, "0.59.1")
        self.assertEqual(query.call_count, 2)

    def test_status_does_not_wait_for_a_running_query(self):
        started = []

        class _DeferredThread(_InlineThread):
            def start(self):
                started.append(self)

        with mock.patch.object(mcu_service.threading, "Thread", _DeferredThread):
            self.assertTrue(self._status().console_pending)
            self.assertTrue(self._status().console_pending)
        self.assertEqual(len(started), 1)  # No second query while running.

    def test_query_uses_a_single_serial_session(self):
        tester = mock.MagicMock()
        tester.send_command.return_value = "Maui version 0.59.1\r\nmaui$ "
        tester.read_hwrev.return_value = (True, 3, "")
        tester.serial_get.return_value = (SN, "")
        with mock.patch.object(
            self.servicer, "_make_tester", return_value=tester
        ), mock.patch.object(mcu_service, "open_serial_port") as open_port:
            info = self.servicer._query_console("/dev/ttyACM0")
        open_port.assert_called_once()
        self.assertEqual(info, mcu_service.ConsoleInfo("0.59.1", 3, SN))


class TestV12Edition(unittest.TestCase):
    """Edition wiring of Maui v1.2."""

    def test_no_edition_registers_a_grpc_service_twice(self):
        for edition in editions.EDITIONS.values():
            grpc_names = [
                editions.GRPC_SERVICE_OF[s]
                for s in edition.services
                if s in editions.GRPC_SERVICE_OF
            ]
            self.assertEqual(len(grpc_names), len(set(grpc_names)), edition.edition_id)

    def test_maui_v12_services(self):
        edition = editions.EDITIONS["maui-v1.2"]
        self.assertEqual(edition.maui_hw_rev, "v1.2")
        self.assertIn(editions.SERVICE_MAUI_MCU, edition.services)
        self.assertNotIn(editions.SERVICE_MAUI_STATION1, edition.services)
        self.assertNotIn(editions.SERVICE_MAUI_STATION2, edition.services)

    def test_serial_page_log_is_a_stage(self):
        logs = [log for _, log, _ in maui_v12_services.V12_STAGE_LOG_MAPPING]
        self.assertIn("serial_label_check.log", logs)


if __name__ == "__main__":
    unittest.main()
