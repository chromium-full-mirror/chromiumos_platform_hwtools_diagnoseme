# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for Maui manufacturing services and console testing framework."""

import json
import os
import tempfile
import unittest
from unittest import mock

from server.generated import maui_manufacturing_pb2
from server.maui_common import (
    UsbDeviceInfo,
    find_offline_fw_file,
    get_device_log_dir,
    write_device_log,
)
from server.maui_console_tester import (
    MauiConsoleTester,
    PdcInfoTestCase,
    RegexMatchTestCase,
)
from server.maui_station1_service import (
    MauiDeviceManagementServicer,
    MauiPdcServicer,
    MauiStation1Servicer,
    analyze_device_manufacturing_logs,
)
from server.maui_station2_service import (
    MauiStation2Servicer,
    is_host_cc_flipped,
    is_sbu_flipped,
)


class TestMauiStation1Servicer(unittest.TestCase):
  """Tests for MauiStation1Servicer."""

  def setUp(self):
    self.servicer = MauiStation1Servicer()
    self.context = mock.MagicMock()

  @mock.patch("server.maui_station1_service.write_device_log")
  @mock.patch("subprocess.run")
  def test_auto_program_mcu_bsl_success(self, mock_run, mock_log):
    mock_run.return_value = mock.Mock(
        stdout="INFO: MCU firmware update successful.\n",
        returncode=0,
    )
    req = maui_manufacturing_pb2.MauiAutoProgramMcuRequest(
        device_port="/dev/ttyUSB0",
        fw_channel="stable",
    )
    resp = self.servicer.auto_program_mcu_bsl(req, self.context)
    self.assertTrue(resp.success)
    self.assertIn("MCU firmware flashed successfully", resp.message)

  @mock.patch("server.maui_station1_service.write_device_log")
  @mock.patch("subprocess.run")
  def test_auto_program_mcu_bsl_failure(self, mock_run, mock_log):
    import subprocess
    mock_run.side_effect = subprocess.CalledProcessError(
        returncode=1,
        cmd=["maui-flash"],
        output="ERROR: Unable to connect to BSL",
    )
    req = maui_manufacturing_pb2.MauiAutoProgramMcuRequest(
        device_port="/dev/ttyUSB0",
        fw_channel="stable",
    )
    resp = self.servicer.auto_program_mcu_bsl(req, self.context)
    self.assertFalse(resp.success)
    self.assertIn("failed", resp.message)

  @mock.patch("server.maui_station1_service.write_device_log")
  @mock.patch("server.maui_station1_service.find_offline_fw_file", return_value="/var/lib/diagnoseme/firmware/mcu/zephyr.txt")
  @mock.patch("subprocess.run")
  def test_auto_program_mcu_bsl_offline_file(self, mock_run, mock_find, mock_log):
    mock_run.return_value = mock.Mock(
        stdout="INFO: MCU firmware update successful from zephyr.txt\n",
        returncode=0,
    )
    req = maui_manufacturing_pb2.MauiAutoProgramMcuRequest(
        device_port="/dev/ttyUSB0",
        fw_channel="offline",
    )
    resp = self.servicer.auto_program_mcu_bsl(req, self.context)
    self.assertTrue(resp.success)
    # Ensure --file flag was used instead of --fw_channel
    called_cmd = mock_run.call_args[0][0]
    self.assertIn("--file", called_cmd)
    self.assertIn("/var/lib/diagnoseme/firmware/mcu/zephyr.txt", called_cmd)

  @mock.patch("server.maui_station1_service.find_offline_fw_file", return_value=None)
  def test_auto_program_mcu_bsl_offline_missing(self, mock_find):
    req = maui_manufacturing_pb2.MauiAutoProgramMcuRequest(
        device_port="/dev/ttyUSB0",
        fw_channel="offline",
    )
    resp = self.servicer.auto_program_mcu_bsl(req, self.context)
    self.assertFalse(resp.success)
    self.assertIn("Offline MCU firmware not found", resp.message)

  @mock.patch("server.maui_station1_service.write_device_log")
  @mock.patch("subprocess.run")
  def test_program_ftdi_eeprom_success(self, mock_run, mock_log):
    mock_run.return_value = mock.Mock(
        stdout="Programming successful! New PID: 0x6002\n",
        returncode=0,
    )
    req = maui_manufacturing_pb2.MauiProgramFtdiRequest(
        serial_number="TEST_MAUI_SERIAL_001",
        ftdi_dev_node="/dev/ttyUSB0",
    )
    resp = self.servicer.program_ftdi_eeprom(req, self.context)
    self.assertTrue(resp.success)
    self.assertIn("0x6002", resp.message)


class TestMauiPdcServicer(unittest.IsolatedAsyncioTestCase):
  """Tests for MauiPdcServicer."""

  def setUp(self):
    self.servicer = MauiPdcServicer()
    self.context = mock.MagicMock()
    self.context.done.return_value = False

  def test_find_offline_fw_file(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      mcu_dir = os.path.join(tmpdir, "mcu")
      os.makedirs(mcu_dir)
      fw_file = os.path.join(mcu_dir, "test_zephyr.txt")
      with open(fw_file, "w") as f:
        f.write("test fw")

      with mock.patch("server.maui_common.glob.glob", side_effect=lambda p: [fw_file] if "mcu" in p else []):
        with mock.patch("os.path.isdir", return_value=True):
          found = find_offline_fw_file("mcu")
          self.assertEqual(found, fw_file)

  @mock.patch("server.maui_station1_service.find_offline_fw_file", return_value=None)
  async def test_program_single_pdc_offline_missing_fails_cleanly(self, mock_find):
    req = maui_manufacturing_pb2.MauiProgramSinglePdcRequest(
        slot_index=1,
        serial_number="TEST_MAUI_SERIAL_001",
        device_port="/dev/ttyUSB0",
        fw_channel="offline",
    )
    responses = []
    async for resp in self.servicer.program_single_pdc(req, self.context):
      responses.append(resp)

    self.assertEqual(len(responses), 1)
    self.assertTrue(responses[0].is_complete)
    self.assertEqual(responses[0].current_block, "Failed")
    self.assertIn("Offline PDC firmware binary not found", responses[0].status_message)

  @mock.patch("server.maui_station1_service.find_offline_fw_file", return_value="/var/lib/diagnoseme/firmware/pdc/v20_0325.bin")
  @mock.patch("asyncio.create_subprocess_exec")
  async def test_program_single_pdc_offline_file_success(self, mock_exec, mock_find):
    mock_proc = mock.AsyncMock()
    mock_proc.returncode = 0
    mock_proc.stdout.readline.side_effect = [
        b"Block 1: Progress: 500/1000 bytes transferred (50.0%)\n",
        b"",
    ]
    mock_proc.stdout.read.return_value = b"Flashing complete."
    mock_proc.wait.return_value = 0
    mock_exec.return_value = mock_proc

    req = maui_manufacturing_pb2.MauiProgramSinglePdcRequest(
        slot_index=1,
        serial_number="TEST_MAUI_SERIAL_001",
        device_port="/dev/ttyUSB0",
        fw_channel="offline",
    )
    responses = []
    async for resp in self.servicer.program_single_pdc(req, self.context):
      responses.append(resp)

    self.assertTrue(len(responses) >= 1)
    final_resp = responses[-1]
    self.assertTrue(final_resp.is_complete)
    self.assertEqual(final_resp.current_block, "Complete")
    self.assertIn("succeeded", final_resp.status_message)

    # Verify that --file was passed to maui-flash subprocess
    call_args = mock_exec.call_args[0]
    self.assertIn("--file", call_args)
    self.assertIn("/var/lib/diagnoseme/firmware/pdc/v20_0325.bin", call_args)


class TestMauiDeviceManagementServicer(unittest.TestCase):
  """Tests for MauiDeviceManagementServicer and log formatting."""

  def setUp(self):
    self.servicer = MauiDeviceManagementServicer()
    self.context = mock.MagicMock()

  def test_log_directory_formatting(self):
    dir_path = get_device_log_dir("TESTSERIAL123")
    self.assertEqual(dir_path, "/tmp/TESTSERIAL123_diagnoseme_logs")

  def test_write_device_log(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      with mock.patch("server.maui_common.get_device_log_dir", return_value=tmpdir):
        saved_path = write_device_log("TESTSERIAL", "test_step", "Sample log content")
        self.assertTrue(os.path.exists(saved_path))
        with open(saved_path, "r", encoding="utf-8") as f:
          content = f.read()
        self.assertIn("Sample log content", content)

  def test_verify_scanned_label_match(self):
    req = maui_manufacturing_pb2.MauiVerifyLabelRequest(
        electronic_serial="TEST_MAUI_SERIAL_001",
        scanned_barcode="TEST_MAUI_SERIAL_001",
        adb_serial="TEST_MAUI_SERIAL_001",
        ftdi_serial="TEST_MAUI_SERIAL_001",
    )
    resp = self.servicer.verify_scanned_label(req, self.context)
    self.assertTrue(resp.matches)
    self.assertTrue(resp.adb_matches)
    self.assertTrue(resp.ftdi_matches)
    self.assertTrue(resp.dual_endpoints_match)
    self.assertIn("Verification PASSED", resp.message)

  def test_verify_scanned_label_endpoint_mismatch(self):
    req = maui_manufacturing_pb2.MauiVerifyLabelRequest(
        electronic_serial="TEST_MAUI_SERIAL_001",
        scanned_barcode="TEST_MAUI_SERIAL_001",
        adb_serial="TEST_MAUI_SERIAL_001",
        ftdi_serial="DIFFERENT_FTDI_SERIAL",
    )
    resp = self.servicer.verify_scanned_label(req, self.context)
    self.assertFalse(resp.matches)
    self.assertFalse(resp.dual_endpoints_match)
    self.assertIn("Hardware Endpoint Mismatch", resp.message)

  def test_verify_scanned_label_mismatch(self):
    req = maui_manufacturing_pb2.MauiVerifyLabelRequest(
        electronic_serial="TEST_MAUI_SERIAL_001",
        scanned_barcode="WRONGSERIAL999",
        adb_serial="TEST_MAUI_SERIAL_001",
        ftdi_serial="TEST_MAUI_SERIAL_001",
    )
    resp = self.servicer.verify_scanned_label(req, self.context)
    self.assertFalse(resp.matches)
    self.assertIn("Sticker Mismatch", resp.message)

  def test_submit_final_manufacturing_results(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      with mock.patch("server.maui_station1_service.get_device_log_dir", return_value=tmpdir):
        for log_name in MauiDeviceManagementServicer.REQUIRED_LOGS:
          with open(os.path.join(tmpdir, log_name), "w", encoding="utf-8") as f:
            f.write("ALL_PASSED=True\nStage completed successfully\n")
        stage_statuses = {
            "mcu_bsl_flashing": "PASSED",
            "ftdi_eeprom_programming": "PASSED",
            "pdc_fw_flashing": "PASSED",
            "host_only_verification": "PASSED",
            "host_dut_verification": "PASSED",
            "label_verified": "PASSED",
        }
        req = maui_manufacturing_pb2.MauiFinalSubmissionRequest(
            serial_number="TEST_MAUI_SERIAL_001",
            operator_id="OP_TEST",
            station_id="STATION_TEST",
            mcu_flashed=True,
            ftdi_programmed=True,
            pdc_flashed=True,
            host_only_passed=True,
            host_dut_passed=True,
            label_verified=True,
            stage_statuses=stage_statuses,
            adb_serial="TEST_MAUI_SERIAL_001",
            ftdi_serial="TEST_MAUI_SERIAL_001",
            scanned_barcode="TEST_MAUI_SERIAL_001",
        )
        resp = self.servicer.submit_final_manufacturing_results(req, self.context)
        self.assertTrue(resp.success)
        summary_path = os.path.join(tmpdir, "final_results.json")
        self.assertTrue(os.path.exists(summary_path))
        with open(summary_path, "r", encoding="utf-8") as f:
          data = json.load(f)
        self.assertEqual(data["overall_status"], "PASS")
        self.assertEqual(data["stage_statuses"]["mcu_bsl_flashing"], "PASSED")
        self.assertEqual(data["endpoints"]["adb_serial"], "TEST_MAUI_SERIAL_001")
        self.assertTrue(resp.mes_tracking_id.startswith("MES-TEST_MAUI_SERIAL_001_"))

  def test_analyze_device_manufacturing_logs_failed_bsl(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      with mock.patch("server.maui_station1_service.get_device_log_dir", return_value=tmpdir):
        # host_only_tests.log was written by Card 1 (USB check passed)
        with open(os.path.join(tmpdir, "host_only_tests.log"), "w", encoding="utf-8") as f:
          f.write("ALL_PASSED=True\nUSB: Hub2=True\n")
        # But BSL button failed
        with open(os.path.join(tmpdir, "bsl_button_test.log"), "w", encoding="utf-8") as f:
          f.write("ALL_PASSED=False\nRESULT: FAILED\nPeak ADC: 0 mV\n")

        statuses, overall, infos, count = analyze_device_manufacturing_logs("TEST_MAUI_SERIAL_001")
        self.assertEqual(statuses["host_only_verification"], "FAILED")
        self.assertEqual(overall, "FAIL")


class TestMauiConsoleTester(unittest.TestCase):
  """Tests for MauiConsoleTester framework."""

  def test_regex_match_test_case(self):
    tc = RegexMatchTestCase(
        name="MCU Version Check",
        command="version",
        pattern=r"Maui version\s+[\w.-]+",
        description="Version test",
    )
    passed, msg = tc.evaluate("maui$ version\r\nMaui version 0.47-f010696\r\n")
    self.assertTrue(passed)

    failed, msg2 = tc.evaluate("maui$ version\r\nUnknown command\r\n")
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


class TestMauiStation2Servicer(unittest.TestCase):
  """Tests for MauiStation2Servicer."""

  def setUp(self):
    self.servicer = MauiStation2Servicer()
    self.context = mock.MagicMock()

  @mock.patch("os.path.exists", return_value=True)
  @mock.patch("server.maui_station2_service.open_serial_port")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch(
      "server.maui_common.scan_usb_devices",
      return_value=[
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/1", vid=0x0BDA, pid=0x5411),
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/2", vid=0x0BDA, pid=0x0411),
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/3", vid=0x0403, pid=0x6002),
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/4", vid=0x18D1, pid=0x506D),
      ],
  )
  def test_run_host_only_tests(self, mock_scan, mock_log, mock_serial, mock_exists):
    mock_ser_instance = mock.MagicMock()
    mock_serial.return_value.__enter__.return_value = mock_ser_instance

    with mock.patch.object(MauiConsoleTester, "send_command") as mock_send:
      mock_send.side_effect = [
          "Maui version 0.47-f010696\r\nmaui$ ",
          "App Config Version: 00000318\r\nFW Version: 00000019\r\nmaui$ ",
          "Plug Present: Yes\r\nVoltage: 20.0 V\r\nMeasured VBUS: 20.0 V (20000 mV)\r\nmaui$ ",
          "[ USB_HOST_CC1] = 895 mV\r\n[ USB_HOST_CC2] = 58 mV\r\n[ PPVAR_USB_CHG_VBUS] = 20000 mV\r\n[ PPVAR_USB_DUT_VBUS] = 20000 mV\r\nmaui$ ",
      ]
      req = maui_manufacturing_pb2.MauiHostOnlyTestRequest(
          serial_number="TEST_MAUI_SERIAL_001",
          device_port="/dev/ttyUSB0",
      )
      resp = self.servicer.run_host_only_tests(req, self.context)
      self.assertTrue(resp.all_passed)
      self.assertTrue(resp.hub_usb2_detected)
      self.assertTrue(resp.ftdi_detected)
      self.assertTrue(resp.host_orientation.startswith("CC1"))
      self.assertEqual(resp.cc1_mv, 895)
      self.assertEqual(resp.cc2_mv, 58)

  @mock.patch("os.path.exists", return_value=True)
  @mock.patch("server.maui_station2_service.open_serial_port")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch("server.util.is_usb_device_present")
  def test_run_host_dut_tests(self, mock_usb, mock_log, mock_serial, mock_exists):
    mock_usb.return_value = True
    mock_ser_instance = mock.MagicMock()
    mock_serial.return_value.__enter__.return_value = mock_ser_instance

    with mock.patch(
        "server.maui_common.scan_usb_devices",
        return_value=[
            UsbDeviceInfo(
                sys_path="/sys/bus/usb/devices/1-1",
                vid=0x18D1,
                pid=0x506D,
                serial="TEST_MAUI_SERIAL_001",
                container_id="cont123",
                speed="5000",
                product="ADB v1",
            )
        ],
    ), mock.patch.object(
        self.servicer,
        "_find_ccd_serial_nodes",
        return_value={"ap": "/dev/ttyUSB1", "ec": "/dev/ttyUSB2", "gsc": "/dev/ttyUSB3"},
    ), mock.patch.object(MauiConsoleTester, "query_ccd_console", return_value=(True, "AP v1.0")):
      req = maui_manufacturing_pb2.MauiHostDutTestRequest(
          serial_number="TEST_MAUI_SERIAL_001",
          expected_serial="TEST_MAUI_SERIAL_001",
          stage="stage1_with_charger",
      )
      with mock.patch.object(MauiConsoleTester, "send_command") as mock_send:
        mock_send.side_effect = [
            "Plug Present: Yes\r\nOrientation: Normal (CC1)\r\nVoltage: 20.0 V\r\nOp Current: 5.0 A\r\nMeasured VBUS: 20.0 V (20000 mV)\r\nmaui$ ",
            "Plug Present: Yes\r\nVoltage: 20.0 V\r\nOp Current: 5.0 A\r\nMeasured VBUS: 20.0 V (20000 mV)\r\nmaui$ ",
            "[ SBU1_DET] = 3300 mV\r\n[ SBU2_DET] = 0 mV\r\n[ PPVAR_USB_CHG_VBUS] = 20000 mV\r\n[ PPVAR_USB_DUT_VBUS] = 20000 mV\r\nmaui$ ",
        ]
        resp = self.servicer.run_host_dut_tests(req, self.context)
        self.assertTrue(resp.all_passed)
        self.assertTrue(resp.h2h_speed_5gbps)
        self.assertTrue(resp.h2h_serial_matched)

  @mock.patch("os.path.exists", return_value=True)
  @mock.patch("server.maui_station2_service.open_serial_port")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch("server.util.is_usb_device_present")
  def test_run_host_dut_stage1_fails_without_charger(self, mock_usb, mock_log, mock_serial, mock_exists):
    mock_usb.return_value = True
    mock_ser_instance = mock.MagicMock()
    mock_serial.return_value.__enter__.return_value = mock_ser_instance

    with mock.patch(
        "server.maui_common.scan_usb_devices",
        return_value=[
            UsbDeviceInfo(
                sys_path="/sys/bus/usb/devices/1-1",
                vid=0x18D1,
                pid=0x506D,
                serial="TEST_MAUI_SERIAL_001",
                speed="5000",
            )
        ],
    ), mock.patch.object(
        self.servicer,
        "_find_ccd_serial_nodes",
        return_value={"ap": "/dev/ttyUSB1", "ec": "/dev/ttyUSB2", "gsc": "/dev/ttyUSB3"},
    ), mock.patch.object(MauiConsoleTester, "query_ccd_console", return_value=(True, "AP v1.0")):
      req = maui_manufacturing_pb2.MauiHostDutTestRequest(
          serial_number="TEST_MAUI_SERIAL_001",
          expected_serial="TEST_MAUI_SERIAL_001",
          stage="stage1_with_charger",
      )
      with mock.patch.object(MauiConsoleTester, "send_command") as mock_send:
        # DUT is 5V (no charger), and Charger port is Unplugged
        mock_send.side_effect = [
            "Plug Present: Yes\r\nVoltage: 5.0 V\r\nMeasured VBUS: 5.0 V\r\nmaui$ ",
            "Plug Present: No\r\nConnection State: 0\r\nActive Contract:  None\r\nmaui$ ",
            "[ SBU1_DET] = 3300 mV\r\n[ SBU2_DET] = 0 mV\r\n[ PPVAR_USB_CHG_VBUS] = 0 mV\r\n[ PPVAR_USB_DUT_VBUS] = 5000 mV\r\nmaui$ ",
        ]
        resp = self.servicer.run_host_dut_tests(req, self.context)
        self.assertFalse(resp.all_passed)
        self.assertIn("FAIL", resp.summary)

  @mock.patch("os.path.exists", return_value=True)
  @mock.patch("server.maui_station2_service.open_serial_port")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch("server.util.is_usb_device_present")
  def test_run_host_dut_stage3_charger_disconnected_passes(self, mock_usb, mock_log, mock_serial, mock_exists):
    mock_usb.return_value = True
    mock_ser_instance = mock.MagicMock()
    mock_serial.return_value.__enter__.return_value = mock_ser_instance

    with mock.patch(
        "server.maui_common.scan_usb_devices",
        return_value=[
            UsbDeviceInfo(
                sys_path="/sys/bus/usb/devices/1-1",
                vid=0x18D1,
                pid=0x506D,
                serial="TEST_MAUI_SERIAL_001",
                speed="5000",
            )
        ],
    ), mock.patch.object(
        self.servicer,
        "_find_ccd_serial_nodes",
        return_value={"ap": "/dev/ttyUSB1", "ec": "/dev/ttyUSB2", "gsc": "/dev/ttyUSB3"},
    ), mock.patch.object(MauiConsoleTester, "query_ccd_console", return_value=(True, "AP v1.0")):
      req = maui_manufacturing_pb2.MauiHostDutTestRequest(
          serial_number="TEST_MAUI_SERIAL_001",
          expected_serial="TEST_MAUI_SERIAL_001",
          stage="stage3_no_charger",
      )
      with mock.patch.object(MauiConsoleTester, "send_command") as mock_send:
        # DUT is attached in battery sink mode (4.8V), Charger is disconnected (4.331V leakage, Plug No, Contract None)
        mock_send.side_effect = [
            "Plug Present: Yes\r\nOrientation: Normal (CC1)\r\nVoltage: 5.0 V\r\nOp Current: 0.50 A\r\nMeasured VBUS: 4.807 V (4807 mV)\r\nmaui$ ",
            "Plug Present:     No\r\n  Active Contract:  None\r\n  Measured VBUS:    4.331 V (4331 mV)\r\nmaui$ ",
            "[ SBU1_DET] = 3299 mV\r\n[ SBU2_DET] = 0 mV\r\n[ PPVAR_USB_CHG_VBUS] = 4331 mV\r\n[ PPVAR_USB_DUT_VBUS] = 4797 mV\r\nmaui$ ",
        ]
        resp = self.servicer.run_host_dut_tests(req, self.context)
        self.assertTrue(resp.all_passed)
        self.assertIn("Charger Disconnected: PASS", resp.summary)

  def test_analyze_device_manufacturing_logs_mcu_warning_timeout_still_passed(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      with mock.patch("server.maui_station1_service.get_device_log_dir", return_value=tmpdir):
        sample_log = """[2026-08-24 19:50:35]
INFO: Bypassing discovery, using explicitly specified port: /dev/ttyUSB0
INFO: Synchronizing shell (waiting for quiet)...
WARNING: Timeout while waiting for prompt. Buffer empty.
INFO: Starting MCU firmware update from /tmp/maui_mcu___q8hxu7.txt
100 %
INFO     main.rs:179 Firmware started
INFO     main.rs:279 Completed successfully
INFO: MCU firmware update successful."""
        with open(os.path.join(tmpdir, "mcu_flashing.log"), "w", encoding="utf-8") as f:
          f.write(sample_log)

        statuses, overall, infos, count = analyze_device_manufacturing_logs("TEST_MAUI_SERIAL_001")
        self.assertEqual(statuses["mcu_bsl_flashing"], "PASSED")

  @mock.patch("server.maui_console_tester.open_serial_port")
  def test_read_adc_sbu_orientation(self, mock_open_serial):
    tester = MauiConsoleTester("/dev/ttyUSB0")
    sample_adc_sbu1 = """maui$ adc status
ADC values:
[        USB_HOST_CC1] =  457 mV
[        USB_HOST_CC2] =  144 mV
[         TEMP_SENSOR] =  607 mV
[            SBU1_DET] = 3299 mV
[            SBU2_DET] =    0 mV
[                 BSL] =    1 mV
[  PPVAR_USB_CHG_VBUS] = 20645 mV
[  PPVAR_USB_DUT_VBUS] = 20627 mV
[PP5000_USB_HOST_VBUS] = 2558 mV
[              PP5000] = 2551 mV
maui$ """
    sample_adc_sbu2 = """maui$ adc status
ADC values:
[        USB_HOST_CC1] =  144 mV
[        USB_HOST_CC2] =  457 mV
[         TEMP_SENSOR] =  607 mV
[            SBU1_DET] =    0 mV
[            SBU2_DET] = 3299 mV
[                 BSL] =    1 mV
maui$ """
    with mock.patch.object(MauiConsoleTester, "send_command", return_value=sample_adc_sbu1):
      ori_str, sbu1, sbu2, _ = tester.read_adc_sbu_orientation()
      self.assertTrue(ori_str.startswith("SBU1"))
      self.assertEqual(sbu1, 3299)
      self.assertEqual(sbu2, 0)

    with mock.patch.object(MauiConsoleTester, "send_command", return_value=sample_adc_sbu2):
      ori_str, sbu1, sbu2, _ = tester.read_adc_sbu_orientation()
      self.assertTrue(ori_str.startswith("SBU2"))
      self.assertEqual(sbu1, 0)
      self.assertEqual(sbu2, 3299)

  @mock.patch("time.sleep")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch("server.maui_console_tester.MauiConsoleTester.read_kernel_uptime")
  def test_reset_button_baseline(self, mock_uptime, mock_log, mock_sleep):
    mock_uptime.return_value = (15420, "Kernel uptime: 15420 ms")
    req = maui_manufacturing_pb2.MauiResetButtonTestRequest(
        serial_number="TEST_MAUI_SERIAL_001",
        device_port="/dev/ttyUSB0",
        action="check_initial_uptime",
    )
    resp = self.servicer.test_reset_button(req, self.context)
    self.assertTrue(resp.success)
    self.assertTrue(resp.uptime_sufficient)
    self.assertEqual(resp.current_uptime_ms, 15420)

  @mock.patch("time.sleep")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch("server.maui_console_tester.MauiConsoleTester.read_kernel_uptime")
  def test_reset_button_post_reset_verification(self, mock_uptime, mock_log, mock_sleep):
    mock_uptime.return_value = (1200, "Kernel uptime: 1200 ms")
    req = maui_manufacturing_pb2.MauiResetButtonTestRequest(
        serial_number="TEST_MAUI_SERIAL_001",
        device_port="/dev/ttyUSB0",
        action="verify_post_reset_uptime",
        initial_uptime_ms=15420,
    )
    resp = self.servicer.test_reset_button(req, self.context)
    self.assertTrue(resp.success)
    self.assertTrue(resp.reset_verified)
    self.assertEqual(resp.current_uptime_ms, 1200)

  @mock.patch("server.maui_console_tester.open_serial_port")
  def test_read_adc_host_orientation(self, mock_open_serial):
    tester = MauiConsoleTester("/dev/ttyUSB0")
    sample_adc_normal = """maui$ adc status
ADC values:
[        USB_HOST_CC1] =  895 mV
[        USB_HOST_CC2] =   58 mV
[         TEMP_SENSOR] =  596 mV
[            SBU1_DET] =    0 mV
[            SBU2_DET] =    0 mV
[                 BSL] =    1 mV
[  PPVAR_USB_CHG_VBUS] = 5105 mV
[  PPVAR_USB_DUT_VBUS] =    0 mV
[PP5000_USB_HOST_VBUS] = 2609 mV
[              PP5000] = 2599 mV
maui$ """
    sample_adc_flipped = """maui$ adc status
ADC values:
[        USB_HOST_CC1] =   61 mV
[        USB_HOST_CC2] =  938 mV
[         TEMP_SENSOR] =  621 mV
[            SBU1_DET] =    0 mV
[            SBU2_DET] =    0 mV
[                 BSL] =    2 mV
[  PPVAR_USB_CHG_VBUS] = 5283 mV
[  PPVAR_USB_DUT_VBUS] =    9 mV
[PP5000_USB_HOST_VBUS] = 2713 mV
[              PP5000] = 2706 mV
maui$ """
    with mock.patch.object(MauiConsoleTester, "send_command", return_value=sample_adc_normal):
      ori_str, cc1, cc2, _ = tester.read_adc_host_orientation()
      self.assertTrue(ori_str.startswith("CC1"))
      self.assertEqual(cc1, 895)
      self.assertEqual(cc2, 58)

    with mock.patch.object(MauiConsoleTester, "send_command", return_value=sample_adc_flipped):
      ori_str, cc1, cc2, _ = tester.read_adc_host_orientation()
      self.assertTrue(ori_str.startswith("CC2"))
      self.assertEqual(cc1, 61)
      self.assertEqual(cc2, 938)

  def test_is_host_cc_flipped(self):
    self.assertTrue(is_host_cc_flipped("CC1 (895mV / 58mV)", "CC2 (61mV / 938mV)"))
    self.assertTrue(is_host_cc_flipped("CC2 (61mV / 938mV)", "CC1 (895mV / 58mV)"))
    self.assertFalse(is_host_cc_flipped("CC1 (895mV / 58mV)", "CC1 (895mV / 58mV)"))
    self.assertFalse(is_host_cc_flipped("Unknown", "CC2"))
    self.assertFalse(is_host_cc_flipped("CC1", "Unknown"))

  @mock.patch("os.path.exists", return_value=True)
  @mock.patch("server.maui_station2_service.open_serial_port")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch(
      "server.maui_common.scan_usb_devices",
      return_value=[
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/1", vid=0x0BDA, pid=0x5411),
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/2", vid=0x0BDA, pid=0x0411),
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/3", vid=0x0403, pid=0x6002),
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/4", vid=0x18D1, pid=0x506D),
      ],
  )
  def test_host_replug_flip_success(self, mock_scan, mock_log, mock_serial, mock_exists):
    mock_ser_instance = mock.MagicMock()
    mock_serial.return_value.__enter__.return_value = mock_ser_instance

    sample_adc_flipped = """maui$ adc status
ADC values:
[        USB_HOST_CC1] =   61 mV
[        USB_HOST_CC2] =  938 mV
[         TEMP_SENSOR] =  621 mV
[  PPVAR_USB_CHG_VBUS] = 5283 mV
[PP5000_USB_HOST_VBUS] = 2713 mV
maui$ """
    with mock.patch.object(MauiConsoleTester, "send_command") as mock_send:
      mock_send.side_effect = [
          "Maui version 0.47-f010696\r\nmaui$ ",
          "App Config Version: 00000318\r\nFW Version: 00000019\r\nmaui$ ",
          sample_adc_flipped,
      ]
      req = maui_manufacturing_pb2.MauiHostReplugTestRequest(
          serial_number="TEST_MAUI_SERIAL_001",
          device_port="/dev/ttyUSB0",
          previous_orientation="CC1 (895mV / 58mV)",
      )
      resp = self.servicer.test_host_replug(req, self.context)
      self.assertTrue(resp.all_passed)
      self.assertTrue(resp.host_flip_ok)
      self.assertTrue(resp.host_orientation.startswith("CC2"))
      self.assertEqual(resp.cc1_mv, 61)
      self.assertEqual(resp.cc2_mv, 938)
      self.assertIn("PASS (CC1", resp.message)

  @mock.patch("os.path.exists", return_value=True)
  @mock.patch("server.maui_station2_service.open_serial_port")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch(
      "server.maui_common.scan_usb_devices",
      return_value=[
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/1", vid=0x0BDA, pid=0x5411),
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/2", vid=0x0BDA, pid=0x0411),
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/3", vid=0x0403, pid=0x6002),
          UsbDeviceInfo(sys_path="/sys/bus/usb/devices/4", vid=0x18D1, pid=0x506D),
      ],
  )
  def test_host_replug_flip_fails_when_unflipped(self, mock_scan, mock_log, mock_serial, mock_exists):
    mock_ser_instance = mock.MagicMock()
    mock_serial.return_value.__enter__.return_value = mock_ser_instance

    sample_adc_unflipped = """maui$ adc status
ADC values:
[        USB_HOST_CC1] =  895 mV
[        USB_HOST_CC2] =   58 mV
[         TEMP_SENSOR] =  596 mV
[  PPVAR_USB_CHG_VBUS] = 5105 mV
[PP5000_USB_HOST_VBUS] = 2609 mV
maui$ """
    with mock.patch.object(MauiConsoleTester, "send_command") as mock_send:
      mock_send.side_effect = [
          "Maui version 0.47-f010696\r\nmaui$ ",
          "App Config Version: 00000318\r\nFW Version: 00000019\r\nmaui$ ",
          sample_adc_unflipped,
      ]
      req = maui_manufacturing_pb2.MauiHostReplugTestRequest(
          serial_number="TEST_MAUI_SERIAL_001",
          device_port="/dev/ttyUSB0",
          previous_orientation="CC1 (895mV / 58mV)",
      )
      resp = self.servicer.test_host_replug(req, self.context)
      self.assertFalse(resp.all_passed)
      self.assertFalse(resp.host_flip_ok)
      self.assertTrue(resp.host_orientation.startswith("CC1"))
      self.assertIn("FAIL (Host cable polarity not inverted", resp.message)


if __name__ == "__main__":
  unittest.main()

