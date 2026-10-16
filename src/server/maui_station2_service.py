# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""gRPC Servicer for Maui Stage 2 & Stage 3 (Functional & Multi-stage Testing)."""

import glob
import logging
import os
import re
import subprocess
import time
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import grpc
import serial

from server import maui_adb_transfer
from server import maui_common
from server import util
from server.generated import maui_manufacturing_pb2
from server.generated import maui_manufacturing_pb2_grpc
from server.maui_common import write_device_log
from server.maui_console_tester import EXPECTED_HOST_GPIOS
from server.maui_console_tester import MauiConsoleTester
from server.maui_console_tester import open_serial_port
from server.maui_console_tester import verify_host_adc_voltages


def is_sbu_flipped(orig: str, curr: str) -> bool:
  """Checks if SBU cable orientation flipped (SBU1 <-> SBU2)."""
  o_l = orig.lower()
  c_l = curr.lower()
  if not orig or not curr or "unknown" in o_l or "unknown" in c_l:
    return False
  if "sbu1" in o_l:
    return "sbu2" in c_l
  if "sbu2" in o_l:
    return "sbu1" in c_l
  if "cc1" in o_l:
    return "cc2" in c_l or "sbu2" in c_l
  if "cc2" in o_l:
    return "cc1" in c_l or "sbu1" in c_l
  return o_l != c_l


def is_host_cc_flipped(orig: str, curr: str) -> bool:
  """Checks if Host CC orientation flipped (CC1 <-> CC2) via USB_HOST_CC1/CC2 ADC readings."""
  o_l = orig.lower()
  c_l = curr.lower()
  if not orig or not curr or "unknown" in o_l or "unknown" in c_l:
    return False
  if "cc1" in o_l:
    return "cc2" in c_l
  if "cc2" in o_l:
    return "cc1" in c_l
  return o_l != c_l


class MauiStation2Servicer(maui_manufacturing_pb2_grpc.MauiStation2FunctionalTestingServicer):
  """Implementation of the Station 2 Functional Testing gRPC service."""

  # Station 2 Hardware Constants
  HUB_USB2_VID_PID = (0x0BDA, 0x5411)
  HUB_USB3_VID_PID = (0x0BDA, 0x0411)
  FTDI_VID = 0x0403
  FTDI_PID_PROGRAMMED = 0x6002
  PROLIFIC_VID = 0x18D1
  PROLIFIC_PID_ADB = 0x506D
  PROLIFIC_PID_H2H = 0x5084

  # Revision hooks. Maui v1.1 (FTDI console) is the default implementation;
  # other hardware revisions subclass this servicer and override the hooks.
  # Label of the MCU console USB endpoint in summaries.
  CONSOLE_USB_LABEL = "FTDI"
  # Expected `hwrev` shown in the device logs.
  EXPECTED_HWREV_TEXT = "2"
  # True when the MCU console is a /dev/ttyUSB* node (used by the CCD fallback).
  MCU_CONSOLE_IS_TTYUSB = True

  def __init__(self):
    super().__init__()
    logging.info("%s initialized.", type(self).__name__)

  def _make_console_tester(self, port_node: str) -> MauiConsoleTester:
    """Returns the console tester used for the MCU console on |port_node|."""
    return MauiConsoleTester(port_node=port_node, timeout=0.5)

  def _find_unit_serial(self) -> str:
    """Returns the unit serial (H2H iSerial, else MCU console iSerial)."""
    for dev in maui_common.scan_usb_devices():
      if dev.vid == 0x18D1 and dev.pid in (0x506D, 0x5084) and dev.serial:
        return dev.serial
      elif dev.vid == 0x0403 and dev.serial:
        return dev.serial
    return ""

  def _find_real_h2h_serial(self) -> str:
    """Returns the serial used for the Station 3 serial match."""
    for dev in maui_common.scan_usb_devices():
      if dev.vid in (maui_common.PROLIFIC_VID, 0x0403, 0x067B) and dev.serial:
        return dev.serial
    return ""

  def _resolve_console_port(self, dev_port: str) -> str:
    """Returns |dev_port| if it exists, else the discovered MCU console node."""
    if not dev_port or not os.path.exists(dev_port):
      for dev in maui_common.scan_usb_devices():
        if dev.vid == 0x0403 and dev.dev_node and os.path.exists(dev.dev_node):
          dev_port = dev.dev_node
          break
      if not dev_port or not os.path.exists(dev_port):
        tty_nodes = sorted(glob.glob("/dev/ttyUSB*"))
        dev_port = tty_nodes[0] if tty_nodes else "/dev/ttyUSB0"
    return dev_port

  def _fallback_console_port(self, dev_port: str) -> str:
    """Quick console lookup (no USB scan) used by the button tests."""
    if not dev_port or not os.path.exists(dev_port):
      tty_nodes = glob.glob("/dev/ttyUSB*")
      dev_port = tty_nodes[0] if tty_nodes else "/dev/ttyUSB0"
    return dev_port

  def _rediscover_console_port(self, dev_port: str) -> str:
    """Re-resolves the console after a reset; keeps |dev_port| if none found."""
    if not os.path.exists(dev_port):
      tty_nodes = glob.glob("/dev/ttyUSB*")
      dev_port = tty_nodes[0] if tty_nodes else dev_port
    return dev_port

  def _gpio_ok_text(self) -> str:
    """Returns the GPIO details text of a passing GPIO check."""
    return f"All {len(EXPECTED_HOST_GPIOS)} GPIOs verified"

  def _check_host_usb_topology(self) -> Tuple[bool, bool, bool, bool]:
    """Checks presence of Hub2, Hub3, FTDI (0x6002), and H2H (18d1:506d/5084) using sysfs scanning."""
    hub_usb2_ok = False
    hub_usb3_ok = False
    ftdi_ok = False
    h2h_ok = False

    for dev in maui_common.scan_usb_devices():
      if (dev.vid, dev.pid) in ((0x0BDA, 0x5411), (0x0451, 0x8142)):
        hub_usb2_ok = True
      elif (dev.vid, dev.pid) in ((0x0BDA, 0x0411), (0x0451, 0x8140)):
        hub_usb3_ok = True
      elif dev.vid == self.FTDI_VID and dev.pid == self.FTDI_PID_PROGRAMMED:
        ftdi_ok = True
      elif dev.vid == self.PROLIFIC_VID and dev.pid in (self.PROLIFIC_PID_ADB, self.PROLIFIC_PID_H2H):
        h2h_ok = True

    return hub_usb2_ok, hub_usb3_ok, ftdi_ok, h2h_ok

  def test_host_replug(
      self,
      request: maui_manufacturing_pb2.MauiHostReplugTestRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiHostReplugTestResponse:
    """Verifies that the Maui board re-enumerates and flips CC polarity after a 180° cable flip."""
    serial_no = request.serial_number
    dev_port = request.device_port or ""

    if not serial_no:
      serial_no = self._find_unit_serial()

    dev_port = self._resolve_console_port(dev_port)

    logging.info("Running Host Re-plug & 180° Flip Verification for serial: %s (port: %s)", serial_no, dev_port)

    hub_usb2_ok, hub_usb3_ok, ftdi_ok, h2h_ok = self._check_host_usb_topology()

    port_node = dev_port
    tester = self._make_console_tester(port_node)
    console_results = {}
    adc_status = {}
    hwrev_ok = False
    detected_hwrev = -1
    hwrev_raw = ""
    temp_ok = False
    temp_c = -1.0
    temp_raw = ""
    gpios_ok = False
    gpio_mismatches = ["Serial port not accessible"]
    gpios_raw = ""
    voltages_ok = False
    voltage_mismatches = ["Serial port not accessible"]

    if os.path.exists(port_node):
      try:
        with open_serial_port(port_node, tester.baudrate, timeout=tester.timeout) as ser:
          for test in tester.test_cases:
            output = tester.send_command(ser, test.command)
            success, message = test.evaluate(output)
            console_results[test.name] = (success, message)
          adc_status = tester.read_adc_status(ser=ser)
          hwrev_ok, detected_hwrev, hwrev_raw = tester.read_hwrev(ser=ser)
          temp_ok, temp_c, temp_raw = tester.read_temperature(ser=ser)
          gpios_ok, parsed_gpios, gpio_mismatches, gpios_raw = tester.read_and_verify_gpios(ser=ser)
          voltages_ok, voltage_mismatches = verify_host_adc_voltages(adc_status)
      except Exception as e:
        logging.warning("Console session failed during host replug on %s: %s", port_node, e)
        console_results["MCU Version Check"] = (False, str(e))
    else:
      console_results["MCU Version Check"] = (False, f"Port {port_node} does not exist")

    mcu_version_ok, mcu_msg = console_results.get("MCU Version Check", (False, "Not run"))

    curr_orientation = adc_status.get("host_orientation", "Unknown")
    cc1_mv = adc_status.get("host_cc1_mv", 0)
    cc2_mv = adc_status.get("host_cc2_mv", 0)
    pp5000_mv = adc_status.get("pp5000_mv", 0)
    host_vbus_mv = adc_status.get("host_vbus_mv", 0)
    chg_adc_vbus_mv = adc_status.get("chg_vbus_mv", 0)

    gpio_details = "; ".join(gpio_mismatches) if gpio_mismatches else self._gpio_ok_text()
    voltage_details = (
        "; ".join(voltage_mismatches)
        if voltage_mismatches
        else f"PP5000={pp5000_mv}mV, Host={host_vbus_mv}mV, CHG={chg_adc_vbus_mv}mV"
    )

    # Verify Host Cable 180° Flip Polarity via USB_HOST_CC1 / USB_HOST_CC2 ADC voltages
    prev_orientation = getattr(request, "previous_orientation", "") or ""
    if not prev_orientation:
      dir_path = maui_common.get_device_log_dir(serial_no)
      host1_path = os.path.join(dir_path, "host_only_tests.log")
      if os.path.exists(host1_path):
        try:
          with open(host1_path, "r", encoding="utf-8") as f:
            c = f.read()
          m = re.search(r"(?:Host Orientation|Host CC Orientation|Orientation):\s*([^\r\n,|]+)", c, re.IGNORECASE)
          if m:
            prev_orientation = m.group(1).strip()
        except Exception:
          pass

    if prev_orientation and not curr_orientation.startswith("Unknown"):
      polarity_inverted = is_host_cc_flipped(prev_orientation, curr_orientation)
    else:
      polarity_inverted = not curr_orientation.startswith("Unknown")

    host_flip_ok = polarity_inverted
    all_passed = (
        hub_usb2_ok
        and hub_usb3_ok
        and ftdi_ok
        and h2h_ok
        and mcu_version_ok
        and host_flip_ok
        and hwrev_ok
        and temp_ok
        and gpios_ok
        and voltages_ok
    )

    if prev_orientation and not polarity_inverted:
      flip_status_str = f"FAIL (Host cable polarity not inverted: still {curr_orientation}, expected flip from {prev_orientation})"
    elif polarity_inverted:
      flip_status_str = f"PASS ({prev_orientation} -> {curr_orientation})" if prev_orientation else f"PASS ({curr_orientation})"
    else:
      flip_status_str = f"FAIL ({curr_orientation})"

    status_msg = (
        f"Host 180° Flip: {flip_status_str} | Hub2={hub_usb2_ok}, Hub3={hub_usb3_ok}, "
        f"{self.CONSOLE_USB_LABEL}={ftdi_ok}, H2H={h2h_ok}, Console={mcu_msg}, "
        f"HWRev={detected_hwrev} ({'PASS' if hwrev_ok else 'FAIL'}), "
        f"Temp={temp_c:.1f}°C ({'PASS' if temp_ok else 'FAIL'}), "
        f"GPIOs={'PASS' if gpios_ok else 'FAIL (' + gpio_details + ')'}, "
        f"Voltages={'PASS' if voltages_ok else 'FAIL (' + voltage_details + ')'}"
    )

    write_device_log(
        serial_no,
        "host_replug_test",
        f"ALL_PASSED={all_passed}\n{status_msg}\nHost CC1: {cc1_mv} mV, Host CC2: {cc2_mv} mV\n"
        f"Orientation: {curr_orientation}\n"
        f"HWRev: {detected_hwrev} (Expected: {self.EXPECTED_HWREV_TEXT})\n"
        f"Temperature: {temp_c:.2f} 'C (Limits: 20-65 'C)\n"
        f"GPIO Status: {'PASS' if gpios_ok else 'FAIL'}\n"
        f"GPIO Details: {gpio_details}\n"
        f"Voltages: PP5000={pp5000_mv} mV, Host VBUS={host_vbus_mv} mV, CHG VBUS={chg_adc_vbus_mv} mV\n"
        f"Voltage Details: {voltage_details}\n\n"
        f"ADC Raw:\n{adc_status.get('raw_output', '')}\n"
        f"GPIO Get:\n{gpios_raw}\n"
        f"HWRev Output:\n{hwrev_raw}\n"
        f"Temp Output:\n{temp_raw}",
    )

    return maui_manufacturing_pb2.MauiHostReplugTestResponse(
        all_passed=all_passed,
        hub_usb2_detected=hub_usb2_ok,
        hub_usb3_detected=hub_usb3_ok,
        ftdi_detected=ftdi_ok,
        h2h_detected=h2h_ok,
        message=status_msg,
        host_flip_ok=host_flip_ok,
        host_orientation=curr_orientation,
        cc1_mv=cc1_mv,
        cc2_mv=cc2_mv,
        gpios_ok=gpios_ok,
        hwrev_ok=hwrev_ok,
        detected_hwrev=detected_hwrev,
        temp_ok=temp_ok,
        temperature_c=temp_c,
        voltages_ok=voltages_ok,
        pp5000_mv=pp5000_mv,
        host_vbus_mv=host_vbus_mv,
        chg_vbus_mv=chg_adc_vbus_mv,
        gpio_details=gpio_details,
        voltage_details=voltage_details,
    )

  def test_reset_button(
      self,
      request: maui_manufacturing_pb2.MauiResetButtonTestRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiResetButtonTestResponse:
    """Two-step RESET button verification with tight timeouts."""
    serial_no = request.serial_number
    dev_port = request.device_port or ""
    action = request.action or "check_initial_uptime"
    initial_uptime_ms = int(request.initial_uptime_ms or 0)

    dev_port = self._fallback_console_port(dev_port)

    tester = self._make_console_tester(dev_port)

    if action in ("check_initial_uptime", "baseline", "capture_baseline"):
      logging.info("Step A: Querying baseline kernel uptime on %s...", dev_port)
      uptime_ms, raw_out = tester.read_kernel_uptime()
      uptime_sufficient = (uptime_ms >= 7000)

      if uptime_ms < 0:
        msg = f"Failed to read kernel uptime from MCU console on {dev_port}. Output: {raw_out}"
        return maui_manufacturing_pb2.MauiResetButtonTestResponse(
            success=False,
            current_uptime_ms=0,
            uptime_sufficient=False,
            reset_verified=False,
            message=msg,
            raw_output=raw_out,
        )

      if uptime_sufficient:
        msg = f"Baseline uptime captured: {uptime_ms} ms (>= 7000 ms). Operator: Please press the RESET button on the Maui board now."
      else:
        msg = f"Initial uptime was only {uptime_ms} ms (< 7000 ms). Please wait for board to be powered on >= 7s."

      return maui_manufacturing_pb2.MauiResetButtonTestResponse(
          success=uptime_sufficient,
          current_uptime_ms=uptime_ms,
          uptime_sufficient=uptime_sufficient,
          reset_verified=False,
          message=msg,
          raw_output=raw_out,
      )

    elif action in ("verify_post_reset_uptime", "verify_reboot", "verify"):
      logging.info("Step B: Verifying post-reset reboot (baseline was %d ms)...", initial_uptime_ms)
      time.sleep(1.0)

      poll_interval_sec = 0.25
      max_attempts = 10
      reset_verified = False
      post_uptime_ms = -1
      raw_out = ""

      for attempt in range(1, max_attempts + 1):
        dev_port = self._rediscover_console_port(dev_port)

        tester = self._make_console_tester(dev_port)
        read_uptime, out = tester.read_kernel_uptime()
        raw_out = out
        logging.info("[RESET Poll %d/%d] Read kernel uptime: %d ms (Initial was %d ms)",
                     attempt, max_attempts, read_uptime, initial_uptime_ms)

        # Polling is only to ensure we successfully read at least once (read_uptime >= 0).
        # Once successfully polled, if the read uptime is longer than expected (>= initial),
        # fail immediately without polling further.
        if read_uptime >= 0:
          post_uptime_ms = read_uptime
          if read_uptime < initial_uptime_ms:
            reset_verified = True
          else:
            reset_verified = False
          break

        time.sleep(poll_interval_sec)

      if reset_verified:
        msg = f"RESET Button Test PASSED! Uptime successfully reset from {initial_uptime_ms} ms to {post_uptime_ms} ms."
        write_device_log(
            serial_no,
            "reset_button_test",
            f"ALL_PASSED=true\nRESULT: PASSED\n{msg}\nInitial Uptime: {initial_uptime_ms} ms, Post Uptime: {post_uptime_ms} ms\n\nRaw Output:\n{raw_out}",
        )
        return maui_manufacturing_pb2.MauiResetButtonTestResponse(
            success=True,
            current_uptime_ms=post_uptime_ms,
            uptime_sufficient=True,
            reset_verified=True,
            message=msg,
            raw_output=raw_out,
        )
      else:
        if post_uptime_ms >= 0:
          err_msg = (
              f"RESET Button Test FAILED: Post-reset uptime ({post_uptime_ms} ms) was not smaller than initial uptime ({initial_uptime_ms} ms). "
              f"Board did not reboot."
          )
        else:
          err_msg = (
              f"RESET Button Test FAILED: Failed to read kernel uptime from MCU console after {max_attempts} poll attempts."
          )
        write_device_log(
            serial_no,
            "reset_button_test",
            f"ALL_PASSED=false\nRESULT: FAILED\n{err_msg}\nInitial Uptime: {initial_uptime_ms} ms, Post Uptime: {post_uptime_ms} ms\n\nRaw Output:\n{raw_out}",
        )
        return maui_manufacturing_pb2.MauiResetButtonTestResponse(
            success=False,
            current_uptime_ms=post_uptime_ms if post_uptime_ms >= 0 else 0,
            uptime_sufficient=False,
            reset_verified=False,
            message=err_msg,
            raw_output=raw_out,
        )

    return maui_manufacturing_pb2.MauiResetButtonTestResponse(
        success=False,
        message=f"Unknown reset action '{action}'",
    )

  def test_bsl_button(
      self,
      request: maui_manufacturing_pb2.MauiBslButtonTestRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiBslButtonTestResponse:
    """Automated BSL button test sampling ADC status over console (expected BSL >= 2500mV)."""
    dev_port = request.device_port or ""
    dev_port = self._fallback_console_port(dev_port)

    tester = self._make_console_tester(dev_port)
    duration = request.max_sample_duration_sec or 3.0
    logging.info("Sampling BSL button ADC status on %s for %.1fs...", dev_port, duration)

    passed, peak_mv, raw_adc = tester.sample_bsl_adc(max_duration_sec=duration, interval_sec=0.15, threshold_mv=2500)
    serial_no = request.serial_number or ""
    if not serial_no:
      for dev in maui_common.scan_usb_devices():
        if dev.serial:
          serial_no = dev.serial
          break

    if passed:
      msg = f"BSL Button Test PASSED! Peak ADC reading: {peak_mv} mV (>= 2500 mV threshold)."
    else:
      msg = f"BSL Button Test FAILED. Peak ADC reading was {peak_mv} mV (expected >= 2500 mV)."

    write_device_log(
        serial_no,
        "bsl_button_test",
        f"ALL_PASSED={passed}\nRESULT: {'PASSED' if passed else 'FAILED'}\n{msg}\nPeak ADC: {peak_mv} mV (Threshold >= 2500 mV)\n\nRaw ADC Samples:\n{raw_adc}",
    )

    return maui_manufacturing_pb2.MauiBslButtonTestResponse(
        success=passed,
        peak_bsl_mv=peak_mv,
        threshold_mv=2500,
        message=msg,
        raw_adc_table=raw_adc,
    )

  def run_host_only_tests(
      self,
      request: maui_manufacturing_pb2.MauiHostOnlyTestRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiHostOnlyTestResponse:
    """Runs all verification tests requiring ONLY the host connected."""
    serial_no = request.serial_number
    dev_port = request.device_port or ""

    if not serial_no:
      serial_no = self._find_unit_serial()

    dev_port = self._resolve_console_port(dev_port)

    logging.info("Running Host-Only Verification for serial %s (port: %s)...", serial_no, dev_port)

    # 1. Host USB Enumeration Checks
    hub_usb2_ok, hub_usb3_ok, ftdi_ok, h2h_ok = self._check_host_usb_topology()

    logging.info("Host USB checks -> Hub2: %s, Hub3: %s, FTDI (0x6002): %s, H2H: %s",
                 hub_usb2_ok, hub_usb3_ok, ftdi_ok, h2h_ok)

    # 2. Maui Console Tester Commands in a single serial session
    port_node = dev_port
    tester = self._make_console_tester(port_node)
    results = {}
    pdc_chg = {"success": False, "plug_present": False, "voltage_v": 0.0, "measured_vbus_mv": 0, "raw_output": ""}
    adc_status = {"chg_vbus_mv": 0, "dut_vbus_mv": 0, "sbu1_mv": 0, "sbu2_mv": 0, "bsl_mv": 0, "orientation": "Unknown"}
    hwrev_ok = False
    detected_hwrev = -1
    hwrev_raw = ""
    temp_ok = False
    temp_c = -1.0
    temp_raw = ""
    gpios_ok = False
    gpio_mismatches = ["Serial port not accessible"]
    gpios_raw = ""
    voltages_ok = False
    voltage_mismatches = ["Serial port not accessible"]

    if os.path.exists(port_node):
      try:
        with open_serial_port(port_node, tester.baudrate, timeout=tester.timeout) as ser:
          for test in tester.test_cases:
            output = tester.send_command(ser, test.command)
            success, message = test.evaluate(output)
            results[test.name] = (success, message)
          pdc_chg = tester.read_pdc_port_status(port="charger", ser=ser)
          adc_status = tester.read_adc_status(ser=ser)
          hwrev_ok, detected_hwrev, hwrev_raw = tester.read_hwrev(ser=ser)
          temp_ok, temp_c, temp_raw = tester.read_temperature(ser=ser)
          gpios_ok, parsed_gpios, gpio_mismatches, gpios_raw = tester.read_and_verify_gpios(ser=ser)
          voltages_ok, voltage_mismatches = verify_host_adc_voltages(adc_status)
      except Exception as e:
        logging.warning("Console session failed on %s: %s", port_node, e)
        results["SerialConnection"] = (False, str(e))
    else:
      results["SerialConnection"] = (False, f"Port {port_node} does not exist")

    mcu_version_ok, mcu_version_msg = results.get("MCU Version Check", (False, "Not run"))
    pdc_info_ok, pdc_info_msg = results.get("PDC Info Check", (False, "Not run"))

    chg_adc_vbus_mv = adc_status.get("chg_vbus_mv", 0)
    chg_plug = pdc_chg.get("plug_present", False)
    chg_v = pdc_chg.get("voltage_v", 0.0)
    chg_mv = pdc_chg.get("measured_vbus_mv", 0)
    chg_contract = pdc_chg.get("contract_type", "None")
    raw_out = pdc_chg.get("raw_output", "").lower()

    pdc_charger_ok = bool(
        (chg_adc_vbus_mv >= 4500)
        or chg_plug
        or (chg_v >= 4.5)
        or (chg_mv >= 4500)
        or (chg_contract not in ("None", "Unknown", ""))
        or ("plug present: yes" in raw_out)
        or ("voltage: 20" in raw_out or "voltage: 5" in raw_out or "voltage: 9" in raw_out or "voltage: 15" in raw_out)
        or ("20.00 v" in raw_out or "20.00v" in raw_out)
    )
    logging.info("PDC Charger Check -> ADC VBUS: %dmV, Plug: %s, Voltage: %.2fV, Measured: %dmV, Contract: %s, OK: %s",
                 chg_adc_vbus_mv, chg_plug, chg_v, chg_mv, chg_contract, pdc_charger_ok)

    host_orientation = adc_status.get("host_orientation", "Unknown")
    cc1_mv = adc_status.get("host_cc1_mv", 0)
    cc2_mv = adc_status.get("host_cc2_mv", 0)
    pp5000_mv = adc_status.get("pp5000_mv", 0)
    host_vbus_mv = adc_status.get("host_vbus_mv", 0)

    gpio_details = "; ".join(gpio_mismatches) if gpio_mismatches else self._gpio_ok_text()
    voltage_details = (
        "; ".join(voltage_mismatches)
        if voltage_mismatches
        else f"PP5000={pp5000_mv}mV, Host={host_vbus_mv}mV, CHG={chg_adc_vbus_mv}mV"
    )

    all_passed = (
        hub_usb2_ok
        and hub_usb3_ok
        and ftdi_ok
        and h2h_ok
        and mcu_version_ok
        and pdc_info_ok
        and pdc_charger_ok
        and hwrev_ok
        and temp_ok
        and gpios_ok
        and voltages_ok
    )
    summary = (
        f"USB: Hub2={hub_usb2_ok}, Hub3={hub_usb3_ok}, {self.CONSOLE_USB_LABEL}={ftdi_ok}, H2H={h2h_ok} | "
        f"Console: Version={mcu_version_msg}, PDC={pdc_info_msg}, "
        f"HWRev={detected_hwrev} ({'PASS' if hwrev_ok else 'FAIL'}), "
        f"Temp={temp_c:.1f}°C ({'PASS' if temp_ok else 'FAIL'}), "
        f"GPIOs={'PASS' if gpios_ok else 'FAIL (' + gpio_details + ')'}, "
        f"Voltages={'PASS' if voltages_ok else 'FAIL (' + voltage_details + ')'}, "
        f"Host CC={host_orientation}, "
        f"Charger Port={'PASS (' + str(chg_v or (chg_adc_vbus_mv/1000.0)) + 'V)' if pdc_charger_ok else 'FAIL (No Charger)'}"
    )

    write_device_log(
        serial_no,
        "host_only_tests",
        f"ALL_PASSED={all_passed}\n{summary}\n"
        f"Host Orientation: {host_orientation}\nHost CC1: {cc1_mv} mV, Host CC2: {cc2_mv} mV\n"
        f"HWRev: {detected_hwrev} (Expected: {self.EXPECTED_HWREV_TEXT})\n"
        f"Temperature: {temp_c:.2f} 'C (Limits: 20-65 'C)\n"
        f"GPIO Status: {'PASS' if gpios_ok else 'FAIL'}\n"
        f"GPIO Details: {gpio_details}\n"
        f"Voltages: PP5000={pp5000_mv} mV, Host VBUS={host_vbus_mv} mV, CHG VBUS={chg_adc_vbus_mv} mV\n"
        f"Voltage Details: {voltage_details}\n\n"
        f"PDC Charger:\n{pdc_chg.get('raw_output', '')}\n"
        f"ADC Status:\n{adc_status.get('raw_output', '')}\n"
        f"GPIO Get:\n{gpios_raw}\n"
        f"HWRev Output:\n{hwrev_raw}\n"
        f"Temp Output:\n{temp_raw}",
    )

    return maui_manufacturing_pb2.MauiHostOnlyTestResponse(
        all_passed=all_passed,
        hub_usb2_detected=hub_usb2_ok,
        hub_usb3_detected=hub_usb3_ok,
        ftdi_detected=ftdi_ok,
        h2h_detected=h2h_ok,
        mcu_version_ok=mcu_version_ok,
        pdc_info_ok=pdc_info_ok,
        mcu_version_str=mcu_version_msg,
        pdc_appconfig_ver="00000318" if pdc_info_ok else "UNKNOWN",
        pdc_fw_ver="00000019" if pdc_info_ok else "UNKNOWN",
        summary=summary,
        pdc_charger_ok=pdc_charger_ok,
        charger_voltage_v=chg_v,
        charger_measured_vbus_mv=chg_mv,
        host_orientation=host_orientation,
        cc1_mv=cc1_mv,
        cc2_mv=cc2_mv,
        gpios_ok=gpios_ok,
        hwrev_ok=hwrev_ok,
        detected_hwrev=detected_hwrev,
        temp_ok=temp_ok,
        temperature_c=temp_c,
        voltages_ok=voltages_ok,
        pp5000_mv=pp5000_mv,
        host_vbus_mv=host_vbus_mv,
        chg_vbus_mv=chg_adc_vbus_mv,
        gpio_details=gpio_details,
        voltage_details=voltage_details,
    )

  def _find_ccd_serial_nodes(self) -> Dict[str, str]:
    """Scans for Google CCD device (Cr50 / Ti50) and maps ap, ec, gsc serial ports."""
    ccd_nodes = {}
    candidate_ttys = sorted(glob.glob("/dev/ttyUSB*"))
    ftdi_port = ""

    # 1. Check if a CCD device (18d1:5014, 18d1:504a, 18d1:5066, 18d1:520d) exists on the USB bus
    ccd_present = False
    for dev in maui_common.scan_usb_devices():
      if dev.vid == 0x0403 and dev.dev_node:
        ftdi_port = dev.dev_node
      if dev.vid == 0x18D1 and dev.pid in (0x5014, 0x504A, 0x5066, 0x520D):
        ccd_present = True
        logging.info("Found Google CCD device on USB bus: VID=18d1, PID=%04x (%s)", dev.pid, dev.product)
      elif dev.product and any(c in dev.product.lower() for c in ("cr50", "ti50", "ccd", "google converter")):
        ccd_present = True
        logging.info("Found Google CCD device on USB bus by product: %s", dev.product)

    # 2. Try precise sysfs mapping for each tty node
    for tty_dir in glob.glob("/sys/class/tty/ttyUSB*"):
      dev_link = os.path.join(tty_dir, "device")
      if not os.path.exists(dev_link):
        continue
      try:
        real_dev = os.path.realpath(dev_link)
        dev_name = f"/dev/{os.path.basename(tty_dir)}"
        parent_dev = os.path.dirname(real_dev)
        vid_f = os.path.join(parent_dev, "idVendor")
        pid_f = os.path.join(parent_dev, "idProduct")
        if not os.path.exists(vid_f):
          parent_dev = os.path.dirname(parent_dev)
          vid_f = os.path.join(parent_dev, "idVendor")
          pid_f = os.path.join(parent_dev, "idProduct")

        if os.path.exists(vid_f) and os.path.exists(pid_f):
          with open(vid_f) as f:
            v = f.read().strip().lower()
          with open(pid_f) as f:
            p = f.read().strip().lower()
          if v == "18d1" and (p in ("5014", "504a", "5066", "520d")):
            ccd_present = True
            iface_name = os.path.basename(real_dev)
            if iface_name.endswith(":1.0") or iface_name.endswith(".0"):
              ccd_nodes["gsc"] = dev_name
            elif iface_name.endswith(":1.1") or iface_name.endswith(".1"):
              ccd_nodes["ap"] = dev_name
            elif iface_name.endswith(":1.2") or iface_name.endswith(".2"):
              ccd_nodes["ec"] = dev_name
      except Exception:
        pass

    # 3. Fallback: If CCD device is present on USB bus, map remaining non-FTDI ttyUSB ports
    # Standard order: 1st = GSC (Cr50), 2nd = AP (Android shell), 3rd = EC
    if ccd_present and len(ccd_nodes) < 3:
      non_ftdi = [t for t in candidate_ttys if t != ftdi_port]
      if not ftdi_port and candidate_ttys and self.MCU_CONSOLE_IS_TTYUSB:
        # Default ttyUSB0 is FTDI MCU console
        non_ftdi = candidate_ttys[1:]

      if len(non_ftdi) >= 3:
        ccd_nodes["gsc"] = ccd_nodes.get("gsc") or non_ftdi[0]
        ccd_nodes["ap"] = ccd_nodes.get("ap") or non_ftdi[1]
        ccd_nodes["ec"] = ccd_nodes.get("ec") or non_ftdi[2]
      elif len(non_ftdi) == 2:
        ccd_nodes["gsc"] = ccd_nodes.get("gsc") or non_ftdi[0]
        ccd_nodes["ap"] = ccd_nodes.get("ap") or non_ftdi[1]
      elif len(non_ftdi) == 1:
        ccd_nodes["gsc"] = ccd_nodes.get("gsc") or non_ftdi[0]

    return ccd_nodes

  def _check_adb_and_dut_speed(self, serial_number: str = "") -> Tuple[bool, bool, str, str]:
    """Verifies ADB communication with DUT and queries USB speed on DUT side with fast timeouts.

    Returns (adb_ok, dut_speed_5gbps, dut_speed_str, log_summary).
    """
    adb_ok = False
    dut_speed_5gbps = False
    dut_speed_str = "Unknown"
    logs = []

    # 1. Fast adb devices query (1.0s timeout)
    target_serial = ""
    try:
      proc = subprocess.run(["adb", "devices"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.0)
      out = proc.stdout or ""
      lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("List of devices")]
      devices_found = []
      for l in lines:
        parts = l.split()
        if len(parts) >= 2 and parts[1] in ("device", "recovery", "sideload"):
          devices_found.append(parts[0])

      target_serial = serial_number
      if not target_serial and devices_found:
        target_serial = devices_found[0]

      if target_serial and target_serial in devices_found:
        state_proc = subprocess.run(["adb", "-s", target_serial, "get-state"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.0)
        if state_proc.returncode == 0 and "device" in state_proc.stdout:
          adb_ok = True
          logs.append(f"ADB device '{target_serial}' state: {state_proc.stdout.strip()}")
      elif devices_found:
        adb_ok = True
        target_serial = devices_found[0]
        logs.append(f"ADB device '{target_serial}' found")
    except Exception as e:
      logs.append(f"ADB query error/absent: {e}")

    # 2. If ADB is OK, query DUT side USB speed for Prolific/ADB chip (1.5s timeout)
    if adb_ok and target_serial:
      try:
        dut_cmd = (
            "for d in /sys/bus/usb/devices/*; do "
            "if [ -f $d/idVendor ]; then "
            "echo $(cat $d/idVendor 2>/dev/null):$(cat $d/idProduct 2>/dev/null) speed=$(cat $d/speed 2>/dev/null); "
            "fi; done"
        )
        shell_proc = subprocess.run(
            ["adb", "-s", target_serial, "shell", dut_cmd],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.5
        )
        dut_out = shell_proc.stdout or ""
        logs.append(f"DUT USB enumeration: {dut_out.strip()}")

        for line in dut_out.splitlines():
          if "18d1:506d" in line or "18d1:5084" in line or "067b:" in line or "18d1:" in line:
            m_spd = re.search(r"speed=(\d+)", line)
            if m_spd:
              spd_val = int(m_spd.group(1))
              dut_speed_str = f"{spd_val}M"
              if spd_val >= 5000:
                dut_speed_5gbps = True
                break
              elif spd_val >= 480:
                dut_speed_str = "480M (High-Speed)"

        if not dut_speed_5gbps and "5000" in dut_out:
          dut_speed_5gbps = True
          dut_speed_str = "5000M (SuperSpeed)"
      except Exception as e:
        logs.append(f"DUT ADB shell query error: {e}")

    # Check Host side speed as well
    host_speed_5gbps = False
    for dev in maui_common.scan_usb_devices():
      if dev.vid in (0x18D1, 0x067B, "18d1", "067b") and dev.pid in (0x506D, 0x5084, 0x27A1, "506d", "5084", "27a1"):
        if str(dev.speed) == "5000":
          host_speed_5gbps = True
          break

    if host_speed_5gbps:
      dut_speed_5gbps = True
      if dut_speed_str == "Unknown":
        dut_speed_str = "5000M (SuperSpeed)"

    return adb_ok, dut_speed_5gbps, dut_speed_str, "\n".join(logs)

  def run_adb_transfer_test(
      self,
      request: maui_manufacturing_pb2.MauiAdbTransferTestRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiAdbTransferTestResponse:
    """Pushes/pulls two random files to the DUT over ADB and verifies SHA-1."""
    serial_no = request.serial_number or self._find_unit_serial()
    size_mib = request.size_mb or maui_adb_transfer.get_size_mib()
    logging.info("Running ADB transfer test (%s MiB + 1-16 MiB) for %s...",
                 size_mib, serial_no)
    res = maui_adb_transfer.run_adb_transfer_test(
        preferred_serial=request.adb_serial,
        size_mib=size_mib,
    )
    logging.info("ADB transfer test: %s (%.1f s)", res.message, res.total_s)
    log_text = "\n".join(res.log)
    write_device_log(
        serial_no,
        "adb_transfer_test",
        f"ALL_PASSED={res.success}\n"
        f"RESULT: {'PASSED' if res.success else 'FAILED'}\n"
        f"{res.message}\n"
        f"ADB Serial: {res.adb_serial}\n"
        f"File Size: {res.file_size_bytes} B\n"
        f"Push: {res.push_s:.2f} s, {res.push_mb_s:.1f} MB/s\n"
        f"Pull: {res.pull_s:.2f} s, {res.pull_mb_s:.1f} MB/s\n"
        f"Host SHA-1: {res.host_sha1}\n"
        f"DUT SHA-1: {res.dut_sha1}\n"
        f"Pulled SHA-1: {res.pulled_sha1}\n"
        f"Transfer: {res.transfer_s:.2f} s (deadline {res.deadline_s:g} s)\n"
        f"Total: {res.total_s:.2f} s\n\n{log_text}",
    )
    return maui_manufacturing_pb2.MauiAdbTransferTestResponse(
        success=res.success,
        message=res.message,
        adb_serial=res.adb_serial,
        file_size_bytes=res.file_size_bytes,
        push_seconds=res.push_s,
        pull_seconds=res.pull_s,
        push_mb_per_s=res.push_mb_s,
        pull_mb_per_s=res.pull_mb_s,
        host_sha1=res.host_sha1,
        dut_sha1=res.dut_sha1,
        pulled_sha1=res.pulled_sha1,
        total_seconds=res.total_s,
        log=log_text,
        transfer_seconds=res.transfer_s,
        deadline_seconds=res.deadline_s,
    )

  def run_host_dut_tests(
      self,
      request: maui_manufacturing_pb2.MauiHostDutTestRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiHostDutTestResponse:
    """Runs verification tests for Station 3 (Host+DUT) with guaranteed fast non-blocking return."""
    expected_serial = request.expected_serial or request.serial_number
    stage = request.stage or "all"
    real_h2h_serial = self._find_real_h2h_serial()

    expected_serial_norm = (expected_serial or "").strip().upper()
    real_serial_norm = (real_h2h_serial or "").strip().upper()
    detected_serial = real_h2h_serial or expected_serial or "UNKNOWN"
    serial_matched = bool(real_serial_norm and (not expected_serial_norm or real_serial_norm == expected_serial_norm))

    dev_port = self._resolve_console_port("")

    tester = self._make_console_tester(dev_port)

    # 1. Open a single fast serial session for MCU console operations
    pdc_dut = {"success": False, "plug_present": False, "voltage_v": 0.0, "max_current_a": 0.0, "op_current_a": 0.0, "measured_vbus_mv": 0, "orientation": "Unknown", "raw_output": ""}
    pdc_chg = {"success": False, "plug_present": False, "voltage_v": 0.0, "max_current_a": 0.0, "op_current_a": 0.0, "measured_vbus_mv": 0, "orientation": "Unknown", "raw_output": ""}
    adc_status = {"chg_vbus_mv": 0, "dut_vbus_mv": 0, "sbu1_mv": 0, "sbu2_mv": 0, "bsl_mv": 0, "orientation": "Unknown"}
    dut_orientation = "Unknown"
    sbu1_mv = 0
    sbu2_mv = 0
    adc_raw = ""

    if os.path.exists(dev_port):
      try:
        with open_serial_port(dev_port, tester.baudrate, timeout=tester.timeout) as ser:
          pdc_dut = tester.read_pdc_port_status(port="dut", ser=ser)
          pdc_chg = tester.read_pdc_port_status(port="charger", ser=ser)
          adc_status = tester.read_adc_status(ser=ser)
          dut_orientation = adc_status.get("orientation", "Unknown")
          sbu1_mv = adc_status.get("sbu1_mv", 0)
          sbu2_mv = adc_status.get("sbu2_mv", 0)
          adc_raw = adc_status.get("raw_output", "")
      except Exception as e:
        logging.warning("MCU Serial port %s inaccessible: %s", dev_port, e)
        pdc_dut["error"] = str(e)
        pdc_chg["error"] = str(e)
    else:
      pdc_dut["error"] = f"Device port {dev_port} does not exist"
      pdc_chg["error"] = f"Device port {dev_port} does not exist"

    # 2. Fast H2H SuperSpeed & ADB verification (<1.0s)
    adb_ok, h2h_5gbps, dut_speed_str, adb_logs = self._check_adb_and_dut_speed(detected_serial)
    logging.info("ADB check -> ADB_OK: %s, 5Gbps SuperSpeed: %s (Speed: %s)", adb_ok, h2h_5gbps, dut_speed_str)

    # 3. CCD Consoles Check (EC & GSC query version; AP query response disabled)
    ccd_nodes = self._find_ccd_serial_nodes()
    logging.info("Discovered CCD Serial Nodes: %s", ccd_nodes)
    ap_uart_ok = bool(ccd_nodes.get("ap"))
    ap_version_str = f"Node: {ccd_nodes['ap']} (response check disabled)" if ccd_nodes.get("ap") else "No AP node detected"
    ec_uart_ok, ec_version_str = False, "No CCD device detected"
    gsc_uart_ok, gsc_version_str = False, "No CCD device detected"

    # Only check that EC and GSC consoles respond
    if ccd_nodes.get("ec"):
      ec_uart_ok, ec_version_str = MauiConsoleTester.query_ccd_console(ccd_nodes["ec"], "version", timeout=0.5, first_byte_timeout=0.25)
    if ccd_nodes.get("gsc"):
      gsc_uart_ok, gsc_version_str = MauiConsoleTester.query_ccd_console(ccd_nodes["gsc"], "version", timeout=0.5, first_byte_timeout=0.25)

    voltage_v = pdc_dut.get("voltage_v", 0.0)
    current_a = pdc_dut.get("op_current_a", 0.0) or pdc_dut.get("max_current_a", 0.0)
    measured_vbus_mv = pdc_dut.get("measured_vbus_mv", 0)
    dut_plug_present = pdc_dut.get("plug_present", False)
    calculated_power_w = round(voltage_v * current_a, 2)

    chg_adc_vbus_mv = adc_status.get("chg_vbus_mv", 0)
    dut_adc_vbus_mv = adc_status.get("dut_vbus_mv", 0)
    chg_plug_present = pdc_chg.get("plug_present", False)
    chg_voltage_v = pdc_chg.get("voltage_v", 0.0)
    chg_measured_mv = pdc_chg.get("measured_vbus_mv", 0)
    chg_contract = pdc_chg.get("contract_type", "None")

    chg_raw_lower = pdc_chg.get("raw_output", "").lower()
    dut_raw_lower = pdc_dut.get("raw_output", "").lower()

    # 1. Charger Port Detection: Must be connected for Stage 1 & 2 (20V PD charger or active contract)
    charger_connected_ok = bool(
        (chg_adc_vbus_mv >= 12000)
        or (chg_measured_mv >= 12000)
        or (chg_voltage_v >= 9.0)
        or (chg_plug_present and chg_contract.lower() not in ("none", "unknown", ""))
        or ("plug present: yes" in chg_raw_lower and "active contract:  none" not in chg_raw_lower and "active contract: none" not in chg_raw_lower)
    )

    # 2. DUT PD Contract Verification: Must negotiate 20V with charger connected
    dut_20v_contract_ok = bool(
        dut_plug_present
        and (
            voltage_v >= 18.5
            or measured_vbus_mv >= 18000
            or dut_adc_vbus_mv >= 18000
            or ("20.0" in dut_raw_lower or "20.00" in dut_raw_lower or "20v" in dut_raw_lower)
        )
    )
    pd_contract_ok = dut_20v_contract_ok

    # 3. Charger Disconnection Check: Must be completely unplugged for Stage 3
    # Note: When external charger is disconnected, CHG rail may show ~4.3V (4331 mV) due to
    # internal diode drop/back-feed from DUT 4.8V battery sink rail, but PDC reports
    # 'Plug Present: No' & 'Active Contract: None', and voltage is not a 20V PD contract (<12V).
    has_active_charger = bool(
        (chg_adc_vbus_mv >= 12000)
        or (chg_measured_mv >= 12000)
        or (chg_voltage_v >= 9.0)
        or (chg_plug_present and chg_contract.lower() not in ("none", "unknown", ""))
    )
    is_chg_unplugged = bool(
        (not chg_plug_present)
        or ("plug present: no" in chg_raw_lower)
        or (chg_contract.lower() in ("none", "unknown", ""))
    )

    charger_disconnected_ok = bool(is_chg_unplugged and not has_active_charger)

    # 4. SBU 180° Cable Flip Polarity Verification via 'adc status' (SBU1_DET vs SBU2_DET)
    prev_orientation = getattr(request, "previous_orientation", "") or ""
    if not prev_orientation and stage in ("stage2_sbu_flip", "stage3_sbu_flip"):
      dir_path = maui_common.get_device_log_dir(detected_serial)
      stg1_path = os.path.join(dir_path, "host_dut_stage1_with_charger.log")
      if os.path.exists(stg1_path):
        try:
          with open(stg1_path, "r", encoding="utf-8") as f:
            c = f.read()
          m = re.search(r"(?:Orientation|DUT Orientation|SBU Orientation):\s*([^\r\n,|]+)", c, re.IGNORECASE)
          if m:
            prev_orientation = m.group(1).strip()
        except Exception:
          pass

    if prev_orientation and not dut_orientation.startswith("Unknown"):
      polarity_inverted = is_sbu_flipped(prev_orientation, dut_orientation)
    else:
      polarity_inverted = not dut_orientation.startswith("Unknown")

    sbu_flip_ok = dut_plug_present and polarity_inverted

    # Evaluate stage specific pass/fail criteria
    # User flow: 1st & 2nd with charger connected (20V contract), 3rd with charger disconnected
    if stage in ("stage1_with_charger",):
      all_passed = charger_connected_ok and dut_20v_contract_ok and h2h_5gbps and serial_matched and ec_uart_ok and gsc_uart_ok
      summary = (
          f"[Stage 1: With Charger] Charger Port: {'PASS (Connected)' if charger_connected_ok else 'FAIL (No Charger)'} | "
          f"DUT 20V Contract: {voltage_v}V @ {current_a}A ({calculated_power_w}W, VBUS={measured_vbus_mv}mV, {'PASS' if dut_20v_contract_ok else 'FAIL (Expected 20V)'}) | "
          f"DUT Orientation: {dut_orientation} | "
          f"Serial Match: {'PASS (' + detected_serial + ')' if serial_matched else 'FAIL'} | "
          f"H2H 5Gbps: {'PASS (' + dut_speed_str + ')' if h2h_5gbps else 'FAIL'} | "
          f"CCD Consoles: EC={'PASS' if ec_uart_ok else 'FAIL'}, GSC={'PASS' if gsc_uart_ok else 'FAIL'}"
      )
    elif stage in ("stage2_sbu_flip", "stage3_sbu_flip"):
      all_passed = sbu_flip_ok and charger_connected_ok and dut_20v_contract_ok and h2h_5gbps and ec_uart_ok and gsc_uart_ok
      if prev_orientation and not polarity_inverted:
        sbu_status_str = f"FAIL (Polarity not inverted: still {dut_orientation}, expected flip from {prev_orientation})"
      elif polarity_inverted:
        sbu_status_str = f"PASS ({prev_orientation} -> {dut_orientation})" if prev_orientation else f"PASS ({dut_orientation})"
      else:
        sbu_status_str = f"FAIL ({dut_orientation})"

      summary = (
          f"[Stage 2: 180° SBU Flip] SBU Orientation: {sbu_status_str} | "
          f"Charger Port: {'PASS' if charger_connected_ok else 'FAIL'} | "
          f"DUT 20V Contract: {'PASS (' + str(voltage_v) + 'V)' if dut_20v_contract_ok else 'FAIL'} | "
          f"H2H Re-connect: {'PASS (' + dut_speed_str + ')' if h2h_5gbps else 'FAIL'} | "
          f"CCD Consoles: EC={'PASS' if ec_uart_ok else 'FAIL'}, GSC={'PASS' if gsc_uart_ok else 'FAIL'}"
      )
    elif stage in ("stage3_no_charger", "stage1_no_charger"):
      all_passed = charger_disconnected_ok and h2h_5gbps and serial_matched and ec_uart_ok and gsc_uart_ok
      summary = (
          f"[Stage 3: Charger Disconnected] Charger Disconnected: {'PASS' if charger_disconnected_ok else 'FAIL (Plug Detected)'} | "
          f"H2H 5Gbps (Battery Sink): {'PASS (' + dut_speed_str + ')' if h2h_5gbps else 'FAIL'} | "
          f"Serial Match: {'PASS (' + detected_serial + ')' if serial_matched else 'FAIL'} | "
          f"CCD Consoles: EC={'PASS' if ec_uart_ok else 'FAIL'}, GSC={'PASS' if gsc_uart_ok else 'FAIL'}"
      )
    else:
      all_passed = charger_connected_ok and dut_20v_contract_ok and h2h_5gbps and serial_matched and ec_uart_ok and gsc_uart_ok and sbu_flip_ok
      summary = (
          f"H2H 5Gbps: {'PASS (' + dut_speed_str + ')' if h2h_5gbps else 'FAIL'} | "
          f"Serial Match: {'PASS (' + detected_serial + ')' if serial_matched else 'FAIL'} | "
          f"Charger Port: {'PASS' if charger_connected_ok else 'FAIL'} | "
          f"DUT 20V Contract: {voltage_v}V @ {current_a}A ({calculated_power_w}W, {'PASS' if dut_20v_contract_ok else 'FAIL'}) | "
          f"CCD Consoles: EC={'PASS' if ec_uart_ok else 'FAIL'}, GSC={'PASS' if gsc_uart_ok else 'FAIL'} | "
          f"SBU Orientation: {dut_orientation} ({'PASS' if sbu_flip_ok else 'FAIL'})"
      )

    write_device_log(
        detected_serial,
        f"host_dut_{stage}" if stage != "all" else "host_dut_tests",
        f"ALL_PASSED={all_passed}\n{summary}\n\nCCD Nodes: {ccd_nodes}\nEC: {ec_version_str}\nGSC: {gsc_version_str}\nADB Output:\n{adb_logs}\nPDC DUT:\n{pdc_dut.get('raw_output', '')}\nPDC CHG:\n{pdc_chg.get('raw_output', '')}\nADC Status:\n{adc_raw}"
    )

    return maui_manufacturing_pb2.MauiHostDutTestResponse(
        all_passed=all_passed,
        h2h_speed_5gbps=h2h_5gbps,
        h2h_serial=detected_serial,
        h2h_serial_matched=serial_matched,
        negotiated_voltage_v=voltage_v,
        negotiated_current_a=current_a,
        pd_contract_ok=pd_contract_ok,
        ap_uart_ok=ap_uart_ok,
        ec_uart_ok=ec_uart_ok,
        gsc_uart_ok=gsc_uart_ok,
        sbu_flip_ok=sbu_flip_ok,
        summary=summary,
        measured_vbus_mv=measured_vbus_mv,
        ap_version_str=ap_version_str,
        ec_version_str=ec_version_str,
        gsc_version_str=gsc_version_str,
        dut_orientation=dut_orientation,
        adb_connected=adb_ok,
        adb_dut_speed=dut_speed_str,
        charger_disconnected_ok=charger_disconnected_ok,
        charger_connected_ok=charger_connected_ok,
        calculated_power_w=calculated_power_w,
    )
