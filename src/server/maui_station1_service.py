#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Maui Station 1 (Initial Flashing, Provisioning & Multi-Device PDC) gRPC Servicer."""

import asyncio
import glob
import json
import logging
import os
import re
import subprocess
import shutil
import tarfile
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from google.protobuf import empty_pb2
import grpc

from server import maui_common
from server import util
from server.generated import maui_manufacturing_pb2
from server.generated import maui_manufacturing_pb2_grpc
from server.maui_common import get_device_log_dir, write_device_log


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
        # Prioritize unprogrammed 0403:6001
        unprog = [d for d in ftdi_devs if d["pid"] == 0x6001]
        if unprog:
          d = unprog[0]
          yield maui_manufacturing_pb2.MauiBslDiscoveryResponse(
              bsl_detected=True,
              device_port=d["port"],
              time_remaining_sec=remaining,
              message=f"Unprogrammed Board (0403:6001) Detected on {d['port']}! Auto-starting MCU flashing.",
              vid=0x0403,
              pid=0x6001,
              auto_flash_eligible=True,
              detected_serial=d["serial"],
          )
          await asyncio.sleep(1.0)
          continue

        # Otherwise report programmed 0403:6002
        prog = [d for d in ftdi_devs if d["pid"] == 0x6002]
        if prog:
          d = prog[0]
          yield maui_manufacturing_pb2.MauiBslDiscoveryResponse(
              bsl_detected=True,
              device_port=d["port"],
              time_remaining_sec=remaining,
              message=f"Programmed Board (0403:6002, Serial: {d['serial']}) Detected on {d['port']}. Manual flash available.",
              vid=0x0403,
              pid=0x6002,
              auto_flash_eligible=False,
              detected_serial=d["serial"],
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
    """Scans sysfs for all connected FTDI (0403:6001 and 0403:6002) devices."""
    devices = []
    for tty_dir in sorted(glob.glob("/sys/class/tty/ttyUSB*")):
      device_link = os.path.join(tty_dir, "device")
      if not os.path.exists(device_link):
        continue

      curr = os.path.realpath(device_link)
      dev_node = f"/dev/{os.path.basename(tty_dir)}"
      vid = 0
      pid = 0
      serial = ""
      container_id = ""

      while curr and curr != "/sys":
        vid_file = os.path.join(curr, "idVendor")
        pid_file = os.path.join(curr, "idProduct")
        s_file = os.path.join(curr, "serial")
        c_file = os.path.join(curr, "container_id")

        if os.path.exists(vid_file) and not vid:
          try:
            with open(vid_file, "r") as f:
              vid = int(f.read().strip(), 16)
          except Exception:
            pass

        if os.path.exists(pid_file) and not pid:
          try:
            with open(pid_file, "r") as f:
              pid = int(f.read().strip(), 16)
          except Exception:
            pass

        if os.path.exists(s_file) and not serial:
          try:
            with open(s_file, "r") as f:
              serial = f.read().strip()
          except Exception:
            pass

        if os.path.exists(c_file) and not container_id:
          try:
            with open(c_file, "r") as f:
              container_id = f.read().strip()
          except Exception:
            pass

        curr = os.path.dirname(curr)

      if vid == 0x0403 and pid in (0x6001, 0x6002):
        devices.append({
            "port": dev_node,
            "vid": vid,
            "pid": pid,
            "serial": serial,
            "container_id": container_id,
        })
    return devices

  def auto_program_mcu_bsl(
      self,
      request: maui_manufacturing_pb2.MauiAutoProgramMcuRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiProgramMcuResponse:
    """Triggers MCU flashing via maui-flash using port-based selection."""
    fw_channel = request.fw_channel or "stable"
    dev_port = request.device_port or "/dev/ttyUSB0"
    serial_no = getattr(request, "serial_number", None)
    if not serial_no:
      _, h2h_serial, _ = self._find_prolific_h2h_info()
      serial_no = h2h_serial or "UNKNOWN_SERIAL"
    logging.info("Starting MCU BSL flashing on port %s (channel: %s, serial: %s)...", dev_port, fw_channel, serial_no)

    maui_flash_bin = shutil.which("maui-flash") or "/usr/local/bin/maui-flash"
    common_args = ["--port", dev_port]
    if "ACM" in dev_port or ("usb" in dev_port.lower() and "ttyusb" not in dev_port.lower()):
      common_args.append("--usb")

    if os.path.exists(maui_flash_bin) or shutil.which("maui-flash"):
      cmd = [maui_flash_bin] + common_args + ["mcu", "--fw_channel", fw_channel, "--bsl-mode"]
    elif os.path.exists("/usr/local/maui/tools/src/maui_flash.py"):
      cmd = ["python3", "/usr/local/maui/tools/src/maui_flash.py"] + common_args + ["mcu", "--fw_channel", fw_channel, "--bsl-mode"]
    elif os.path.exists("/usr/local/maui/tools/scripts/maui-flash"):
      cmd = ["/usr/local/maui/tools/scripts/maui-flash"] + common_args + ["mcu", "--fw_channel", fw_channel, "--bsl-mode"]
    else:
      logging.error("maui-flash tool not found on system.")
      return maui_manufacturing_pb2.MauiProgramMcuResponse(
          success=False,
          message=f"maui-flash tool not found on host/container to flash MCU on {dev_port}.",
          logs="Error: maui-flash binary not found in PATH or /usr/local/bin/maui-flash. Please ensure maui tools are installed.",
      )

    env = os.environ.copy()
    env["PYTHONPATH"] = f"/usr/local/maui/tools:/usr/local/maui:{env.get('PYTHONPATH', '')}"

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
      logging.exception("maui-flash binary not found")
      write_device_log(serial_no, "mcu_flashing", f"ERROR: {e}")
      return maui_manufacturing_pb2.MauiProgramMcuResponse(
          success=False,
          message="maui-flash tool not installed on host system.",
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

  def discover_topology_and_serial(
      self,
      unused_request: empty_pb2.Empty,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiTopologyDiscoveryResponse:
    """Discovers pre-flashed serial from Prolific/ADB chip with a 5s re-enumeration settling window (10 attempts x 0.5s)."""
    logging.info("Starting USB topology discovery via container_id (5s settling window)...")
    h2h_sys_path, h2h_serial, container_id = None, None, None
    ftdi_dev_node, usb2_serial = None, None

    # Wait and probe over a 5s window to allow USB re-enumeration after MCU reboot
    for attempt in range(1, 11):
      try:
        h2h_sys_path, h2h_serial, container_id = self._find_prolific_h2h_info()
        if h2h_serial:
          ftdi_dev_node, usb2_serial = self._find_ftdi_node_by_container_id(container_id or "", h2h_serial)
          if ftdi_dev_node:
            logging.info("Topology discovery succeeded on attempt %d: ADB Serial=%s, FTDI Node=%s", attempt, h2h_serial, ftdi_dev_node)
            return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
                success=True,
                read_serial_number=h2h_serial,
                usb2_hub_serial=usb2_serial,
                ftdi_dev_node=ftdi_dev_node,
                container_id=container_id or "",
            )
          elif attempt >= 6:
            # If FTDI node isn't strictly matched by container_id yet, fallback to default /dev/ttyUSB0 if present
            logging.info("ADB Serial=%s found on attempt %d; checking ttyUSB candidate", h2h_serial, attempt)
            dev_nodes = sorted(glob.glob("/dev/ttyUSB*"))
            if dev_nodes:
              return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
                  success=True,
                  read_serial_number=h2h_serial,
                  usb2_hub_serial=usb2_serial,
                  ftdi_dev_node=dev_nodes[0],
                  container_id=container_id or "",
              )
      except Exception as e:
        logging.warning("Topology discovery attempt %d error: %s", attempt, e)

      time.sleep(0.5)

    if not h2h_serial:
      return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
          success=False,
          error_message="Could not find ADB/Prolific chip (18d1:506d / 18d1:5084) with serial after 5s re-enumeration window.",
      )

    return maui_manufacturing_pb2.MauiTopologyDiscoveryResponse(
        success=False,
        read_serial_number=h2h_serial,
        container_id=container_id or "",
        error_message=f"Found ADB serial '{h2h_serial}', but could not locate FTDI chip under USB container_id {container_id}.",
    )

  def _find_prolific_h2h_info(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Scans sysfs, lsusb, and adb devices for ADB / Prolific H2H device and returns (sys_path, serial_number, container_id)."""
    # 1. Scan sysfs for VID 18d1 (Google ADB / Maui) or 067b (Prolific)
    for dev_dir in glob.glob("/sys/bus/usb/devices/*"):
      vid_file = os.path.join(dev_dir, "idVendor")
      pid_file = os.path.join(dev_dir, "idProduct")
      serial_file = os.path.join(dev_dir, "serial")
      container_file = os.path.join(dev_dir, "container_id")

      if os.path.exists(vid_file) and os.path.exists(pid_file):
        try:
          with open(vid_file, "r") as f:
            vid = f.read().strip().lower()
          with open(pid_file, "r") as f:
            pid = f.read().strip().lower()

          if vid in ("18d1", "067b"):
            serial = ""
            if os.path.exists(serial_file):
              with open(serial_file, "r") as f:
                serial = f.read().strip()

            container_id = ""
            curr = dev_dir
            while curr and curr != "/sys/bus/usb/devices" and curr != "/sys":
              c_file = os.path.join(curr, "container_id")
              if os.path.exists(c_file):
                try:
                  with open(c_file, "r") as f:
                    c_id = f.read().strip()
                  if c_id:
                    container_id = c_id
                    break
                except Exception:
                  pass
              curr = os.path.dirname(curr)

            if serial:
              logging.info("Found ADB/Prolific chip with serial '%s' at %s (VID=%s, PID=%s, Container: %s)", serial, dev_dir, vid, pid, container_id)
              return dev_dir, serial, container_id
        except Exception:
          continue

    # 2. Fallback: Query lsusb -v for VID 18d1 or 067b
    try:
      proc = subprocess.run(["lsusb", "-v", "-d", "18d1:"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.5)
      if proc.returncode == 0 and proc.stdout:
        for line in proc.stdout.splitlines():
          m = re.search(r"iSerial\s+\d+\s+([^\s]+)", line)
          if m:
            serial_str = m.group(1).strip()
            if serial_str and serial_str.lower() not in ("0", "none", "unknown"):
              logging.info("Found ADB serial from lsusb: %s", serial_str)
              return "/sys/bus/usb/devices", serial_str, ""
    except Exception as e:
      logging.debug("lsusb fallback failed: %s", e)

    # 3. Fallback: Query adb devices
    try:
      proc = subprocess.run(["adb", "devices"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.5)
      if proc.returncode == 0 and proc.stdout:
        for line in proc.stdout.splitlines():
          parts = line.strip().split()
          if len(parts) >= 2 and parts[1] in ("device", "recovery", "sideload", "unauthorized"):
            serial_str = parts[0].strip()
            if serial_str:
              logging.info("Found ADB serial from adb devices: %s", serial_str)
              return "/sys/bus/usb/devices", serial_str, ""
    except Exception as e:
      logging.debug("adb devices fallback failed: %s", e)

    return None, None, None

  def _find_ftdi_node_by_container_id(self, target_container_id: str, h2h_serial: str = "") -> Tuple[Optional[str], Optional[str]]:
    """Locates the FTDI ttyUSB node matching target_container_id, serial, or FTDI VID 0403."""
    candidate_node = None
    candidate_serial = ""

    for tty_dir in glob.glob("/sys/class/tty/ttyUSB*"):
      device_link = os.path.join(tty_dir, "device")
      if not os.path.exists(device_link):
        continue

      curr = os.path.realpath(device_link)
      dev_name = os.path.basename(tty_dir)
      dev_node = f"/dev/{dev_name}"

      ftdi_found = False
      node_container_id = ""
      node_serial = ""

      while curr and curr != "/sys":
        vid_file = os.path.join(curr, "idVendor")
        pid_file = os.path.join(curr, "idProduct")
        c_file = os.path.join(curr, "container_id")
        s_file = os.path.join(curr, "serial")

        if os.path.exists(s_file) and not node_serial:
          try:
            with open(s_file, "r") as f:
              node_serial = f.read().strip()
          except Exception:
            pass

        if os.path.exists(c_file) and not node_container_id:
          try:
            with open(c_file, "r") as f:
              node_container_id = f.read().strip()
          except Exception:
            pass

        if os.path.exists(vid_file):
          try:
            with open(vid_file, "r") as f:
              vid = f.read().strip()
            pid = ""
            if os.path.exists(pid_file):
              with open(pid_file, "r") as f:
                pid = f.read().strip()

            if vid.lower() == "0403" and pid.lower() in ("6001", "6002"):
              ftdi_found = True
          except Exception:
            pass

        curr = os.path.dirname(curr)

      if ftdi_found:
        if target_container_id and node_container_id == target_container_id:
          logging.info("Found FTDI node %s matching container_id %s", dev_node, target_container_id)
          return dev_node, node_serial

        if h2h_serial and node_serial == h2h_serial:
          logging.info("Found FTDI node %s matching H2H serial %s", dev_node, h2h_serial)
          return dev_node, node_serial

        if not candidate_node:
          candidate_node = dev_node
          candidate_serial = node_serial

    if candidate_node:
      logging.info("Using candidate FTDI node %s (serial: %s)", candidate_node, candidate_serial)
      return candidate_node, candidate_serial

    return None, None

  def program_ftdi_eeprom(
      self,
      request: maui_manufacturing_pb2.MauiProgramFtdiRequest,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiProgramFtdiResponse:
    """Programs FTDI EEPROM with the ADB serial using ft232r_prog --new-pid 0x6002 --new-serial-number <ADB_SERIAL>."""
    serial_no = request.serial_number
    dev_node = request.ftdi_dev_node or "/dev/ttyUSB0"
    if not serial_no:
      return maui_manufacturing_pb2.MauiProgramFtdiResponse(
          success=False,
          message="No ADB target serial number provided. Cannot program FTDI EEPROM.",
          logs="Error: serial_number is empty.",
      )

    logging.info("Programming FTDI EEPROM with ADB serial %s on %s...", serial_no, dev_node)

    # Use ft232r_prog (Mark Lord v1.25): --new-pid 0x6002 --new-serial-number <SERIAL>
    ft232r_prog_bin = shutil.which("ft232r_prog") or "/usr/local/bin/ft232r_prog"
    cmd = [
        ft232r_prog_bin,
        "--new-pid", "0x6002",
        "--new-serial-number", serial_no,
    ]

    try:
      proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True, timeout=10)
      logging.info("ft232r_prog output:\n%s", proc.stdout)
      write_device_log(serial_no, "ftdi_programming", f"SUCCESS:\n{proc.stdout}")
      # Consolidate any earlier logs (e.g. mcu_flashing.log) into the serial-specific log directory
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
      logging.warning("ft232r_prog default invocation failed (exit %d). Retrying with --old-pid 0x6002...", e.returncode)
      try:
        cmd_retry = [
            ft232r_prog_bin,
            "--old-pid", "0x6002",
            "--new-pid", "0x6002",
            "--new-serial-number", serial_no,
        ]
        proc_retry = subprocess.run(cmd_retry, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True, timeout=10)
        logging.info("ft232r_prog retry output:\n%s", proc_retry.stdout)
        write_device_log(serial_no, "ftdi_programming", f"SUCCESS (reflash):\n{proc_retry.stdout}")
        return maui_manufacturing_pb2.MauiProgramFtdiResponse(
            success=True,
            message=f"FTDI EEPROM reprogrammed successfully with PID 0x6002 and ADB serial {serial_no}.",
            logs=str(proc_retry.stdout or ""),
        )
      except Exception as retry_err:
        retry_logs = str(getattr(retry_err, "output", retry_err))
        logging.exception("ft232r_prog retry also failed: %s", retry_logs)
        write_device_log(serial_no, "ftdi_programming", f"FAILED:\nInitial:\n{err_logs}\nRetry:\n{retry_logs}")
        return maui_manufacturing_pb2.MauiProgramFtdiResponse(
            success=False,
            message=f"FTDI EEPROM programming failed: {err_logs}",
            logs=f"Initial attempt:\n{err_logs}\nRetry attempt:\n{retry_logs}",
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

  def discover_connected_pdc_devices(
      self,
      unused_request: empty_pb2.Empty,
      unused_context: grpc.ServicerContext,
  ) -> maui_manufacturing_pb2.MauiConnectedPdcDevicesResponse:
    """Scans and returns all connected 0403:6002 Maui units (up to 4 devices)."""
    logging.info("Scanning for connected 0403:6002 Maui PDC units...")
    found_devices = []
    slot = 1

    for tty_dir in sorted(glob.glob("/sys/class/tty/ttyUSB*")):
      if slot > 4:
        break
      device_link = os.path.join(tty_dir, "device")
      if not os.path.exists(device_link):
        continue

      curr = os.path.realpath(device_link)
      dev_node = f"/dev/{os.path.basename(tty_dir)}"
      vid = ""
      pid = ""
      serial = ""
      container_id = ""
      product = "Maui Serial Console"

      while curr and curr != "/sys":
        vid_file = os.path.join(curr, "idVendor")
        pid_file = os.path.join(curr, "idProduct")
        s_file = os.path.join(curr, "serial")
        c_file = os.path.join(curr, "container_id")
        p_file = os.path.join(curr, "product")

        if os.path.exists(vid_file) and not vid:
          try:
            with open(vid_file, "r") as f:
              vid = f.read().strip()
          except Exception:
            pass

        if os.path.exists(pid_file) and not pid:
          try:
            with open(pid_file, "r") as f:
              pid = f.read().strip()
          except Exception:
            pass

        if os.path.exists(s_file) and not serial:
          try:
            with open(s_file, "r") as f:
              serial = f.read().strip()
          except Exception:
            pass

        if os.path.exists(c_file) and not container_id:
          try:
            with open(c_file, "r") as f:
              container_id = f.read().strip()
          except Exception:
            pass

        if os.path.exists(p_file):
          try:
            with open(p_file, "r") as f:
              product = f.read().strip()
          except Exception:
            pass

        curr = os.path.dirname(curr)

      if vid.lower() == "0403" and pid.lower() == "6002":
        found_devices.append(
            maui_manufacturing_pb2.MauiPdcDeviceInfo(
                slot_index=slot,
                serial_number=serial or "",
                device_port=dev_node,
                container_id=container_id,
                product_description=product,
            )
        )
        slot += 1

    logging.info("Found %d connected 0403:6002 devices for PDC flashing.", len(found_devices))
    return maui_manufacturing_pb2.MauiConnectedPdcDevicesResponse(
        success=True,
        devices=found_devices,
        message=f"Discovered {len(found_devices)} Maui device(s) ready for PDC flashing.",
    )

  async def program_single_pdc(
      self,
      request: maui_manufacturing_pb2.MauiProgramSinglePdcRequest,
      context: grpc.ServicerContext,
  ) -> AsyncGenerator[maui_manufacturing_pb2.MauiProgramPdcProgressResponse, None]:
    """Flashes PDC firmware on a single device and streams block transfer progress."""
    slot_index = request.slot_index or 1
    serial_no = request.serial_number or ""
    dev_port = request.device_port or "/dev/ttyUSB0"
    fw_channel = request.fw_channel or "stable"

    logging.info("Starting PDC flash for slot %d (serial: %s, port: %s, channel: %s)...",
                 slot_index, serial_no, dev_port, fw_channel)

    maui_flash_bin = shutil.which("maui-flash") or "/usr/local/bin/maui-flash"
    common_args = []
    if dev_port and os.path.exists(dev_port):
      common_args.extend(["--port", dev_port])
    elif serial_no:
      common_args.extend(["--serial", serial_no])

    if os.path.exists(maui_flash_bin) or shutil.which("maui-flash"):
      cmd = [maui_flash_bin] + common_args + ["pdc", "--fw_channel", fw_channel]
    elif os.path.exists("/usr/local/maui/tools/src/maui_flash.py"):
      cmd = ["python3", "/usr/local/maui/tools/src/maui_flash.py"] + common_args + ["pdc", "--fw_channel", fw_channel]
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

    try:
      env = os.environ.copy()
      env["PYTHONPATH"] = f"/usr/local/maui/tools:/usr/local/maui:{env.get('PYTHONPATH', '')}"
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
    date_str = time.strftime("%Y-%m-%d")
    base_name = f"{serial_no}_{date_str}_{pass_fail_tag}"

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
        "station_id": request.station_id or "STATION_FINAL",
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
            ),
        },
        "stage_statuses": stage_statuses,
        "log_files": log_files_list,
    }

    # 1. Save standard final_results.json and [SerialNumber]_[Date]_[PASS/FAIL].json in log directory
    summary_path = os.path.join(dir_path, "final_results.json")
    named_summary_path = os.path.join(dir_path, f"{base_name}.json")
    try:
      with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results_dict, f, indent=2)
      with open(named_summary_path, "w", encoding="utf-8") as f:
        json.dump(results_dict, f, indent=2)
      try:
        os.chmod(summary_path, 0o666)
        os.chmod(named_summary_path, 0o666)
      except Exception:
        pass
    except Exception as e:
      logging.exception("Failed to write final_results.json: %s", e)

    # 2. Package detailed full logs into tar.gz archive [SerialNumber]_[Date]_[PASS/FAIL].tar.gz
    archive_name = f"{base_name}.tar.gz"
    local_archive_path = os.path.join(dir_path, archive_name)
    try:
      with tarfile.open(local_archive_path, "w:gz") as tar:
        for root, _, files in os.walk(dir_path):
          for file in files:
            if file.endswith((".log", ".json", ".txt")) and not file.endswith(archive_name):
              full_p = os.path.join(root, file)
              arcname = os.path.relpath(full_p, dir_path)
              tar.add(full_p, arcname=arcname)
      try:
        os.chmod(local_archive_path, 0o666)
      except Exception:
        pass
    except Exception as e:
      logging.warning("Failed to create log archive in %s: %s", dir_path, e)

    # 3. Export to /root/diagnoseme_results/ (mapped to host ~/diagnoseme_results/) and /tmp/diagnoseme_results/
    #    highlevel: JSON under [SerialNumber]_[Date]_[PASS/FAIL].json
    #    detailed: full logs packed as archive [SerialNumber]_[Date]_[PASS/FAIL].tar.gz
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
        highlevel_dir = os.path.join(base_dir, "highlevel")
        detailed_dir = os.path.join(base_dir, "detailed")
        os.makedirs(highlevel_dir, exist_ok=True)
        os.makedirs(detailed_dir, exist_ok=True)
        try:
          os.chmod(base_dir, 0o777)
          os.chmod(highlevel_dir, 0o777)
          os.chmod(detailed_dir, 0o777)
        except Exception:
          pass

        # Highlevel JSON
        hl_file = os.path.join(highlevel_dir, f"{base_name}.json")
        with open(hl_file, "w", encoding="utf-8") as f:
          json.dump(results_dict, f, indent=2)
        try:
          os.chmod(hl_file, 0o666)
        except Exception:
          pass

        # Detailed Archive
        if os.path.exists(local_archive_path):
          dt_file = os.path.join(detailed_dir, archive_name)
          shutil.copy2(local_archive_path, dt_file)
          try:
            os.chmod(dt_file, 0o666)
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
        message=f"Results recorded ({base_name}). JSON exported to ~/diagnoseme_results/highlevel/{base_name}.json, full logs archive exported to ~/diagnoseme_results/detailed/{archive_name}.",
    )

