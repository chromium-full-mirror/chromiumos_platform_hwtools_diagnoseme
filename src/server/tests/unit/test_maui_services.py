# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for Maui manufacturing services and console testing framework."""

# pylint: disable=protected-access,too-many-lines,too-many-public-methods
# pylint: disable=missing-function-docstring,no-member

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from server import maui_common
from server import maui_console_tester
from server import maui_station1_service
from server import maui_utils_runner
from server import util
from server.generated import maui_manufacturing_pb2
from server.maui_common import device_log_dir_path
from server.maui_common import extract_container_id_from_bos_file
from server.maui_common import find_offline_fw_file
from server.maui_common import get_device_log_dir
from server.maui_common import LogStatus
from server.maui_common import parse_log_status
from server.maui_common import safe_log_step_name
from server.maui_common import UsbDeviceInfo
from server.maui_common import write_device_log
from server.maui_console_tester import ConsolePortBusyError
from server.maui_console_tester import MauiConsoleTester
from server.maui_console_tester import PdcInfoTestCase
from server.maui_console_tester import port_lock
from server.maui_console_tester import RegexMatchTestCase
from server.maui_console_tester import verify_host_adc_voltages
from server.maui_station1_service import analyze_device_manufacturing_logs
from server.maui_station1_service import derive_card_log_status
from server.maui_station1_service import MauiDeviceManagementServicer
from server.maui_station1_service import MauiPdcServicer
from server.maui_station1_service import MauiStation1Servicer
from server.maui_station1_service import STAGE_LOGS
from server.maui_station2_service import _resolve_console_port
from server.maui_station2_service import is_host_cc_flipped
from server.maui_station2_service import is_sbu_flipped
from server.maui_station2_service import MauiStation2Servicer


SAMPLE_HWREV_OUTPUT = "Hardware revision: 2\r\nmaui$ "
SAMPLE_TEMP_OUTPUT = "Temperature = 43.26 'C\r\nmaui$ "
SAMPLE_VERSION_OUTPUT = "Maui version 0.47-f010696\r\nmaui$ "
SAMPLE_PDC_INFO_OUTPUT = (
    "App Config Version: 00000318\r\nFW Version: 00000019\r\nmaui$ "
)
SAMPLE_ADC_OUTPUT = """ADC values:
[        USB_HOST_CC1] =  885 mV
[        USB_HOST_CC2] =   54 mV
[         TEMP_SENSOR] =  599 mV
[            SBU1_DET] =    0 mV
[            SBU2_DET] =    0 mV
[                 BSL] =    0 mV
[  PPVAR_USB_CHG_VBUS] = 5105 mV
[  PPVAR_USB_DUT_VBUS] =    0 mV
[PP5000_USB_HOST_VBUS] = 2616 mV
[              PP5000] = 2609 mV
maui$ """

SAMPLE_ADC_FLIPPED_OUTPUT = """ADC values:
[        USB_HOST_CC1] =   61 mV
[        USB_HOST_CC2] =  938 mV
[         TEMP_SENSOR] =  621 mV
[            SBU1_DET] =    0 mV
[            SBU2_DET] =    0 mV
[                 BSL] =    0 mV
[  PPVAR_USB_CHG_VBUS] = 5105 mV
[  PPVAR_USB_DUT_VBUS] =    0 mV
[PP5000_USB_HOST_VBUS] = 2616 mV
[              PP5000] = 2609 mV
maui$ """

SAMPLE_GPIO_OUTPUT = """[              h2h_reset_l] O = 0
[        usb2_mux_hub_oe_l] O = 1
[         usb2_mux_hub_sel] O = 0
[      usb3_mux_dut_oe_r_l] O = 1
[       usb3_mux_dut_sel_r] O = 0
[      usb2_mux_dut_oe_r_l] O = 1
[       usb2_mux_dut_sel_r] O = 0
[            pp5000_cutoff] O = 0
[        usb3_mux_hub_oe_l] O = 1
[         usb3_mux_hub_sel] O = 0
[     usb3_mux_host_oe_r_l] O = 1
[      usb3_mux_host_sel_r] O = 1
[              hub_reset_l] O = 0
[    h2h_host_access_led_l] I = 1
[     h2h_dut_access_led_l] I = 0
[         hub_ss_up_status] I = 1
[         hub_hs_up_status] I = 0
[           hub_ss_suspend] I = 1
[    pdc_hi_priority_irq_l] I = 0
[         pp3300_ec_status] I = 1
[h2h_dut_host_access_led_l] I = 0
[                pdc_gpio8] I = 1
[                pdc_gpio9] I = 1
[               pdc_gpio10] I = 1
[               pdc_gpio11] I = 1
[             pp1200_pg_od] I = 1
[             pp3300_pg_od] I = 1
[             pp1100_pg_od] I = 1
[           hub_hs_suspend] I = 1
maui$ """

PDC_DUT_20V = (
    "Plug Present: Yes\r\nOrientation: Normal (CC1)\r\nVoltage: 20.0 V\r\nOp Current: 5.0 A\r\n"
    "Measured VBUS: 20.0 V (20000 mV)\r\nmaui$ "
)
PDC_CHG_20V = "Plug Present: Yes\r\nVoltage: 20.0 V\r\nOp Current: 5.0 A\r\nMeasured VBUS: 20.0 V (20000 mV)\r\nmaui$ "
ADC_SBU1_20V = (
    "[ SBU1_DET] = 3300 mV\r\n[ SBU2_DET] = 0 mV\r\n[ PPVAR_USB_CHG_VBUS] = 20000 mV\r\n"
    "[ PPVAR_USB_DUT_VBUS] = 20000 mV\r\nmaui$ "
)
ADC_SBU2_20V = ADC_SBU1_20V.replace("SBU1_DET] = 3300", "SBU1_DET] = 0").replace(
    "SBU2_DET] = 0", "SBU2_DET] = 3300"
)

HOST_TOPOLOGY = [
    UsbDeviceInfo(sys_path="/sys/bus/usb/devices/1", vid=0x0BDA, pid=0x5411),
    UsbDeviceInfo(sys_path="/sys/bus/usb/devices/2", vid=0x0BDA, pid=0x0411),
    UsbDeviceInfo(sys_path="/sys/bus/usb/devices/3", vid=0x0403, pid=0x6002),
    UsbDeviceInfo(sys_path="/sys/bus/usb/devices/4", vid=0x18D1, pid=0x506D),
]
MAUI_SERIAL = "TEST_MAUI_SERIAL_001"
MAUI_UTILS_IMAGE = "maui-utils:test"
NO_SUCH_IMAGE_OUTPUT = (
    f"docker: Error response from daemon: No such image: {MAUI_UTILS_IMAGE}."
)


def tool_command(cmd):
    """[tool, *args] of a maui-utils `docker run` command line (image dropped)."""
    cmd = list(cmd)
    assert cmd[:2] == ["docker", "run"], cmd
    entrypoint = cmd.index("--entrypoint")
    assert cmd[entrypoint + 2] == MAUI_UTILS_IMAGE, cmd
    return [cmd[entrypoint + 1]] + cmd[entrypoint + 3 :]


def container_name(cmd):
    """Container name of a maui-utils `docker run` command line."""
    return cmd[list(cmd).index("--name") + 1]


class _IsolatedEnvMixin:
    """Points all Maui log/result dirs to a temp dir and hides the host's USB devices."""

    def setUp(self):  # pylint: disable=invalid-name
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name
        self.log_base = os.path.join(self.tmpdir, "devices")
        self.results_dir = os.path.join(self.tmpdir, "results")
        env = mock.patch.dict(
            os.environ,
            {
                maui_common.DEVICE_LOG_DIR_ENV_VAR: self.log_base,
                maui_common.RESULTS_DIR_ENV_VAR: self.results_dir,
                "MAUI_UTILS_IMAGE": MAUI_UTILS_IMAGE,
                maui_utils_runner.SIMULATE_ENV_VAR: "",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        # maui-utils: staging in a temp dir, files are not copied and no real
        # docker command is ever run (subprocess calls are mocked per test).
        for patcher in (
            mock.patch.object(
                maui_utils_runner,
                "STAGING_ROOT",
                os.path.join(self.tmpdir, "maui_utils_staging"),
            ),
            mock.patch.object(
                maui_utils_runner, "get_docker_bin", return_value="docker"
            ),
            mock.patch.object(
                maui_utils_runner.MauiUtilsSession,
                "stage",
                side_effect=lambda path: f"/mnt/firmware_dir/{os.path.basename(path)}",
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        kill = mock.patch.object(maui_utils_runner.MauiUtilsSession, "kill")
        self.mock_kill = kill.start()
        self.addCleanup(kill.stop)
        scan = mock.patch("server.maui_common.scan_usb_devices", return_value=[])
        scan.start()
        self.addCleanup(scan.stop)
        registry = mock.patch.object(
            maui_station1_service,
            "_pre_serial_logs",
            maui_station1_service._PreSerialLogDirs(),
        )
        registry.start()
        self.addCleanup(registry.stop)

    def make_port(self, name: str = "ttyUSB_FAKE") -> str:
        """Creates a real file standing in for a serial device node."""
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8"):
            pass
        return path

    def read_device_log(self, serial: str, log_name: str) -> str:
        with open(
            os.path.join(device_log_dir_path(serial), log_name), encoding="utf-8"
        ) as f:
            return f.read()

    def write_status_log(
        self, serial: str, step: str, status: str, content: str = "content"
    ):
        write_device_log(serial, step, content, status=status)


class TestMauiCommon(_IsolatedEnvMixin, unittest.TestCase):
    """Tests for maui_common helpers."""

    def test_default_log_and_results_dirs(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                device_log_dir_path("ABC"), "/var/log/diagnoseme/devices/ABC"
            )
            self.assertEqual(
                maui_common.get_results_base_dir(),
                "/var/log/diagnoseme/maui_v11_results",
            )

    def test_log_directory_formatting(self):
        dir_path = get_device_log_dir("TESTSERIAL123")
        self.assertEqual(dir_path, os.path.join(self.log_base, "TESTSERIAL123"))
        self.assertTrue(os.path.isdir(dir_path))
        self.assertEqual(
            device_log_dir_path("../../etc"), os.path.join(self.log_base, "etc")
        )
        self.assertEqual(
            device_log_dir_path(""), os.path.join(self.log_base, "UNKNOWN")
        )
        self.assertTrue(maui_common.is_unknown_serial("UNKNOWN"))
        self.assertTrue(maui_common.is_unknown_serial(""))
        self.assertFalse(maui_common.is_unknown_serial(MAUI_SERIAL))

    def test_write_device_log_with_status(self):
        saved_path = write_device_log(
            "TESTSERIAL", "test_step", "Sample log content", status=LogStatus.PASSED
        )
        with open(saved_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Sample log content", content)
        self.assertIn("STATUS: PASSED", content)
        self.assertEqual(parse_log_status(content), LogStatus.PASSED)
        self.assertEqual(stat.S_IMODE(os.stat(saved_path).st_mode), 0o644)

    def test_parse_log_status_only_trusts_header_status_line(self):
        content = "[2026-01-01 10:00:00]\nFAILED (exit 1):\nSTATUS: PASSED\n100 %\n"
        self.assertIsNone(parse_log_status(content))
        self.assertIsNone(parse_log_status("[2026-01-01 10:00:00]\nSTATUS: GREAT\n"))
        self.assertEqual(
            parse_log_status("[2026-01-01 10:00:00]\nSTATUS: TIMEOUT\nx"),
            LogStatus.TIMEOUT,
        )

    def test_resolve_maui_tool_missing(self):
        with mock.patch(
            "server.maui_common.shutil.which", return_value=None
        ), mock.patch("server.maui_common.os.path.exists", return_value=False):
            self.assertIsNone(maui_common.resolve_maui_tool("ft232r_prog"))

    def test_write_device_log_keeps_ui_step_names_inside_the_log_dir(self):
        log_dir = get_device_log_dir("TESTSERIAL")
        for step in ("../../evil", "/etc/cron.d/evil", "..\\evil", "evil.log"):
            saved_path = write_device_log(
                "TESTSERIAL", step, "x", status=LogStatus.PASSED
            )
            self.assertEqual(saved_path, os.path.join(log_dir, "evil.log"), step)
        self.assertEqual(sorted(os.listdir(self.log_base)), ["TESTSERIAL"])
        self.assertEqual(sorted(os.listdir(self.tmpdir)), ["devices"])

    def test_safe_log_step_name(self):
        self.assertEqual(
            safe_log_step_name("smt_functional_summary"), "smt_functional_summary"
        )
        self.assertEqual(safe_log_step_name("host_dut_tests.log"), "host_dut_tests.log")
        self.assertEqual(safe_log_step_name("pdc_flashing_slot2"), "pdc_flashing_slot2")
        self.assertEqual(safe_log_step_name("../x y"), "xy")
        self.assertEqual(safe_log_step_name(".hidden"), "hidden")
        self.assertEqual(safe_log_step_name(".."), "device_log")
        self.assertEqual(safe_log_step_name(""), "device_log")
        self.assertEqual(safe_log_step_name(None), "device_log")

    def test_sorted_tty_nodes_numeric(self):
        with mock.patch(
            "server.maui_common.glob.glob",
            return_value=["/dev/ttyUSB10", "/dev/ttyUSB2", "/dev/ttyUSB1"],
        ):
            self.assertEqual(
                maui_common.sorted_tty_nodes(),
                ["/dev/ttyUSB1", "/dev/ttyUSB2", "/dev/ttyUSB10"],
            )

    def test_extract_container_id_from_bos_file(self):
        # Synthetic 25-byte BOS descriptor: 5-byte header + 20-byte ContainerID capability
        uuid_bytes = bytes.fromhex("0189000205086ab4121a27c2b6c9d000")
        bos_data = bytes([5, 15, 25, 0, 1]) + bytes([20, 16, 4, 0]) + uuid_bytes
        bos_path = os.path.join(self.tmpdir, "bos")
        with open(bos_path, "wb") as f:
            f.write(bos_data)
        self.assertEqual(
            extract_container_id_from_bos_file(bos_path),
            "01890002-0508-6ab4-121a-27c2b6c9d000",
        )

        # Invalid / corrupt BOS data
        with open(bos_path, "wb") as f:
            f.write(b"\x00\x01\x02")
        self.assertIsNone(extract_container_id_from_bos_file(bos_path))

        # Nonexistent file
        self.assertIsNone(extract_container_id_from_bos_file("/nonexistent/path/bos"))

    def test_find_offline_fw_file(self):
        mcu_dir = os.path.join(self.tmpdir, "mcu")
        os.makedirs(mcu_dir)
        fw_file = os.path.join(mcu_dir, "test_zephyr.txt")
        with open(fw_file, "w", encoding="utf-8") as f:
            f.write("test fw")

        with mock.patch(
            "server.maui_common.glob.glob",
            side_effect=lambda p: (
                [fw_file] if "mcu" in p and p.endswith(".txt") else []
            ),
        ), mock.patch("os.path.isdir", return_value=True):
            self.assertEqual(find_offline_fw_file("mcu"), fw_file)

    def _stable_fw_dir(self, md5=None, file_name="zephyr_new.txt", old=True):
        """MCU dir with the STABLE.json image (and an old image sorting last)."""
        mcu_dir = os.path.join(self.tmpdir, "fw", "mcu")
        os.makedirs(mcu_dir, exist_ok=True)
        images = (("zz_old.txt", "old"),) if old else ()
        for name, content in images + (("zephyr_new.txt", "new fw"),):
            with open(os.path.join(mcu_dir, name), "w", encoding="utf-8") as f:
                f.write(content)
        with open(
            os.path.join(mcu_dir, maui_common.STABLE_FW_MARKER), "w", encoding="utf-8"
        ) as f:
            json.dump(
                {
                    "version": "maui_v11_2026_10",
                    "file": file_name,
                    "md5": md5 or hashlib.md5(b"new fw").hexdigest(),
                    "source": "gs://maui-firmware/stable/mcu/zephyr_new.txt",
                },
                f,
            )
        return mcu_dir

    def _images_dir(self, *names):
        """MCU dir with |names| images and no STABLE.json."""
        mcu_dir = os.path.join(self.tmpdir, "fw", "mcu")
        os.makedirs(mcu_dir, exist_ok=True)
        for name in names:
            with open(os.path.join(mcu_dir, name), "w", encoding="utf-8") as f:
                f.write(name)
        return mcu_dir

    def _select_mcu_fw_in(self, mcu_dir):
        with mock.patch.object(
            maui_common, "FIRMWARE_ROOT_DIRS", (os.path.dirname(mcu_dir),)
        ), mock.patch.object(maui_common, "FIRMWARE_FLAT_DIRS", ()):
            return maui_common.select_offline_fw("mcu")

    def _find_mcu_fw_in(self, mcu_dir):
        with mock.patch.object(
            maui_common, "FIRMWARE_ROOT_DIRS", (os.path.dirname(mcu_dir),)
        ), mock.patch.object(maui_common, "FIRMWARE_FLAT_DIRS", ()):
            return find_offline_fw_file("mcu")

    def _assert_fw_error(self, mcu_dir, *texts):
        selection = self._select_mcu_fw_in(mcu_dir)
        self.assertIsNone(selection.path)
        for text in texts + (maui_common.FW_UPDATE_HINT,):
            self.assertIn(text, selection.error)
        with self.assertRaises(maui_common.FirmwareSelectionError), self.assertLogs(
            level="ERROR"
        ):
            self._find_mcu_fw_in(mcu_dir)

    def test_valid_stable_marker_is_used(self):
        mcu_dir = self._stable_fw_dir()
        selection = self._select_mcu_fw_in(mcu_dir)
        self.assertEqual(selection.path, os.path.join(mcu_dir, "zephyr_new.txt"))
        self.assertEqual(selection.version, "maui_v11_2026_10")
        self.assertEqual((selection.warning, selection.error), ("", ""))
        self.assertEqual(
            self._find_mcu_fw_in(mcu_dir), os.path.join(mcu_dir, "zephyr_new.txt")
        )

    def test_stable_marker_md5_mismatch_fails(self):
        # Never falls back to another image (D3: fail closed).
        self._assert_fw_error(
            self._stable_fw_dir(md5="0" * 32), "MD5 mismatch", "zephyr_new.txt"
        )
        # Also when the marker's image is the only one.
        self._assert_fw_error(
            self._stable_fw_dir(md5="0" * 32, old=False), "MD5 mismatch"
        )

    def test_stable_marker_with_missing_file_fails(self):
        self._assert_fw_error(
            self._stable_fw_dir(file_name="missing.txt"), "missing.txt", "not exist"
        )

    def test_stable_marker_with_invalid_name_or_json_fails(self):
        self._assert_fw_error(
            self._stable_fw_dir(file_name="../zz_old.txt"), "invalid file"
        )
        mcu_dir = self._stable_fw_dir()
        with open(
            os.path.join(mcu_dir, maui_common.STABLE_FW_MARKER), "w", encoding="utf-8"
        ) as f:
            f.write("{not json")
        self._assert_fw_error(mcu_dir, "unreadable")

    def test_no_marker_and_one_image_is_used_with_a_warning(self):
        mcu_dir = self._images_dir("zephyr.txt")
        selection = self._select_mcu_fw_in(mcu_dir)
        self.assertEqual(selection.path, os.path.join(mcu_dir, "zephyr.txt"))
        self.assertIn("No STABLE.json", selection.warning)
        self.assertEqual(selection.error, "")
        with self.assertLogs(level="WARNING") as logs:
            self.assertEqual(
                self._find_mcu_fw_in(mcu_dir), os.path.join(mcu_dir, "zephyr.txt")
            )
        self.assertIn("flashing the only mcu image zephyr.txt", "\n".join(logs.output))

    def test_no_marker_and_several_images_fails(self):
        self._assert_fw_error(
            self._images_dir("a.txt", "b.hex"),
            "2 mcu images",
            "a.txt, b.hex",
            "no STABLE.json",
        )

    def test_no_firmware_is_not_an_error(self):
        mcu_dir = self._images_dir()
        self.assertEqual(self._select_mcu_fw_in(mcu_dir), maui_common.FwSelection())
        self.assertIsNone(self._find_mcu_fw_in(mcu_dir))

    def test_first_directory_with_firmware_decides(self):
        empty = os.path.join(self.tmpdir, "empty")
        os.makedirs(os.path.join(empty, "mcu"))
        mcu_dir = self._stable_fw_dir(md5="0" * 32)
        with mock.patch.object(
            maui_common, "FIRMWARE_ROOT_DIRS", (empty, os.path.dirname(mcu_dir))
        ), mock.patch.object(maui_common, "FIRMWARE_FLAT_DIRS", ()):
            self.assertIn("MD5 mismatch", maui_common.select_offline_fw("mcu").error)

    def test_flat_directory_uses_the_preferred_extension(self):
        flat = os.path.join(self.tmpdir, "flat")
        os.makedirs(flat)
        for name in ("zephyr.txt", "pdc.bin"):
            with open(os.path.join(flat, name), "w", encoding="utf-8") as f:
                f.write(name)
        with mock.patch.object(
            maui_common, "FIRMWARE_ROOT_DIRS", (os.path.join(self.tmpdir, "none"),)
        ), mock.patch.object(maui_common, "FIRMWARE_FLAT_DIRS", (flat,)):
            self.assertEqual(
                maui_common.select_offline_fw("mcu").path,
                os.path.join(flat, "zephyr.txt"),
            )
            self.assertEqual(
                maui_common.select_offline_fw("pdc").path, os.path.join(flat, "pdc.bin")
            )

    def test_stable_marker_version_skips_md5_failures(self):
        mcu_dir = self._stable_fw_dir()
        self.assertEqual(maui_common.stable_marker_version(mcu_dir), "maui_v11_2026_10")
        mcu_dir = self._stable_fw_dir(md5="0" * 32)
        with self.assertLogs(level="ERROR"):
            self.assertEqual(maui_common.stable_marker_version(mcu_dir), "")
        mcu_dir = self._stable_fw_dir(file_name="missing.txt")
        with self.assertLogs(level="ERROR"):
            self.assertEqual(maui_common.stable_marker_version(mcu_dir), "")
        self.assertEqual(maui_common.stable_marker_version(self.tmpdir), "")

    def test_maui_adb_vid_pids_single_list(self):
        self.assertIn((0x18D1, 0x5085), maui_common.MAUI_ADB_VID_PIDS)
        for vid, pid in maui_common.MAUI_ADB_VID_PIDS:
            self.assertTrue(maui_common.is_maui_adb_endpoint(vid, pid))
        self.assertFalse(maui_common.is_maui_adb_endpoint(0x18D1, 0x5014))
        self.assertEqual(
            {(maui_common.GOOGLE_VID, p) for p in maui_common.GOOGLE_ADB_PIDS},
            {vp for vp in maui_common.MAUI_ADB_VID_PIDS if vp[0] == 0x18D1},
        )


class TestMauiStation1Servicer(_IsolatedEnvMixin, unittest.IsolatedAsyncioTestCase):
    """Tests for MauiStation1Servicer."""

    def setUp(self):
        super().setUp()
        self.servicer = MauiStation1Servicer()
        self.context = mock.MagicMock()
        self.context.done.return_value = False
        port_info = mock.patch.object(
            MauiStation1Servicer,
            "_find_ftdi_info_for_port",
            return_value=(None, 0x6001, None, None),
        )
        self.mock_port_info = port_info.start()
        self.addCleanup(port_info.stop)

    @mock.patch.object(MauiStation1Servicer, "_scan_ftdi_devices")
    async def test_stream_bsl_auto_discovery_multiple_devices(self, mock_scan):
        mock_scan.return_value = [
            {
                "port": "/dev/ttyUSB0",
                "vid": 0x0403,
                "pid": 0x6001,
                "serial": "DEV0",
                "container_id": "c1",
            },
            {
                "port": "/dev/ttyUSB1",
                "vid": 0x0403,
                "pid": 0x6001,
                "serial": "DEV1",
                "container_id": "c2",
            },
        ]
        # Simulate context.done after 1 iteration
        self.context.done.side_effect = [False, False, True]

        responses = []
        async for resp in self.servicer.stream_bsl_auto_discovery(
            mock.MagicMock(), self.context
        ):
            responses.append(resp)
            if len(responses) >= 2:
                break

        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0].device_port, "/dev/ttyUSB0")
        self.assertEqual(responses[0].pid, 0x6001)
        self.assertTrue(responses[0].auto_flash_eligible)
        self.assertEqual(responses[1].device_port, "/dev/ttyUSB1")
        self.assertTrue(responses[1].auto_flash_eligible)

    def _mcu_request(self, channel="stable"):
        return maui_manufacturing_pb2.MauiAutoProgramMcuRequest(
            device_port="/dev/ttyUSB0", fw_channel=channel, serial_number=MAUI_SERIAL
        )

    def _mcu_tools(self, offline_fw=None):
        """Patches the local MCU firmware lookup."""
        p = mock.patch(
            "server.maui_station1_service.find_offline_fw_file",
            return_value=offline_fw,
        )
        p.start()
        self.addCleanup(p.stop)

    @mock.patch("subprocess.run")
    def test_auto_program_mcu_bsl_success(self, mock_run):
        self._mcu_tools()
        mock_run.return_value = mock.Mock(
            stdout="INFO: MCU firmware update successful.\n", returncode=0
        )
        resp = self.servicer.auto_program_mcu_bsl(self._mcu_request(), self.context)
        self.assertTrue(resp.success)
        self.assertIn("MCU firmware flashed successfully", resp.message)
        # maui-flash runs in the maui-utils container (same argv as the former
        # in-image tool).
        self.assertEqual(
            tool_command(mock_run.call_args[0][0]),
            [
                "maui-flash",
                "--port",
                "/dev/ttyUSB0",
                "mcu",
                "--fw_channel",
                "stable",
                "--bsl-mode",
            ],
        )
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["mcu_bsl_flashing"], "PASSED")

    @mock.patch("time.sleep")
    @mock.patch("subprocess.run")
    def test_auto_program_mcu_bsl_failure_retries_once(self, mock_run, _):
        self._mcu_tools()
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["maui-flash"], output="ERROR: Unable to connect to BSL"
        )
        resp = self.servicer.auto_program_mcu_bsl(self._mcu_request(), self.context)
        self.assertFalse(resp.success)
        self.assertIn("failed", resp.message)
        self.assertEqual(mock_run.call_count, 2)
        # The retry builds a fresh maui-utils command (container name).
        first, second = (c[0][0] for c in mock_run.call_args_list)
        self.assertNotEqual(container_name(first), container_name(second))
        self.assertEqual(tool_command(first), tool_command(second))
        statuses, overall, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["mcu_bsl_flashing"], "FAILED")
        self.assertEqual(overall, "FAIL")

    @mock.patch("time.sleep")
    @mock.patch("subprocess.run")
    def test_auto_program_mcu_bsl_retry_timeout_is_reported(self, mock_run, _):
        self._mcu_tools()
        mock_run.side_effect = [
            subprocess.CalledProcessError(
                returncode=1, cmd=["maui-flash"], output="Unable to connect to BSL"
            ),
            subprocess.TimeoutExpired(cmd=["maui-flash"], timeout=30, output="100 %"),
        ]
        resp = self.servicer.auto_program_mcu_bsl(self._mcu_request(), self.context)
        self.assertFalse(resp.success)
        self.assertIn("timed out", resp.message)
        self.assertIn(
            "STATUS: TIMEOUT", self.read_device_log(MAUI_SERIAL, "mcu_flashing.log")
        )
        statuses, _, infos, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["mcu_bsl_flashing"], "FAILED")
        self.assertEqual(infos[0].details, "Stage timed out")
        # The hung container is removed (killing the docker CLI does not stop it).
        self.mock_kill.assert_called()

    @mock.patch("time.sleep")
    @mock.patch("subprocess.run")
    def test_auto_program_mcu_bsl_retry_success(self, mock_run, _):
        self._mcu_tools(offline_fw="/var/lib/diagnoseme/firmware/mcu/zephyr.txt")
        mock_run.side_effect = [
            subprocess.CalledProcessError(
                returncode=1, cmd=["fw-updater"], output="Unable to connect to BSL"
            ),
            mock.Mock(stdout="INFO: MCU firmware update successful\n"),
        ]
        resp = self.servicer.auto_program_mcu_bsl(
            self._mcu_request("offline"), self.context
        )
        self.assertTrue(resp.success)
        self.assertIn("retry 2", resp.message)
        first, second = (c[0][0] for c in mock_run.call_args_list)
        self.assertNotEqual(container_name(first), container_name(second))
        self.assertEqual(tool_command(first), tool_command(second))
        # Staged once for both attempts.
        maui_utils_runner.MauiUtilsSession.stage.assert_called_once()
        self.assertEqual(
            parse_log_status(self.read_device_log(MAUI_SERIAL, "mcu_flashing.log")),
            LogStatus.PASSED,
        )

    @mock.patch("subprocess.run")
    def test_auto_program_mcu_bsl_offline_file(self, mock_run):
        self._mcu_tools(offline_fw="/var/lib/diagnoseme/firmware/mcu/zephyr.txt")
        mock_run.return_value = mock.Mock(
            stdout="INFO: MCU firmware update successful from zephyr.txt\n"
        )
        resp = self.servicer.auto_program_mcu_bsl(
            self._mcu_request("offline"), self.context
        )
        self.assertTrue(resp.success)
        # fw-updater runs in maui-utils with the staged local file.
        maui_utils_runner.MauiUtilsSession.stage.assert_called_once_with(
            "/var/lib/diagnoseme/firmware/mcu/zephyr.txt"
        )
        self.assertEqual(
            tool_command(mock_run.call_args[0][0]),
            [
                "fw-updater",
                "/mnt/firmware_dir/zephyr.txt",
                "--uart",
                "/dev/ttyUSB0",
                "--bsl-mode",
            ],
        )

    @mock.patch("subprocess.run")
    def test_auto_program_mcu_bsl_prefers_fw_updater_with_offline_file(self, mock_run):
        self._mcu_tools(offline_fw="/fw/mcu/zephyr.txt")
        mock_run.return_value = mock.Mock(stdout="ok")
        self.assertTrue(
            self.servicer.auto_program_mcu_bsl(
                self._mcu_request(), self.context
            ).success
        )
        cmd = mock_run.call_args[0][0]
        self.assertIn("--privileged", cmd)
        self.assertEqual(cmd[cmd.index("--pull") + 1], "never")
        self.assertEqual(
            tool_command(cmd),
            [
                "fw-updater",
                "/mnt/firmware_dir/zephyr.txt",
                "--uart",
                "/dev/ttyUSB0",
                "--bsl-mode",
            ],
        )

    def test_auto_program_mcu_bsl_offline_missing(self):
        self._mcu_tools()
        resp = self.servicer.auto_program_mcu_bsl(
            self._mcu_request("offline"), self.context
        )
        self.assertFalse(resp.success)
        self.assertIn("Offline MCU firmware not found", resp.message)

    @mock.patch("subprocess.run")
    def test_auto_program_mcu_bsl_invalid_station_firmware_fails(self, mock_run):
        # D3: a broken STABLE.json is never replaced by a guess or a download.
        for channel in ("stable", "offline"):
            with mock.patch(
                "server.maui_station1_service.find_offline_fw_file",
                side_effect=maui_common.FirmwareSelectionError(
                    "MD5 mismatch for zephyr.txt. Update the station firmware"
                ),
            ):
                resp = self.servicer.auto_program_mcu_bsl(
                    self._mcu_request(channel), self.context
                )
            self.assertFalse(resp.success)
            self.assertIn("update the station firmware", resp.message)
            self.assertIn("MD5 mismatch", resp.logs)
        mock_run.assert_not_called()
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["mcu_bsl_flashing"], "FAILED")

    @mock.patch("subprocess.run")
    def test_auto_program_mcu_bsl_fails_when_tool_missing(self, mock_run):
        # No docker CLI to start maui-utils: the step fails, nothing is simulated.
        self._mcu_tools(offline_fw="/fw/mcu/zephyr.txt")
        mock_run.side_effect = FileNotFoundError("docker")
        resp = self.servicer.auto_program_mcu_bsl(self._mcu_request(), self.context)
        self.assertFalse(resp.success)
        self.assertEqual(resp.message, "Flashing tool not installed on host system.")
        self.assertEqual(mock_run.call_count, 1)
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["mcu_bsl_flashing"], "FAILED")

    @mock.patch("subprocess.run")
    def test_auto_program_mcu_bsl_fails_when_maui_utils_image_missing(self, mock_run):
        self._mcu_tools(offline_fw="/fw/mcu/zephyr.txt")
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=125,
            cmd=["docker"],
            output=NO_SUCH_IMAGE_OUTPUT,
        )
        resp = self.servicer.auto_program_mcu_bsl(self._mcu_request(), self.context)
        self.assertFalse(resp.success)
        self.assertIn("not loaded on this station", resp.message)
        self.assertEqual(mock_run.call_count, 1)
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["mcu_bsl_flashing"], "FAILED")

    @mock.patch("subprocess.run")
    def test_auto_program_mcu_bsl_fails_when_staging_fails(self, mock_run):
        self._mcu_tools(offline_fw="/fw/mcu/zephyr.txt")
        maui_utils_runner.MauiUtilsSession.stage.side_effect = (
            maui_utils_runner.MauiUtilsError("File to stage does not exist")
        )
        resp = self.servicer.auto_program_mcu_bsl(self._mcu_request(), self.context)
        self.assertFalse(resp.success)
        self.assertIn("File to stage does not exist", resp.message)
        mock_run.assert_not_called()
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["mcu_bsl_flashing"], "FAILED")

    def _ftdi_tool(self, path="/usr/local/bin/ft232r_prog"):
        p = mock.patch(
            "server.maui_station1_service.resolve_maui_tool", return_value=path
        )
        p.start()
        self.addCleanup(p.stop)

    @mock.patch("subprocess.run")
    def test_program_ftdi_eeprom_success(self, mock_run):
        self._ftdi_tool()
        mock_run.return_value = mock.Mock(
            stdout="Programming successful! New PID: 0x6002\n"
        )
        req = maui_manufacturing_pb2.MauiProgramFtdiRequest(
            serial_number=MAUI_SERIAL, ftdi_dev_node="/dev/ttyUSB0"
        )
        resp = self.servicer.program_ftdi_eeprom(req, self.context)
        self.assertTrue(resp.success)
        self.assertIn("0x6002", resp.message)
        self.assertNotIn("--old-serial-number", mock_run.call_args[0][0])
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["ftdi_eeprom_programming"], "PASSED")

    @mock.patch("subprocess.run")
    def test_program_ftdi_eeprom_with_old_serial(self, mock_run):
        self._ftdi_tool()
        mock_run.return_value = mock.Mock(
            stdout="Programming successful! New PID: 0x6002\n"
        )
        req = maui_manufacturing_pb2.MauiProgramFtdiRequest(
            serial_number=MAUI_SERIAL,
            ftdi_dev_node="/dev/ttyUSB0",
            old_serial_number="A50285BI",
        )
        resp = self.servicer.program_ftdi_eeprom(req, self.context)
        self.assertTrue(resp.success)
        self.assertEqual(
            mock_run.call_args[0][0],
            [
                "/usr/local/bin/ft232r_prog",
                "--old-serial-number",
                "A50285BI",
                "--new-pid",
                "0x6002",
                "--new-serial-number",
                MAUI_SERIAL,
            ],
        )

    @mock.patch("subprocess.run")
    def test_program_ftdi_eeprom_refuses_ambiguous_target(self, mock_run):
        self._ftdi_tool()
        two_ftdi = [
            UsbDeviceInfo(sys_path="/sys/bus/usb/devices/1-1", vid=0x0403, pid=0x6001),
            UsbDeviceInfo(
                sys_path="/sys/bus/usb/devices/1-2",
                vid=0x0403,
                pid=0x6002,
                serial="OTHER",
            ),
        ]
        with mock.patch("server.maui_common.scan_usb_devices", return_value=two_ftdi):
            req = maui_manufacturing_pb2.MauiProgramFtdiRequest(
                serial_number=MAUI_SERIAL, ftdi_dev_node="/dev/ttyUSB0"
            )
            resp = self.servicer.program_ftdi_eeprom(req, self.context)
        self.assertFalse(resp.success)
        self.assertIn("Refusing to program FTDI EEPROM", resp.message)
        mock_run.assert_not_called()
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["ftdi_eeprom_programming"], "FAILED")

    @mock.patch("subprocess.run")
    def test_program_ftdi_eeprom_fails_when_tool_missing(self, mock_run):
        self._ftdi_tool(path=None)
        req = maui_manufacturing_pb2.MauiProgramFtdiRequest(
            serial_number=MAUI_SERIAL,
            ftdi_dev_node="/dev/ttyUSB0",
            old_serial_number="A50285BI",
        )
        resp = self.servicer.program_ftdi_eeprom(req, self.context)
        self.assertFalse(resp.success)
        self.assertIn("ft232r_prog", resp.message)
        mock_run.assert_not_called()

    def _program_ftdi(self, serial, port, old_serial):
        self._ftdi_tool()
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="ok")):
            req = maui_manufacturing_pb2.MauiProgramFtdiRequest(
                serial_number=serial, ftdi_dev_node=port, old_serial_number=old_serial
            )
            self.assertTrue(
                self.servicer.program_ftdi_eeprom(req, self.context).success
            )

    def _flash_mcu_without_serial(self, port, ftdi_serial):
        self._mcu_tools()
        self.mock_port_info.return_value = (ftdi_serial, 0x6001, None, None)
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="ok")):
            req = maui_manufacturing_pb2.MauiAutoProgramMcuRequest(
                device_port=port, fw_channel="stable", serial_number=""
            )
            self.assertTrue(
                self.servicer.auto_program_mcu_bsl(req, self.context).success
            )

    def test_ftdi_merges_mcu_log_of_re_enumerated_port(self):
        self._flash_mcu_without_serial("/dev/ttyUSB0", "FTDI_A")
        self.assertIn(
            "STATUS: PASSED", self.read_device_log("PORT_ttyUSB0", "mcu_flashing.log")
        )

        # The console re-enumerates from ttyUSB0 to ttyUSB1 after the MCU boots.
        def port_info(port, **unused_kwargs):
            if port == "/dev/ttyUSB1":
                return ("FTDI_A", 0x6001, None, MAUI_SERIAL)
            return ("OTHER", 0x6001, None, None)

        self.mock_port_info.side_effect = port_info
        with mock.patch("glob.glob", return_value=["/dev/ttyUSB0", "/dev/ttyUSB1"]):
            resp = self.servicer.discover_topology_and_serial(
                maui_manufacturing_pb2.MauiTopologyDiscoveryRequest(
                    device_port="/dev/ttyUSB0", ftdi_serial="FTDI_A"
                ),
                self.context,
            )
        self.assertEqual(resp.ftdi_dev_node, "/dev/ttyUSB1")

        self._program_ftdi(MAUI_SERIAL, "/dev/ttyUSB1", "FTDI_A")
        self.assertEqual(
            sorted(os.listdir(device_log_dir_path(MAUI_SERIAL))),
            ["ftdi_programming.log", "mcu_flashing.log"],
        )
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["mcu_bsl_flashing"], "PASSED")

    def test_ftdi_never_merges_other_boards_or_stale_logs(self):
        # Stale log of an earlier board in the PORT dir (older than this board).
        stale = write_device_log(
            "PORT_ttyUSB0", "pdc_flashing", "stale", status=LogStatus.PASSED
        )
        os.utime(stale, (1, 1))
        self._flash_mcu_without_serial("/dev/ttyUSB0", "FTDI_A")
        # Another board (other slot) on ttyUSB1 with its MCU and PDC temp dirs.
        self._flash_mcu_without_serial("/dev/ttyUSB1", "FTDI_B")
        maui_station1_service._pre_serial_logs.add_dir(
            "MAUI-SLOT2", "/dev/ttyUSB1", ftdi_serial="FTDI_B"
        )
        write_device_log("MAUI-SLOT2", "pdc_flashing", "B", status=LogStatus.PASSED)
        write_device_log("UNKNOWN", "host_only_tests", "?", status=LogStatus.PASSED)

        self._program_ftdi(MAUI_SERIAL, "/dev/ttyUSB0", "FTDI_A")
        self.assertEqual(
            sorted(os.listdir(device_log_dir_path(MAUI_SERIAL))),
            ["ftdi_programming.log", "mcu_flashing.log"],
        )
        # Board A's dirs are forgotten; board B's are still tracked.
        self.assertEqual(
            maui_station1_service._pre_serial_logs.dirs_for("/dev/ttyUSB0", "FTDI_A"),
            {},
        )
        self.assertEqual(
            sorted(maui_station1_service._pre_serial_logs.dirs_for("/dev/ttyUSB1")),
            ["MAUI-SLOT2", "PORT_ttyUSB1"],
        )

    def test_new_board_on_same_port_does_not_inherit_old_dirs(self):
        registry = maui_station1_service._pre_serial_logs
        registry.start_board("PORT_ttyUSB0", "/dev/ttyUSB0", "FTDI_A")
        registry.add_dir("MAUI-SLOT1", "/dev/ttyUSB0")
        registry.start_board("", "/dev/ttyUSB0", "FTDI_C")
        self.assertEqual(registry.dirs_for("/dev/ttyUSB0", "FTDI_C"), {})
        self.assertEqual(
            sorted(registry.dirs_for(ftdi_serial="FTDI_A")),
            ["MAUI-SLOT1", "PORT_ttyUSB0"],
        )

    def test_discover_topology_and_serial_targeted_port(self):
        self.mock_port_info.return_value = ("A50285BI", 0x6001, "cont1", "MAUI_ADB_123")
        req = maui_manufacturing_pb2.MauiTopologyDiscoveryRequest(
            device_port="/dev/ttyUSB0"
        )
        resp = self.servicer.discover_topology_and_serial(req, self.context)
        self.assertEqual(resp.read_serial_number, "MAUI_ADB_123")
        self.assertEqual(resp.ftdi_dev_node, "/dev/ttyUSB0")
        self.assertEqual(resp.ftdi_current_serial, "A50285BI")
        self.assertEqual(resp.ftdi_current_pid, 0x6001)

    @mock.patch("glob.glob", return_value=["/dev/ttyUSB0", "/dev/ttyUSB1"])
    def test_discover_topology_and_serial_ftdi_relocation(self, _):
        def side_effect(port, **unused_kwargs):
            if port == "/dev/ttyUSB0":
                return ("OTHER_SERIAL", 0x6001, "cont0", None)
            if port == "/dev/ttyUSB1":
                return ("A50285BI", 0x6001, "cont1", "MAUI_ADB_123")
            return (None, 0, None, None)

        self.mock_port_info.side_effect = side_effect
        req = maui_manufacturing_pb2.MauiTopologyDiscoveryRequest(
            device_port="/dev/ttyUSB0", ftdi_serial="A50285BI"
        )
        resp = self.servicer.discover_topology_and_serial(req, self.context)
        self.assertTrue(resp.success)
        self.assertEqual(resp.ftdi_dev_node, "/dev/ttyUSB1")
        self.assertEqual(resp.read_serial_number, "MAUI_ADB_123")
        self.assertEqual(resp.ftdi_current_serial, "A50285BI")

    @mock.patch("time.sleep")
    def test_discover_topology_and_serial_settling_without_adb(self, _):
        self.mock_port_info.return_value = ("A50285BI", 0x6001, "cont1", None)
        req = maui_manufacturing_pb2.MauiTopologyDiscoveryRequest(
            device_port="/dev/ttyUSB0", ftdi_serial="A50285BI"
        )
        resp = self.servicer.discover_topology_and_serial(req, self.context)
        self.assertTrue(resp.success)
        self.assertEqual(resp.ftdi_dev_node, "/dev/ttyUSB0")
        self.assertEqual(resp.ftdi_current_serial, "A50285BI")
        self.assertEqual(resp.read_serial_number, "")
        self.assertIn("awaiting ADB sibling enumeration", resp.error_message)

    def test_match_ftdi_to_adb_superspeed_split_bus(self):
        # Simulates real topology from local test setup with 3 connected Maui boards
        adb_candidates = [
            {
                "dir": "/sys/devices/pci/usb4/4-2/4-2.1/4-2.1.3",
                "name": "4-2.1.3",
                "serial": "MAUIV11G26060059",
                "devpath": "2.1.3",
                "container_id": "",
            },
            {
                "dir": "/sys/devices/pci/usb4/4-2/4-2.2/4-2.2.3",
                "name": "4-2.2.3",
                "serial": "MAUIV11G26060068",
                "devpath": "2.2.3",
                "container_id": "",
            },
            {
                "dir": "/sys/devices/pci/usb3/3-6/3-6.4/3-6.4.3",
                "name": "3-6.4.3",
                "serial": "MAUIV11G26060056",
                "devpath": "6.4.3",
                "container_id": "",
            },
        ]
        for ftdi_name, expected in (
            ("3-6.1.2", "MAUIV11G26060059"),
            ("3-6.2.2", "MAUIV11G26060068"),
            ("3-6.4.2", "MAUIV11G26060056"),
        ):
            match = self.servicer._match_ftdi_to_adb(
                ftdi_dir=f"/sys/devices/pci/usb3/3-6/{ftdi_name.rsplit('.', 1)[0]}/{ftdi_name}",
                ftdi_name=ftdi_name,
                ftdi_devpath=ftdi_name.split("-", 1)[1],
                ftdi_container_id="",
                adb_candidates=adb_candidates,
            )
            self.assertEqual(match, expected)

    def test_match_ftdi_to_adb_arbitrary_host_buses_and_chained_hubs(self):
        # Host -> Hub Tier 1 (Port 4) -> Hub Tier 2 (Port 1) -> Maui Board
        adb_candidates = [
            {
                "dir": "/sys/devices/pci/usb2/2-1/2-1.4.1/2-1.4.1.3",
                "name": "2-1.4.1.3",
                "serial": "MAUI_CHAINED_001",
                "devpath": "1.4.1.3",
                "container_id": "",
            },
            {
                "dir": "/sys/devices/pci/usb2/2-1/2-1.4.2/2-1.4.2.3",
                "name": "2-1.4.2.3",
                "serial": "MAUI_CHAINED_002",
                "devpath": "1.4.2.3",
                "container_id": "",
            },
        ]
        match1 = self.servicer._match_ftdi_to_adb(
            ftdi_dir="/sys/devices/pci/usb1/1-3/1-3.4.1/1-3.4.1.2",
            ftdi_name="1-3.4.1.2",
            ftdi_devpath="3.4.1.2",
            ftdi_container_id="",
            adb_candidates=adb_candidates,
        )
        self.assertEqual(match1, "MAUI_CHAINED_001")
        match2 = self.servicer._match_ftdi_to_adb(
            ftdi_dir="/sys/devices/pci/usb1/1-3/1-3.4.2/1-3.4.2.2",
            ftdi_name="1-3.4.2.2",
            ftdi_devpath="3.4.2.2",
            ftdi_container_id="",
            adb_candidates=adb_candidates,
        )
        self.assertEqual(match2, "MAUI_CHAINED_002")

    def test_match_ftdi_to_adb_multiple_separate_external_hubs(self):
        adb_candidates = [
            {
                "dir": "/sys/devices/pci/usb2/2-1/2-1.2/2-1.2.3",
                "name": "2-1.2.3",
                "serial": "MAUI_HUB_A",
                "devpath": "1.2.3",
                "container_id": "",
            },
            {
                "dir": "/sys/devices/pci/usb2/2-2/2-2.2/2-2.2.3",
                "name": "2-2.2.3",
                "serial": "MAUI_HUB_B",
                "devpath": "2.2.3",
                "container_id": "",
            },
        ]
        match_a = self.servicer._match_ftdi_to_adb(
            ftdi_dir="/sys/devices/pci/usb1/1-1/1-1.2/1-1.2.2",
            ftdi_name="1-1.2.2",
            ftdi_devpath="1.2.2",
            ftdi_container_id="",
            adb_candidates=adb_candidates,
        )
        self.assertEqual(match_a, "MAUI_HUB_A")
        match_b = self.servicer._match_ftdi_to_adb(
            ftdi_dir="/sys/devices/pci/usb1/1-2/1-2.2/1-2.2.2",
            ftdi_name="1-2.2.2",
            ftdi_devpath="2.2.2",
            ftdi_container_id="",
            adb_candidates=adb_candidates,
        )
        self.assertEqual(match_b, "MAUI_HUB_B")

    def test_match_ftdi_to_adb_direct_connection(self):
        adb_candidates = [
            {
                "dir": "/sys/devices/pci/usb2/2-1/2-1.3",
                "name": "2-1.3",
                "serial": "MAUI_DIRECT_001",
                "devpath": "1.3",
                "container_id": "",
            },
        ]
        match = self.servicer._match_ftdi_to_adb(
            ftdi_dir="/sys/devices/pci/usb1/1-1/1-1.2",
            ftdi_name="1-1.2",
            ftdi_devpath="1.2",
            ftdi_container_id="",
            adb_candidates=adb_candidates,
        )
        self.assertEqual(match, "MAUI_DIRECT_001")

    def test_match_ftdi_to_adb_multi_controller_container_id_split_buses(self):
        cid_board1 = "01890002-0508-6ab4-121a-27c2b6c9d000"
        cid_board2 = "01890002-4508-6a4d-121a-27c2b6c9d000"
        adb_candidates = [
            {
                "dir": "/sys/devices/pci/usb2/2-3/2-3.3",
                "name": "2-3.3",
                "serial": "MAUIV11G26060066",
                "devpath": "3.3",
                "container_id": "",
                "hub_container_id": cid_board1,
            },
            {
                "dir": "/sys/devices/pci/usb4/4-2/4-2.3",
                "name": "4-2.3",
                "serial": "MAUIV11G26060056",
                "devpath": "2.3",
                "container_id": "",
                "hub_container_id": cid_board2,
            },
        ]
        match1 = self.servicer._match_ftdi_to_adb(
            ftdi_dir="/sys/devices/pci/usb3/3-1/3-1.2",
            ftdi_name="3-1.2",
            ftdi_devpath="1.2",
            ftdi_container_id="",
            ftdi_hub_container_id=cid_board1,
            adb_candidates=adb_candidates,
        )
        self.assertEqual(match1, "MAUIV11G26060066")
        match2 = self.servicer._match_ftdi_to_adb(
            ftdi_dir="/sys/devices/pci/usb3/3-6/3-6.2",
            ftdi_name="3-6.2",
            ftdi_devpath="6.2",
            ftdi_container_id="",
            ftdi_hub_container_id=cid_board2,
            adb_candidates=adb_candidates,
        )
        self.assertEqual(match2, "MAUIV11G26060056")

    def test_match_ftdi_to_adb_no_cross_contamination_when_single_adb_active(self):
        # Board 2's FTDI (/dev/ttyUSB1) must NEVER steal Board 1's serial.
        only_board1_adb = [
            {
                "dir": "/sys/devices/pci/usb2/2-3/2-3.3",
                "name": "2-3.3",
                "serial": "MAUIV11G26060066",
                "devpath": "3.3",
                "container_id": "",
                "hub_container_id": "01890002-0508-6ab4-121a-27c2b6c9d000",
            },
        ]
        match_board2 = self.servicer._match_ftdi_to_adb(
            ftdi_dir="/sys/devices/pci/usb3/3-6/3-6.2",
            ftdi_name="3-6.2",
            ftdi_devpath="6.2",
            ftdi_container_id="",
            ftdi_hub_container_id="01890002-4508-6a4d-121a-27c2b6c9d000",
            adb_candidates=only_board1_adb,
        )
        self.assertIsNone(match_board2)

    def test_match_ftdi_to_adb_direct_pc_differing_root_ports(self):
        cid_board = "01890002-0508-6ab4-121a-27c2b6c9d000"
        for hub_cid in (cid_board, ""):
            adb_candidates = [
                {
                    "dir": "/sys/devices/pci/usb2/2-1/2-1.3",
                    "name": "2-1.3",
                    "serial": "MAUIV11G26060068",
                    "devpath": "1.3",
                    "container_id": "",
                    "hub_container_id": hub_cid,
                },
            ]
            matched = self.servicer._match_ftdi_to_adb(
                ftdi_dir="/sys/devices/pci/usb3/3-3/3-3.2",
                ftdi_name="3-3.2",
                ftdi_devpath="3.2",
                ftdi_container_id="",
                ftdi_hub_container_id=hub_cid,
                adb_candidates=adb_candidates,
            )
            self.assertEqual(matched, "MAUIV11G26060068")

    def test_parse_lsusb_hub_container_ids(self):
        fake_lsusb = """
Bus 002 Device 091: ID 0451:8140 Texas Instruments, Inc. TUSB8041 4-Port Hub
  ContainerID             {01890002-0508-6ab4-121a-27c2b6c9d000}
Bus 003 Device 098: ID 0451:8142 Texas Instruments, Inc. TUSB8041 4-Port Hub
  ContainerID             {01890002-0508-6ab4-121a-27c2b6c9d000}
Bus 003 Device 100: ID 0451:8142 Texas Instruments, Inc. TUSB8041 4-Port Hub
  ContainerID             {01890002-4508-6a4d-121a-27c2b6c9d000}
Bus 004 Device 062: ID 0451:8140 Texas Instruments, Inc. TUSB8041 4-Port Hub
  ContainerID             {01890002-4508-6a4d-121a-27c2b6c9d000}
"""
        sysfs = os.path.join(self.tmpdir, "sys")
        bus_dev = {
            "3-1": ("3", "98"),
            "2-3": ("2", "91"),
            "3-6": ("3", "100"),
            "4-2": ("4", "62"),
        }
        for name, (bus, dev) in bus_dev.items():
            os.makedirs(os.path.join(sysfs, name))
            for attr, val in (("busnum", bus), ("devnum", dev)):
                with open(os.path.join(sysfs, name, attr), "w", encoding="utf-8") as f:
                    f.write(val + "\n")
        dev_dirs = [os.path.join(sysfs, n) for n in bus_dev]
        with mock.patch("subprocess.run") as mock_run, mock.patch(
            "glob.glob", return_value=dev_dirs
        ):
            mock_run.return_value = mock.Mock(
                returncode=0, stdout=fake_lsusb, stderr=""
            )
            cids = self.servicer._get_usb_hub_container_ids()
        self.assertEqual(cids.get("3-1"), "01890002-0508-6ab4-121a-27c2b6c9d000")
        self.assertEqual(cids.get("2-3"), "01890002-0508-6ab4-121a-27c2b6c9d000")
        self.assertEqual(cids.get("3-6"), "01890002-4508-6a4d-121a-27c2b6c9d000")
        self.assertEqual(cids.get("4-2"), "01890002-4508-6a4d-121a-27c2b6c9d000")


class TestV11OneConsoleLock(_IsolatedEnvMixin, unittest.IsolatedAsyncioTestCase):
    """M5 on Maui v1.1: MCU / PDC flashing claim the FTDI console.

    Station 2 cards use port_lock, so they are refused on a console that an
    MCU or PDC flash claimed, and a flash is refused on a console in use.
    """

    def setUp(self):
        super().setUp()
        self.context = mock.MagicMock()
        self.context.done.return_value = False
        port_info = mock.patch.object(
            MauiStation1Servicer,
            "_find_ftdi_info_for_port",
            return_value=(None, 0x6001, None, None),
        )
        port_info.start()
        self.addCleanup(port_info.stop)
        self.addCleanup(self._assert_no_claims_left)

    def _assert_no_claims_left(self):
        # pylint: disable=protected-access
        claims = dict(maui_console_tester._port_claims)
        users = {k: n for k, n in maui_console_tester._port_users.items() if n}
        maui_console_tester._port_claims.clear()
        maui_console_tester._port_users.clear()
        self.assertEqual(claims, {})
        self.assertEqual(users, {})

    async def test_claim_and_port_lock_exclude_each_other(self):
        port = "/dev/ttyUSB7"
        self.assertEqual(maui_console_tester.claim_ports([port], "MCU flash"), "")
        with self.assertRaisesRegex(ConsolePortBusyError, "MCU flash is running"):
            with port_lock(port, timeout=30):
                self.fail("port_lock must not be granted")
        maui_console_tester.release_ports([port])
        with port_lock(port):
            self.assertIn(
                "a console test on",
                maui_console_tester.claim_ports([port], "PDC flash"),
            )

    async def test_pdc_flash_holds_the_ftdi_console(self):
        servicer = MauiPdcServicer()
        async with servicer._hold_slot_console(  # pylint: disable=protected-access
            "/dev/ttyUSB3"
        ):
            # A Station 2 card on that console is refused.
            with self.assertRaisesRegex(
                ConsolePortBusyError, "PDC flashing on /dev/ttyUSB3 is running"
            ):
                with port_lock("/dev/ttyUSB3"):
                    pass
            # So is an MCU flash.
            resp = MauiStation1Servicer().auto_program_mcu_bsl(
                maui_manufacturing_pb2.MauiAutoProgramMcuRequest(
                    device_port="/dev/ttyUSB3", serial_number=MAUI_SERIAL
                ),
                self.context,
            )
            self.assertFalse(resp.success)
            self.assertIn("busy: PDC flashing on /dev/ttyUSB3", resp.message)
        # Nothing to claim without a resolved console port.
        async with servicer._hold_slot_console(""):  # pylint: disable=protected-access
            self.assertEqual(maui_console_tester._port_claims, {})

    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        return_value="/var/lib/diagnoseme/firmware/pdc/v20_0325.bin",
    )
    @mock.patch("server.maui_station1_service.ensure_zephyr_shell_awake")
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_pdc_flash_on_a_busy_console_is_refused(self, mock_exec, *_):
        port = self.make_port("ttyUSB_PDC")
        servicer = MauiPdcServicer()
        request = maui_manufacturing_pb2.MauiProgramSinglePdcRequest(
            slot_index=1,
            serial_number=MAUI_SERIAL,
            device_port=port,
            fw_channel="offline",
        )
        with port_lock(port):
            responses = [
                r async for r in servicer.program_single_pdc(request, self.context)
            ]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].current_block, "Busy")
        self.assertIn("a console test on", responses[0].error_message)
        mock_exec.assert_not_called()

        # A free console is claimed for the whole flash.
        def run(*unused_args, **unused_kwargs):
            self.assertEqual(
                maui_console_tester.port_claim(port), f"PDC flashing on {port}"
            )
            return TestMauiPdcServicer._proc(0, b"Flashing complete.")

        mock_exec.side_effect = run
        responses = [
            r async for r in servicer.program_single_pdc(request, self.context)
        ]
        self.assertEqual(responses[-1].current_block, "Complete")
        self.assertEqual(maui_console_tester.port_claim(port), "")

    @mock.patch("subprocess.run")
    def test_mcu_flash_claims_the_console(self, mock_run):
        def run(*unused_args, **unused_kwargs):
            self.assertEqual(
                maui_console_tester.port_claim("/dev/ttyUSB0"),
                "MCU flashing on /dev/ttyUSB0",
            )
            return mock.Mock(
                stdout="INFO: MCU firmware update successful.\n", returncode=0
            )

        mock_run.side_effect = run
        with mock.patch(
            "server.maui_station1_service.find_offline_fw_file", return_value=None
        ):
            resp = MauiStation1Servicer().auto_program_mcu_bsl(
                maui_manufacturing_pb2.MauiAutoProgramMcuRequest(
                    device_port="/dev/ttyUSB0", serial_number=MAUI_SERIAL
                ),
                self.context,
            )
        self.assertTrue(resp.success, resp.logs)
        mock_run.assert_called()
        self.assertEqual(maui_console_tester.port_claim("/dev/ttyUSB0"), "")


class TestMauiPdcServicer(_IsolatedEnvMixin, unittest.IsolatedAsyncioTestCase):
    """Tests for MauiPdcServicer."""

    def setUp(self):
        super().setUp()
        self.servicer = MauiPdcServicer()
        self.context = mock.MagicMock()
        self.context.done.return_value = False
        port_info = mock.patch.object(
            MauiStation1Servicer,
            "_find_ftdi_info_for_port",
            return_value=(None, 0x6001, None, None),
        )
        self.mock_port_info = port_info.start()
        self.addCleanup(port_info.stop)

    def _request(self, fw_channel="offline"):
        return maui_manufacturing_pb2.MauiProgramSinglePdcRequest(
            slot_index=1,
            serial_number=MAUI_SERIAL,
            device_port="/dev/ttyUSB0",
            fw_channel=fw_channel,
        )

    async def _collect(self, request):
        return [
            resp
            async for resp in self.servicer.program_single_pdc(request, self.context)
        ]

    @staticmethod
    def _proc(returncode, rest_output=b"", lines=()):
        proc = mock.AsyncMock()
        proc.returncode = returncode
        proc.stdout.readline.side_effect = list(lines) + [b""]
        proc.stdout.read.return_value = rest_output
        proc.wait.return_value = returncode
        proc.kill = mock.Mock()
        return proc

    @mock.patch("server.maui_station1_service.find_offline_fw_file", return_value=None)
    async def test_program_single_pdc_offline_missing_fails_cleanly(self, _):
        responses = await self._collect(self._request())
        self.assertEqual(len(responses), 1)
        self.assertTrue(responses[0].is_complete)
        self.assertEqual(responses[0].current_block, "Failed")
        self.assertIn(
            "Offline PDC firmware binary not found", responses[0].status_message
        )

    @mock.patch("asyncio.create_subprocess_exec")
    async def test_program_single_pdc_invalid_station_firmware_fails(self, mock_exec):
        error = maui_common.FirmwareSelectionError("2 pdc images and no STABLE.json")
        for channel in ("offline", "stable"):
            with mock.patch(
                "server.maui_station1_service.find_offline_fw_file", side_effect=error
            ):
                responses = await self._collect(self._request(channel))
            self.assertEqual(len(responses), 1)
            self.assertTrue(responses[0].is_complete)
            self.assertEqual(responses[0].current_block, "Failed")
            self.assertIn("update the station firmware", responses[0].status_message)
            self.assertIn("2 pdc images", responses[0].error_message)
        mock_exec.assert_not_called()
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["pdc_fw_flashing"], "FAILED")

    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        side_effect=maui_common.FirmwareSelectionError("broken"),
    )
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_program_single_pdc_download_channel_ignores_station_image(
        self, mock_exec, _
    ):
        # The --fw_channel download does not use the (broken) station image.
        mock_exec.return_value = self._proc(0, b"Flashing complete.")
        responses = await self._collect(self._request("dev"))
        self.assertEqual(responses[-1].current_block, "Complete")
        self.assertEqual(
            tool_command(mock_exec.call_args[0])[-3:], ["pdc", "--fw_channel", "dev"]
        )

    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        return_value="/var/lib/diagnoseme/firmware/pdc/v20_0325.bin",
    )
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_program_single_pdc_offline_file_success(self, mock_exec, *_):
        mock_exec.return_value = self._proc(0, b"Flashing complete.")
        responses = await self._collect(self._request())
        final_resp = responses[-1]
        self.assertTrue(final_resp.is_complete)
        self.assertEqual(final_resp.current_block, "Complete")
        self.assertIn("succeeded", final_resp.status_message)
        # maui-flash runs in maui-utils with --file <staged file>.
        self.assertEqual(
            tool_command(mock_exec.call_args[0]),
            [
                "maui-flash",
                "--serial",
                MAUI_SERIAL,
                "pdc",
                "--file",
                "/mnt/firmware_dir/v20_0325.bin",
            ],
        )
        maui_utils_runner.MauiUtilsSession.stage.assert_called_once_with(
            "/var/lib/diagnoseme/firmware/pdc/v20_0325.bin"
        )
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["pdc_fw_flashing"], "PASSED")

    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        return_value="/var/lib/diagnoseme/firmware/pdc/v20_0325.bin",
    )
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_program_single_pdc_stable_channel_uses_the_local_file(
        self, mock_exec, mock_find
    ):
        # The UI sends 'stable'; '' means the same (the station's stable image).
        for channel in ("stable", ""):
            with self.subTest(channel=channel):
                mock_exec.reset_mock()
                mock_exec.return_value = self._proc(0, b"done")
                responses = await self._collect(self._request(channel))
                self.assertEqual(responses[-1].current_block, "Complete")
                args = tool_command(mock_exec.call_args[0])
                self.assertIn("--file", args)
                self.assertNotIn("--fw_channel", args)
        mock_find.assert_called_with("pdc")

    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        return_value="/fw/pdc/v20.bin",
    )
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_program_single_pdc_fails_when_tool_missing(self, mock_exec, *_):
        # No docker CLI to start maui-utils: the step fails, nothing is simulated.
        mock_exec.side_effect = FileNotFoundError("docker")
        responses = await self._collect(self._request())
        self.assertEqual(len(responses), 1)
        self.assertTrue(responses[0].is_complete)
        self.assertEqual(responses[0].progress_percent, 0)
        self.assertEqual(responses[0].current_block, "Error")
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["pdc_fw_flashing"], "FAILED")

    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        return_value="/fw/pdc/v20.bin",
    )
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_program_single_pdc_fails_when_maui_utils_image_missing(
        self, mock_exec, *_
    ):
        mock_exec.return_value = self._proc(
            125,
            NO_SUCH_IMAGE_OUTPUT.encode(),
        )
        responses = await self._collect(self._request())
        self.assertEqual(mock_exec.call_count, 1)
        self.assertEqual(responses[-1].current_block, "Failed")
        self.assertIn("not loaded on this station", responses[-1].error_message)
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["pdc_fw_flashing"], "FAILED")

    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        return_value="/fw/pdc/v20.bin",
    )
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_program_single_pdc_fails_when_staging_fails(self, mock_exec, *_):
        maui_utils_runner.MauiUtilsSession.stage.side_effect = OSError("read-only")
        responses = await self._collect(self._request())
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].current_block, "Failed")
        self.assertIn("read-only", responses[0].error_message)
        mock_exec.assert_not_called()
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["pdc_fw_flashing"], "FAILED")

    @mock.patch("asyncio.sleep")
    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        return_value="/fw/pdc/v20.bin",
    )
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_program_single_pdc_simulation_needs_explicit_opt_in(
        self, mock_exec, *_
    ):
        with mock.patch.dict(os.environ, {maui_utils_runner.SIMULATE_ENV_VAR: "1"}):
            responses = await self._collect(self._request())
        mock_exec.assert_not_called()
        self.assertEqual(responses[-1].progress_percent, 100)
        self.assertIn("[DEV MODE]", responses[-1].status_message)
        # A simulated flash never counts as a passed stage.
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["pdc_fw_flashing"], "NOT_RUN")

    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        return_value="/fw/pdc/v20.bin",
    )
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_program_single_pdc_retries_after_prompt_timeout(self, mock_exec, *_):
        mock_exec.side_effect = [
            self._proc(1, b"WARNING: Timeout while waiting for prompt"),
            self._proc(0, b"done"),
        ]
        responses = await self._collect(self._request())
        self.assertEqual(mock_exec.call_count, 2)
        self.assertEqual(responses[-1].current_block, "Complete")
        # The retry builds a fresh maui-utils command (container name); the log
        # names the command that succeeded.
        first, second = (c[0] for c in mock_exec.call_args_list)
        self.assertNotEqual(container_name(first), container_name(second))
        self.assertEqual(tool_command(first), tool_command(second))
        log = self.read_device_log(MAUI_SERIAL, "pdc_flashing.log")
        self.assertIn(container_name(second), log)
        self.assertEqual(parse_log_status(log), LogStatus.PASSED)

    @mock.patch("asyncio.wait_for")
    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        return_value="/fw/pdc/v20.bin",
    )
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_program_single_pdc_kills_the_hung_container(
        self, mock_exec, _, mock_wait_for
    ):
        async def wait_for(awaitable, timeout):  # pylint: disable=unused-argument
            awaitable.close()
            raise TimeoutError()

        mock_wait_for.side_effect = wait_for
        mock_exec.return_value = self._proc(None)
        await self._collect(self._request())
        self.mock_kill.assert_called()

    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        return_value="/fw/pdc/v20.bin",
    )
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_pdc_log_under_slot_dir_is_merged_into_serial(self, mock_exec, *_):
        # Slot 2 board: ADB serial not resolvable yet, so the log goes to MAUI-SLOT2.
        self.mock_port_info.return_value = ("FTDI_B", 0x6001, None, None)
        mock_exec.return_value = self._proc(0, b"done")
        responses = await self._collect(
            maui_manufacturing_pb2.MauiProgramSinglePdcRequest(
                slot_index=2,
                serial_number="MAUI-SLOT2",
                device_port="/dev/ttyUSB3",
                fw_channel="offline",
            )
        )
        self.assertEqual(responses[-1].current_block, "Complete")
        self.assertIn(
            "STATUS: PASSED", self.read_device_log("MAUI-SLOT2", "pdc_flashing.log")
        )
        # Slot 1 board has its own slot dir, which must not follow.
        write_device_log(
            "MAUI-SLOT1", "pdc_flashing", "slot 1", status=LogStatus.FAILED
        )
        maui_station1_service._pre_serial_logs.add_dir("MAUI-SLOT1", "/dev/ttyUSB0")

        station1 = MauiStation1Servicer()
        with mock.patch(
            "server.maui_station1_service.resolve_maui_tool",
            return_value="/usr/local/bin/ft232r_prog",
        ), mock.patch("subprocess.run", return_value=mock.Mock(stdout="ok")):
            resp = station1.program_ftdi_eeprom(
                maui_manufacturing_pb2.MauiProgramFtdiRequest(
                    serial_number=MAUI_SERIAL,
                    ftdi_dev_node="/dev/ttyUSB3",
                    old_serial_number="FTDI_B",
                ),
                self.context,
            )
        self.assertTrue(resp.success)
        statuses, overall, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["pdc_fw_flashing"], "PASSED")
        self.assertEqual(overall, "PASS")
        self.assertNotIn(
            "slot 1", self.read_device_log(MAUI_SERIAL, "pdc_flashing.log")
        )

    @mock.patch(
        "server.maui_station1_service.find_offline_fw_file",
        return_value="/fw/pdc/v20.bin",
    )
    @mock.patch("asyncio.create_subprocess_exec")
    async def test_pdc_with_known_serial_merges_mcu_port_log(self, mock_exec, *_):
        maui_station1_service._pre_serial_logs.start_board(
            "PORT_ttyUSB0", "/dev/ttyUSB0", "FTDI_A"
        )
        write_device_log("PORT_ttyUSB0", "mcu_flashing", "mcu", status=LogStatus.PASSED)
        maui_station1_service._pre_serial_logs.relocate("FTDI_A", "/dev/ttyUSB2")
        mock_exec.return_value = self._proc(0, b"done")
        request = self._request()
        request.device_port = "/dev/ttyUSB2"
        await self._collect(request)
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["mcu_bsl_flashing"], "PASSED")
        self.assertEqual(statuses["pdc_fw_flashing"], "PASSED")

    def test_progress_response(self):
        resp = MauiPdcServicer._progress_response(
            "Block 5: Progress: 500/1000 bytes transferred (50.0%)",
            "Flashing PDC Firmware",
            2,
            "SER",
        )
        self.assertEqual(resp.progress_percent, 50)
        self.assertEqual(resp.current_block, "Block 5: 500/1000 bytes")
        self.assertEqual(resp.status_message, "Flashing PDC Firmware... 50%")
        self.assertIsNone(MauiPdcServicer._progress_response("noise", "x", 1, "SER"))


class TestMauiDeviceManagementServicer(_IsolatedEnvMixin, unittest.TestCase):
    """Tests for MauiDeviceManagementServicer and log analysis."""

    def setUp(self):
        super().setUp()
        self.servicer = MauiDeviceManagementServicer()
        self.context = mock.MagicMock()

    def test_verify_scanned_label_match(self):
        req = maui_manufacturing_pb2.MauiVerifyLabelRequest(
            electronic_serial=MAUI_SERIAL,
            scanned_barcode=MAUI_SERIAL,
            adb_serial=MAUI_SERIAL,
            ftdi_serial=MAUI_SERIAL,
        )
        resp = self.servicer.verify_scanned_label(req, self.context)
        self.assertTrue(resp.matches)
        self.assertTrue(resp.adb_matches)
        self.assertTrue(resp.ftdi_matches)
        self.assertTrue(resp.dual_endpoints_match)
        self.assertIn("Verification PASSED", resp.message)
        statuses, _, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["label_verified"], "PASSED")

    def test_verify_scanned_label_endpoint_mismatch(self):
        req = maui_manufacturing_pb2.MauiVerifyLabelRequest(
            electronic_serial=MAUI_SERIAL,
            scanned_barcode=MAUI_SERIAL,
            adb_serial=MAUI_SERIAL,
            ftdi_serial="DIFFERENT_FTDI_SERIAL",
        )
        resp = self.servicer.verify_scanned_label(req, self.context)
        self.assertFalse(resp.matches)
        self.assertFalse(resp.dual_endpoints_match)
        self.assertIn("Hardware Endpoint Mismatch", resp.message)

    def test_verify_scanned_label_mismatch(self):
        req = maui_manufacturing_pb2.MauiVerifyLabelRequest(
            electronic_serial=MAUI_SERIAL,
            scanned_barcode="WRONGSERIAL999",
            adb_serial=MAUI_SERIAL,
            ftdi_serial=MAUI_SERIAL,
        )
        resp = self.servicer.verify_scanned_label(req, self.context)
        self.assertFalse(resp.matches)
        self.assertIn("Sticker Mismatch", resp.message)
        statuses, overall, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["label_verified"], "FAILED")
        self.assertEqual(overall, "FAIL")

    def _submit(self, station_id, **kwargs):
        req = maui_manufacturing_pb2.MauiFinalSubmissionRequest(
            serial_number=MAUI_SERIAL,
            operator_id="OP_TEST",
            station_id=station_id,
            adb_serial=MAUI_SERIAL,
            ftdi_serial=MAUI_SERIAL,
            **kwargs,
        )
        return self.servicer.submit_final_manufacturing_results(req, self.context)

    def test_submit_final_manufacturing_results(self):
        for _, log_name, _ in STAGE_LOGS:
            self.write_status_log(
                MAUI_SERIAL, log_name, LogStatus.PASSED, "ALL_PASSED=True"
            )

        resp_smt = self._submit("STATION_SMT_FUNCTIONAL", label_verified=False)
        self.assertTrue(resp_smt.success)
        self.assertIn("SMT_FUNCTIONAL", resp_smt.mes_tracking_id)
        self.assertIn("_PASS", resp_smt.mes_tracking_id)
        with open(resp_smt.summary_file_path, "r", encoding="utf-8") as f:
            data_smt = json.load(f)
        self.assertEqual(data_smt["phase"], "smt_functional")
        self.assertEqual(data_smt["stage_statuses"]["label_verified"], "NOT_RUN")

        resp_fatp = self._submit(
            "STATION_FATP_QT0", label_verified=True, scanned_barcode=MAUI_SERIAL
        )
        self.assertTrue(resp_fatp.success)
        self.assertIn("FATP_TESTS", resp_fatp.mes_tracking_id)
        with open(resp_fatp.summary_file_path, "r", encoding="utf-8") as f:
            data_fatp = json.load(f)
        self.assertEqual(data_fatp["phase"], "fatp_tests")
        self.assertEqual(data_fatp["overall_status"], "PASS")
        self.assertTrue(data_fatp["endpoints"]["serials_matched"])

        # Results are exported to the single results dir only.
        tag = resp_fatp.mes_tracking_id[len("MES-") :]
        result_json = os.path.join(
            self.results_dir, "fatp_tests", "results", f"{tag}.json"
        )
        result_log = os.path.join(self.results_dir, "fatp_tests", "logs", f"{tag}.log")
        self.assertTrue(os.path.isfile(result_json))
        self.assertTrue(os.path.isfile(result_log))
        self.assertEqual(stat.S_IMODE(os.stat(result_json).st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(os.stat(self.results_dir).st_mode) & 0o022, 0)
        self.assertIn(self.results_dir, resp_fatp.message)
        self.assertEqual(sorted(os.listdir(self.tmpdir)), ["devices", "results"])

    def test_submit_final_merges_ui_stage_statuses(self):
        for _, log_name, _ in STAGE_LOGS:
            self.write_status_log(
                MAUI_SERIAL, log_name, LogStatus.PASSED, "ALL_PASSED=True"
            )
        first_stage = STAGE_LOGS[0][0]

        resp = self._submit(
            "STATION_SMT_FUNCTIONAL",
            stage_statuses={first_stage: "BYPASSED", "ui_only_card": "PASSED"},
        )
        with open(resp.summary_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["stage_statuses"][first_stage], "BYPASSED")
        self.assertEqual(data["stage_statuses"]["ui_only_card"], "PASSED")
        self.assertEqual(data["overall_status"], "PASS_WITH_BYPASS")

        resp = self._submit(
            "STATION_SMT_FUNCTIONAL", stage_statuses={"ui_only_card": "failed"}
        )
        with open(resp.summary_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["stage_statuses"]["ui_only_card"], "FAILED")
        self.assertEqual(data["overall_status"], "FAIL")

    def test_merge_ui_stage_statuses_keeps_log_failures(self):
        merged = maui_station1_service._merge_ui_stage_statuses(
            {"a": "FAILED", "b": "PASSED"},
            {"a": "BYPASSED", "b": "bogus", "c": "NOT_RUN"},
        )
        self.assertEqual(merged, {"a": "FAILED", "b": "PASSED", "c": "NOT_RUN"})

    def test_merge_ui_stage_statuses_rules(self):
        cases = (
            # (log verdict, UI value, merged)
            (None, "PASSED", "PASSED"),
            ("NOT_RUN", "PASSED", "PASSED"),
            ("NOT_RUN", "NOT_RUN", "NOT_RUN"),
            ("PASSED", "NOT_RUN", "PASSED"),
            ("FAILED", "PASSED", "FAILED"),
            ("PASSED", "FAILED", "FAILED"),
            ("NOT_RUN", "FAILED", "FAILED"),
            ("PASSED", "BYPASSED", "BYPASSED"),
            ("NOT_RUN", "BYPASSED", "BYPASSED"),
            (None, "BYPASSED", "BYPASSED"),
            ("FAILED", "BYPASSED", "FAILED"),
            ("BYPASSED", "PASSED", "BYPASSED"),
            ("PASSED", "timeout", "FAILED"),
            (None, "TIMEOUT", "FAILED"),
        )
        for log_value, ui_value, expected in cases:
            with self.subTest(log=log_value, ui=ui_value):
                log_statuses = {} if log_value is None else {"step": log_value}
                merged = maui_station1_service._merge_ui_stage_statuses(
                    log_statuses, {"step": ui_value}
                )
                self.assertEqual(merged["step"], expected)
                self.assertEqual(
                    maui_station1_service._overall_verdict(merged) == "FAIL",
                    expected == "FAILED",
                )

    def test_submit_final_ui_passed_fills_step1_not_run(self):
        self.write_status_log(MAUI_SERIAL, "ftdi_programming", LogStatus.PASSED)
        resp = self._submit(
            "STATION_SMT_STEP1",
            stage_statuses={
                "mcu_bsl_flashing": "PASSED",
                "ftdi_eeprom_programming": "PASSED",
                "pdc_fw_flashing": "PASSED",
            },
        )
        with open(resp.summary_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["stage_statuses"]["mcu_bsl_flashing"], "PASSED")
        self.assertEqual(data["stage_statuses"]["pdc_fw_flashing"], "PASSED")
        self.assertEqual(data["overall_status"], "PASS")

    def test_submit_final_reports_local_write_failure(self):
        os.makedirs(self.log_base)
        with open(device_log_dir_path(MAUI_SERIAL), "w", encoding="utf-8") as f:
            f.write("not a directory")
        resp = self._submit("STATION_SMT_STEP1")
        self.assertFalse(resp.success)
        self.assertIn("device log directory", resp.message)
        self.assertEqual(resp.summary_file_path, "")

    def test_submit_final_manufacturing_results_reports_export_failure(self):
        with open(self.results_dir, "w", encoding="utf-8") as f:
            f.write("not a directory")
        resp = self._submit("STATION_SMT_STEP1")
        self.assertFalse(resp.success)
        self.assertIn("export", resp.message)
        self.assertTrue(os.path.isfile(resp.summary_file_path))

    def test_analyze_device_manufacturing_logs_failed_bsl(self):
        self.write_status_log(
            MAUI_SERIAL,
            "host_only_tests",
            LogStatus.PASSED,
            "ALL_PASSED=True\nUSB: Hub2=True",
        )
        self.write_status_log(
            MAUI_SERIAL,
            "bsl_button_test",
            LogStatus.FAILED,
            "ALL_PASSED=False\nPeak ADC: 0 mV",
        )
        statuses, overall, infos, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["host_only_verification"], "FAILED")
        self.assertEqual(overall, "FAIL")
        host_info = [i for i in infos if i.log_name == "host_only_tests.log"][0]
        self.assertIn("bsl_button_test.log", host_info.details)

    def test_analyze_parses_only_status_line(self):
        log_dir = get_device_log_dir(MAUI_SERIAL)
        # Legacy MCU log without STATUS line that looks successful -> FAILED (unknown).
        with open(
            os.path.join(log_dir, "mcu_flashing.log"), "w", encoding="utf-8"
        ) as f:
            f.write(
                "[2026-08-24 19:50:35]\nTIMEOUT (30s):\n100 %\nINFO: MCU firmware update successful.\n"
            )
        self.write_status_log(
            MAUI_SERIAL, "pdc_flashing", LogStatus.PASSED, "tool said: failed exception"
        )
        self.write_status_log(MAUI_SERIAL, "host_only_tests", LogStatus.BYPASSED)
        statuses, overall, infos, count = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["mcu_bsl_flashing"], "FAILED")
        self.assertIn("STATUS", infos[0].details)
        self.assertEqual(statuses["ftdi_eeprom_programming"], "NOT_RUN")
        self.assertEqual(statuses["pdc_fw_flashing"], "PASSED")
        self.assertEqual(statuses["host_only_verification"], "BYPASSED")
        self.assertEqual(statuses["label_verified"], "NOT_RUN")
        self.assertEqual(overall, "FAIL")
        self.assertEqual(count, 3)

    def test_analyze_adb_transfer_stage(self):
        self.assertIn(
            ("adb_transfer_test", "adb_transfer_test.log", "ADB Transfer Reliability"),
            STAGE_LOGS,
        )
        statuses, overall, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["adb_transfer_test"], "NOT_RUN")
        self.assertEqual(overall, "PASS")
        self.write_status_log(MAUI_SERIAL, "adb_transfer_test", LogStatus.FAILED)
        statuses, overall, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["adb_transfer_test"], "FAILED")
        self.assertEqual(overall, "FAIL")
        # A UI bypass never overrides the failed log.
        resp = self._submit(
            "STATION_SMT_FUNCTIONAL", stage_statuses={"adb_transfer_test": "BYPASSED"}
        )
        with open(resp.summary_file_path, "r", encoding="utf-8") as f:
            self.assertEqual(
                json.load(f)["stage_statuses"]["adb_transfer_test"], "FAILED"
            )

    def test_stage_logs_revision_hook(self):
        class OneStageServicer(MauiDeviceManagementServicer):
            """A revision that only evaluates the PDC log."""

            STAGE_LOGS = (("pdc_fw_flashing", "pdc_flashing.log", "PDC FW Flashing"),)
            CONSOLE_LABEL = "CONSOLE"

        self.write_status_log(MAUI_SERIAL, "mcu_flashing", LogStatus.FAILED)
        self.write_status_log(MAUI_SERIAL, "pdc_flashing", LogStatus.PASSED)
        servicer = OneStageServicer()
        resp = servicer.get_device_logs_status(
            maui_manufacturing_pb2.MauiLogsStatusRequest(serial_number=MAUI_SERIAL),
            self.context,
        )
        self.assertEqual([info.log_name for info in resp.logs], ["pdc_flashing.log"])
        self.assertNotIn("mcu_bsl_flashing", resp.stage_statuses)
        self.assertEqual(resp.overall_verdict, "PASS")
        resp = servicer.verify_scanned_label(
            maui_manufacturing_pb2.MauiVerifyLabelRequest(
                electronic_serial=MAUI_SERIAL,
                scanned_barcode=MAUI_SERIAL,
                adb_serial=MAUI_SERIAL,
                ftdi_serial=MAUI_SERIAL,
            ),
            self.context,
        )
        self.assertIn("CONSOLE Endpoint", resp.message)

    def test_analyze_label_bypassed_is_not_passed(self):
        self.write_status_log(
            MAUI_SERIAL,
            "label_verification",
            LogStatus.BYPASSED,
            "Label check bypassed",
        )
        statuses, overall, _, _ = analyze_device_manufacturing_logs(MAUI_SERIAL)
        self.assertEqual(statuses["label_verified"], "BYPASSED")
        self.assertEqual(overall, "PASS_WITH_BYPASS")

    def test_analyze_and_logs_status_are_read_only(self):
        write_device_log(
            "UNKNOWN", "mcu_flashing", "other board", status=LogStatus.PASSED
        )
        write_device_log(
            "PORT_ttyUSB0", "mcu_flashing", "other board", status=LogStatus.PASSED
        )
        before = sorted(os.listdir(self.log_base))
        resp = self.servicer.get_device_logs_status(
            maui_manufacturing_pb2.MauiLogsStatusRequest(serial_number="NEWSERIAL"),
            self.context,
        )
        self.assertFalse(resp.directory_exists)
        self.assertEqual(resp.stage_statuses["mcu_bsl_flashing"], "NOT_RUN")
        self.assertEqual(sorted(os.listdir(self.log_base)), before)

    def test_save_card_log_adds_status_line(self):
        cases = (
            (
                "=== Maui SMT Step 1 Provisioning Record ===\nStatus: PROVISIONED\n",
                LogStatus.PASSED,
            ),
            ("Slot Index: 2\nStatus: IDLE\n", LogStatus.NOT_RUN),
            ("Slot Index: 2\nStatus: PDC_FLASHING\nError: x\n", LogStatus.FAILED),
            ("Card 1: PASS\nOVERALL RESULT: FAIL\n", LogStatus.FAILED),
            ("Card 1: PASS\nOVERALL RESULT: PASS\n", LogStatus.PASSED),
            ("STATUS: BYPASSED\nOperator bypass\n", LogStatus.BYPASSED),
            ("free text without verdict", LogStatus.FAILED),
        )
        for content, expected in cases:
            with self.subTest(content=content):
                self.assertEqual(derive_card_log_status(content), expected)
                resp = self.servicer.save_card_log(
                    maui_manufacturing_pb2.MauiSaveLogRequest(
                        serial_number=MAUI_SERIAL,
                        log_step_name="step1_provisioning",
                        log_content=content,
                    ),
                    self.context,
                )
                self.assertTrue(resp.success)
                with open(resp.saved_file_path, encoding="utf-8") as f:
                    saved = f.read()
                self.assertEqual(parse_log_status(saved), expected)
                self.assertIn(content.strip(), saved)


class TestMauiConsoleTester(unittest.TestCase):
    """Tests for MauiConsoleTester framework."""

    def test_regex_match_test_case(self):
        tc = RegexMatchTestCase(
            name="MCU Version Check",
            command="version",
            pattern=r"Maui version\s+[\w.-]+",
            description="Version test",
        )
        passed, _ = tc.evaluate("maui$ version\r\nMaui version 0.47-f010696\r\n")
        self.assertTrue(passed)
        failed, _ = tc.evaluate("maui$ version\r\nUnknown command\r\n")
        self.assertFalse(failed)

    def test_pdc_info_test_case(self):
        tc = PdcInfoTestCase(name="PDC Info Check")
        sample_output = (
            "Using TPS6699x device: tps6699x@20\n"
            "Mode: APP1\n"
            "Version: e0131000\n"
            "App Config Version: 00000318\n"
            "FW Version: 00000019\n"
        )
        passed, msg = tc.evaluate(sample_output)
        self.assertTrue(passed)
        self.assertIn("00000318", msg)
        self.assertEqual(
            PdcInfoTestCase.parse_versions(sample_output), ("00000318", "00000019")
        )

    def test_pdc_info_test_case_fails_when_unparseable(self):
        tc = PdcInfoTestCase(name="PDC Info Check")
        self.assertFalse(tc.evaluate("")[0])
        self.assertFalse(tc.evaluate("App Config Version: 00000318\n")[0])
        self.assertEqual(PdcInfoTestCase.parse_versions(""), ("UNKNOWN", "UNKNOWN"))
        mismatch = PdcInfoTestCase(name="x", expected_fw_ver="00000020")
        passed, msg = mismatch.evaluate(
            "App Config Version: 00000318\nFW Version: 00000019\n"
        )
        self.assertFalse(passed)
        self.assertIn("mismatch", msg)

    def test_run_all_tests_uses_given_session(self):
        tester = MauiConsoleTester("/dev/ttyUSB0")
        with mock.patch.object(
            MauiConsoleTester,
            "send_command",
            side_effect=[SAMPLE_VERSION_OUTPUT, SAMPLE_PDC_INFO_OUTPUT],
        ):
            results = tester.run_all_tests(ser=mock.MagicMock())
        self.assertTrue(results["MCU Version Check"].success)
        self.assertTrue(results["PDC Info Check"].success)
        self.assertEqual(results["PDC Info Check"].output, SAMPLE_PDC_INFO_OUTPUT)

    def test_ensure_dut_data_and_power_is_truthful(self):
        tester = MauiConsoleTester("/dev/ttyUSB0")
        with mock.patch.object(MauiConsoleTester, "send_command", return_value=""):
            self.assertFalse(tester.ensure_dut_data_and_power(ser=mock.MagicMock())[0])
        with mock.patch.object(
            MauiConsoleTester, "send_command", return_value="ok\r\nmaui$ "
        ):
            self.assertTrue(tester.ensure_dut_data_and_power(ser=mock.MagicMock())[0])

    def test_port_lock_busy_raises(self):
        self.assertEqual(maui_console_tester.PORT_LOCK_TIMEOUT_SEC, 10.0)
        held = threading.Event()
        release = threading.Event()

        def holder():
            with port_lock("/dev/ttyUSB_BUSY"):
                held.set()
                release.wait(5)

        thread = threading.Thread(target=holder)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(release.set)
        held.wait(5)
        with self.assertRaisesRegex(ConsolePortBusyError, "busy"):
            with port_lock("/dev/ttyUSB_BUSY", timeout=0.05):
                self.fail("Entered a busy port")

    def test_port_lock_is_reentrant(self):
        with port_lock("/dev/ttyUSB_TEST"):
            with port_lock("/dev/ttyUSB_TEST", timeout=0.1):
                pass

    @mock.patch("server.maui_console_tester.open_serial_port")
    def test_read_adc_sbu_orientation(self, _):
        tester = MauiConsoleTester("/dev/ttyUSB0")
        sample_adc_sbu1 = (
            "[            SBU1_DET] = 3299 mV\n[            SBU2_DET] =    0 mV\nmaui$ "
        )
        sample_adc_sbu2 = (
            "[            SBU1_DET] =    0 mV\n[            SBU2_DET] = 3299 mV\nmaui$ "
        )
        with mock.patch.object(
            MauiConsoleTester, "send_command", return_value=sample_adc_sbu1
        ):
            ori_str, sbu1, sbu2, _ = tester.read_adc_sbu_orientation()
            self.assertTrue(ori_str.startswith("SBU1"))
            self.assertEqual((sbu1, sbu2), (3299, 0))
        with mock.patch.object(
            MauiConsoleTester, "send_command", return_value=sample_adc_sbu2
        ):
            ori_str, sbu1, sbu2, _ = tester.read_adc_sbu_orientation()
            self.assertTrue(ori_str.startswith("SBU2"))
            self.assertEqual((sbu1, sbu2), (0, 3299))

    @mock.patch("server.maui_console_tester.open_serial_port")
    def test_read_adc_host_orientation(self, _):
        tester = MauiConsoleTester("/dev/ttyUSB0")
        with mock.patch.object(
            MauiConsoleTester, "send_command", return_value=SAMPLE_ADC_OUTPUT
        ):
            ori_str, cc1, cc2, _ = tester.read_adc_host_orientation()
            self.assertTrue(ori_str.startswith("CC1"))
            self.assertEqual((cc1, cc2), (885, 54))
        with mock.patch.object(
            MauiConsoleTester, "send_command", return_value=SAMPLE_ADC_FLIPPED_OUTPUT
        ):
            ori_str, cc1, cc2, _ = tester.read_adc_host_orientation()
            self.assertTrue(ori_str.startswith("CC2"))
            self.assertEqual((cc1, cc2), (61, 938))

    def test_read_pdc_port_status_bad_number_is_reported(self):
        tester = MauiConsoleTester("/dev/ttyUSB0")
        with mock.patch.object(
            MauiConsoleTester, "send_command", return_value="Voltage: 1.2.3 V\r\n"
        ):
            res = tester.read_pdc_port_status(port="dut", ser=mock.MagicMock())
        self.assertFalse(res["success"])
        self.assertTrue(res["error"])

    def test_read_hwrev(self):
        tester = MauiConsoleTester(port_node="/dev/ttyUSB0")
        mock_ser = mock.MagicMock()
        with mock.patch.object(
            MauiConsoleTester, "send_command", return_value=SAMPLE_HWREV_OUTPUT
        ):
            self.assertEqual(tester.read_hwrev(ser=mock_ser)[:2], (True, 2))
        with mock.patch.object(
            MauiConsoleTester,
            "send_command",
            return_value="Hardware revision: 1\r\nmaui$ ",
        ):
            self.assertEqual(tester.read_hwrev(ser=mock_ser)[:2], (False, 1))

    def test_read_temperature(self):
        tester = MauiConsoleTester(port_node="/dev/ttyUSB0")
        mock_ser = mock.MagicMock()
        with mock.patch.object(
            MauiConsoleTester, "send_command", return_value=SAMPLE_TEMP_OUTPUT
        ):
            ok, temp_c, _ = tester.read_temperature(ser=mock_ser)
            self.assertTrue(ok)
            self.assertAlmostEqual(temp_c, 43.26)
        for out in (
            "Temperature = 15.0 'C\r\nmaui$ ",
            "Temperature = 70.0 'C\r\nmaui$ ",
        ):
            with mock.patch.object(MauiConsoleTester, "send_command", return_value=out):
                self.assertFalse(tester.read_temperature(ser=mock_ser)[0])

    def test_read_and_verify_gpios(self):
        tester = MauiConsoleTester(port_node="/dev/ttyUSB0")
        mock_ser = mock.MagicMock()
        with mock.patch.object(
            MauiConsoleTester, "send_command", return_value=SAMPLE_GPIO_OUTPUT
        ):
            ok, gpios, mismatches, _ = tester.read_and_verify_gpios(ser=mock_ser)
            self.assertTrue(ok)
            self.assertEqual(len(mismatches), 0)
            self.assertEqual(gpios["h2h_reset_l"], 0)
            self.assertEqual(gpios["hub_ss_up_status"], 1)

        bad_gpio = SAMPLE_GPIO_OUTPUT.replace(
            "[         hub_ss_up_status] I = 1", "[         hub_ss_up_status] I = 0"
        )
        with mock.patch.object(
            MauiConsoleTester, "send_command", return_value=bad_gpio
        ):
            ok, gpios, mismatches, _ = tester.read_and_verify_gpios(ser=mock_ser)
            self.assertFalse(ok)
            self.assertEqual(len(mismatches), 1)
            self.assertIn("hub_ss_up_status: expected 1, got 0", mismatches[0])

    def test_verify_host_adc_voltages(self):
        ok, mismatches = verify_host_adc_voltages(
            {"pp5000_mv": 2500, "host_vbus_mv": 2500, "chg_vbus_mv": 5000}
        )
        self.assertTrue(ok)
        self.assertEqual(len(mismatches), 0)

        ok, mismatches = verify_host_adc_voltages(
            {"pp5000_mv": 2100, "host_vbus_mv": 2750, "chg_vbus_mv": 5000}
        )
        self.assertFalse(ok)
        self.assertEqual(
            mismatches, ["PP5000: 2100 mV outside [2250, 2750] mV (2500 mV +-10%)"]
        )

        ok, mismatches = verify_host_adc_voltages(
            {"pp5000_mv": 2500, "host_vbus_mv": 2500, "chg_vbus_mv": 4200}
        )
        self.assertFalse(ok)
        self.assertIn(
            "PPVAR_USB_CHG_VBUS: 4200 mV outside [4500, 5500] mV", mismatches[0]
        )

    def test_flip_helpers(self):
        self.assertTrue(is_host_cc_flipped("CC1 (895mV / 58mV)", "CC2 (61mV / 938mV)"))
        self.assertTrue(is_host_cc_flipped("CC2 (61mV / 938mV)", "CC1 (895mV / 58mV)"))
        self.assertFalse(is_host_cc_flipped("CC1 (895mV / 58mV)", "CC1 (895mV / 58mV)"))
        self.assertFalse(is_host_cc_flipped("Unknown", "CC2"))
        self.assertFalse(is_host_cc_flipped("CC1", "Unknown"))
        self.assertTrue(is_sbu_flipped("SBU1 (3300mV / 0mV)", "SBU2 (0mV / 3300mV)"))
        self.assertFalse(is_sbu_flipped("SBU1 (3300mV / 0mV)", "SBU1 (3300mV / 0mV)"))


class TestMauiStation2Servicer(_IsolatedEnvMixin, unittest.TestCase):
    """Tests for MauiStation2Servicer."""

    def setUp(self):
        super().setUp()
        self.servicer = MauiStation2Servicer()
        self.context = mock.MagicMock()
        self.port = self.make_port()
        serial_port = mock.patch("server.maui_station2_service.open_serial_port")
        self.mock_serial = serial_port.start()
        self.addCleanup(serial_port.stop)
        self.mock_serial.return_value.__enter__.return_value = mock.MagicMock()

    def _send(self, outputs):
        p = mock.patch.object(
            MauiConsoleTester, "send_command", side_effect=list(outputs)
        )
        mock_send = p.start()
        self.addCleanup(p.stop)
        return mock_send

    def _scan(self, devices):
        p = mock.patch("server.maui_common.scan_usb_devices", return_value=devices)
        p.start()
        self.addCleanup(p.stop)

    # ---- Host-only / re-plug ----

    def test_run_host_only_tests(self):
        self._scan(HOST_TOPOLOGY)
        self._send(
            [
                SAMPLE_VERSION_OUTPUT,
                SAMPLE_PDC_INFO_OUTPUT,
                "Plug Present: Yes\r\nVoltage: 5.0 V\r\nMeasured VBUS: 5.0 V (5105 mV)\r\nmaui$ ",
                SAMPLE_ADC_OUTPUT,
                SAMPLE_HWREV_OUTPUT,
                SAMPLE_TEMP_OUTPUT,
                SAMPLE_GPIO_OUTPUT,
            ]
        )
        req = maui_manufacturing_pb2.MauiHostOnlyTestRequest(
            serial_number=MAUI_SERIAL, device_port=self.port
        )
        resp = self.servicer.run_host_only_tests(req, self.context)
        self.assertTrue(resp.all_passed)
        self.assertTrue(resp.hub_usb2_detected)
        self.assertTrue(resp.ftdi_detected)
        self.assertTrue(resp.host_orientation.startswith("CC1"))
        self.assertEqual((resp.cc1_mv, resp.cc2_mv), (885, 54))
        self.assertTrue(resp.hwrev_ok)
        self.assertEqual(resp.detected_hwrev, 2)
        self.assertTrue(resp.temp_ok)
        self.assertAlmostEqual(resp.temperature_c, 43.26)
        self.assertTrue(resp.gpios_ok)
        self.assertTrue(resp.voltages_ok)
        self.assertEqual(
            (resp.pp5000_mv, resp.host_vbus_mv, resp.chg_vbus_mv), (2609, 2616, 5105)
        )
        self.assertEqual(
            (resp.pdc_appconfig_ver, resp.pdc_fw_ver), ("00000318", "00000019")
        )
        log = self.read_device_log(MAUI_SERIAL, "host_only_tests.log")
        self.assertEqual(parse_log_status(log), LogStatus.PASSED)
        self.assertIn("Host Orientation: CC1", log)
        self.assertIn("(Limits: 20-65 'C)", log)

    def test_revision_hooks(self):
        class OtherRevision(MauiStation2Servicer):
            """A revision with other console texts."""

            CONSOLE_USB_LABEL = "CONSOLE"
            EXPECTED_HWREV_TEXT = "3"

            def _gpio_ok_text(self):
                return "GPIOs verified (other revision)"

        self._scan(HOST_TOPOLOGY)
        self._send(
            [
                SAMPLE_VERSION_OUTPUT,
                SAMPLE_PDC_INFO_OUTPUT,
                "Plug Present: Yes\r\nVoltage: 5.0 V\r\nMeasured VBUS: 5.0 V (5105 mV)\r\nmaui$ ",
                SAMPLE_ADC_OUTPUT,
                SAMPLE_HWREV_OUTPUT,
                SAMPLE_TEMP_OUTPUT,
                SAMPLE_GPIO_OUTPUT,
            ]
        )
        servicer = OtherRevision()
        with mock.patch.object(
            OtherRevision, "_make_console_tester", wraps=servicer._make_console_tester
        ) as make_tester:
            resp = servicer.run_host_only_tests(
                maui_manufacturing_pb2.MauiHostOnlyTestRequest(
                    serial_number=MAUI_SERIAL, device_port=self.port
                ),
                self.context,
            )
        make_tester.assert_called_with(self.port)
        self.assertIn("CONSOLE=True", resp.summary)
        self.assertEqual(resp.gpio_details, "GPIOs verified (other revision)")
        log = self.read_device_log(MAUI_SERIAL, "host_only_tests.log")
        self.assertIn("(Expected: 3)", log)

    def test_run_host_only_tests_reports_pdc_versions_read(self):
        self._scan(HOST_TOPOLOGY)
        self._send(
            [
                SAMPLE_VERSION_OUTPUT,
                "App Config Version: 00000400\r\nmaui$ ",  # FW version missing
                "Plug Present: Yes\r\nmaui$ ",
                SAMPLE_ADC_OUTPUT,
                SAMPLE_HWREV_OUTPUT,
                SAMPLE_TEMP_OUTPUT,
                SAMPLE_GPIO_OUTPUT,
            ]
        )
        req = maui_manufacturing_pb2.MauiHostOnlyTestRequest(
            serial_number=MAUI_SERIAL, device_port=self.port
        )
        resp = self.servicer.run_host_only_tests(req, self.context)
        self.assertFalse(resp.pdc_info_ok)
        self.assertFalse(resp.all_passed)
        self.assertEqual(
            (resp.pdc_appconfig_ver, resp.pdc_fw_ver), ("00000400", "UNKNOWN")
        )

    def test_run_host_only_tests_charger_uses_parsed_fields_only(self):
        self._scan(HOST_TOPOLOGY)
        adc_no_chg = SAMPLE_ADC_OUTPUT.replace("5105 mV", "0 mV")
        self._send(
            [
                SAMPLE_VERSION_OUTPUT,
                SAMPLE_PDC_INFO_OUTPUT,
                "Plug Present: No\r\nLast event: 20.00v overshoot, voltage: 5 retries\r\nmaui$ ",
                adc_no_chg,
                SAMPLE_HWREV_OUTPUT,
                SAMPLE_TEMP_OUTPUT,
                SAMPLE_GPIO_OUTPUT,
            ]
        )
        req = maui_manufacturing_pb2.MauiHostOnlyTestRequest(
            serial_number=MAUI_SERIAL, device_port=self.port
        )
        resp = self.servicer.run_host_only_tests(req, self.context)
        self.assertFalse(resp.pdc_charger_ok)
        self.assertIn("Charger Port=FAIL (No Charger)", resp.summary)

    def _replug(self, adc_output, previous_orientation="", serial_number=MAUI_SERIAL):
        self._scan(HOST_TOPOLOGY)
        self._send(
            [
                SAMPLE_VERSION_OUTPUT,
                SAMPLE_PDC_INFO_OUTPUT,
                adc_output,
                SAMPLE_HWREV_OUTPUT,
                SAMPLE_TEMP_OUTPUT,
                SAMPLE_GPIO_OUTPUT,
            ]
        )
        req = maui_manufacturing_pb2.MauiHostReplugTestRequest(
            serial_number=serial_number,
            device_port=self.port,
            previous_orientation=previous_orientation,
        )
        return self.servicer.test_host_replug(req, self.context)

    def test_host_replug_flip_success(self):
        resp = self._replug(
            SAMPLE_ADC_FLIPPED_OUTPUT, previous_orientation="CC1 (895mV / 58mV)"
        )
        self.assertTrue(resp.all_passed)
        self.assertTrue(resp.host_flip_ok)
        self.assertTrue(resp.host_orientation.startswith("CC2"))
        self.assertEqual((resp.cc1_mv, resp.cc2_mv), (61, 938))
        self.assertTrue(resp.hwrev_ok)
        self.assertTrue(resp.temp_ok)
        self.assertTrue(resp.gpios_ok)
        self.assertTrue(resp.voltages_ok)
        self.assertIn("PASS (CC1", resp.message)
        log = self.read_device_log(MAUI_SERIAL, "host_replug_test.log")
        self.assertEqual(parse_log_status(log), LogStatus.PASSED)

    def test_host_replug_flip_fails_when_unflipped(self):
        resp = self._replug(
            SAMPLE_ADC_OUTPUT, previous_orientation="CC1 (895mV / 58mV)"
        )
        self.assertFalse(resp.all_passed)
        self.assertFalse(resp.host_flip_ok)
        self.assertTrue(resp.host_orientation.startswith("CC1"))
        self.assertIn("FAIL (Host cable polarity not inverted", resp.message)

    def test_host_replug_without_baseline_fails(self):
        resp = self._replug(SAMPLE_ADC_FLIPPED_OUTPUT)
        self.assertFalse(resp.host_flip_ok)
        self.assertFalse(resp.all_passed)
        self.assertIn("No baseline host orientation", resp.message)

    def test_host_replug_uses_host_only_log_baseline(self):
        write_device_log(
            MAUI_SERIAL,
            "host_only_tests",
            "ALL_PASSED=True\nHost Orientation: CC1 (885mV / 54mV)\n",
        )
        resp = self._replug(SAMPLE_ADC_FLIPPED_OUTPUT)
        self.assertTrue(resp.host_flip_ok)
        self.assertIn("PASS (CC1 (885mV / 54mV) -> CC2", resp.message)

    def test_host_replug_never_uses_unknown_serial_baseline(self):
        # A previous board without a serial left a baseline in the shared UNKNOWN dir.
        write_device_log(
            "UNKNOWN", "host_only_tests", "Host Orientation: CC1 (885mV / 54mV)\n"
        )
        resp = self._replug(SAMPLE_ADC_FLIPPED_OUTPUT, serial_number="")
        self.assertFalse(resp.host_flip_ok)
        self.assertIn("No baseline host orientation", resp.message)

    # ---- RESET / BSL buttons ----

    @mock.patch("time.sleep")
    @mock.patch("server.maui_console_tester.MauiConsoleTester.read_kernel_uptime")
    def test_reset_button_baseline(self, mock_uptime, _):
        mock_uptime.return_value = (15420, "Kernel uptime: 15420 ms")
        req = maui_manufacturing_pb2.MauiResetButtonTestRequest(
            serial_number=MAUI_SERIAL,
            device_port=self.port,
            action="check_initial_uptime",
        )
        resp = self.servicer.test_reset_button(req, self.context)
        self.assertTrue(resp.success)
        self.assertTrue(resp.uptime_sufficient)
        self.assertEqual(resp.current_uptime_ms, 15420)

    @mock.patch("time.sleep")
    @mock.patch("server.maui_console_tester.MauiConsoleTester.read_kernel_uptime")
    def test_reset_button_baseline_fails_below_7s(self, mock_uptime, _):
        mock_uptime.return_value = (5000, "Kernel uptime: 5000 ms")
        req = maui_manufacturing_pb2.MauiResetButtonTestRequest(
            serial_number=MAUI_SERIAL,
            device_port=self.port,
            action="check_initial_uptime",
        )
        resp = self.servicer.test_reset_button(req, self.context)
        self.assertFalse(resp.success)
        self.assertFalse(resp.uptime_sufficient)
        self.assertEqual(resp.current_uptime_ms, 5000)
        self.assertIn("(< 7000 ms)", resp.message)

    @mock.patch("time.sleep")
    @mock.patch("server.maui_console_tester.MauiConsoleTester.read_kernel_uptime")
    def test_reset_button_post_reset_verification(self, mock_uptime, _):
        for action, uptime in (
            ("verify_post_reset_uptime", 1200),
            ("verify_reboot", 1150),
        ):
            mock_uptime.return_value = (uptime, f"Kernel uptime: {uptime} ms")
            req = maui_manufacturing_pb2.MauiResetButtonTestRequest(
                serial_number=MAUI_SERIAL,
                device_port=self.port,
                action=action,
                initial_uptime_ms=15420,
            )
            resp = self.servicer.test_reset_button(req, self.context)
            self.assertTrue(resp.success)
            self.assertTrue(resp.reset_verified)
            self.assertEqual(resp.current_uptime_ms, uptime)
        log = self.read_device_log(MAUI_SERIAL, "reset_button_test.log")
        self.assertEqual(parse_log_status(log), LogStatus.PASSED)

    @mock.patch("time.sleep")
    @mock.patch("server.maui_console_tester.MauiConsoleTester.read_kernel_uptime")
    def test_reset_button_post_reset_fails_immediately_when_uptime_exceeds_initial(
        self, mock_uptime, _
    ):
        mock_uptime.return_value = (16000, "Kernel uptime: 16000 ms")
        req = maui_manufacturing_pb2.MauiResetButtonTestRequest(
            serial_number=MAUI_SERIAL,
            device_port=self.port,
            action="verify_post_reset_uptime",
            initial_uptime_ms=15420,
        )
        resp = self.servicer.test_reset_button(req, self.context)
        self.assertFalse(resp.success)
        self.assertFalse(resp.reset_verified)
        self.assertEqual(resp.current_uptime_ms, 16000)
        self.assertIn("Board did not reboot", resp.message)
        self.assertEqual(mock_uptime.call_count, 1)
        log = self.read_device_log(MAUI_SERIAL, "reset_button_test.log")
        self.assertEqual(parse_log_status(log), LogStatus.FAILED)

    @mock.patch("time.sleep")
    @mock.patch("server.maui_console_tester.MauiConsoleTester.read_kernel_uptime")
    def test_reset_button_post_reset_polls_until_successful_read(self, mock_uptime, _):
        mock_uptime.side_effect = [
            (-1, "Read timeout"),
            (1200, "Kernel uptime: 1200 ms"),
        ]
        req = maui_manufacturing_pb2.MauiResetButtonTestRequest(
            serial_number=MAUI_SERIAL,
            device_port=self.port,
            action="verify_post_reset_uptime",
            initial_uptime_ms=15420,
        )
        resp = self.servicer.test_reset_button(req, self.context)
        self.assertTrue(resp.success)
        self.assertTrue(resp.reset_verified)
        self.assertEqual(resp.current_uptime_ms, 1200)
        self.assertEqual(mock_uptime.call_count, 2)

    @mock.patch(
        "server.maui_console_tester.MauiConsoleTester.read_kernel_uptime",
        return_value=(-1, "no answer"),
    )
    def test_reset_button_resolves_ftdi_console_port(self, _):
        ftdi_port = self.make_port("ttyUSB_FTDI")
        self._scan(
            [
                UsbDeviceInfo(
                    sys_path="/sys/bus/usb/devices/3",
                    vid=0x0403,
                    pid=0x6002,
                    dev_node=ftdi_port,
                )
            ]
        )
        req = maui_manufacturing_pb2.MauiResetButtonTestRequest(
            serial_number=MAUI_SERIAL,
            device_port="/dev/does_not_exist",
            action="check_initial_uptime",
        )
        resp = self.servicer.test_reset_button(req, self.context)
        self.assertFalse(resp.success)
        self.assertIn(ftdi_port, resp.message)

    def test_resolve_console_port_numeric_fallback(self):
        with mock.patch(
            "server.maui_common.glob.glob",
            return_value=["/dev/ttyUSB10", "/dev/ttyUSB2"],
        ):
            self.assertEqual(
                _resolve_console_port("/dev/does_not_exist"), "/dev/ttyUSB2"
            )
        with mock.patch("server.maui_common.glob.glob", return_value=[]):
            self.assertEqual(_resolve_console_port(""), "/dev/ttyUSB0")
        self.assertEqual(_resolve_console_port(self.port), self.port)

    def test_bsl_button(self):
        for sample, expected_ok, expected_status in (
            ((True, 3010, "[ BSL] = 3010 mV"), True, LogStatus.PASSED),
            ((False, 12, "[ BSL] = 12 mV"), False, LogStatus.FAILED),
        ):
            with self.subTest(expected_ok=expected_ok), mock.patch.object(
                MauiConsoleTester, "sample_bsl_adc", return_value=sample
            ) as mock_sample:
                req = maui_manufacturing_pb2.MauiBslButtonTestRequest(
                    serial_number=MAUI_SERIAL, device_port=self.port
                )
                resp = self.servicer.test_bsl_button(req, self.context)
                self.assertEqual(resp.success, expected_ok)
                self.assertEqual(resp.peak_bsl_mv, sample[1])
                self.assertEqual(resp.threshold_mv, 2500)
                self.assertEqual(
                    mock_sample.call_args.kwargs,
                    {
                        "max_duration_sec": 3.0,
                        "interval_sec": 0.15,
                        "threshold_mv": 2500,
                    },
                )
                log = self.read_device_log(MAUI_SERIAL, "bsl_button_test.log")
                self.assertEqual(parse_log_status(log), expected_status)

    # ---- Host + DUT ----

    def _host_dut(self, stage, outputs, devices=None, **options):
        """Runs one Host+DUT stage; options: expected_serial, previous_orientation."""
        expected_serial = options.get("expected_serial", MAUI_SERIAL)
        adb_result = (True, True, "5000M", "adb ok")
        ftdi_console = UsbDeviceInfo(
            sys_path="/sys/bus/usb/devices/3",
            vid=0x0403,
            pid=0x6002,
            dev_node=self.port,
        )
        maui_adb = UsbDeviceInfo(
            sys_path="/sys/bus/usb/devices/1-1",
            vid=0x18D1,
            pid=0x506D,
            serial=MAUI_SERIAL,
            speed="5000",
        )
        self._scan(devices if devices is not None else [ftdi_console, maui_adb])
        self._send(outputs)
        patches = [
            mock.patch.object(
                self.servicer, "_check_adb_and_dut_speed", return_value=adb_result
            ),
            mock.patch.object(
                self.servicer,
                "_find_ccd_serial_nodes",
                return_value={
                    "ap": "/dev/ttyUSB1",
                    "ec": "/dev/ttyUSB2",
                    "gsc": "/dev/ttyUSB3",
                },
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        req = maui_manufacturing_pb2.MauiHostDutTestRequest(
            serial_number=expected_serial,
            expected_serial=expected_serial,
            stage=stage,
            previous_orientation=options.get("previous_orientation", ""),
        )
        return self.servicer.run_host_dut_tests(req, self.context)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_run_host_dut_tests(self, _):
        resp = self._host_dut(
            "stage1_with_charger", [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU1_20V]
        )
        self.assertTrue(resp.all_passed)
        self.assertTrue(resp.h2h_speed_5gbps)
        self.assertTrue(resp.h2h_serial_matched)
        self.assertTrue(resp.pd_contract_ok)
        log = self.read_device_log(MAUI_SERIAL, "host_dut_stage1_with_charger.log")
        self.assertEqual(parse_log_status(log), LogStatus.PASSED)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_run_host_dut_stage1_fails_without_charger(self, _):
        resp = self._host_dut(
            "stage1_with_charger",
            [
                "Plug Present: Yes\r\nVoltage: 5.0 V\r\nMeasured VBUS: 5.0 V\r\nmaui$ ",
                "Plug Present: No\r\nConnection State: 0\r\nActive Contract:  None\r\nmaui$ ",
                "[ SBU1_DET] = 3300 mV\r\n[ SBU2_DET] = 0 mV\r\n[ PPVAR_USB_CHG_VBUS] = 0 mV\r\n"
                "[ PPVAR_USB_DUT_VBUS] = 5000 mV\r\nmaui$ ",
            ],
        )
        self.assertFalse(resp.all_passed)
        self.assertIn("FAIL", resp.summary)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_run_host_dut_20v_contract_uses_parsed_voltage_only(self, _):
        resp = self._host_dut(
            "stage1_with_charger",
            [
                "Plug Present: Yes\r\nVoltage: 5.0 V\r\nFW: 120.0 build 20v\r\nMeasured VBUS: 5.0 V (5000 mV)\r\nmaui$ ",
                PDC_CHG_20V,
                ADC_SBU1_20V.replace(
                    "PPVAR_USB_DUT_VBUS] = 20000", "PPVAR_USB_DUT_VBUS] = 5000"
                ),
            ],
        )
        self.assertFalse(resp.pd_contract_ok)
        self.assertFalse(resp.all_passed)
        self.assertIn("FAIL (Expected 20V)", resp.summary)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_run_host_dut_stage3_charger_disconnected_passes(self, _):
        resp = self._host_dut(
            "stage3_no_charger",
            [
                "Plug Present: Yes\r\nOrientation: Normal (CC1)\r\nVoltage: 5.0 V\r\nOp Current: 0.50 A\r\n"
                "Measured VBUS: 4.807 V (4807 mV)\r\nmaui$ ",
                "Plug Present:     No\r\n  Active Contract:  None\r\n  Measured VBUS:    4.331 V (4331 mV)\r\nmaui$ ",
                "[ SBU1_DET] = 3299 mV\r\n[ SBU2_DET] = 0 mV\r\n[ PPVAR_USB_CHG_VBUS] = 4331 mV\r\n"
                "[ PPVAR_USB_DUT_VBUS] = 4797 mV\r\nmaui$ ",
            ],
        )
        self.assertTrue(resp.all_passed)
        self.assertIn("Charger Disconnected: PASS", resp.summary)

    def test_run_host_dut_stage1_passes_without_ap_console_response(self):
        def fake_query_ccd(node, unused_cmd, timeout=0.5, first_byte_timeout=0.25):
            del timeout, first_byte_timeout
            if node == "/dev/ttyUSB2":
                return True, "EC version: 1.2.3"
            if node == "/dev/ttyUSB3":
                return True, "GSC version: 0.0.1"
            return False, "Timeout"

        with mock.patch.object(
            MauiConsoleTester, "query_ccd_console", side_effect=fake_query_ccd
        ) as mock_query:
            resp = self._host_dut(
                "stage1_with_charger", [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU1_20V]
            )
        self.assertTrue(resp.all_passed)
        self.assertTrue(resp.ec_uart_ok)
        self.assertTrue(resp.gsc_uart_ok)
        queried_nodes = [c.args[0] for c in mock_query.call_args_list]
        self.assertNotIn("/dev/ttyUSB1", queried_nodes)
        self.assertIn("/dev/ttyUSB2", queried_nodes)
        self.assertIn("/dev/ttyUSB3", queried_nodes)
        self.assertIn("CCD Consoles: EC=PASS, GSC=PASS", resp.summary)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_stage2_sbu_flip_with_previous_orientation(self, _):
        resp = self._host_dut(
            "stage2_sbu_flip",
            [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU2_20V],
            previous_orientation="SBU1 (3300mV / 0mV)",
        )
        self.assertTrue(resp.sbu_flip_ok)
        self.assertTrue(resp.all_passed)
        self.assertIn(
            "SBU Orientation: PASS (SBU1 (3300mV / 0mV) -> SBU2", resp.summary
        )

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_stage2_sbu_flip_not_inverted(self, _):
        resp = self._host_dut(
            "stage2_sbu_flip",
            [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU1_20V],
            previous_orientation="SBU1 (3300mV / 0mV)",
        )
        self.assertFalse(resp.sbu_flip_ok)
        self.assertIn("Polarity not inverted", resp.summary)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_stage2_sbu_flip_uses_stage1_log_baseline(self, _):
        write_device_log(
            MAUI_SERIAL,
            "host_dut_stage1_with_charger",
            "DUT Orientation: SBU1 (3300mV / 0mV) | x",
        )
        resp = self._host_dut(
            "stage2_sbu_flip", [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU2_20V]
        )
        self.assertTrue(resp.sbu_flip_ok)
        log = self.read_device_log(MAUI_SERIAL, "host_dut_stage2_sbu_flip.log")
        self.assertEqual(parse_log_status(log), LogStatus.PASSED)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_stage2_sbu_flip_without_baseline_fails(self, _):
        resp = self._host_dut(
            "stage2_sbu_flip", [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU2_20V]
        )
        self.assertFalse(resp.sbu_flip_ok)
        self.assertFalse(resp.all_passed)
        self.assertIn("No baseline SBU orientation", resp.summary)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_stage2_sbu_flip_never_uses_unknown_serial_baseline(self, _):
        write_device_log(
            "UNKNOWN",
            "host_dut_stage1_with_charger",
            "DUT Orientation: SBU1 (3300mV / 0mV) | x",
        )
        ftdi_console = UsbDeviceInfo(
            sys_path="/sys/bus/usb/devices/3",
            vid=0x0403,
            pid=0x6002,
            dev_node=self.port,
        )
        resp = self._host_dut(
            "stage2_sbu_flip",
            [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU2_20V],
            devices=[ftdi_console],
            expected_serial="",
        )
        self.assertEqual(resp.h2h_serial, "UNKNOWN")
        self.assertFalse(resp.sbu_flip_ok)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_host_dut_serial_lookup_ignores_ccd_and_requires_expected(self, _):
        ftdi_console = UsbDeviceInfo(
            sys_path="/sys/bus/usb/devices/3",
            vid=0x0403,
            pid=0x6002,
            dev_node=self.port,
        )
        ccd = UsbDeviceInfo(
            sys_path="/sys/bus/usb/devices/2-1",
            vid=0x18D1,
            pid=0x5014,
            serial="CR50SERIAL",
        )
        maui_h2h = UsbDeviceInfo(
            sys_path="/sys/bus/usb/devices/1-1",
            vid=0x18D1,
            pid=0x5084,
            serial=MAUI_SERIAL,
        )
        resp = self._host_dut(
            "stage1_with_charger",
            [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU1_20V],
            devices=[ccd, ftdi_console, maui_h2h],
        )
        self.assertEqual(resp.h2h_serial, MAUI_SERIAL)
        self.assertTrue(resp.h2h_serial_matched)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_host_dut_empty_expected_serial_accepts_real_h2h_serial(self, _):
        resp = self._host_dut(
            "stage1_with_charger",
            [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU1_20V],
            expected_serial="",
        )
        self.assertEqual(resp.h2h_serial, MAUI_SERIAL)
        self.assertTrue(resp.h2h_serial_matched)
        self.assertTrue(resp.all_passed)
        self.assertIn(f"Serial Match: PASS ({MAUI_SERIAL})", resp.summary)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_host_dut_serial_mismatch_and_missing_h2h_fail(self, _):
        resp = self._host_dut(
            "stage1_with_charger",
            [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU1_20V],
            expected_serial="OTHER_SERIAL",
        )
        self.assertFalse(resp.h2h_serial_matched)
        self.assertIn("Serial Match: FAIL |", resp.summary)

        resp = self._host_dut(
            "stage3_no_charger",
            [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU1_20V],
            devices=[],
            expected_serial="",
        )
        self.assertFalse(resp.h2h_serial_matched)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_host_dut_alt_h2h_pid_is_maui_serial(self, _):
        maui_h2h_alt = UsbDeviceInfo(
            sys_path="/sys/bus/usb/devices/1-1",
            vid=0x18D1,
            pid=0x5085,
            serial=MAUI_SERIAL,
        )
        resp = self._host_dut(
            "stage1_with_charger",
            [PDC_DUT_20V, PDC_CHG_20V, ADC_SBU1_20V],
            devices=[maui_h2h_alt],
        )
        self.assertEqual(resp.h2h_serial, MAUI_SERIAL)
        self.assertTrue(resp.h2h_serial_matched)

    def _busy_port_lock(self):
        def busy(port_node, timeout=10.0):
            raise ConsolePortBusyError(port_node, timeout)

        p = mock.patch("server.maui_station2_service.port_lock", side_effect=busy)
        p.start()
        self.addCleanup(p.stop)

    def test_busy_console_port_fails_cards(self):
        self._busy_port_lock()
        bsl = self.servicer.test_bsl_button(
            maui_manufacturing_pb2.MauiBslButtonTestRequest(
                serial_number=MAUI_SERIAL, device_port=self.port
            ),
            self.context,
        )
        self.assertFalse(bsl.success)
        self.assertIn("busy", bsl.message)
        self.assertEqual(
            parse_log_status(self.read_device_log(MAUI_SERIAL, "bsl_button_test.log")),
            LogStatus.FAILED,
        )

        baseline = self.servicer.test_reset_button(
            maui_manufacturing_pb2.MauiResetButtonTestRequest(
                action="check_initial_uptime", device_port=self.port
            ),
            self.context,
        )
        self.assertFalse(baseline.success)
        self.assertIn("busy", baseline.message)

        with mock.patch("time.sleep"):
            verify = self.servicer.test_reset_button(
                maui_manufacturing_pb2.MauiResetButtonTestRequest(
                    action="verify",
                    serial_number=MAUI_SERIAL,
                    device_port=self.port,
                    initial_uptime_ms=9000,
                ),
                self.context,
            )
        self.assertFalse(verify.reset_verified)
        self.assertIn("busy", verify.message)

        self._scan(HOST_TOPOLOGY)
        host = self.servicer.run_host_only_tests(
            maui_manufacturing_pb2.MauiHostOnlyTestRequest(
                serial_number=MAUI_SERIAL, device_port=self.port
            ),
            self.context,
        )
        self.assertFalse(host.all_passed)
        self.assertIn("busy", host.summary)

    @mock.patch.object(
        MauiConsoleTester, "query_ccd_console", return_value=(True, "v1.0")
    )
    def test_busy_console_port_fails_host_dut(self, _):
        self._busy_port_lock()
        resp = self._host_dut("stage1_with_charger", [])
        self.assertFalse(resp.all_passed)
        self.assertIn("busy", resp.summary)

    # ---- H2H speed (B4) ----

    def _adb_env(self, dut_output, adb_devices=("DUTSER",), host_devices=()):
        def fake_run(cmd, **unused_kwargs):
            if cmd[-1] == "get-state":
                return mock.Mock(returncode=0, stdout="device\n")
            return mock.Mock(returncode=0, stdout=dut_output)

        patches = [
            mock.patch(
                "server.maui_common.list_adb_devices", return_value=list(adb_devices)
            ),
            mock.patch(
                "server.maui_station2_service.subprocess.run", side_effect=fake_run
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self._scan(list(host_devices))

    def test_h2h_speed_ignores_root_hub_and_host_override(self):
        host_h2h = UsbDeviceInfo(
            sys_path="/sys/bus/usb/devices/2-1", vid=0x18D1, pid=0x5084, speed="5000"
        )
        self._adb_env(
            "1d6b:0003 speed=5000\n18d1:5084 speed=480\n", host_devices=[host_h2h]
        )
        adb_ok, h2h_5gbps, speed_str, logs = self.servicer._check_adb_and_dut_speed(
            "DUTSER"
        )
        self.assertTrue(adb_ok)
        self.assertFalse(h2h_5gbps)
        self.assertEqual(speed_str, "480M (High-Speed)")
        self.assertIn("Host side=5000M", logs)

    def test_h2h_speed_dut_superspeed(self):
        self._adb_env("1d6b:0002 speed=480\n18d1:5084 speed=5000\n")
        _, h2h_5gbps, speed_str, _ = self.servicer._check_adb_and_dut_speed("DUTSER")
        self.assertTrue(h2h_5gbps)
        self.assertEqual(speed_str, "5000M")

    def test_h2h_speed_ignores_other_google_devices(self):
        self._adb_env("18d1:5014 speed=5000\n18d1:4ee7 speed=5000\n")
        _, h2h_5gbps, speed_str, _ = self.servicer._check_adb_and_dut_speed("DUTSER")
        self.assertFalse(h2h_5gbps)
        self.assertEqual(speed_str, "Unknown")

    def test_h2h_speed_host_side_when_adb_unavailable(self):
        with mock.patch(
            "server.maui_common.list_adb_devices", side_effect=FileNotFoundError("adb")
        ):
            self._scan(
                [
                    UsbDeviceInfo(
                        sys_path="/sys/bus/usb/devices/2-1",
                        vid=0x18D1,
                        pid=0x5014,
                        speed="5000",
                    )
                ]
            )
            self.assertFalse(self.servicer._check_adb_and_dut_speed("DUTSER")[1])
            self._scan(
                [
                    UsbDeviceInfo(
                        sys_path="/sys/bus/usb/devices/2-1",
                        vid=0x067B,
                        pid=0x27A1,
                        speed="5000",
                    )
                ]
            )
            adb_ok, h2h_5gbps, speed_str, _ = self.servicer._check_adb_and_dut_speed(
                "DUTSER"
            )
        self.assertFalse(adb_ok)
        self.assertTrue(h2h_5gbps)
        self.assertEqual(speed_str, "5000M (SuperSpeed)")

    # ---- CCD node mapping ----

    def _fake_ccd_sysfs(self, ifaces):
        """Creates <tmp>/sys/class/tty/<tty>/device -> <usb dev>/<iface>/<tty> for 18d1:5014."""
        usb_dir = os.path.join(self.tmpdir, "sys", "devices", "usb1", "1-2")
        os.makedirs(usb_dir)
        for attr, val in (("idVendor", "18d1"), ("idProduct", "5014")):
            with open(os.path.join(usb_dir, attr), "w", encoding="utf-8") as f:
                f.write(val + "\n")
        tty_dirs = []
        for tty, iface in ifaces.items():
            port_dir = os.path.join(usb_dir, f"1-2:1.{iface}", tty)
            os.makedirs(port_dir)
            class_dir = os.path.join(self.tmpdir, "sys", "class", "tty", tty)
            os.makedirs(class_dir)
            os.symlink(port_dir, os.path.join(class_dir, "device"))
            tty_dirs.append(class_dir)
        return tty_dirs

    def test_find_ccd_serial_nodes_maps_interfaces(self):
        tty_dirs = self._fake_ccd_sysfs({"ttyUSB1": 0, "ttyUSB2": 1, "ttyUSB10": 2})

        def fake_glob(pattern):
            if pattern.startswith("/sys/class/tty"):
                return tty_dirs
            return ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2", "/dev/ttyUSB10"]

        with mock.patch("server.maui_common.glob.glob", side_effect=fake_glob):
            nodes = self.servicer._find_ccd_serial_nodes()
        self.assertEqual(
            nodes, {"gsc": "/dev/ttyUSB1", "ap": "/dev/ttyUSB2", "ec": "/dev/ttyUSB10"}
        )

    def test_find_ccd_serial_nodes_numeric_fallback(self):
        self._scan(
            [
                UsbDeviceInfo(
                    sys_path="/sys/bus/usb/devices/3",
                    vid=0x0403,
                    pid=0x6002,
                    dev_node="/dev/ttyUSB0",
                ),
                UsbDeviceInfo(
                    sys_path="/sys/bus/usb/devices/2-1", vid=0x18D1, pid=0x5014
                ),
            ]
        )

        def fake_glob(pattern):
            if pattern.startswith("/sys/class/tty"):
                return []
            return ["/dev/ttyUSB10", "/dev/ttyUSB0", "/dev/ttyUSB2", "/dev/ttyUSB1"]

        with mock.patch("server.maui_common.glob.glob", side_effect=fake_glob):
            nodes = self.servicer._find_ccd_serial_nodes()
        self.assertEqual(
            nodes, {"gsc": "/dev/ttyUSB1", "ap": "/dev/ttyUSB2", "ec": "/dev/ttyUSB10"}
        )


def _load_merge_tool():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "tools",
        "merge_factory_logs.py",
    )
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("merge_factory_logs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


merge_factory_logs = _load_merge_tool()


@unittest.skipIf(
    merge_factory_logs is None, "tools/merge_factory_logs.py not available"
)
class TestMergeFactoryLogs(unittest.TestCase):
    """Tests for tools/merge_factory_logs.py."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self._tmp.cleanup)
        self.input_dir = os.path.join(self._tmp.name, "maui_v11_results")
        os.makedirs(os.path.join(self.input_dir, "smt_functional", "results"))

    def _write(self, name, data):
        path = os.path.join(self.input_dir, "smt_functional", "results", name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    @staticmethod
    def _record(status, completion_time, padding=0):
        data = {
            "serial_number": "SER1",
            "station_id": "STATION_SMT_FUNCTIONAL",
            "phase": "smt_functional",
            "overall_status": status,
            "stage_statuses": {"host_only_verification": status},
            "padding": "x" * padding,
        }
        if completion_time:
            data["completion_time"] = completion_time
        return data

    def test_latest_record_wins_not_longest(self):
        self._write(
            "a.json", self._record("FAIL", "2026-10-01T10:00:00Z", padding=5000)
        )
        self._write("b.json", self._record("PASS", "2026-10-01T11:00:00Z"))
        self._write(
            "c.json", self._record("FAIL", "", padding=9000)
        )  # No timestamp: never "now".
        files = merge_factory_logs.find_all_result_files([self.input_dir])
        records = []
        for f in files:
            with open(f, encoding="utf-8") as fp:
                records.append(merge_factory_logs.extract_record_info(json.load(fp), f))
        devices = merge_factory_logs.merge_device_records(records)
        self.assertEqual(devices["SER1"]["smt_step2"]["overall_status"], "PASS")
        self.assertEqual(len(devices["SER1"]["all_records"]), 3)
        self.assertEqual(
            merge_factory_logs._latest_timestamp(devices["SER1"]),
            "2026-10-01T11:00:00Z",
        )

    def test_main_excludes_outputs_and_escapes_html(self):
        self._write(
            "x.json",
            dict(
                self._record("PASS", "2026-10-01T11:00:00Z"), serial_number="<b>SER</b>"
            ),
        )
        output_dir = os.path.join(self.input_dir, "consolidated")
        for _ in range(2):  # Re-run must not read back its own output.
            merge_factory_logs.main(["-i", self.input_dir, "-o", output_dir])
        files = merge_factory_logs.find_all_result_files(
            [self.input_dir], exclude_dirs=[output_dir]
        )
        self.assertEqual(len(files), 1)
        with open(
            os.path.join(output_dir, "factory_summary_report.html"), encoding="utf-8"
        ) as f:
            html_report = f.read()
        self.assertIn("&lt;b&gt;SER&lt;/b&gt;", html_report)
        self.assertNotIn("<b>SER</b>", html_report)
        self.assertTrue(
            os.path.exists(os.path.join(output_dir, "FINAL_REPORT__b_SER__b_.json"))
        )

    def test_final_report_files_are_never_inputs(self):
        self._write("FINAL_REPORT_SER1.json", {"serial_number": "SER1"})
        self.assertEqual(merge_factory_logs.find_all_result_files([self.input_dir]), [])

    def test_default_inputs_include_present_legacy_dirs(self):
        home = os.path.join(self._tmp.name, "home")
        legacy_home = os.path.join(home, "diagnoseme_results")
        os.makedirs(legacy_home)
        with mock.patch.dict(os.environ, {"HOME": home}), mock.patch.object(
            merge_factory_logs,
            "LEGACY_INPUT_DIRS",
            ("~/diagnoseme_results", os.path.join(self._tmp.name, "absent")),
        ), mock.patch.object(merge_factory_logs, "DEFAULT_INPUT_DIR", self.input_dir):
            self.assertEqual(
                merge_factory_logs.default_input_dirs(), [self.input_dir, legacy_home]
            )
            with open(
                os.path.join(legacy_home, "old.json"), "w", encoding="utf-8"
            ) as f:
                json.dump(self._record("PASS", "2026-10-01T11:00:00Z"), f)
            merge_factory_logs.main([])
            output_dir = os.path.join(home, "diagnoseme_consolidated")
            self.assertTrue(
                os.path.exists(os.path.join(output_dir, "FINAL_REPORT_SER1.json"))
            )
        for input_dir in (
            merge_factory_logs.DEFAULT_INPUT_DIR,
            *merge_factory_logs.LEGACY_INPUT_DIRS,
        ):
            self.assertFalse(
                merge_factory_logs._is_within(
                    os.path.realpath(
                        os.path.expanduser(merge_factory_logs.DEFAULT_OUTPUT_DIR)
                    ),
                    os.path.expanduser(input_dir),
                )
            )


class TestUsbPresence(unittest.TestCase):
    """Tests for util.is_usb_device_present with a fake sysfs tree."""

    def test_sysfs_lookup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dev = os.path.join(tmpdir, "1-1")
            os.makedirs(dev)
            for attr, val in (("idVendor", "0bda"), ("idProduct", "5411")):
                with open(os.path.join(dev, attr), "w", encoding="utf-8") as f:
                    f.write(val + "\n")
            with mock.patch("server.util.glob.glob", return_value=[dev]), mock.patch(
                "server.util.run_command", side_effect=FileNotFoundError("lsusb")
            ) as mock_lsusb:
                self.assertTrue(util.is_usb_device_present(0x0BDA, [0x5411]))
                self.assertTrue(util.is_usb_device_present("0bda", ["5411"]))
                mock_lsusb.assert_not_called()
                self.assertFalse(util.is_usb_device_present(0x0BDA, [0x0411]))
                self.assertFalse(util.is_usb_device_present("zz", [0x5411]))


if __name__ == "__main__":
    unittest.main()
