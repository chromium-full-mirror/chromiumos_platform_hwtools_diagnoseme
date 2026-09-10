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
    extract_container_id_from_bos_file,
    find_offline_fw_file,
    get_device_log_dir,
    write_device_log,
)
from server.maui_console_tester import (
    EXPECTED_HOST_GPIOS,
    MauiConsoleTester,
    PdcInfoTestCase,
    RegexMatchTestCase,
    verify_host_adc_voltages,
)

SAMPLE_HWREV_OUTPUT = "Hardware revision: 2\r\nmaui$ "
SAMPLE_TEMP_OUTPUT = "Temperature = 43.26 'C\r\nmaui$ "
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


class TestMauiStation1Servicer(unittest.IsolatedAsyncioTestCase):
  """Tests for MauiStation1Servicer."""

  def setUp(self):
    self.servicer = MauiStation1Servicer()
    self.context = mock.MagicMock()
    self.context.done.return_value = False

  @mock.patch.object(MauiStation1Servicer, "_scan_ftdi_devices")
  async def test_stream_bsl_auto_discovery_multiple_devices(self, mock_scan):
    mock_scan.return_value = [
        {"port": "/dev/ttyUSB0", "vid": 0x0403, "pid": 0x6001, "serial": "DEV0", "container_id": "c1"},
        {"port": "/dev/ttyUSB1", "vid": 0x0403, "pid": 0x6001, "serial": "DEV1", "container_id": "c2"},
    ]
    # Simulate context.done after 1 iteration
    self.context.done.side_effect = [False, False, True]

    responses = []
    async for resp in self.servicer.stream_bsl_auto_discovery(mock.MagicMock(), self.context):
      responses.append(resp)
      if len(responses) >= 2:
        break

    self.assertEqual(len(responses), 2)
    self.assertEqual(responses[0].device_port, "/dev/ttyUSB0")
    self.assertEqual(responses[0].pid, 0x6001)
    self.assertTrue(responses[0].auto_flash_eligible)

    self.assertEqual(responses[1].device_port, "/dev/ttyUSB1")
    self.assertEqual(responses[1].pid, 0x6001)
    self.assertTrue(responses[1].auto_flash_eligible)

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
    # Ensure offline file path was passed in command
    called_cmd = mock_run.call_args[0][0]
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

  @mock.patch("server.maui_station1_service.write_device_log")
  @mock.patch("subprocess.run")
  def test_program_ftdi_eeprom_with_old_serial(self, mock_run, mock_log):
    mock_run.return_value = mock.Mock(
        stdout="Programming successful! New PID: 0x6002\n",
        returncode=0,
    )
    req = maui_manufacturing_pb2.MauiProgramFtdiRequest(
        serial_number="TEST_MAUI_SERIAL_001",
        ftdi_dev_node="/dev/ttyUSB0",
        old_serial_number="A50285BI",
    )
    resp = self.servicer.program_ftdi_eeprom(req, self.context)
    self.assertTrue(resp.success)
    called_cmd = mock_run.call_args[0][0]
    self.assertIn("--old-serial-number", called_cmd)
    self.assertIn("A50285BI", called_cmd)
    self.assertIn("--new-pid", called_cmd)
    self.assertIn("0x6002", called_cmd)
    self.assertIn("--new-serial-number", called_cmd)
    self.assertIn("TEST_MAUI_SERIAL_001", called_cmd)

  @mock.patch.object(MauiStation1Servicer, "_find_ftdi_info_for_port", return_value=("A50285BI", 0x6001, "cont1", "MAUI_ADB_123"))
  def test_discover_topology_and_serial_targeted_port(self, mock_info):
    req = maui_manufacturing_pb2.MauiTopologyDiscoveryRequest(
        device_port="/dev/ttyUSB0",
    )
    resp = self.servicer.discover_topology_and_serial(req, self.context)
    self.assertEqual(resp.read_serial_number, "MAUI_ADB_123")
    self.assertEqual(resp.ftdi_dev_node, "/dev/ttyUSB0")
    self.assertEqual(resp.ftdi_current_serial, "A50285BI")
    self.assertEqual(resp.ftdi_current_pid, 0x6001)

  @mock.patch("glob.glob", return_value=["/dev/ttyUSB0", "/dev/ttyUSB1"])
  def test_discover_topology_and_serial_ftdi_relocation(self, mock_glob):
    def side_effect(port, **kwargs):
      if port == "/dev/ttyUSB0":
        return ("OTHER_SERIAL", 0x6001, "cont0", None)
      if port == "/dev/ttyUSB1":
        return ("A50285BI", 0x6001, "cont1", "MAUI_ADB_123")
      return (None, 0, None, None)

    with mock.patch.object(self.servicer, "_find_ftdi_info_for_port", side_effect=side_effect):
      req = maui_manufacturing_pb2.MauiTopologyDiscoveryRequest(
          device_port="/dev/ttyUSB0",
          ftdi_serial="A50285BI",
      )
      resp = self.servicer.discover_topology_and_serial(req, self.context)
      self.assertTrue(resp.success)
      self.assertEqual(resp.ftdi_dev_node, "/dev/ttyUSB1")
      self.assertEqual(resp.read_serial_number, "MAUI_ADB_123")
      self.assertEqual(resp.ftdi_current_serial, "A50285BI")

  @mock.patch.object(MauiStation1Servicer, "_find_ftdi_info_for_port", return_value=("A50285BI", 0x6001, "cont1", None))
  def test_discover_topology_and_serial_settling_without_adb(self, mock_info):
    req = maui_manufacturing_pb2.MauiTopologyDiscoveryRequest(
        device_port="/dev/ttyUSB0",
        ftdi_serial="A50285BI",
    )
    with mock.patch("time.sleep"):
      resp = self.servicer.discover_topology_and_serial(req, self.context)
    self.assertTrue(resp.success)
    self.assertEqual(resp.ftdi_dev_node, "/dev/ttyUSB0")
    self.assertEqual(resp.ftdi_current_serial, "A50285BI")
    self.assertEqual(resp.read_serial_number, "")
    self.assertIn("awaiting ADB sibling enumeration", resp.error_message)

  def test_match_ftdi_to_adb_superspeed_split_bus(self):
    # Simulates real topology from local test setup with 3 connected Maui boards
    adb_candidates = [
        {"dir": "/sys/devices/pci/usb4/4-2/4-2.1/4-2.1.3", "name": "4-2.1.3", "serial": "MAUIV11G26060059", "devpath": "2.1.3", "container_id": ""},
        {"dir": "/sys/devices/pci/usb4/4-2/4-2.2/4-2.2.3", "name": "4-2.2.3", "serial": "MAUIV11G26060068", "devpath": "2.2.3", "container_id": ""},
        {"dir": "/sys/devices/pci/usb3/3-6/3-6.4/3-6.4.3", "name": "3-6.4.3", "serial": "MAUIV11G26060056", "devpath": "6.4.3", "container_id": ""},
    ]

    # Slot 1: FTDI at 3-6.1.2 (devpath 6.1.2) -> matches MAUIV11G26060059 (4-2.1.3)
    match1 = self.servicer._match_ftdi_to_adb(
        ftdi_dir="/sys/devices/pci/usb3/3-6/3-6.1/3-6.1.2",
        ftdi_name="3-6.1.2",
        ftdi_devpath="6.1.2",
        ftdi_container_id="",
        adb_candidates=adb_candidates,
    )
    self.assertEqual(match1, "MAUIV11G26060059")

    # Slot 2: FTDI at 3-6.2.2 (devpath 6.2.2) -> matches MAUIV11G26060068 (4-2.2.3)
    match2 = self.servicer._match_ftdi_to_adb(
        ftdi_dir="/sys/devices/pci/usb3/3-6/3-6.2/3-6.2.2",
        ftdi_name="3-6.2.2",
        ftdi_devpath="6.2.2",
        ftdi_container_id="",
        adb_candidates=adb_candidates,
    )
    self.assertEqual(match2, "MAUIV11G26060068")

    # Slot 3 (external port 4): FTDI at 3-6.4.2 (devpath 6.4.2) -> matches MAUIV11G26060056 (3-6.4.3)
    match3 = self.servicer._match_ftdi_to_adb(
        ftdi_dir="/sys/devices/pci/usb3/3-6/3-6.4/3-6.4.2",
        ftdi_name="3-6.4.2",
        ftdi_devpath="6.4.2",
        ftdi_container_id="",
        adb_candidates=adb_candidates,
    )
    self.assertEqual(match3, "MAUIV11G26060056")

  def test_match_ftdi_to_adb_arbitrary_host_buses_and_chained_hubs(self):
    # Host setup with Bus 1 (USB 2.0) and Bus 2 (USB 3.0), through chained multi-tier hubs:
    # Host -> Hub Tier 1 (Port 4) -> Hub Tier 2 (Port 1) -> Maui Board
    adb_candidates = [
        {"dir": "/sys/devices/pci/usb2/2-1/2-1.4.1/2-1.4.1.3", "name": "2-1.4.1.3", "serial": "MAUI_CHAINED_001", "devpath": "1.4.1.3", "container_id": ""},
        {"dir": "/sys/devices/pci/usb2/2-1/2-1.4.2/2-1.4.2.3", "name": "2-1.4.2.3", "serial": "MAUI_CHAINED_002", "devpath": "1.4.2.3", "container_id": ""},
    ]

    # Slot 1 on chained hub: FTDI at 1-3.4.1.2 (devpath 3.4.1.2)
    match1 = self.servicer._match_ftdi_to_adb(
        ftdi_dir="/sys/devices/pci/usb1/1-3/1-3.4.1/1-3.4.1.2",
        ftdi_name="1-3.4.1.2",
        ftdi_devpath="3.4.1.2",
        ftdi_container_id="",
        adb_candidates=adb_candidates,
    )
    self.assertEqual(match1, "MAUI_CHAINED_001")

    # Slot 2 on chained hub: FTDI at 1-3.4.2.2 (devpath 3.4.2.2)
    match2 = self.servicer._match_ftdi_to_adb(
        ftdi_dir="/sys/devices/pci/usb1/1-3/1-3.4.2/1-3.4.2.2",
        ftdi_name="1-3.4.2.2",
        ftdi_devpath="3.4.2.2",
        ftdi_container_id="",
        adb_candidates=adb_candidates,
    )
    self.assertEqual(match2, "MAUI_CHAINED_002")

  def test_match_ftdi_to_adb_multiple_separate_external_hubs(self):
    # Two separate external hubs plugged into two distinct host ports:
    # Hub A on Host Port 1 -> Maui board on Hub A Port 2
    # Hub B on Host Port 2 -> Maui board on Hub B Port 2
    adb_candidates = [
        {"dir": "/sys/devices/pci/usb2/2-1/2-1.2/2-1.2.3", "name": "2-1.2.3", "serial": "MAUI_HUB_A", "devpath": "1.2.3", "container_id": ""},
        {"dir": "/sys/devices/pci/usb2/2-2/2-2.2/2-2.2.3", "name": "2-2.2.3", "serial": "MAUI_HUB_B", "devpath": "2.2.3", "container_id": ""},
    ]

    # FTDI on Hub A (Port 2): 1-1.2.2 (devpath 1.2.2)
    match_a = self.servicer._match_ftdi_to_adb(
        ftdi_dir="/sys/devices/pci/usb1/1-1/1-1.2/1-1.2.2",
        ftdi_name="1-1.2.2",
        ftdi_devpath="1.2.2",
        ftdi_container_id="",
        adb_candidates=adb_candidates,
    )
    self.assertEqual(match_a, "MAUI_HUB_A")

    # FTDI on Hub B (Port 2): 1-2.2.2 (devpath 2.2.2)
    match_b = self.servicer._match_ftdi_to_adb(
        ftdi_dir="/sys/devices/pci/usb1/1-2/1-2.2/1-2.2.2",
        ftdi_name="1-2.2.2",
        ftdi_devpath="2.2.2",
        ftdi_container_id="",
        adb_candidates=adb_candidates,
    )
    self.assertEqual(match_b, "MAUI_HUB_B")

  def test_match_ftdi_to_adb_direct_connection(self):
    # Directly plugged into host USB-C port (no intermediate external hub)
    adb_candidates = [
        {"dir": "/sys/devices/pci/usb2/2-1/2-1.3", "name": "2-1.3", "serial": "MAUI_DIRECT_001", "devpath": "1.3", "container_id": ""},
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
    # Simulates real multi-controller xHCI topology from factory host:
    # Board 1: FTDI on 3-1.2 (Hub CID: 01890002-0508-6ab4-121a-27c2b6c9d000), ADB on 2-3.3 (Hub CID: 01890002-0508-6ab4-121a-27c2b6c9d000)
    # Board 2: FTDI on 3-6.2 (Hub CID: 01890002-4508-6a4d-121a-27c2b6c9d000), ADB on 4-2.3 (Hub CID: 01890002-4508-6a4d-121a-27c2b6c9d000)
    cid_board1 = "01890002-0508-6ab4-121a-27c2b6c9d000"
    cid_board2 = "01890002-4508-6a4d-121a-27c2b6c9d000"

    adb_candidates = [
        {"dir": "/sys/devices/pci/usb2/2-3/2-3.3", "name": "2-3.3", "serial": "MAUIV11G26060066", "devpath": "3.3", "container_id": "", "hub_container_id": cid_board1},
        {"dir": "/sys/devices/pci/usb4/4-2/4-2.3", "name": "4-2.3", "serial": "MAUIV11G26060056", "devpath": "2.3", "container_id": "", "hub_container_id": cid_board2},
    ]

    # Slot 1: /dev/ttyUSB0 (3-1.2) must strictly match Board 1 (MAUIV11G26060066)
    match1 = self.servicer._match_ftdi_to_adb(
        ftdi_dir="/sys/devices/pci/usb3/3-1/3-1.2",
        ftdi_name="3-1.2",
        ftdi_devpath="1.2",
        ftdi_container_id="",
        ftdi_hub_container_id=cid_board1,
        adb_candidates=adb_candidates,
    )
    self.assertEqual(match1, "MAUIV11G26060066")

    # Slot 2: /dev/ttyUSB1 (3-6.2) must strictly match Board 2 (MAUIV11G26060056)
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
    # When Board 2's ADB chip has not enumerated yet, only Board 1's ADB is visible.
    # Board 2's FTDI (/dev/ttyUSB1) must NEVER steal Board 1's serial.
    cid_board1 = "01890002-0508-6ab4-121a-27c2b6c9d000"
    cid_board2 = "01890002-4508-6a4d-121a-27c2b6c9d000"

    only_board1_adb = [
        {"dir": "/sys/devices/pci/usb2/2-3/2-3.3", "name": "2-3.3", "serial": "MAUIV11G26060066", "devpath": "3.3", "container_id": "", "hub_container_id": cid_board1},
    ]

    # Query for Board 2's FTDI (3-6.2) -> must return None!
    match_board2 = self.servicer._match_ftdi_to_adb(
        ftdi_dir="/sys/devices/pci/usb3/3-6/3-6.2",
        ftdi_name="3-6.2",
        ftdi_devpath="6.2",
        ftdi_container_id="",
        ftdi_hub_container_id=cid_board2,
        adb_candidates=only_board1_adb,
    )
    self.assertIsNone(match_board2)

  def test_extract_container_id_from_bos_file(self):
    # Synthetic 25-byte BOS descriptor: 5-byte header + 20-byte ContainerID capability
    uuid_bytes = bytes.fromhex("0189000205086ab4121a27c2b6c9d000")
    bos_header = bytes([5, 15, 25, 0, 1])
    cap_header = bytes([20, 16, 4, 0])
    bos_data = bos_header + cap_header + uuid_bytes

    with tempfile.NamedTemporaryFile() as tmp:
      tmp.write(bos_data)
      tmp.flush()
      cid = extract_container_id_from_bos_file(tmp.name)
      self.assertEqual(cid, "01890002-0508-6ab4-121a-27c2b6c9d000")

    # Invalid / corrupt BOS data
    with tempfile.NamedTemporaryFile() as tmp:
      tmp.write(b"\x00\x01\x02")
      tmp.flush()
      self.assertIsNone(extract_container_id_from_bos_file(tmp.name))

    # Nonexistent file
    self.assertIsNone(extract_container_id_from_bos_file("/nonexistent/path/bos"))

  def test_match_ftdi_to_adb_direct_pc_differing_root_ports_via_hub_cid(self):
    # Simulates direct PC connection where root hub port numbers differ (3-3.2 vs 2-1.3)
    # but the physical on-board TUSB8041 hub shares the same BOS ContainerID.
    cid_board = "01890002-0508-6ab4-121a-27c2b6c9d000"
    adb_candidates = [
        {
            "dir": "/sys/devices/pci/usb2/2-1/2-1.3",
            "name": "2-1.3",
            "serial": "MAUIV11G26060068",
            "devpath": "1.3",
            "container_id": "",
            "hub_container_id": cid_board,
        },
    ]
    matched = self.servicer._match_ftdi_to_adb(
        ftdi_dir="/sys/devices/pci/usb3/3-3/3-3.2",
        ftdi_name="3-3.2",
        ftdi_devpath="3.2",
        ftdi_container_id="",
        ftdi_hub_container_id=cid_board,
        adb_candidates=adb_candidates,
    )
    self.assertEqual(matched, "MAUIV11G26060068")

  def test_match_ftdi_to_adb_direct_pc_differing_root_ports_without_cid(self):
    # Simulates direct PC connection where root hub port numbers differ (3-3.2 vs 2-1.3)
    # and ContainerID is absent, falling back to endpoint topology scoring / single candidate.
    adb_candidates = [
        {
            "dir": "/sys/devices/pci/usb2/2-1/2-1.3",
            "name": "2-1.3",
            "serial": "MAUIV11G26060068",
            "devpath": "1.3",
            "container_id": "",
            "hub_container_id": "",
        },
    ]
    matched = self.servicer._match_ftdi_to_adb(
        ftdi_dir="/sys/devices/pci/usb3/3-3/3-3.2",
        ftdi_name="3-3.2",
        ftdi_devpath="3.2",
        ftdi_container_id="",
        ftdi_hub_container_id="",
        adb_candidates=adb_candidates,
    )
    self.assertEqual(matched, "MAUIV11G26060068")

  def test_parse_lsusb_hub_container_ids(self):
    # Verifies parsing of lsusb -v BOS container ID capability descriptors
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
    with mock.patch("subprocess.run") as mock_run:
      mock_run.return_value = mock.Mock(returncode=0, stdout=fake_lsusb, stderr="")
      with mock.patch("glob.glob", return_value=["/sys/bus/usb/devices/3-1", "/sys/bus/usb/devices/2-3", "/sys/bus/usb/devices/3-6", "/sys/bus/usb/devices/4-2"]):
        with mock.patch("os.path.exists", return_value=True):
          def fake_open(p, *args, **kwargs):
            val = ""
            if "3-1/busnum" in p: val = "3\n"
            elif "3-1/devnum" in p: val = "98\n"
            elif "2-3/busnum" in p: val = "2\n"
            elif "2-3/devnum" in p: val = "91\n"
            elif "3-6/busnum" in p: val = "3\n"
            elif "3-6/devnum" in p: val = "100\n"
            elif "4-2/busnum" in p: val = "4\n"
            elif "4-2/devnum" in p: val = "62\n"
            m = mock.mock_open(read_data=val)()
            return m
          with mock.patch("builtins.open", side_effect=fake_open):
            cids = self.servicer._get_usb_hub_container_ids()
            self.assertEqual(cids.get("3-1"), "01890002-0508-6ab4-121a-27c2b6c9d000")
            self.assertEqual(cids.get("2-3"), "01890002-0508-6ab4-121a-27c2b6c9d000")
            self.assertEqual(cids.get("3-6"), "01890002-4508-6a4d-121a-27c2b6c9d000")
            self.assertEqual(cids.get("4-2"), "01890002-4508-6a4d-121a-27c2b6c9d000")




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

  @mock.patch("server.maui_station1_service.find_offline_fw_file", return_value="/var/lib/diagnoseme/firmware/pdc/v20_0325_100W.bin")
  @mock.patch("asyncio.create_subprocess_exec")
  async def test_program_single_pdc_100w_channel_success(self, mock_exec, mock_find):
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
        fw_channel="v20_0325_100W",
    )
    responses = []
    async for resp in self.servicer.program_single_pdc(req, self.context):
      responses.append(resp)

    self.assertTrue(len(responses) >= 1)
    final_resp = responses[-1]
    self.assertTrue(final_resp.is_complete)
    self.assertEqual(final_resp.current_block, "Complete")

    # Verify that --file was passed with v20_0325_100W.bin
    call_args = mock_exec.call_args[0]
    self.assertIn("--file", call_args)
    self.assertIn("/var/lib/diagnoseme/firmware/pdc/v20_0325_100W.bin", call_args)


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
        req_smt = maui_manufacturing_pb2.MauiFinalSubmissionRequest(
            serial_number="TEST_MAUI_SERIAL_001",
            operator_id="OP_TEST",
            station_id="STATION_SMT_FUNCTIONAL",
            mcu_flashed=True,
            ftdi_programmed=True,
            pdc_flashed=True,
            host_only_passed=True,
            host_dut_passed=True,
            label_verified=False,
            stage_statuses=stage_statuses,
            adb_serial="TEST_MAUI_SERIAL_001",
            ftdi_serial="TEST_MAUI_SERIAL_001",
        )
        resp_smt = self.servicer.submit_final_manufacturing_results(req_smt, self.context)
        self.assertTrue(resp_smt.success)
        self.assertIn("SMT_FUNCTIONAL", resp_smt.mes_tracking_id)
        self.assertIn("_PASS", resp_smt.mes_tracking_id)
        with open(resp_smt.summary_file_path, "r", encoding="utf-8") as f:
          data_smt = json.load(f)
        self.assertEqual(data_smt["phase"], "smt_functional")

        req_fatp = maui_manufacturing_pb2.MauiFinalSubmissionRequest(
            serial_number="TEST_MAUI_SERIAL_001",
            operator_id="OP_TEST",
            station_id="STATION_FATP_QT0",
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
        resp_fatp = self.servicer.submit_final_manufacturing_results(req_fatp, self.context)
        self.assertTrue(resp_fatp.success)
        self.assertIn("FATP_TESTS", resp_fatp.mes_tracking_id)
        with open(resp_fatp.summary_file_path, "r", encoding="utf-8") as f:
          data_fatp = json.load(f)
        self.assertEqual(data_fatp["phase"], "fatp_tests")

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
          "Plug Present: Yes\r\nVoltage: 5.0 V\r\nMeasured VBUS: 5.0 V (5105 mV)\r\nmaui$ ",
          SAMPLE_ADC_OUTPUT,
          SAMPLE_HWREV_OUTPUT,
          SAMPLE_TEMP_OUTPUT,
          SAMPLE_GPIO_OUTPUT,
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
      self.assertEqual(resp.cc1_mv, 885)
      self.assertEqual(resp.cc2_mv, 54)
      self.assertTrue(resp.hwrev_ok)
      self.assertEqual(resp.detected_hwrev, 2)
      self.assertTrue(resp.temp_ok)
      self.assertAlmostEqual(resp.temperature_c, 43.26)
      self.assertTrue(resp.gpios_ok)
      self.assertTrue(resp.voltages_ok)
      self.assertEqual(resp.pp5000_mv, 2609)
      self.assertEqual(resp.host_vbus_mv, 2616)
      self.assertEqual(resp.chg_vbus_mv, 5105)

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

  @mock.patch("os.path.exists", return_value=True)
  @mock.patch("server.maui_station2_service.open_serial_port")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch("server.util.is_usb_device_present")
  def test_run_host_dut_stage1_passes_without_ap_console_response(self, mock_usb, mock_log, mock_serial, mock_exists):
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
    ):
      def fake_query_ccd(node, cmd, timeout=0.5, first_byte_timeout=0.25):
        if node == "/dev/ttyUSB2":
          return True, "EC version: 1.2.3"
        if node == "/dev/ttyUSB3":
          return True, "GSC version: 0.0.1"
        return False, "Timeout"

      with mock.patch.object(MauiConsoleTester, "query_ccd_console", side_effect=fake_query_ccd) as mock_query:
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
          self.assertTrue(resp.ec_uart_ok)
          self.assertTrue(resp.gsc_uart_ok)
          queried_nodes = [c.args[0] for c in mock_query.call_args_list]
          self.assertNotIn("/dev/ttyUSB1", queried_nodes)
          self.assertIn("/dev/ttyUSB2", queried_nodes)
          self.assertIn("/dev/ttyUSB3", queried_nodes)
          self.assertIn("CCD Consoles: EC=PASS, GSC=PASS", resp.summary)

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
  def test_reset_button_baseline_fails_below_7s(self, mock_uptime, mock_log, mock_sleep):
    mock_uptime.return_value = (5000, "Kernel uptime: 5000 ms")
    req = maui_manufacturing_pb2.MauiResetButtonTestRequest(
        serial_number="TEST_MAUI_SERIAL_001",
        device_port="/dev/ttyUSB0",
        action="check_initial_uptime",
    )
    resp = self.servicer.test_reset_button(req, self.context)
    self.assertFalse(resp.success)
    self.assertFalse(resp.uptime_sufficient)
    self.assertEqual(resp.current_uptime_ms, 5000)
    self.assertIn("(< 7000 ms)", resp.message)

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

  @mock.patch("time.sleep")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch("server.maui_console_tester.MauiConsoleTester.read_kernel_uptime")
  def test_reset_button_verify_reboot_action(self, mock_uptime, mock_log, mock_sleep):
    mock_uptime.return_value = (1150, "Kernel uptime: 1150 ms")
    req = maui_manufacturing_pb2.MauiResetButtonTestRequest(
        serial_number="TEST_MAUI_SERIAL_001",
        device_port="/dev/ttyUSB0",
        action="verify_reboot",
        initial_uptime_ms=15420,
    )
    resp = self.servicer.test_reset_button(req, self.context)
    self.assertTrue(resp.success)
    self.assertTrue(resp.reset_verified)
    self.assertEqual(resp.current_uptime_ms, 1150)

  @mock.patch("time.sleep")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch("server.maui_console_tester.MauiConsoleTester.read_kernel_uptime")
  def test_reset_button_post_reset_fails_immediately_when_uptime_exceeds_initial(
      self, mock_uptime, mock_log, mock_sleep
  ):
    # If read uptime is longer than expected (>= initial), fail immediately without polling further
    mock_uptime.return_value = (16000, "Kernel uptime: 16000 ms")
    req = maui_manufacturing_pb2.MauiResetButtonTestRequest(
        serial_number="TEST_MAUI_SERIAL_001",
        device_port="/dev/ttyUSB0",
        action="verify_post_reset_uptime",
        initial_uptime_ms=15420,
    )
    resp = self.servicer.test_reset_button(req, self.context)
    self.assertFalse(resp.success)
    self.assertFalse(resp.reset_verified)
    self.assertEqual(resp.current_uptime_ms, 16000)
    self.assertIn("Board did not reboot", resp.message)
    self.assertEqual(mock_uptime.call_count, 1)

  @mock.patch("time.sleep")
  @mock.patch("server.maui_common.write_device_log")
  @mock.patch("server.maui_console_tester.MauiConsoleTester.read_kernel_uptime")
  def test_reset_button_post_reset_polls_until_successful_read(
      self, mock_uptime, mock_log, mock_sleep
  ):
    # Attempt 1 fails to read (-1), Attempt 2 successfully reads 1200ms -> stops polling
    mock_uptime.side_effect = [(-1, "Read timeout"), (1200, "Kernel uptime: 1200 ms")]
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
    self.assertEqual(mock_uptime.call_count, 2)

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
          SAMPLE_ADC_FLIPPED_OUTPUT,
          SAMPLE_HWREV_OUTPUT,
          SAMPLE_TEMP_OUTPUT,
          SAMPLE_GPIO_OUTPUT,
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
      self.assertTrue(resp.hwrev_ok)
      self.assertEqual(resp.detected_hwrev, 2)
      self.assertTrue(resp.temp_ok)
      self.assertTrue(resp.gpios_ok)
      self.assertTrue(resp.voltages_ok)
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

    with mock.patch.object(MauiConsoleTester, "send_command") as mock_send:
      mock_send.side_effect = [
          "Maui version 0.47-f010696\r\nmaui$ ",
          "App Config Version: 00000318\r\nFW Version: 00000019\r\nmaui$ ",
          SAMPLE_ADC_OUTPUT,
          SAMPLE_HWREV_OUTPUT,
          SAMPLE_TEMP_OUTPUT,
          SAMPLE_GPIO_OUTPUT,
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

  def test_read_hwrev(self):
    tester = MauiConsoleTester(port_node="/dev/ttyUSB0")
    mock_ser = mock.MagicMock()
    with mock.patch.object(MauiConsoleTester, "send_command", return_value=SAMPLE_HWREV_OUTPUT):
      ok, rev, _ = tester.read_hwrev(ser=mock_ser)
      self.assertTrue(ok)
      self.assertEqual(rev, 2)
    with mock.patch.object(MauiConsoleTester, "send_command", return_value="Hardware revision: 1\r\nmaui$ "):
      ok, rev, _ = tester.read_hwrev(ser=mock_ser)
      self.assertFalse(ok)
      self.assertEqual(rev, 1)

  def test_read_temperature(self):
    tester = MauiConsoleTester(port_node="/dev/ttyUSB0")
    mock_ser = mock.MagicMock()
    with mock.patch.object(MauiConsoleTester, "send_command", return_value=SAMPLE_TEMP_OUTPUT):
      ok, temp_c, _ = tester.read_temperature(ser=mock_ser)
      self.assertTrue(ok)
      self.assertAlmostEqual(temp_c, 43.26)
    with mock.patch.object(MauiConsoleTester, "send_command", return_value="Temperature = 15.0 'C\r\nmaui$ "):
      ok, temp_c, _ = tester.read_temperature(ser=mock_ser)
      self.assertFalse(ok)
    with mock.patch.object(MauiConsoleTester, "send_command", return_value="Temperature = 70.0 'C\r\nmaui$ "):
      ok, temp_c, _ = tester.read_temperature(ser=mock_ser)
      self.assertFalse(ok)

  def test_read_and_verify_gpios(self):
    tester = MauiConsoleTester(port_node="/dev/ttyUSB0")
    mock_ser = mock.MagicMock()
    with mock.patch.object(MauiConsoleTester, "send_command", return_value=SAMPLE_GPIO_OUTPUT):
      ok, gpios, mismatches, _ = tester.read_and_verify_gpios(ser=mock_ser)
      self.assertTrue(ok)
      self.assertEqual(len(mismatches), 0)
      self.assertEqual(gpios["h2h_reset_l"], 0)
      self.assertEqual(gpios["hub_ss_up_status"], 1)

    bad_gpio = SAMPLE_GPIO_OUTPUT.replace("[         hub_ss_up_status] I = 1", "[         hub_ss_up_status] I = 0")
    with mock.patch.object(MauiConsoleTester, "send_command", return_value=bad_gpio):
      ok, gpios, mismatches, _ = tester.read_and_verify_gpios(ser=mock_ser)
      self.assertFalse(ok)
      self.assertEqual(len(mismatches), 1)
      self.assertIn("hub_ss_up_status: expected 1, got 0", mismatches[0])

  def test_verify_host_adc_voltages(self):
    adc_valid = {"pp5000_mv": 2500, "host_vbus_mv": 2500, "chg_vbus_mv": 5000}
    ok, mismatches = verify_host_adc_voltages(adc_valid)
    self.assertTrue(ok)
    self.assertEqual(len(mismatches), 0)

    adc_bad_pp5000 = {"pp5000_mv": 2100, "host_vbus_mv": 2500, "chg_vbus_mv": 5000}
    ok, mismatches = verify_host_adc_voltages(adc_bad_pp5000)
    self.assertFalse(ok)
    self.assertIn("PP5000: 2100 mV outside", mismatches[0])

    adc_bad_chg = {"pp5000_mv": 2500, "host_vbus_mv": 2500, "chg_vbus_mv": 4200}
    ok, mismatches = verify_host_adc_voltages(adc_bad_chg)
    self.assertFalse(ok)
    self.assertIn("PPVAR_USB_CHG_VBUS: 4200 mV outside", mismatches[0])


if __name__ == "__main__":
  unittest.main()

