#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Extensible Modular Console Testing Framework for Maui devices."""

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import serial


def open_serial_port(
    port_node: str,
    baudrate: int = 115200,
    timeout: float = 0.5,
    write_timeout: float = 0.3,
) -> serial.Serial:
  """Opens a serial port with strict read/write timeouts and flow control disabled to prevent blocking."""
  return serial.Serial(
      port=port_node,
      baudrate=baudrate,
      timeout=timeout,
      write_timeout=write_timeout,
      rtscts=False,
      dsrdtr=False,
      xonxoff=False,
  )


class ConsoleTestCase:
  """Base class for an extensible console test case."""

  def __init__(self, name: str, command: str):
    self.name = name
    self.command = command

  def evaluate(self, output: str) -> Tuple[bool, str]:
    raise NotImplementedError("Subclasses must implement evaluate()")


class RegexMatchTestCase(ConsoleTestCase):
  """Test case validating output against a regex pattern."""

  def __init__(self, name: str, command: str, pattern: str, description: str):
    super().__init__(name, command)
    self.pattern = re.compile(pattern)
    self.description = description

  def evaluate(self, output: str) -> Tuple[bool, str]:
    match = self.pattern.search(output)
    if match:
      return True, f"Passed: Found expected pattern for {self.name}"
    return False, f"Failed: Pattern '{self.pattern.pattern}' not found in output of '{self.command}'"


class PdcInfoTestCase(ConsoleTestCase):
  """Test case validating PDC info fields (App Config Version & FW Version)."""

  def __init__(self, name: str, expected_app_config_ver: Optional[str] = None, expected_fw_ver: Optional[str] = None):
    super().__init__(name, "pdc info")
    self.expected_app_config_ver = expected_app_config_ver
    self.expected_fw_ver = expected_fw_ver

  def evaluate(self, output: str) -> Tuple[bool, str]:
    app_config_match = re.search(r"App Config Version:\s*([0-9a-fA-F]+)", output)
    fw_ver_match = re.search(r"FW Version:\s*([0-9a-fA-F]+)", output)

    app_config_ver = app_config_match.group(1) if app_config_match else "UNKNOWN"
    fw_ver = fw_ver_match.group(1) if fw_ver_match else "UNKNOWN"

    errors = []
    if self.expected_app_config_ver and app_config_ver.lower() != self.expected_app_config_ver.lower():
      errors.append(f"AppConfig ver mismatch: expected {self.expected_app_config_ver}, got {app_config_ver}")
    if self.expected_fw_ver and fw_ver.lower() != self.expected_fw_ver.lower():
      errors.append(f"PDC FW ver mismatch: expected {self.expected_fw_ver}, got {fw_ver}")

    if errors:
      return False, "; ".join(errors)
    return True, f"PDC Info verified (AppConfig: {app_config_ver}, FW: {fw_ver})"


class MauiConsoleTester:
  """Extensible framework for running console commands and validating responses.

  Configuration constants can easily be updated for future test suites.
  """

  EXPECTED_MCU_VERSION_PATTERN = r"Maui version\s+[\w.-]+"
  EXPECTED_PDC_APP_CONFIG_VER = None
  EXPECTED_PDC_FW_VER = None

  def __init__(self, port_node: str, baudrate: int = 115200, timeout: float = 0.5):
    self.port_node = port_node
    self.baudrate = baudrate
    self.timeout = timeout
    self.test_cases: List[ConsoleTestCase] = []
    self._register_default_tests()

  def _register_default_tests(self):
    """Registers default test cases for Station 1 Quick Test."""
    self.test_cases.append(
        RegexMatchTestCase(
            name="MCU Version Check",
            command="version",
            pattern=self.EXPECTED_MCU_VERSION_PATTERN,
            description="Validates MCU version string format from 'version' command",
        )
    )
    self.test_cases.append(
        PdcInfoTestCase(
            name="PDC Info Check",
            expected_app_config_ver=self.EXPECTED_PDC_APP_CONFIG_VER,
            expected_fw_ver=self.EXPECTED_PDC_FW_VER,
        )
    )

  def add_test_case(self, test_case: ConsoleTestCase):
    """Allows adding new test cases dynamically for future test suites."""
    self.test_cases.append(test_case)

  def send_command(
      self,
      ser: serial.Serial,
      command: str,
      timeout: float = 1.0,
      first_byte_timeout: float = 0.8,
      inter_byte_timeout: float = 0.08,
  ) -> str:
    """Sends a single command to the MCU console and reads response with minimal latency."""
    try:
      # Drain any stale characters left over in the line
      while ser.in_waiting > 0:
        ser.read(ser.in_waiting)
        time.sleep(0.005)
      ser.reset_input_buffer()
      ser.reset_output_buffer()
      ser.write(f"{command.strip()}\r\n".encode("utf-8"))
      ser.flush()
    except Exception as e:
      logging.warning("Failed to write command '%s' to serial port: %s", command, e)
      return ""

    response = bytearray()
    start_time = time.time()
    last_data_time = start_time
    cmd_bytes = command.strip().encode("utf-8")
    saw_echo = False

    while (time.time() - start_time) < timeout:
      try:
        in_waiting = ser.in_waiting
      except Exception:
        break

      if in_waiting > 0:
        try:
          data = ser.read(in_waiting)
          if data:
            response.extend(data)
            last_data_time = time.time()
            if not saw_echo and cmd_bytes in response:
              saw_echo = True
            # Only terminate if we've seen output/echo and line ends with prompt
            if saw_echo or (len(response) > len(cmd_bytes)):
              if (
                  response.endswith(b"maui$ ")
                  or response.endswith(b"\nmaui$ ")
                  or response.endswith(b"\r\nmaui$ ")
                  or response.endswith(b"\n$ ")
                  or response.endswith(b"\r\n$ ")
              ):
                break
        except Exception:
          break
      else:
        # Fast exit if no initial byte arrives within first_byte_timeout
        if not response and (time.time() - start_time) > first_byte_timeout:
          break
        # Fast exit once stream pauses after receiving response
        if response and (time.time() - last_data_time) > inter_byte_timeout:
          if saw_echo or (time.time() - start_time) > 0.3:
            break
        time.sleep(0.01)

    return response.decode("utf-8", errors="ignore")

  def read_kernel_uptime(self, ser: Optional[serial.Serial] = None) -> Tuple[int, str]:
    """Sends 'kernel uptime' and parses milliseconds. Returns (uptime_ms, raw_output)."""
    try:
      if ser is not None:
        output = self.send_command(ser, "kernel uptime")
      else:
        with open_serial_port(self.port_node, self.baudrate, timeout=self.timeout) as s:
          output = self.send_command(s, "kernel uptime")

      match = re.search(r"Uptime:\s*(\d+)\s*ms", output, re.IGNORECASE)
      if match:
        uptime_ms = int(match.group(1))
        return uptime_ms, output
      return -1, output
    except Exception as e:
      logging.warning("Failed to read kernel uptime on %s: %s", self.port_node, e)
      return -1, f"Error: {e}"

  def ensure_min_uptime(self, min_uptime_ms: int = 10000) -> Tuple[int, str]:
    """Ensures device has been powered on for at least min_uptime_ms (10s) before reset test."""
    uptime_ms, output = self.read_kernel_uptime()
    if 0 <= uptime_ms < min_uptime_ms:
      wait_time_sec = (min_uptime_ms - uptime_ms) / 1000.0 + 0.5
      logging.info("Uptime is %d ms (<%d ms). Waiting %.1f seconds...", uptime_ms, min_uptime_ms, wait_time_sec)
      time.sleep(wait_time_sec)
      uptime_ms, output = self.read_kernel_uptime()
    return uptime_ms, output

  def sample_bsl_adc(self, max_duration_sec: float = 3.0, interval_sec: float = 0.15, threshold_mv: int = 2500) -> Tuple[bool, int, str]:
    """Repeatedly samples 'adc status' every interval_sec to detect BSL button press (>=2500mV).

    Returns (success, peak_bsl_mv, last_raw_output).
    """
    peak_bsl_mv = 0
    last_output = ""
    start_time = time.time()
    try:
      with open_serial_port(self.port_node, self.baudrate, timeout=self.timeout) as ser:
        while (time.time() - start_time) < max_duration_sec:
          last_output = self.send_command(ser, "adc status", first_byte_timeout=0.15, inter_byte_timeout=0.03)
          match = re.search(r"\[\s*BSL\s*\]\s*=\s*(\d+)\s*mV", last_output, re.IGNORECASE)
          if match:
            bsl_mv = int(match.group(1))
            if bsl_mv > peak_bsl_mv:
              peak_bsl_mv = bsl_mv
            logging.info("BSL ADC sample: %d mV (Peak: %d mV)", bsl_mv, peak_bsl_mv)
            if bsl_mv >= threshold_mv:
              return True, peak_bsl_mv, last_output
          time.sleep(interval_sec)
    except Exception as e:
      logging.warning("Failed to sample BSL ADC on %s: %s", self.port_node, e)
      return False, 0, f"Error: {e}"

    return peak_bsl_mv >= threshold_mv, peak_bsl_mv, last_output

  def read_pdc_port_status(self, port: str = "dut", ser: Optional[serial.Serial] = None) -> Dict[str, Any]:
    """Sends 'pdc status <port>' and parses plug, orientation, active contract, and VBUS readings.

    Returns dictionary containing parsed status fields.
    """
    res = {
        "success": False,
        "plug_present": False,
        "orientation": "Unknown",
        "power_role": "Unknown",
        "data_role": "Unknown",
        "contract_type": "None",
        "voltage_v": 0.0,
        "max_current_a": 0.0,
        "op_current_a": 0.0,
        "measured_vbus_mv": 0,
        "raw_output": "",
        "error": "",
    }
    try:
      cmd = f"pdc status {port}"
      if ser is not None:
        output = self.send_command(ser, cmd, timeout=1.2, first_byte_timeout=0.8)
      else:
        with open_serial_port(self.port_node, self.baudrate, timeout=self.timeout) as s:
          output = self.send_command(s, cmd, timeout=1.2, first_byte_timeout=0.8)

      res["raw_output"] = output

      # Isolate the relevant section for the requested port if multiple outputs exist
      port_name = "charger" if port.lower() in ("charger", "chg", "2") else "dut"
      relevant_text = output
      m_hdr = re.search(
          r"===\s*PDC Status:\s*" + re.escape(port_name) + r"[^=]*===(.*?)(?====|\Z)",
          output,
          re.IGNORECASE | re.DOTALL,
      )
      if m_hdr:
        relevant_text = m_hdr.group(1)
        res["raw_output"] = relevant_text.strip()

      # 1. Plug detection
      m_plug_no = re.search(r"Plug Present:\s*No", relevant_text, re.IGNORECASE)
      m_plug_yes = re.search(r"Plug Present:\s*(?:Yes|Connected|Attached)", relevant_text, re.IGNORECASE)
      if m_plug_no:
        res["plug_present"] = False
      elif m_plug_yes:
        res["plug_present"] = True
      else:
        res["plug_present"] = False

      m_ori = re.search(r"Orientation:\s*([^\r\n]+)", relevant_text, re.IGNORECASE)
      if m_ori:
        res["orientation"] = m_ori.group(1).strip()

      m_prole = re.search(r"Power Role:\s*([^\r\n]+)", relevant_text, re.IGNORECASE)
      if m_prole:
        res["power_role"] = m_prole.group(1).strip()
      m_drole = re.search(r"Data Role:\s*([^\r\n]+)", relevant_text, re.IGNORECASE)
      if m_drole:
        res["data_role"] = m_drole.group(1).strip()

      m_type = re.search(r"Type:\s*([^\r\n]+)", relevant_text, re.IGNORECASE)
      if m_type:
        res["contract_type"] = m_type.group(1).strip()
      else:
        m_act = re.search(r"Active Contract:\s*([^\r\n]+)", relevant_text, re.IGNORECASE)
        if m_act:
          contract_val = m_act.group(1).strip()
          if contract_val.lower() not in ("none", "unknown", ""):
            res["contract_type"] = contract_val
            if not m_plug_no:
              res["plug_present"] = True

      m_volt = re.search(r"Voltage:\s*([\d.]+)\s*V", relevant_text, re.IGNORECASE)
      if m_volt:
        res["voltage_v"] = float(m_volt.group(1))

      m_maxc = re.search(r"Max Current:\s*([\d.]+)\s*A", relevant_text, re.IGNORECASE)
      if m_maxc:
        res["max_current_a"] = float(m_maxc.group(1))

      m_opc = re.search(r"Op Current:\s*([\d.]+)\s*A", relevant_text, re.IGNORECASE)
      if m_opc:
        res["op_current_a"] = float(m_opc.group(1))
      elif res["max_current_a"] > 0:
        res["op_current_a"] = res["max_current_a"]

      m_vbus_mv = re.search(r"Measured VBUS:\s*[\d.]+\s*V\s*\((\d+)\s*mV\)", relevant_text, re.IGNORECASE)
      if m_vbus_mv:
        res["measured_vbus_mv"] = int(m_vbus_mv.group(1))
      else:
        m_vbus_v = re.search(r"(?:Measured\s+)?VBUS:\s*([\d.]+)\s*V", relevant_text, re.IGNORECASE)
        if m_vbus_v:
          res["measured_vbus_mv"] = int(float(m_vbus_v.group(1)) * 1000)

      res["success"] = True
    except Exception as e:
      logging.warning("Failed to read PDC port status for '%s' on %s: %s", port, self.port_node, e)
      res["error"] = str(e)

    return res

  def read_adc_status(self, ser: Optional[serial.Serial] = None) -> Dict[str, Any]:
    """Sends 'adc status' to MCU console and reads all analog voltage channels.

    Returns:
      dict with keys: 'orientation', 'sbu1_mv', 'sbu2_mv', 'chg_vbus_mv', 'dut_vbus_mv', 'bsl_mv', 'raw_output'
    """
    res = {
        "orientation": "Unknown",
        "sbu1_mv": 0,
        "sbu2_mv": 0,
        "chg_vbus_mv": 0,
        "dut_vbus_mv": 0,
        "bsl_mv": 0,
        "raw_output": "",
    }
    try:
      if ser is not None:
        output = self.send_command(ser, "adc status", timeout=1.0, first_byte_timeout=0.5)
      else:
        with open_serial_port(self.port_node, self.baudrate, timeout=self.timeout) as s:
          output = self.send_command(s, "adc status", timeout=1.0, first_byte_timeout=0.5)

      res["raw_output"] = output

      m_sbu1 = re.search(r"\[\s*SBU1_DET\s*\]\s*=\s*(\d+)\s*mV", output, re.IGNORECASE)
      if m_sbu1:
        res["sbu1_mv"] = int(m_sbu1.group(1))

      m_sbu2 = re.search(r"\[\s*SBU2_DET\s*\]\s*=\s*(\d+)\s*mV", output, re.IGNORECASE)
      if m_sbu2:
        res["sbu2_mv"] = int(m_sbu2.group(1))

      m_chg = re.search(r"\[\s*PPVAR_USB_CHG_VBUS\s*\]\s*=\s*(\d+)\s*mV", output, re.IGNORECASE)
      if m_chg:
        res["chg_vbus_mv"] = int(m_chg.group(1))

      m_dut = re.search(r"\[\s*PPVAR_USB_DUT_VBUS\s*\]\s*=\s*(\d+)\s*mV", output, re.IGNORECASE)
      if m_dut:
        res["dut_vbus_mv"] = int(m_dut.group(1))

      m_bsl = re.search(r"\[\s*BSL\s*\]\s*=\s*(\d+)\s*mV", output, re.IGNORECASE)
      if m_bsl:
        res["bsl_mv"] = int(m_bsl.group(1))

      sbu1 = res["sbu1_mv"]
      sbu2 = res["sbu2_mv"]
      if sbu1 >= 1500 and sbu2 < 1000:
        res["orientation"] = f"SBU1 ({sbu1}mV / {sbu2}mV)"
      elif sbu2 >= 1500 and sbu1 < 1000:
        res["orientation"] = f"SBU2 ({sbu1}mV / {sbu2}mV)"
      elif sbu1 > sbu2 + 800:
        res["orientation"] = f"SBU1 ({sbu1}mV / {sbu2}mV)"
      elif sbu2 > sbu1 + 800:
        res["orientation"] = f"SBU2 ({sbu1}mV / {sbu2}mV)"
      else:
        res["orientation"] = f"Unknown ({sbu1}mV / {sbu2}mV)"

      return res
    except Exception as e:
      logging.warning("Failed to read ADC status on %s: %s", self.port_node, e)
      res["orientation"] = f"Error: {e}"
      res["raw_output"] = str(e)
      return res

  def read_adc_sbu_orientation(self, ser: Optional[serial.Serial] = None) -> Tuple[str, int, int, str]:
    """Sends 'adc status' to MCU console and reads SBU1_DET and SBU2_DET voltages.

    Returns:
      (orientation_str, sbu1_mv, sbu2_mv, raw_output)
    """
    res = self.read_adc_status(ser=ser)
    return res["orientation"], res["sbu1_mv"], res["sbu2_mv"], res["raw_output"]

  def ensure_dut_data_and_power(self, ser: Optional[serial.Serial] = None) -> Tuple[bool, str]:
    """Ensures DUT power and data multiplexers are enabled (power_dut on && data_dut on)."""
    logs = []
    try:
      if ser is not None:
        out_p = self.send_command(ser, "power_dut on", first_byte_timeout=0.15)
        logs.append(f"power_dut on: {out_p.strip()}")
        out_d = self.send_command(ser, "data_dut on", first_byte_timeout=0.15)
        logs.append(f"data_dut on: {out_d.strip()}")
      else:
        with open_serial_port(self.port_node, self.baudrate, timeout=self.timeout) as s:
          out_p = self.send_command(s, "power_dut on", first_byte_timeout=0.15)
          logs.append(f"power_dut on: {out_p.strip()}")
          out_d = self.send_command(s, "data_dut on", first_byte_timeout=0.15)
          logs.append(f"data_dut on: {out_d.strip()}")
      return True, "; ".join(logs)
    except Exception as e:
      logging.warning("Failed to enable DUT power/data muxes on %s: %s", self.port_node, e)
      return False, str(e)

  @staticmethod
  def query_ccd_console(
      port_node: str,
      command: str = "version",
      baudrate: int = 115200,
      timeout: float = 0.3,
      first_byte_timeout: float = 0.15,
  ) -> Tuple[bool, str]:
    """Safely queries CCD console with fast non-blocking return."""
    if not port_node or not os.path.exists(port_node):
      return False, "Port does not exist"

    try:
      with open_serial_port(port_node, baudrate, timeout=timeout, write_timeout=0.2) as ser:
        ser.reset_input_buffer()
        ser.write(f"\r\n{command}\r\n".encode("utf-8"))

        response = bytearray()
        start_time = time.time()
        last_data_time = start_time
        while (time.time() - start_time) < timeout:
          try:
            in_waiting = ser.in_waiting
          except Exception:
            break

          if in_waiting > 0:
            try:
              data = ser.read(in_waiting)
              if data:
                response.extend(data)
                last_data_time = time.time()
                if response.endswith(b"> ") or response.endswith(b"$ ") or response.endswith(b"# "):
                  break
            except Exception:
              break
          else:
            if not response and (time.time() - start_time) > first_byte_timeout:
              break
            if response and (time.time() - last_data_time) > 0.04:
              break
            time.sleep(0.01)

        raw_str = response.decode("utf-8", errors="ignore")
        clean_str = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", raw_str).strip()
        lines = [line.strip() for line in clean_str.splitlines() if line.strip() and line.strip() != command.strip()]
        out_summary = " | ".join(lines[:3]) if lines else clean_str

        is_ok = bool(clean_str and len(clean_str) > 3)
        return is_ok, out_summary or clean_str or "No response"
    except Exception as e:
      logging.warning("Error querying CCD console at %s: %s", port_node, e)
      return False, f"Port error: {e}"

  def run_all_tests(self) -> Dict[str, Tuple[bool, str]]:
    """Executes all registered test cases over the serial port."""
    results = {}
    try:
      with open_serial_port(self.port_node, self.baudrate, timeout=self.timeout) as ser:
        for test in self.test_cases:
          output = self.send_command(ser, test.command, first_byte_timeout=0.3)
          success, message = test.evaluate(output)
          results[test.name] = (success, message)
    except Exception as e:
      logging.warning("Failed to connect to MCU console at %s: %s", self.port_node, e)
      results["SerialConnection"] = (False, f"Could not open serial port {self.port_node}: {str(e)}")

    return results
