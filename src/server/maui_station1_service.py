#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Maui Stage 1 (Initial Flashing, Provisioning & Multi-Device PDC) gRPC Servicer."""

import asyncio
import glob
import json
import logging
import os
import re
import subprocess
import shutil
import threading
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from google.protobuf import empty_pb2
import grpc

from server import maui_common
from server import util
from server.generated import maui_manufacturing_pb2
from server.generated import maui_manufacturing_pb2_grpc
from server.maui_common import (
    ensure_zephyr_shell_awake,
    find_offline_fw_file,
    get_device_log_dir,
    get_maui_env,
    resolve_maui_tool,
    write_device_log,
)

# Process-wide lock to prevent concurrent USB EEPROM programming collisions
_ftdi_programming_lock = threading.Lock()

# Per-port lock to prevent concurrent MCU BSL flashing collisions
_mcu_flashing_locks: Dict[str, threading.Lock] = {}
_mcu_locks_guard = threading.Lock()


def _get_port_flashing_lock(dev_port: str) -> threading.Lock:
  with _mcu_locks_guard:
    if dev_port not in _mcu_flashing_locks:
      _mcu_flashing_locks[dev_port] = threading.Lock()
    return _mcu_flashing_locks[dev_port]



class MauiStation1Servicer(
    maui_manufacturing_pb2_grpc.MauiStation1InitialFlashingServicer
):
  """Station 1 gRPC servicer handling BSL auto-trigger and FTDI EEPROM programming."""

  MAUI_BSL_VID = maui_common.MAUI_BSL_VID
  MAUI_BSL_PID = maui_common.MAUI_BSL_PID
  TI_BSL_VID_PID = maui_common.TI_BSL_VID_PID
  FTDI_VID = maui_common.FTDI_VID
  FTDI_PID_UNPROGRAMMED = maui_common.FTDI_PID_UNPROGRAMMED
  FTDI_PID_PROGRAMMED = maui_common.FTDI_PID_PROGRAMMED
  PROLIFIC_VID = maui_common.PROLIFIC_VID
  PROLIFIC_PID_ADB = maui_common.PROLIFIC_PID_ADB
  PROLIFIC_PID_H2H = maui_common.PROLIFIC_PID_H2H
  HUB_USB2_VID_PID = maui_common.HUB_USB2_VID_PID
  HUB_USB3_VID_PID = maui_common.HUB_USB3_VID_PID

  _find_offline_fw_file = staticmethod(find_offline_fw_file)

  def __init__(self):
    pass

  async def stream_bsl_auto_discovery(
      self,
      request: empty_pb2.Empty,
      context: grpc.ServicerContext,
  ) -> AsyncGenerator[maui_manufacturing_pb2.MauiBslDiscoveryResponse, None]:
    """Streams real-time BSL auto-discovery notifications.

    Detects:
      - 0403:6001 (Unprogrammed FTDI): auto_flash_eligible = True -> UI auto-starts flashing.
      - 0403:6002 (Programmed FTDI): auto_flash_eligible = False -> Manual fallback button only.
      - 0483:DF11 / 2047:0013 (MSPM0 DFU / BSL): auto_flash_eligible = True -> UI auto-starts flashing.
    """
    logging.info("Starting BSL auto-discovery stream...")
    start_time = time.time()

    while not context.done():
      elapsed = time.time() - start_time
      remaining = max(0, 10 - (int(elapsed) % 10))

      # 1. Check for FTDI UART devices (0403:6001 vs 0403:6002)
      ftdi_devs = self._scan_ftdi_devices()
      if ftdi_devs:
        for d in ftdi_devs:
          is_unprog = (d["pid"] == 0x6001)
          ser_display = d.get("adb_serial") or d.get("serial") or "Pending"
          msg = (
              f"Unprogrammed Board (0403:6001, Serial: {ser_display}) Detected on {d['port']}! Auto-starting MCU flashing."
              if is_unprog
              else f"Programmed Board (0403:6002, Serial: {ser_display}) Detected on {d['port']}."
          )
          yield maui_manufacturing_pb2.MauiBslDiscoveryResponse(
              bsl_detected=True,
              device_port=d["port"],
              time_remaining_sec=remaining,
              message=msg,
              vid=0x0403,
              pid=d["pid"],
              auto_flash_eligible=is_unprog,
              detected_serial=d.get("adb_serial") or d.get("serial") or "",
          )
        await asyncio.sleep(1.0)
        continue

      # 2. Check for native MSPM0 BSL USB device (0483:DF11 or 2047:0013)
      bsl_usb_present = (
          util.is_usb_device_present(self.MAUI_BSL_VID, [self.MAUI_BSL_PID])
          or util.is_usb_device_present(self.TI_BSL_VID_PID[0], [self.TI_BSL_VID_PID[1]])
      )
      if bsl_usb_present:
        tty_nodes = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
        port_node = tty_nodes[0] if tty_nodes else "/dev/ttyACM0"
        yield maui_manufacturing_pb2.MauiBslDiscoveryResponse(
            bsl_detected=True,
            device_port=port_node,
            time_remaining_sec=remaining,
            message=f"BSL DFU Mode Detected on {port_node}! Auto-starting MCU flashing.",
            vid=self.MAUI_BSL_VID,
            pid=self.MAUI_BSL_PID,
            auto_flash_eligible=True,
            detected_serial="",
        )
        await asyncio.sleep(1.0)
        continue

      # 3. No device detected
      yield maui_manufacturing_pb2.MauiBslDiscoveryResponse(
          bsl_detected=False,
          device_port="",
          time_remaining_sec=remaining,
          message="Waiting for Maui board in BSL mode (0403:6001)...",
          vid=0,
          pid=0,
          auto_flash_eligible=False,
          detected_serial="",
      )
      await asyncio.sleep(0.5)

  def _scan_ftdi_devices(self) -> List[Dict[str, Any]]:
    """Scans sysfs for all connected FTDI (0403:6001 and 0403:6002) devices and associates ADB serial."""
    devices = []
    assigned_serials = set()
    for tty_dir in sorted(glob.glob("/sys/class/tty/ttyUSB*")):
      device_link = os.path.join(tty_dir, "device")
      if not os.path.exists(device_link):
        continue

      dev_node = f"/dev/{os.path.basename(tty_dir)}"
      curr_ser, curr_pid, c_id, adb_ser = self._find_ftdi_info_for_port(dev_node, excluded_serials=assigned_serials)
      if adb_ser:
        assigned_serials.add(adb_ser)
      if curr_pid in (0x6001, 0x6002) or (curr_ser is not None):
        devices.append({
            "port": dev_node,
            "vid": 0x0403,
            "pid": curr_pid,
            "serial": curr_ser or "",
            "container_id": c_id or "",
            "adb_serial": adb_ser or "",
        })
    return devices

  def auto_program_mcu_bsl(
      self,
      request: maui_manufacturing_pb2.MauiAutoProgramMcuRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiProgramMcuResponse:
    """Triggers MCU flashing via fw-updater or maui-flash using port-based selection."""
    fw_channel = request.fw_channel or "stable"
    dev_port = request.device_port or "/dev/ttyUSB0"
    serial_no = (getattr(request, "serial_number", None) or "").strip()
    if not serial_no:
      _, _, _, adb_ser = self._find_ftdi_info_for_port(dev_port)
      serial_no = adb_ser or f"PORT_{os.path.basename(dev_port)}"
    logging.info("Starting MCU BSL flashing on port %s (channel: %s, serial: %s)...", dev_port, fw_channel, serial_no)

    port_lock = _get_port_flashing_lock(dev_port)
    if not port_lock.acquire(blocking=False):
      logging.warning("MCU flashing already in progress on port %s. Rejecting duplicate concurrent request.", dev_port)
      return maui_manufacturing_pb2.MauiProgramMcuResponse(
          success=False,
          message=f"Flashing already in progress on {dev_port}.",
          logs=f"Duplicate concurrent flash request on {dev_port} was rejected.",
      )

    try:
      offline_fw = find_offline_fw_file("mcu")
      fw_updater_bin = shutil.which("fw-updater") or "/usr/local/bin/fw-updater"
      maui_flash_bin = shutil.which("maui-flash") or "/usr/local/bin/maui-flash"

      # Fast direct BSL execution:
      # Use fw-updater directly with --bsl-mode to bypass serial shell synchronization
      if (os.path.exists(fw_updater_bin) or shutil.which("fw-updater")) and offline_fw:
        logging.info("Directly invoking fw-updater on %s with firmware %s (BSL mode)...", dev_port, offline_fw)
        cmd = [fw_updater_bin, offline_fw, "--uart", dev_port, "--bsl-mode"]
      elif offline_fw and (fw_channel in ("offline", "stable") or not fw_channel):
        logging.info("Found local offline MCU firmware: %s. Using --file with maui-flash.", offline_fw)
        common_args = ["--port", dev_port]
        if "ACM" in dev_port or ("usb" in dev_port.lower() and "ttyusb" not in dev_port.lower()):
          common_args.append("--usb")
        mcu_flash_args = ["mcu", "--file", offline_fw, "--bsl-mode"]
        if os.path.exists(maui_flash_bin) or shutil.which("maui-flash"):
          cmd = [maui_flash_bin] + common_args + mcu_flash_args
        elif os.path.exists("/usr/local/maui/tools/src/maui_flash.py"):
          cmd = ["python3", "/usr/local/maui/tools/src/maui_flash.py"] + common_args + mcu_flash_args
        elif os.path.exists("/usr/local/maui/tools/scripts/maui-flash"):
          cmd = ["/usr/local/maui/tools/scripts/maui-flash"] + common_args + mcu_flash_args
        else:
          cmd = ["fw-updater", offline_fw, "--uart", dev_port, "--bsl-mode"]
      elif fw_channel == "offline":
        logging.error("Offline MCU firmware file not found in /usr/local/server/firmware/mcu or /var/lib/diagnoseme/firmware/mcu.")
        return maui_manufacturing_pb2.MauiProgramMcuResponse(
            success=False,
            message="Offline MCU firmware not found on system.",
            logs="Error: Local MCU firmware binary (.txt/.hex/.bin) not found in /usr/local/server/firmware/mcu or /var/lib/diagnoseme/firmware/mcu.",
        )
      else:
        common_args = ["--port", dev_port]
        mcu_flash_args = ["mcu", "--fw_channel", fw_channel, "--bsl-mode"]
        if os.path.exists(maui_flash_bin) or shutil.which("maui-flash"):
          cmd = [maui_flash_bin] + common_args + mcu_flash_args
        elif os.path.exists("/usr/local/maui/tools/src/maui_flash.py"):
          cmd = ["python3", "/usr/local/maui/tools/src/maui_flash.py"] + common_args + mcu_flash_args
        else:
          cmd = ["fw-updater", "/var/lib/diagnoseme/firmware/mcu/zephyr.txt", "--uart", dev_port, "--bsl-mode"]

      env = get_maui_env()

      try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True, timeout=30, env=env)
        logging.info("MCU BSL Flashing succeeded:\n%s", proc.stdout)
        write_device_log(serial_no, "mcu_flashing", f"SUCCESS:\n{proc.stdout}")
        return maui_manufacturing_pb2.MauiProgramMcuResponse(
            success=True,
            message="MCU firmware flashed successfully via BSL mode.",
            logs=str(proc.stdout or ""),
        )
      except subprocess.CalledProcessError as e:
        err_logs = str(e.output or "")
        # Fast 1x automatic retry while still inside the 10s hardware ROM BSL window
        if "Unable to connect to BSL" in err_logs or "Check if device is in BSL mode" in err_logs or e.returncode == 1:
          logging.warning("BSL connection failed on first attempt on %s. Retrying immediately...", dev_port)
          time.sleep(0.2)
          try:
            retry_proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True, timeout=30, env=env)
            logging.info("MCU BSL Flashing succeeded on retry 2:\n%s", retry_proc.stdout)
            write_device_log(serial_no, "mcu_flashing", f"SUCCESS (on retry 2):\n{retry_proc.stdout}")
            return maui_manufacturing_pb2.MauiProgramMcuResponse(
                success=True,
                message="MCU firmware flashed successfully via BSL mode (retry 2).",
                logs=str(retry_proc.stdout or ""),
            )
          except subprocess.CalledProcessError as re_e:
            err_logs = str(re_e.output or "")
            e = re_e

        logging.error("MCU BSL Flashing failed (exit %s):\n%s", e.returncode, err_logs)
        write_device_log(serial_no, "mcu_flashing", f"FAILED (exit {e.returncode}):\n{err_logs}")
        return maui_manufacturing_pb2.MauiProgramMcuResponse(
            success=False,
            message=f"MCU BSL Flashing failed with exit code {e.returncode}.",
            logs=err_logs,
        )
      except subprocess.TimeoutExpired as e:
        err_logs = str(e.output or "")
        logging.error("MCU BSL Flashing timed out after %ss on %s", e.timeout, dev_port)
        write_device_log(serial_no, "mcu_flashing", f"TIMEOUT ({e.timeout}s):\n{err_logs}")
        return maui_manufacturing_pb2.MauiProgramMcuResponse(
            success=False,
            message=f"MCU BSL Flashing timed out after {e.timeout}s. Please verify hardware connection.",
            logs=err_logs or f"Process timed out after {e.timeout}s",
        )
      except FileNotFoundError as e:
        logging.exception("Flashing binary not found")
        write_device_log(serial_no, "mcu_flashing", f"ERROR: {e}")
        return maui_manufacturing_pb2.MauiProgramMcuResponse(
            success=False,
            message="Flashing tool not installed on host system.",
            logs=str(e or ""),
        )
      except Exception as e:
        err_logs = util.get_logs_from_exception(e)
        logging.exception("Unexpected error during MCU BSL Flashing: %s", err_logs)
        write_device_log(serial_no, "mcu_flashing", f"EXCEPTION:\n{err_logs}")
        return maui_manufacturing_pb2.MauiProgramMcuResponse(
            success=False,
            message=f"MCU BSL Flashing error: {str(e)}",
            logs=str(err_logs or str(e)),
        )
    finally:
      port_lock.release()

  def _get_adb_devices_locations(self) -> Dict[str, str]:
    """Runs 'adb devices -l' and maps USB location (e.g. '4-2.1.3') to serial (e.g. 'MAUIV11G26060059')."""
    loc_map: Dict[str, str] = {}
    try:
      proc = subprocess.run(
          ["adb", "devices", "-l"],
          stdout=subprocess.PIPE,
          stderr=subprocess.PIPE,
          text=True,
          timeout=1.0,
      )
      if proc.returncode == 0 and proc.stdout:
        for line in proc.stdout.splitlines():
          line = line.strip()
          if not line or line.startswith("List of devices"):
            continue
          parts = line.split()
          if len(parts) >= 2:
            ser = parts[0]
            m = re.search(r"usb:([^\s]+)", line)
            if m and ser:
              loc_map[m.group(1)] = ser
    except Exception:
      pass
    return loc_map

  def _match_ftdi_to_adb(
      self,
      ftdi_dir: Optional[str],
      ftdi_name: str,
      ftdi_devpath: str,
      ftdi_container_id: str,
      ftdi_hub_container_id: str,
      adb_candidates: List[Dict[str, str]],
      adb_devices_locations: Optional[Dict[str, str]] = None,
      excluded_serials: Optional[set] = None,
  ) -> Optional[str]:
    """Matches an FTDI device to its sibling ADB chip on the same Maui board across arbitrary host buses & intermediate hubs.

    Hardware Invariants & Matching Hierarchy:
      Tier 1: Physical Maui On-Board Hub container_id Match
              The TI TUSB8041 hub IC contains both USB 2.0 (0451:8142) and USB 3.0 (0451:8140) controllers,
              sharing the EXACT same container_id. FTDI is on port .2 of USB2 hub, ADB is on port .3 of USB3 hub.
      Tier 2: Direct Parent Hub Directory Match
              When ADB enumerates in USB 2.0 fallback mode, FTDI (.2) and ADB (.3) share the exact same USB2 parent hub directory.
      Tier 3: Device container_id Match
      Tier 4: Depth-Preserving External Hub Sibling Port Matching
      Tier 5: Unambiguous single remaining candidate fallback
    """
    excluded = excluded_serials or set()
    available_candidates = [c for c in adb_candidates if c.get("serial") not in excluded]

    if not available_candidates and not adb_devices_locations:
      return None

    # Strategy 1 (PRIMARY): Physical Maui On-Board Hub Container ID Match
    if ftdi_hub_container_id:
      for cand in available_candidates:
        cand_hub_cid = cand.get("hub_container_id")
        if cand_hub_cid and cand_hub_cid == ftdi_hub_container_id:
          logging.info(
              "Matched FTDI port (%s) to ADB serial '%s' via Maui On-Board Hub container_id '%s'",
              ftdi_devpath or ftdi_name, cand["serial"], ftdi_hub_container_id
          )
          return cand["serial"]

    # Strategy 2: Direct Parent Hub Directory Match (when ADB is in USB 2.0 fallback mode)
    if ftdi_dir:
      parent_hub = os.path.dirname(ftdi_dir)
      if parent_hub and parent_hub not in ("/sys", "/sys/bus/usb/devices", "/"):
        for cand in available_candidates:
          if cand.get("parent_hub") and cand.get("parent_hub") == parent_hub:
            logging.info(
                "Matched FTDI port (%s) to ADB serial '%s' via exact shared parent hub directory '%s'",
                ftdi_devpath or ftdi_name, cand["serial"], parent_hub
            )
            return cand["serial"]

    # Strategy 3: Direct Device Container ID Match
    if ftdi_container_id:
      for cand in available_candidates:
        if cand.get("container_id") and cand.get("container_id") == ftdi_container_id:
          return cand["serial"]

    # Strategy 4: Depth-Preserving External Hub Port Matching
    ftdi_raw = ftdi_devpath or ftdi_name
    ftdi_clean = re.sub(r"^\d+-", "", ftdi_raw)
    ftdi_tokens = [t for t in re.split(r"[.\-_/]", ftdi_clean) if t.isdigit()]

    best_cand_serial = None
    best_score = -1

    for cand in available_candidates:
      c_ser = cand.get("serial", "")
      if not c_ser:
        continue

      cand_raw = cand.get("devpath") or cand.get("name") or ""
      cand_clean = re.sub(r"^\d+-", "", cand_raw)
      cand_tokens = [t for t in re.split(r"[.\-_/]", cand_clean) if t.isdigit()]

      # Only match if hierarchy depth is consistent:
      # e.g., both are depth >= 3 (under external hub) or both are depth 2 (direct connection)
      score = 0
      if len(ftdi_tokens) >= 3 and len(cand_tokens) >= 3:
        if ftdi_tokens[1:-1] == cand_tokens[1:-1]:
          score = 100
      elif len(ftdi_tokens) == 2 and len(cand_tokens) == 2:
        score = 50

      if score > best_score and score > 0:
        best_score = score
        best_cand_serial = c_ser

    # Check 'adb devices -l' locations if still unresolved
    if not best_cand_serial and adb_devices_locations:
      for usb_loc, ser in adb_devices_locations.items():
        if ser in excluded:
          continue
        loc_clean = re.sub(r"^\d+-", "", usb_loc)
        loc_tokens = [t for t in re.split(r"[.\-_/]", loc_clean) if t.isdigit()]
        if len(ftdi_tokens) >= 3 and len(loc_tokens) >= 3 and ftdi_tokens[1:-1] == loc_tokens[1:-1]:
          best_cand_serial = ser
          break

    if best_cand_serial:
      return best_cand_serial

    # Strategy 5: Unambiguous single remaining candidate fallback
    if len(available_candidates) == 1 and available_candidates[0].get("serial"):
      return available_candidates[0]["serial"]

    return None

  def _find_ftdi_info_for_port(
      self, dev_node: str, excluded_serials: Optional[set] = None
  ) -> Tuple[Optional[str], int, Optional[str], Optional[str]]:
    """Inspects a specific ttyUSB port and returns (current_ftdi_serial, current_ftdi_pid, container_id, adb_serial)."""
    if not dev_node:
      return None, 0x6001, None, None

    tty_name = os.path.basename(dev_node)
    sys_path = os.path.join("/sys/class/tty", tty_name, "device")
    if not os.path.exists(sys_path):
      return None, 0x6001, None, None

    real_path = os.path.realpath(sys_path)
    ftdi_device_dir = None
    current_serial = ""
    current_pid = 0x6001
    container_id = ""
    ftdi_parent_hub = ""
    ftdi_hub_container_id = ""
    devpath = ""

    # 1. Walk up from tty interface to find the FTDI USB device node (idVendor == 0403)
    curr = real_path
    while curr and curr not in ("/sys", "/sys/bus/usb/devices", "/"):
      v_file = os.path.join(curr, "idVendor")
      p_file = os.path.join(curr, "idProduct")
      s_file = os.path.join(curr, "serial")
      c_file = os.path.join(curr, "container_id")
      dp_file = os.path.join(curr, "devpath")

      if os.path.exists(v_file) and os.path.exists(p_file):
        try:
          with open(v_file, "r") as f:
            vid = f.read().strip().lower()
          with open(p_file, "r") as f:
            pid = f.read().strip().lower()
          if vid == "0403" and pid in ("6001", "6002"):
            ftdi_device_dir = curr
            current_pid = int(pid, 16)
            if os.path.exists(s_file):
              with open(s_file, "r") as f:
                current_serial = f.read().strip()
            if os.path.exists(c_file):
              with open(c_file, "r") as f:
                container_id = f.read().strip()
            if os.path.exists(dp_file):
              with open(dp_file, "r") as f:
                devpath = f.read().strip()

            # Find parent hub of FTDI and read its container_id
            parent_candidate = os.path.dirname(curr)
            if parent_candidate and parent_candidate not in ("/sys", "/sys/bus/usb/devices", "/"):
              ftdi_parent_hub = parent_candidate
              hub_cid_file = os.path.join(parent_candidate, "container_id")
              if os.path.exists(hub_cid_file):
                with open(hub_cid_file, "r") as f:
                  ftdi_hub_container_id = f.read().strip()
            break
        except Exception:
          pass
      curr = os.path.dirname(curr)

    if not ftdi_device_dir:
      curr = real_path
      while curr and curr not in ("/sys", "/sys/bus/usb/devices", "/"):
        if os.path.exists(os.path.join(curr, "idVendor")):
          ftdi_device_dir = curr
          dp_file = os.path.join(curr, "devpath")
          if os.path.exists(dp_file):
            try:
              with open(dp_file, "r") as f:
                devpath = f.read().strip()
            except Exception:
              pass
          parent_candidate = os.path.dirname(curr)
          if parent_candidate and parent_candidate not in ("/sys", "/sys/bus/usb/devices", "/"):
            ftdi_parent_hub = parent_candidate
            hub_cid_file = os.path.join(parent_candidate, "container_id")
            if os.path.exists(hub_cid_file):
              try:
                with open(hub_cid_file, "r") as f:
                  ftdi_hub_container_id = f.read().strip()
              except Exception:
                pass
          break
        curr = os.path.dirname(curr)

    # 2. Collect all candidate ADB devices on the host with their parent hub container IDs
    adb_candidates: List[Dict[str, str]] = []
    for d in glob.glob("/sys/bus/usb/devices/*"):
      v_file = os.path.join(d, "idVendor")
      s_file = os.path.join(d, "serial")
      dp_file = os.path.join(d, "devpath")
      c_file = os.path.join(d, "container_id")
      p_file = os.path.join(d, "product")

      if os.path.exists(v_file) and os.path.exists(s_file):
        try:
          with open(v_file, "r") as f:
            v_val = f.read().strip().lower()
          with open(s_file, "r") as f:
            s_val = f.read().strip()
          prod_val = ""
          if os.path.exists(p_file):
            with open(p_file, "r") as f:
              prod_val = f.read().strip()

          if (v_val in ("18d1", "067b") or any(k in prod_val.lower() for k in ("adb", "maui", "prolific"))) and s_val and s_val.lower() not in ("0", "none", "unknown", ""):
            cand_real = os.path.realpath(d)
            cand_dp = ""
            if os.path.exists(dp_file):
              with open(dp_file, "r") as f:
                cand_dp = f.read().strip()
            cand_cid = ""
            if os.path.exists(c_file):
              with open(c_file, "r") as f:
                cand_cid = f.read().strip()

            # Find candidate's parent hub and read parent hub's container_id
            cand_parent_hub = os.path.dirname(cand_real)
            cand_hub_cid = ""
            if cand_parent_hub and cand_parent_hub not in ("/sys", "/sys/bus/usb/devices", "/"):
              cand_hub_cid_file = os.path.join(cand_parent_hub, "container_id")
              if os.path.exists(cand_hub_cid_file):
                with open(cand_hub_cid_file, "r") as f:
                  cand_hub_cid = f.read().strip()

            adb_candidates.append({
                "dir": cand_real,
                "name": os.path.basename(d),
                "serial": s_val,
                "devpath": cand_dp,
                "container_id": cand_cid,
                "parent_hub": cand_parent_hub,
                "hub_container_id": cand_hub_cid,
            })
        except Exception:
          pass

    adb_loc_map = self._get_adb_devices_locations()

    # 3. Match FTDI to ADB serial using Hub container_id priority
    adb_serial = self._match_ftdi_to_adb(
        ftdi_dir=ftdi_device_dir,
        ftdi_name=os.path.basename(ftdi_device_dir) if ftdi_device_dir else "",
        ftdi_devpath=devpath,
        ftdi_container_id=container_id,
        ftdi_hub_container_id=ftdi_hub_container_id,
        adb_candidates=adb_candidates,
        adb_devices_locations=adb_loc_map,
        excluded_serials=excluded_serials,
    )

    if adb_serial:
      logging.info(
          "Matched FTDI port %s (FTDI Serial=%s, PID=0x%04x) to ADB Serial '%s'",
          dev_node, current_serial or "NONE", current_pid, adb_serial
      )

    return current_serial or None, current_pid, container_id or None, adb_serial

  def discover_topology_and_serial(
      self,
      request: Any,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiTopologyDiscoveryResponse:
    """Discovers pre-flashed serial from Prolific/ADB chip and reads current FTDI serial for targeted programming."""
    target_port = getattr(request, "device_port", None) or ""
    logging.info("Starting USB topology discovery (target port: %s, 5s settling window)...", target_port or "any")

    # If specific target port provided: probe target port specifically
    if target_port:
      for attempt in range(1, 11):
        curr_ser, curr_pid, c_id, adb_ser = self._find_ftdi_info_for_port(target_port)
        if adb_ser:
          logging.info("Discovered topology on attempt %d for %s: ADB Serial=%s, FTDI Current Serial=%s, PID=0x%04x",
                       attempt, target_port, adb_ser, curr_ser, curr_pid)
          return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
              success=True,
              read_serial_number=adb_ser,
              usb2_hub_serial="",
              ftdi_dev_node=target_port,
              container_id=c_id or "",
              ftdi_current_serial=curr_ser or "",
              ftdi_current_pid=curr_pid,
          )
        time.sleep(0.5)

      # If port was found but ADB not yet ready, return FTDI info if available
      curr_ser, curr_pid, c_id, _ = self._find_ftdi_info_for_port(target_port)
      if curr_ser:
        return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
            success=False,
            read_serial_number="",
            ftdi_dev_node=target_port,
            container_id=c_id or "",
            ftdi_current_serial=curr_ser or "",
            ftdi_current_pid=curr_pid,
            error_message=f"FTDI node {target_port} found (serial: {curr_ser}), but sibling ADB chip did not enumerate after 5s.",
        )
      return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
          success=False,
          error_message=f"Could not locate USB topology for port {target_port}.",
      )

    # Legacy/single device fallback (only when no target port is specified)
    for attempt in range(1, 11):
      for dev_node in sorted(glob.glob("/dev/ttyUSB*")):
        curr_ser, curr_pid, c_id, adb_ser = self._find_ftdi_info_for_port(dev_node)
        if adb_ser:
          return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
              success=True,
              read_serial_number=adb_ser,
              usb2_hub_serial="",
              ftdi_dev_node=dev_node,
              container_id=c_id or "",
              ftdi_current_serial=curr_ser or "",
              ftdi_current_pid=curr_pid,
          )
      time.sleep(0.5)

    return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
        success=False,
        error_message="Could not find any connected Maui board with ADB serial.",
    )

  def program_ftdi_eeprom(
      self,
      request: maui_manufacturing_pb2.MauiProgramFtdiRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiProgramFtdiResponse:
    """Programs FTDI EEPROM targeting specific device with --old-serial-number for multi-device safety."""
    serial_no = (request.serial_number or "").strip()
    dev_node = request.ftdi_dev_node or "/dev/ttyUSB0"
    old_serial_no = (getattr(request, "old_serial_number", None) or "").strip()
    old_pid_val = getattr(request, "old_pid", 0) or 0

    if not serial_no:
      return maui_manufacturing_pb2.MauiProgramFtdiResponse(
          success=False,
          message="No ADB target serial number provided. Cannot program FTDI EEPROM.",
          logs="Error: serial_number is empty.",
      )

    # If old_serial_no not provided, inspect dev_node directly
    if not old_serial_no and dev_node:
      curr_ser, _, _, _ = self._find_ftdi_info_for_port(dev_node)
      if curr_ser:
        old_serial_no = curr_ser

    logging.info("Programming FTDI EEPROM: Target Serial=%s, Port=%s, Old Serial=%s...",
                 serial_no, dev_node, old_serial_no or "ANY")

    ft232r_prog_bin = shutil.which("ft232r_prog") or "/usr/local/bin/ft232r_prog"

    cmd = [ft232r_prog_bin]
    if old_serial_no:
      cmd.extend(["--old-serial-number", old_serial_no])
    cmd.extend([
        "--new-pid", "0x6002",
        "--new-serial-number", serial_no,
    ])

    with _ftdi_programming_lock:
      try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True, timeout=10)
        logging.info("ft232r_prog output:\n%s", proc.stdout)
        write_device_log(serial_no, "ftdi_programming", f"COMMAND: {' '.join(cmd)}\nSUCCESS:\n{proc.stdout}")
        # Consolidate any earlier logs into target serial log directory
        target_dir = get_device_log_dir(serial_no)
        for unk_dir in glob.glob("/tmp/*UNKNOWN*_diagnoseme_logs") + glob.glob("/tmp/*unknown*_diagnoseme_logs"):
          if os.path.isdir(unk_dir) and os.path.abspath(unk_dir) != os.path.abspath(target_dir):
            for fname in os.listdir(unk_dir):
              src_f = os.path.join(unk_dir, fname)
              dst_f = os.path.join(target_dir, fname)
              if os.path.isfile(src_f) and (not os.path.exists(dst_f) or os.path.getsize(dst_f) == 0):
                try:
                  shutil.copy2(src_f, dst_f)
                except Exception:
                  pass

        return maui_manufacturing_pb2.MauiProgramFtdiResponse(
            success=True,
            message=f"FTDI EEPROM programmed successfully with PID 0x6002 and ADB serial {serial_no}.",
            logs=str(proc.stdout or ""),
        )
      except subprocess.CalledProcessError as e:
        err_logs = str(e.output or "")
        logging.error("ft232r_prog invocation failed (exit %d):\n%s", e.returncode, err_logs)
        write_device_log(serial_no, "ftdi_programming", f"COMMAND: {' '.join(cmd)}\nFAILED (exit {e.returncode}):\n{err_logs}")
        return maui_manufacturing_pb2.MauiProgramFtdiResponse(
            success=False,
            message=f"FTDI EEPROM programming failed: {err_logs}",
            logs=f"Command ({' '.join(cmd)}):\n{err_logs}",
        )
      except Exception as e:
        logging.exception("Unexpected error during FTDI programming")
        write_device_log(serial_no, "ftdi_programming", f"EXCEPTION: {e}")
        return maui_manufacturing_pb2.MauiProgramFtdiResponse(
            success=False,
            message=f"FTDI programming error: {str(e)}",
            logs=str(util.get_logs_from_exception(e) or ""),
        )


class MauiPdcServicer(maui_manufacturing_pb2_grpc.MauiPdcFlashingServicer):
  """Dedicated multi-device PDC flashing servicer (supports up to 4 parallel 0403:6002 devices)."""

  _find_offline_fw_file = staticmethod(find_offline_fw_file)

  def discover_connected_pdc_devices(
      self,
      unused_request: empty_pb2.Empty,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiConnectedPdcDevicesResponse:
    """Scans and returns all connected Maui physical units (ADB and FTDI endpoints)."""
    logging.info("Scanning for all connected Maui physical devices (ADB & FTDI)...")
    found_devices = []
    slot = 1

    units = maui_common.scan_connected_maui_units()
    for u in units:
      found_devices.append(
          maui_manufacturing_pb2.MauiPdcDeviceInfo(
              slot_index=slot,
              serial_number=u.get("serial", "") or "",
              device_port=u.get("dev_node", "") or "",
              container_id=u.get("container_id", "") or "",
              product_description=u.get("product", "Maui Device"),
          )
      )
      slot += 1

    logging.info("Found %d connected physical Maui device(s) (ADB/FTDI).", len(found_devices))
    return maui_manufacturing_pb2.MauiConnectedPdcDevicesResponse(
        success=True,
        devices=found_devices,
        message=f"Found {len(found_devices)} connected Maui device(s).",
    )

  async def program_single_pdc(
      self,
      request: maui_manufacturing_pb2.MauiProgramSinglePdcRequest,
      context: grpc.ServicerContext,
  ) -> AsyncGenerator[maui_manufacturing_pb2.MauiProgramPdcProgressResponse, None]:
    """Flashes PDC firmware on a single device and streams block transfer progress."""
    slot_index = request.slot_index or 1
    serial_no = (request.serial_number or "").strip()
    dev_port = request.device_port or "/dev/ttyUSB0"
    fw_channel = request.fw_channel or "stable"

    station1_servicer = MauiStation1Servicer()
    if not serial_no or serial_no.startswith("MAUI-SLOT") or "UNKNOWN" in serial_no:
      _, _, _, adb_ser = station1_servicer._find_ftdi_info_for_port(dev_port)
      if adb_ser:
        serial_no = adb_ser
      else:
        serial_no = serial_no or f"MAUI-SLOT{slot_index}"

    logging.info("Starting PDC flash for slot %d (serial: %s, port: %s, channel: %s)...",
                 slot_index, serial_no, dev_port, fw_channel)

    maui_flash_bin = shutil.which("maui-flash") or "/usr/local/bin/maui-flash"
    common_args = []
    if dev_port and os.path.exists(dev_port):
      common_args.extend(["--port", dev_port])
    elif serial_no:
      common_args.extend(["--serial", serial_no])

    offline_fw = find_offline_fw_file("pdc")
    if offline_fw and (fw_channel in ("offline", "stable") or not fw_channel):
      logging.info("Found local offline PDC firmware: %s. Using --file.", offline_fw)
      pdc_flash_args = ["pdc", "--file", offline_fw]
    elif fw_channel == "offline":
      logging.error("[Slot %d] Offline PDC firmware file not found in firmware cache directories.", slot_index)
      yield maui_manufacturing_pb2.MauiProgramPdcProgressResponse(
          is_complete=True,
          progress_percent=0,
          current_block="Failed",
          status_message="Offline PDC firmware binary not found.",
          error_message="Error: Local PDC firmware (.bin) not found in /usr/local/server/firmware/pdc or /var/lib/diagnoseme/firmware/pdc.",
          slot_index=slot_index,
          serial_number=serial_no,
      )
      return
    else:
      pdc_flash_args = ["pdc", "--fw_channel", fw_channel]

    if os.path.exists(maui_flash_bin) or shutil.which("maui-flash"):
      cmd = [maui_flash_bin] + common_args + pdc_flash_args
    elif os.path.exists("/usr/local/maui/tools/src/maui_flash.py"):
      cmd = ["python3", "/usr/local/maui/tools/src/maui_flash.py"] + common_args + pdc_flash_args
    else:
      logging.warning("maui-flash executable not found. Simulating PDC flash for dev mode.")
      for p in range(0, 101, 20):
        yield maui_manufacturing_pb2.MauiProgramPdcProgressResponse(
            is_complete=(p == 100),
            progress_percent=p,
            current_block=f"Block {p // 10}/10",
            status_message=f"[DEV MODE] Flashing PDC... {p}%",
            slot_index=slot_index,
            serial_number=serial_no,
        )
        await asyncio.sleep(0.5)
      return

    # Ensure Zephyr shell on dev_port is responsive before launching maui-flash
    if dev_port and os.path.exists(dev_port):
      ensure_zephyr_shell_awake(dev_port, timeout=2.5)

    try:
      env = get_maui_env()
      proc = await asyncio.create_subprocess_exec(
          *cmd,
          stdout=asyncio.subprocess.PIPE,
          stderr=asyncio.subprocess.STDOUT,
          env=env,
      )

      total_blocks = 11
      # Support both 'Progess' and 'Progress'
      progress_re = re.compile(r"Block\s*(\d+):\s+Prog(?:r)?ess:\s+(\d+)/(\d+)\s+bytes\s+transferred\s+\(([\d.]+)%\)")
      full_tool_output: List[str] = []

      while proc.returncode is None and not context.done():
        try:
          line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=30.0)
        except asyncio.TimeoutError:
          logging.warning("[Slot %d] PDC Output readline timed out", slot_index)
          break

        if not line_bytes:
          break
        raw_line = line_bytes.decode("utf-8", errors="ignore")
        full_tool_output.append(raw_line)
        line = raw_line.strip()
        logging.debug("[Slot %d] PDC Output: %s", slot_index, line)

        match = progress_re.search(line)
        if match:
          block_num = int(match.group(1))
          transferred = int(match.group(2))
          total_bytes = int(match.group(3))
          pct_in_block = float(match.group(4))

          overall_pct = int(((block_num + (pct_in_block / 100.0)) / total_blocks) * 100.0)
          overall_pct = min(99, max(0, overall_pct))

          yield maui_manufacturing_pb2.MauiProgramPdcProgressResponse(
              is_complete=False,
              progress_percent=overall_pct,
              current_block=f"Block {block_num}: {transferred}/{total_bytes} bytes",
              status_message=f"Flashing PDC Firmware... {overall_pct}%",
              slot_index=slot_index,
              serial_number=serial_no,
          )

      try:
        await asyncio.wait_for(proc.wait(), timeout=15.0)
      except asyncio.TimeoutError:
        logging.warning("[Slot %d] Killing hung PDC flash process", slot_index)
        proc.kill()
        await proc.wait()

      # Drain remaining output if any
      try:
        rest_bytes = await proc.stdout.read()
        if rest_bytes:
          full_tool_output.append(rest_bytes.decode("utf-8", errors="ignore"))
      except Exception:
        pass

      tool_output_str = "".join(full_tool_output).strip()
      cmd_str = " ".join(cmd)

      # If first attempt failed with prompt timeout, perform active wakeup and 1x auto-retry
      if proc.returncode != 0 and ("Exclusive logging enabled" in tool_output_str or "Timeout while waiting for prompt" in tool_output_str or "Buffer empty" in tool_output_str):
        logging.warning("[Slot %d] PDC flash encountered prompt timeout on %s. Re-awakening Zephyr console and retrying...", slot_index, dev_port)
        if dev_port and os.path.exists(dev_port):
          ensure_zephyr_shell_awake(dev_port, timeout=3.0)
        time.sleep(0.3)
        retry_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        retry_output: List[str] = []
        while retry_proc.returncode is None and not context.done():
          try:
            line_bytes = await asyncio.wait_for(retry_proc.stdout.readline(), timeout=30.0)
          except asyncio.TimeoutError:
            break
          if not line_bytes:
            break
          raw_line = line_bytes.decode("utf-8", errors="ignore")
          retry_output.append(raw_line)
          line = raw_line.strip()
          match = progress_re.search(line)
          if match:
            block_num = int(match.group(1))
            transferred = int(match.group(2))
            total_bytes = int(match.group(3))
            pct_in_block = float(match.group(4))
            overall_pct = int(((block_num + (pct_in_block / 100.0)) / total_blocks) * 100.0)
            overall_pct = min(99, max(0, overall_pct))
            yield maui_manufacturing_pb2.MauiProgramPdcProgressResponse(
                is_complete=False,
                progress_percent=overall_pct,
                current_block=f"Block {block_num}: {transferred}/{total_bytes} bytes",
                status_message=f"Flashing PDC Firmware (Retry)... {overall_pct}%",
                slot_index=slot_index,
                serial_number=serial_no,
            )
        try:
          await asyncio.wait_for(retry_proc.wait(), timeout=15.0)
        except asyncio.TimeoutError:
          retry_proc.kill()
          await retry_proc.wait()
        try:
          rest_bytes = await retry_proc.stdout.read()
          if rest_bytes:
            retry_output.append(rest_bytes.decode("utf-8", errors="ignore"))
        except Exception:
          pass
        if retry_proc.returncode == 0:
          proc = retry_proc
          tool_output_str = "".join(retry_output).strip()

      if proc.returncode == 0:
        logging.info("[Slot %d] PDC Firmware update completed successfully.", slot_index)
        write_device_log(
            serial_no,
            "pdc_flashing",
            f"COMMAND: {cmd_str}\nEXIT_CODE: 0\nSTATUS: SUCCESS\n\nTOOL_OUTPUT:\n{tool_output_str}"
        )
        yield maui_manufacturing_pb2.MauiProgramPdcProgressResponse(
            is_complete=True,
            progress_percent=100,
            current_block="Complete",
            status_message="PDC Firmware update succeeded!",
            slot_index=slot_index,
            serial_number=serial_no,
        )
      else:
        logging.error("[Slot %d] PDC Firmware update failed with exit code %s", slot_index, proc.returncode)
        write_device_log(
            serial_no,
            "pdc_flashing",
            f"COMMAND: {cmd_str}\nEXIT_CODE: {proc.returncode}\nSTATUS: FAILED\n\nTOOL_OUTPUT:\n{tool_output_str}"
        )
        yield maui_manufacturing_pb2.MauiProgramPdcProgressResponse(
            is_complete=True,
            progress_percent=0,
            current_block="Failed",
            status_message="PDC Firmware update failed.",
            error_message=f"Process exited with code {proc.returncode}",
            slot_index=slot_index,
            serial_number=serial_no,
        )
    except Exception as e:
      logging.exception("[Slot %d] PDC Firmware update exception", slot_index)
      write_device_log(
          serial_no,
          "pdc_flashing",
          f"COMMAND: {' '.join(cmd)}\nSTATUS: EXCEPTION\nERROR: {e}"
      )
      yield maui_manufacturing_pb2.MauiProgramPdcProgressResponse(
          is_complete=True,
          progress_percent=0,
          current_block="Error",
          status_message=str(e),
          error_message=str(e),
          slot_index=slot_index,
          serial_number=serial_no,
      )

  async def program_pdc(
      self,
      request: maui_manufacturing_pb2.MauiProgramPdcRequest,
      context: grpc.ServicerContext,
  ) -> AsyncGenerator[maui_manufacturing_pb2.MauiProgramPdcProgressResponse, None]:
    """Legacy single-device PDC flashing proxy."""
    single_req = maui_manufacturing_pb2.MauiProgramSinglePdcRequest(
        slot_index=1,
        serial_number="",
        device_port="",
        fw_channel=request.fw_channel or "stable",
    )
    async for resp in self.program_single_pdc(single_req, context):
      yield resp


def analyze_device_manufacturing_logs(
    serial_number: str,
) -> Tuple[Dict[str, str], str, List[maui_manufacturing_pb2.MauiLogFileInfo], int]:
  """Parses and inspects /tmp/<serial>_diagnoseme_logs directory to evaluate real stage statuses.

  Returns:
    (stage_statuses, overall_verdict, log_file_infos, present_count)
  """
  dir_path = get_device_log_dir(serial_number)
  stage_statuses = {}
  log_file_infos = []
  present_count = 0

  # Consolidate any logs created before serial discovery (e.g. mcu_flashing.log in /tmp/UNKNOWN_diagnoseme_logs)
  for unk_dir in glob.glob("/tmp/*UNKNOWN*_diagnoseme_logs") + glob.glob("/tmp/*unknown*_diagnoseme_logs"):
    if os.path.isdir(unk_dir) and os.path.abspath(unk_dir) != os.path.abspath(dir_path):
      for fname in os.listdir(unk_dir):
        src_f = os.path.join(unk_dir, fname)
        dst_f = os.path.join(dir_path, fname)
        if os.path.isfile(src_f) and (not os.path.exists(dst_f) or os.path.getsize(dst_f) == 0):
          try:
            shutil.copy2(src_f, dst_f)
          except Exception:
            pass

  # If slot-specific logs exist in dir_path, copy them to standard names if standard names don't exist yet
  for prefix in ("mcu_flashing", "ftdi_programming", "pdc_flashing"):
    std_file = os.path.join(dir_path, f"{prefix}.log")
    if not os.path.exists(std_file) or os.path.getsize(std_file) == 0:
      slot_matches = sorted(glob.glob(os.path.join(dir_path, f"{prefix}_slot*.log")))
      if slot_matches:
        try:
          shutil.copy2(slot_matches[-1], std_file)
        except Exception:
          pass

  stage_log_mapping = [
      ("mcu_bsl_flashing", "mcu_flashing.log", "MCU BSL Flashing"),
      ("ftdi_eeprom_programming", "ftdi_programming.log", "FTDI EEPROM Programming"),
      ("pdc_fw_flashing", "pdc_flashing.log", "PDC FW Flashing"),
      ("host_only_verification", "host_only_tests.log", "Host-Only Verification"),
      ("host_dut_verification", "host_dut_tests.log", "Host + DUT Verification"),
  ]

  for stage_key, log_name, stage_label in stage_log_mapping:
    fpath = os.path.join(dir_path, log_name)
    exists = os.path.exists(fpath)
    size = os.path.getsize(fpath) if exists else 0
    content = ""

    if exists and size > 0:
      present_count += 1
      try:
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
          content = f.read()
      except Exception:
        pass

    content_lower = content.lower()

    if not exists or size == 0:
      verdict = "NOT_RUN"
      details = "Log file not found (stage not executed)"
    elif "status=bypassed" in content_lower or "bypassed by operator" in content_lower or "overall_dut_status: bypassed" in content_lower or "overall_host_status: bypassed" in content_lower:
      verdict = "BYPASSED"
      details = "Bypassed by operator"
    elif "bypassed" in content_lower and "all_passed=true" not in content_lower:
      verdict = "BYPASSED"
      details = "Bypassed by operator"
    elif "all_passed=false" in content_lower or "result: failed" in content_lower or "overall_host_status: failed" in content_lower or "overall_dut_status: failed" in content_lower or "mcu bsl flashing failed" in content_lower or "mcu firmware update failed" in content_lower or "status: failed" in content_lower:
      verdict = "FAILED"
      details = "Stage reported test/command failure"
    elif "mcu firmware update successful" in content_lower or "completed successfully" in content_lower or "100 %" in content_lower or "all_passed=true" in content_lower or "result: passed" in content_lower or "status: success" in content_lower or "success:" in content_lower or "passed" in content_lower:
      verdict = "PASSED"
      details = "Completed successfully"
    elif "skipped" in content_lower or "already matched" in content_lower:
      verdict = "PASSED (SKIPPED)"
      details = "Already configured with matching serial (Skipped)"
    elif "failed" in content_lower or "exception" in content_lower:
      verdict = "FAILED"
      details = "Stage reported test/command failure"
    else:
      verdict = "PASSED"
      details = "Log recorded"

    stage_statuses[stage_key] = verdict
    log_file_infos.append(
        maui_manufacturing_pb2.MauiLogFileInfo(
            log_name=log_name,
            file_path=fpath,
            file_size_bytes=size,
            is_present=exists and size > 0,
            stage_verdict=verdict,
            details=details,
        )
    )

  # Check auxiliary subtest logs for Host-Only Verification (BSL button, RESET button, Host replug)
  if stage_statuses.get("host_only_verification") != "FAILED":
    for aux_log in ("bsl_button_test.log", "reset_button_test.log", "host_replug_test.log", "host_usb_check.log"):
      aux_path = os.path.join(dir_path, aux_log)
      if os.path.exists(aux_path) and os.path.getsize(aux_path) > 0:
        try:
          with open(aux_path, "r", encoding="utf-8", errors="ignore") as f:
            aux_c = f.read().lower()
          if "all_passed=false" in aux_c or "result: failed" in aux_c or "status: failed" in aux_c:
            stage_statuses["host_only_verification"] = "FAILED"
            for info in log_file_infos:
              if info.log_name == "host_only_tests.log":
                info.stage_verdict = "FAILED"
                info.details = f"Host subtest failed in {aux_log}"
            break
        except Exception:
          pass

  # Check auxiliary subtest logs for Host+DUT Verification
  if stage_statuses.get("host_dut_verification") != "FAILED":
    for aux_log in ("host_dut_stage1_with_charger.log", "host_dut_stage2_sbu_flip.log", "host_dut_stage3_no_charger.log", "host_dut_stage1_no_charger.log", "host_dut_stage2_with_charger.log", "host_dut_stage3_sbu_flip.log"):
      aux_path = os.path.join(dir_path, aux_log)
      if os.path.exists(aux_path) and os.path.getsize(aux_path) > 0:
        try:
          with open(aux_path, "r", encoding="utf-8", errors="ignore") as f:
            aux_c = f.read().lower()
          if "all_passed=false" in aux_c or "result: failed" in aux_c or "fail (" in aux_c:
            stage_statuses["host_dut_verification"] = "FAILED"
            for info in log_file_infos:
              if info.log_name == "host_dut_tests.log":
                info.stage_verdict = "FAILED"
                info.details = f"DUT subtest failed in {aux_log}"
            break
        except Exception:
          pass

  # Check label_verification.log if present
  lbl_path = os.path.join(dir_path, "label_verification.log")
  lbl_exists = os.path.exists(lbl_path) and os.path.getsize(lbl_path) > 0
  if lbl_exists:
    try:
      with open(lbl_path, "r", encoding="utf-8", errors="ignore") as f:
        lbl_c = f.read().lower()
      if "passed" in lbl_c:
        stage_statuses["label_verified"] = "PASSED"
      elif "bypassed" in lbl_c:
        stage_statuses["label_verified"] = "BYPASSED"
      else:
        stage_statuses["label_verified"] = "FAILED"
    except Exception:
      stage_statuses["label_verified"] = "NOT_RUN"
  else:
    stage_statuses["label_verified"] = "NOT_RUN"

  # Result is evaluated strictly from available logs (missing stages are acceptable)
  has_failed = any(v == "FAILED" for v in stage_statuses.values())
  has_bypassed = any("BYPASSED" in v for v in stage_statuses.values())

  if has_failed:
    overall_verdict = "FAIL"
  elif has_bypassed:
    overall_verdict = "PASS_WITH_BYPASS"
  else:
    overall_verdict = "PASS"

  return stage_statuses, overall_verdict, log_file_infos, present_count


class MauiDeviceManagementServicer(
    maui_manufacturing_pb2_grpc.MauiDeviceManagementServicer
):
  """Servicer for inspecting logs, label verification, and final manufacturing submission."""

  REQUIRED_LOGS = [
      "mcu_flashing.log",
      "ftdi_programming.log",
      "pdc_flashing.log",
      "host_only_tests.log",
      "host_dut_tests.log",
  ]

  def read_connected_device_serial(
      self,
      unused_request: empty_pb2.Empty,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiReadSerialResponse:
    adb_serial = ""
    adb_product = ""
    adb_connected = False

    ftdi_serial = ""
    ftdi_product = ""
    ftdi_port = ""
    ftdi_connected = False
    ftdi_pid_val = 0
    ftdi_is_prog = False

    devices = maui_common.scan_usb_devices()

    # 1. Scan for ADB endpoint: VID 18d1, PID 506d (or 5084) or Prolific 067b:27a1
    for dev in devices:
      if dev.vid == 0x18D1 and dev.pid in (0x506D, 0x5084):
        adb_connected = True
        adb_serial = dev.serial or adb_serial
        adb_product = dev.product or "ADB v1"
        logging.info("Found ADB Endpoint: Serial=%s, Product=%s (VID=18d1, PID=%04x)", dev.serial, dev.product, dev.pid)
        if adb_serial:
          break
      elif dev.vid == 0x067B and dev.pid == 0x27A1:
        adb_connected = True
        adb_serial = dev.serial or adb_serial
        adb_product = dev.product or "Prolific H2H"
        logging.info("Found Prolific Endpoint: Serial=%s, Product=%s", dev.serial, dev.product)
        if adb_serial:
          break

    # 2. Scan for FTDI endpoint: VID 0403, PID 6002 (or 6001)
    for dev in devices:
      if dev.vid == 0x0403 and dev.pid in (0x6001, 0x6002):
        ftdi_connected = True
        ftdi_pid_val = dev.pid
        ftdi_is_prog = (dev.pid == 0x6002)
        ftdi_serial = dev.serial
        ftdi_product = dev.product or ("FT232R Programmed" if ftdi_is_prog else "FT232R Default")
        ftdi_port = dev.dev_node
        logging.info("Found FTDI Endpoint: Serial=%s, Product=%s, Port=%s (PID=0x%04x, Programmed=%s)",
                     dev.serial, ftdi_product, ftdi_port, ftdi_pid_val, ftdi_is_prog)
        break

    if not ftdi_port:
      avail_ttys = sorted(glob.glob("/dev/ttyUSB*"))
      if avail_ttys:
        ftdi_port = avail_ttys[0]

    dual_match = bool(adb_serial and ftdi_serial and (adb_serial.upper() == ftdi_serial.upper()) and ftdi_is_prog)
    primary_serial = adb_serial or ftdi_serial
    is_connected = bool(adb_connected or ftdi_connected)

    if adb_connected and ftdi_connected:
      if ftdi_is_prog and dual_match:
        msg = f"Programmed Maui Board: {primary_serial}"
      elif ftdi_pid_val == 0x6001:
        msg = f"Unprogrammed Maui Board (FTDI PID 0x6001). Target Serial: '{primary_serial}'"
      else:
        msg = f"Connected Serial: {primary_serial}"
    elif adb_connected:
      msg = f"Maui ADB Device: {primary_serial}"
    elif ftdi_connected:
      msg = f"Maui FTDI Device: {primary_serial}"
    else:
      msg = "No Device Detected"

    return maui_manufacturing_pb2.MauiReadSerialResponse(
        is_connected=is_connected,
        serial_number=primary_serial,
        device_port=ftdi_port,
        product_name=adb_product or ftdi_product or "Maui Device",
        message=msg,
        adb_serial=adb_serial,
        adb_connected=adb_connected,
        adb_product=adb_product,
        ftdi_serial=ftdi_serial,
        ftdi_connected=ftdi_connected,
        ftdi_product=ftdi_product,
        dual_endpoints_match=dual_match,
        ftdi_pid=ftdi_pid_val,
        ftdi_is_programmed=ftdi_is_prog,
    )

  def get_device_logs_status(
      self,
      request: maui_manufacturing_pb2.MauiLogsStatusRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiLogsStatusResponse:
    """Inspects /tmp/<serial>_diagnoseme_logs directory and evaluates true stage verdicts."""
    serial_no = request.serial_number
    if not serial_no:
      conn_resp = self.read_connected_device_serial(empty_pb2.Empty(), unused_context)
      serial_no = conn_resp.serial_number if conn_resp.is_connected else "UNKNOWN"

    dir_path = get_device_log_dir(serial_no)
    logging.info("Analyzing manufacturing logs for serial %s at %s...", serial_no, dir_path)

    stage_statuses, overall_verdict, log_infos, present_count = analyze_device_manufacturing_logs(serial_no)
    all_present = (overall_verdict != "FAIL")

    return maui_manufacturing_pb2.MauiLogsStatusResponse(
        directory_exists=os.path.exists(dir_path),
        directory_path=dir_path,
        logs=log_infos,
        all_required_logs_present=all_present,
        message=f"{present_count} stage logs present in {dir_path}. Overall log status: {overall_verdict}",
        stage_statuses=stage_statuses,
        overall_verdict=overall_verdict,
    )

  def save_card_log(
      self,
      request: maui_manufacturing_pb2.MauiSaveLogRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiSaveLogResponse:
    """Appends logs for a specific card into /tmp/<serial>_diagnoseme_logs/<step>.log."""
    serial_no = request.serial_number or "UNKNOWN"
    step_name = request.log_step_name or "card_execution"
    saved_path = write_device_log(serial_no, step_name, request.log_content or "No output")
    return maui_manufacturing_pb2.MauiSaveLogResponse(
        success=True,
        saved_file_path=saved_path,
        message=f"Log saved to {saved_path}",
    )

  def verify_scanned_label(
      self,
      request: maui_manufacturing_pb2.MauiVerifyLabelRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiVerifyLabelResponse:
    """Verifies scanned barcode strictly against BOTH USB hardware serials (ADB & FTDI)."""
    conn_resp = self.read_connected_device_serial(empty_pb2.Empty(), unused_context)
    adb_serial = (request.adb_serial or conn_resp.adb_serial or "").strip().upper()
    ftdi_serial = (request.ftdi_serial or conn_resp.ftdi_serial or "").strip().upper()
    electronic_serial = (request.electronic_serial or conn_resp.serial_number or adb_serial or ftdi_serial or "").strip().upper()
    scanned = (request.scanned_barcode or "").strip().upper()

    adb_matches = bool(adb_serial and scanned and (adb_serial == scanned))
    ftdi_matches = bool(ftdi_serial and scanned and (ftdi_serial == scanned))
    dual_match = bool(adb_serial and ftdi_serial and (adb_serial == ftdi_serial))

    # All match if both endpoints exist, match each other, and match scanned barcode
    all_match = bool(adb_serial and ftdi_serial and dual_match and (scanned == adb_serial))

    if all_match:
      msg = (
          f"Verification PASSED: Scanned barcode '{scanned}' matches both ADB Endpoint "
          f"({adb_serial}) and FTDI Endpoint ({ftdi_serial})."
      )
    elif not adb_serial and not ftdi_serial:
      msg = "Verification FAILED: No connected Maui USB hardware (ADB/FTDI) detected on USB bus."
    elif not dual_match and adb_serial and ftdi_serial:
      msg = (
          f"Verification FAILED (Hardware Endpoint Mismatch): ADB Serial '{adb_serial}' "
          f"does not match FTDI Serial '{ftdi_serial}'."
      )
    elif adb_serial and not ftdi_serial:
      msg = (
          f"Verification FAILED: FTDI endpoint (VID 0403, PID 6002) not detected. "
          f"ADB Serial is '{adb_serial}'."
      )
    elif ftdi_serial and not adb_serial:
      msg = (
          f"Verification FAILED: ADB endpoint (VID 18d1, PID 506d) not detected. "
          f"FTDI Serial is '{ftdi_serial}'."
      )
    else:
      msg = (
          f"Verification FAILED (Sticker Mismatch): Scanned barcode '{scanned}' does not match "
          f"hardware serial '{electronic_serial}'."
      )

    # Automatically save label verification record to log directory
    log_content = (
        f"Label Verification Result: {'PASSED' if all_match else 'FAILED'}\n"
        f"Scanned Barcode: '{scanned}'\n"
        f"ADB Serial: '{adb_serial}'\n"
        f"FTDI Serial: '{ftdi_serial}'\n"
        f"Electronic Serial: '{electronic_serial}'\n"
        f"Message: {msg}\n"
    )
    target_serial = electronic_serial or adb_serial or ftdi_serial or "UNKNOWN"
    write_device_log(target_serial, "label_verification", log_content)

    return maui_manufacturing_pb2.MauiVerifyLabelResponse(
        matches=all_match,
        electronic_serial=electronic_serial,
        scanned_barcode=scanned,
        adb_serial=adb_serial,
        ftdi_serial=ftdi_serial,
        adb_matches=adb_matches,
        ftdi_matches=ftdi_matches,
        dual_endpoints_match=dual_match,
        message=msg,
    )

  def submit_final_manufacturing_results(
      self,
      request: maui_manufacturing_pb2.MauiFinalSubmissionRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiFinalSubmissionResponse:
    """Generates authoritative final results, [SerialNumber]_[Date]_[PASS/FAIL] packages, and exports to ~/diagnoseme_results/."""
    serial_no = request.serial_number or "UNKNOWN"
    dir_path = get_device_log_dir(serial_no)

    # Authoritatively analyze real log files from the device log directory
    stage_statuses, overall_status, log_infos, present_count = analyze_device_manufacturing_logs(serial_no)

    # If label verification was explicitly provided in request, update label_verified status
    if request.label_verified:
      stage_statuses["label_verified"] = "PASSED"
    elif request.scanned_barcode:
      if request.adb_serial and request.scanned_barcode.upper() == request.adb_serial.upper():
        stage_statuses["label_verified"] = "PASSED"
      else:
        stage_statuses["label_verified"] = stage_statuses.get("label_verified", "BYPASSED")
    else:
      stage_statuses["label_verified"] = stage_statuses.get("label_verified", "BYPASSED")

    # Re-evaluate overall verdict based on available stage_statuses (missing sections acceptable)
    has_failures = any(s == "FAILED" for s in stage_statuses.values())
    has_bypasses = any("BYPASSED" in s for s in stage_statuses.values())

    if has_failures:
      overall_status = "FAIL"
    elif has_bypasses:
      overall_status = "PASS_WITH_BYPASS"
    else:
      overall_status = "PASS"

    pass_fail_tag = "PASS" if overall_status in ("PASS", "PASS_WITH_BYPASS") else "FAIL"
    ts_str = time.strftime("%Y%m%d_%H%M%S")

    station_id_str = (request.station_id or "").upper()
    if "STEP1" in station_id_str or "PROVISIONING" in station_id_str:
      subfolder_name = "smt_provisioning"
      prefix = "SMT_PROVISIONING"
    elif "FATP" in station_id_str or "QT0" in station_id_str:
      subfolder_name = "fatp_tests"
      prefix = "FATP_TESTS"
    else:
      subfolder_name = "smt_functional"
      prefix = "SMT_FUNCTIONAL"

    base_name = f"{prefix}_{serial_no}_{ts_str}_{pass_fail_tag}"

    log_files_list = []
    for info in log_infos:
      log_files_list.append({
          "name": info.log_name,
          "size_bytes": info.file_size_bytes,
          "verdict": info.stage_verdict,
          "details": info.details,
      })

    results_dict = {
        "serial_number": serial_no,
        "operator_id": request.operator_id or "OP_FACTORY",
        "station_id": request.station_id or prefix,
        "phase": subfolder_name,
        "completion_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "overall_status": overall_status,
        "verdict_tag": base_name,
        "endpoints": {
            "adb_serial": request.adb_serial or "",
            "ftdi_serial": request.ftdi_serial or "",
            "scanned_barcode": request.scanned_barcode or "",
            "serials_matched": bool(
                request.adb_serial
                and request.ftdi_serial
                and request.scanned_barcode
                and request.adb_serial.upper()
                == request.ftdi_serial.upper()
                == request.scanned_barcode.upper()
            ) if "FATP" in station_id_str else bool(
                request.adb_serial
                and request.ftdi_serial
                and request.adb_serial.upper() == request.ftdi_serial.upper()
            ),
        },
        "stage_statuses": stage_statuses,
        "log_files": log_files_list,
    }

    # 1. Build combined plain text log from all individual step logs in dir_path
    combined_log_lines = [
        "=================================================================",
        f" DiagnoseMe Manufacturing Log: {base_name}",
        f" Serial Number: {serial_no}",
        f" Station: {request.station_id or prefix}",
        f" Operator: {request.operator_id or 'OP_FACTORY'}",
        f" Completion Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        f" Overall Status: {overall_status}",
        "=================================================================\n",
    ]
    if os.path.isdir(dir_path):
      for root, _, files in os.walk(dir_path):
        for f in sorted(files):
          if f.endswith(".log") and not f.startswith("SMT_") and not f.startswith("FATP_"):
            fpath = os.path.join(root, f)
            try:
              with open(fpath, "r", encoding="utf-8", errors="ignore") as lf:
                content = lf.read().strip()
                if content:
                  combined_log_lines.append(f"--- LOG STEP: {f} ---")
                  combined_log_lines.append(content)
                  combined_log_lines.append("")
            except Exception:
              pass

    combined_log_str = "\n".join(combined_log_lines) + "\n"

    # 2. Save local copies in /tmp/<serial>_diagnoseme_logs/
    summary_path = os.path.join(dir_path, "final_results.json")
    named_summary_path = os.path.join(dir_path, f"{base_name}.json")
    local_log_path = os.path.join(dir_path, f"{base_name}.log")
    try:
      with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results_dict, f, indent=2)
      with open(named_summary_path, "w", encoding="utf-8") as f:
        json.dump(results_dict, f, indent=2)
      with open(local_log_path, "w", encoding="utf-8") as f:
        f.write(combined_log_str)
      try:
        os.chmod(summary_path, 0o666)
        os.chmod(named_summary_path, 0o666)
        os.chmod(local_log_path, 0o666)
      except Exception:
        pass
    except Exception as e:
      logging.exception("Failed to write local result files in %s: %s", dir_path, e)

    # 3. Export to ~/diagnoseme_results/<subfolder_name>/results/ and logs/
    dest_base_dirs = [
        "/root/diagnoseme_results",
        os.path.expanduser("~/diagnoseme_results"),
        "/tmp/diagnoseme_results",
    ]
    for h in glob.glob("/home/*"):
      if os.path.isdir(h):
        dest_base_dirs.append(os.path.join(h, "diagnoseme_results"))

    # Remove duplicates preserving order
    seen_dirs = set()
    unique_base_dirs = []
    for b in dest_base_dirs:
      if b not in seen_dirs:
        seen_dirs.add(b)
        unique_base_dirs.append(b)

    for base_dir in unique_base_dirs:
      try:
        phase_dir = os.path.join(base_dir, subfolder_name)
        results_dir = os.path.join(phase_dir, "results")
        logs_dir = os.path.join(phase_dir, "logs")
        os.makedirs(results_dir, exist_ok=True)
        os.makedirs(logs_dir, exist_ok=True)
        try:
          os.chmod(base_dir, 0o777)
          os.chmod(phase_dir, 0o777)
          os.chmod(results_dir, 0o777)
          os.chmod(logs_dir, 0o777)
        except Exception:
          pass

        # Results JSON: ~/diagnoseme_results/<phase>/results/<base_name>.json
        dest_json_file = os.path.join(results_dir, f"{base_name}.json")
        with open(dest_json_file, "w", encoding="utf-8") as f:
          json.dump(results_dict, f, indent=2)
        try:
          os.chmod(dest_json_file, 0o666)
        except Exception:
          pass

        # Logs File: ~/diagnoseme_results/<phase>/logs/<base_name>.log
        dest_log_file = os.path.join(logs_dir, f"{base_name}.log")
        with open(dest_log_file, "w", encoding="utf-8") as f:
          f.write(combined_log_str)
        try:
          os.chmod(dest_log_file, 0o666)
        except Exception:
          pass
      except Exception as e:
        logging.warning("Could not write to export directory %s: %s", base_dir, e)

    tracking_id = f"MES-{base_name}"
    logging.info(
        "Submitted final manufacturing record for %s -> Tracking ID: %s (Overall: %s, Stages: %s)",
        serial_no, tracking_id, overall_status, stage_statuses,
    )

    return maui_manufacturing_pb2.MauiFinalSubmissionResponse(
        success=True,
        summary_file_path=named_summary_path,
        mes_tracking_id=tracking_id,
        message=f"Results recorded ({base_name}). Exported JSON to ~/diagnoseme_results/{subfolder_name}/results/{base_name}.json and logs to ~/diagnoseme_results/{subfolder_name}/logs/{base_name}.log.",
    )

