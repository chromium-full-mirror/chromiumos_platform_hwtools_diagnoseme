#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Consolidation and merging utility for multi-PC Maui factory test logs.

Ingests test result files from ~/diagnoseme_results/ (or specified input directories)
across:
  1. SMT Step 1 (Provisioning: MCU + PDC + FTDI)
  2. SMT Step 2 (Functional: Host + DUT + Charger)
  3. FATP Step 4 (QT0: Barcode + Quick Test Suite)

Consolidates per-device serial records and generates:
  - FINAL_REPORT_<SERIAL>.json: Consolidated per-unit JSON records
  - factory_summary_dashboard.csv: CSV table of all manufactured devices
  - factory_summary_report.html: Visual HTML manufacturing report
"""

import argparse
import csv
import glob
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple


def find_all_result_files(input_dirs: List[str]) -> List[str]:
  """Searches for all JSON result files across input directories."""
  seen = set()
  files = []
  for d in input_dirs:
    if os.path.isdir(d):
      for path in glob.glob(os.path.join(d, "**", "*.json"), recursive=True):
        r_path = os.path.realpath(path)
        base = os.path.basename(r_path)
        if r_path not in seen and not base.startswith("final_results") and not base.startswith("factory_summary"):
          seen.add(r_path)
          files.append(r_path)
  return sorted(files)


def extract_record_info(data: Dict[str, Any], filepath: str) -> Dict[str, Any]:
  """Extracts and normalizes record metadata."""
  serial = (data.get("serial_number") or "").strip()
  if not serial or serial.startswith("BATCH_STEP1"):
    # Try finding serial from endpoints or filename
    endpoints = data.get("endpoints", {})
    serial = endpoints.get("adb_serial") or endpoints.get("ftdi_serial") or serial
    if not serial:
      base = os.path.basename(filepath).replace(".json", "")
      parts = base.split("_")
      if len(parts) >= 1 and parts[0] != "BATCH":
        serial = parts[0]

  station_id = (data.get("station_id") or data.get("phase") or "").upper()
  phase = (data.get("phase") or "").upper()
  stages = data.get("stage_statuses", {})
  overall_status = (data.get("overall_status") or "UNKNOWN").upper()
  comp_time = data.get("completion_time") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

  # Classify phase
  if "STEP1" in station_id or "PROVISIONING" in station_id or phase == "SMT_STEP1":
    phase_tag = "SMT_STEP1"
  elif "FATP" in station_id or "FATP" in phase or data.get("scanned_barcode"):
    phase_tag = "FATP_QT0"
  elif "FUNCTIONAL" in station_id or "SMT" in phase or "host_dut_verification" in stages or "host_only_verification" in stages:
    phase_tag = "SMT_STEP2"
  else:
    phase_tag = "OTHER"

  return {
      "serial_number": serial or "UNKNOWN",
      "station_id": station_id,
      "phase_tag": phase_tag,
      "overall_status": overall_status,
      "completion_time": comp_time,
      "operator_id": data.get("operator_id") or "OP_FACTORY",
      "stage_statuses": stages,
      "endpoints": data.get("endpoints", {}),
      "log_files": data.get("log_files", []),
      "raw_data": data,
      "source_file": filepath,
  }


def merge_device_records(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
  """Groups and merges records by device serial number."""
  devices: Dict[str, Dict[str, Any]] = {}

  for r in records:
    ser = r["serial_number"]
    if ser not in devices:
      devices[ser] = {
          "serial_number": ser,
          "smt_step1": None,
          "smt_step2": None,
          "fatp_qt0": None,
          "all_records": [],
      }

    devices[ser]["all_records"].append(r)
    ptag = r["phase_tag"]
    if ptag == "SMT_STEP1":
      if not devices[ser]["smt_step1"] or len(str(r["raw_data"])) > len(str(devices[ser]["smt_step1"]["raw_data"])):
        devices[ser]["smt_step1"] = r
    elif ptag == "SMT_STEP2":
      if not devices[ser]["smt_step2"] or len(str(r["raw_data"])) > len(str(devices[ser]["smt_step2"]["raw_data"])):
        devices[ser]["smt_step2"] = r
    elif ptag == "FATP_QT0":
      if not devices[ser]["fatp_qt0"] or len(str(r["raw_data"])) > len(str(devices[ser]["fatp_qt0"]["raw_data"])):
        devices[ser]["fatp_qt0"] = r

  return devices


def evaluate_final_verdict(dev_entry: Dict[str, Any]) -> Tuple[str, List[str]]:
  """Computes the overall final verdict across available manufacturing phases."""
  verdicts = []
  notes = []

  for phase_name, rec in [("SMT Step 1", dev_entry["smt_step1"]), ("SMT Step 2", dev_entry["smt_step2"]), ("FATP QT0", dev_entry["fatp_qt0"])]:
    if rec:
      status = rec["overall_status"]
      verdicts.append(status)
      if status == "FAIL":
        notes.append(f"{phase_name}: FAIL")
      elif status == "PASS_WITH_BYPASS":
        notes.append(f"{phase_name}: PASS (BYPASS)")
    else:
      notes.append(f"{phase_name}: NOT_RECORDED")

  if not verdicts:
    return "UNKNOWN", notes
  if any(v == "FAIL" for v in verdicts):
    return "FAIL", notes
  if any(v == "PASS_WITH_BYPASS" for v in verdicts):
    return "PASS_WITH_BYPASS", notes
  return "PASS", notes


def export_merged_reports(
    devices: Dict[str, Dict[str, Any]],
    output_dir: str,
    write_json: bool = True,
    write_csv: bool = True,
    write_html: bool = True,
):
  """Exports consolidated per-device JSONs, dashboard CSV, and HTML reports."""
  os.makedirs(output_dir, exist_ok=True)
  now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

  # 1. Consolidated per-device JSONs
  if write_json:
    for ser, dev in devices.items():
      final_verdict, notes = evaluate_final_verdict(dev)
      report_data = {
          "serial_number": ser,
          "generated_at": now_str,
          "final_verdict": final_verdict,
          "verdict_notes": notes,
          "phases": {
              "smt_step1_provisioning": dev["smt_step1"]["raw_data"] if dev["smt_step1"] else None,
              "smt_step2_functional": dev["smt_step2"]["raw_data"] if dev["smt_step2"] else None,
              "fatp_step4_qt0": dev["fatp_qt0"]["raw_data"] if dev["fatp_qt0"] else None,
          },
      }
      out_json = os.path.join(output_dir, f"FINAL_REPORT_{ser}.json")
      with open(out_json, "w", encoding="utf-8") as fp:
        json.dump(report_data, fp, indent=2)

  # 2. Summary CSV
  if write_csv:
    csv_path = os.path.join(output_dir, "factory_summary_dashboard.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fp:
      writer = csv.writer(fp)
      writer.writerow([
          "Serial Number",
          "Final Verdict",
          "SMT Step 1 (Provisioning)",
          "SMT Step 2 (Functional)",
          "FATP Step 4 (QT0)",
          "Last Timestamp",
          "Notes",
      ])
      for ser, dev in sorted(devices.items()):
        final_verdict, notes = evaluate_final_verdict(dev)
        s1_status = dev["smt_step1"]["overall_status"] if dev["smt_step1"] else "N/A"
        s2_status = dev["smt_step2"]["overall_status"] if dev["smt_step2"] else "N/A"
        fatp_status = dev["fatp_qt0"]["overall_status"] if dev["fatp_qt0"] else "N/A"
        latest_ts = ""
        for r in dev["all_records"]:
          if r["completion_time"] > latest_ts:
            latest_ts = r["completion_time"]
        writer.writerow([
            ser,
            final_verdict,
            s1_status,
            s2_status,
            fatp_status,
            latest_ts,
            " | ".join(notes),
        ])

  # 3. HTML Visual Report
  if write_html:
    html_path = os.path.join(output_dir, "factory_summary_report.html")
    total_devs = len(devices)
    pass_devs = 0
    fail_devs = 0
    bypass_devs = 0

    rows_html = []
    for ser, dev in sorted(devices.items()):
      final_verdict, notes = evaluate_final_verdict(dev)
      if final_verdict == "PASS":
        pass_devs += 1
        badge_cls = "badge-pass"
      elif final_verdict == "PASS_WITH_BYPASS":
        bypass_devs += 1
        badge_cls = "badge-bypass"
      elif final_verdict == "FAIL":
        fail_devs += 1
        badge_cls = "badge-fail"
      else:
        badge_cls = "badge-na"

      s1_status = dev["smt_step1"]["overall_status"] if dev["smt_step1"] else "N/A"
      s2_status = dev["smt_step2"]["overall_status"] if dev["smt_step2"] else "N/A"
      fatp_status = dev["fatp_qt0"]["overall_status"] if dev["fatp_qt0"] else "N/A"

      rows_html.append(f"""
        <tr>
          <td><strong>{ser}</strong></td>
          <td><span class="badge {badge_cls}">{final_verdict}</span></td>
          <td>{s1_status}</td>
          <td>{s2_status}</td>
          <td>{fatp_status}</td>
          <td><small>{' | '.join(notes)}</small></td>
        </tr>
      """)

    yield_pct = ((pass_devs + bypass_devs) / total_devs * 100.0) if total_devs else 0.0

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Maui Manufacturing Factory Summary Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 24px; background: #f8f9fa; color: #202124; }}
    h1 {{ color: #1a73e8; margin-bottom: 4px; }}
    .kpi-container {{ display: flex; gap: 16px; margin: 20px 0; }}
    .kpi-card {{ background: white; border: 1px solid #dadce0; border-radius: 8px; padding: 16px; flex: 1; text-align: center; }}
    .kpi-val {{ font-size: 28px; font-weight: bold; margin-top: 6px; }}
    .val-pass {{ color: #137333; }}
    .val-fail {{ color: #c5221f; }}
    .val-bypass {{ color: #b06000; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid #dadce0; }}
    th {{ background: #f1f3f4; font-weight: 600; color: #5f6368; }}
    .badge {{ padding: 4px 8px; border-radius: 4px; font-weight: 600; font-size: 12px; }}
    .badge-pass {{ background: #e6f4ea; color: #137333; }}
    .badge-fail {{ background: #fce8e6; color: #c5221f; }}
    .badge-bypass {{ background: #fef7e0; color: #b06000; }}
    .badge-na {{ background: #f1f3f4; color: #5f6368; }}
  </style>
</head>
<body>
  <h1>Maui Manufacturing Factory Summary Report</h1>
  <p style="color: #5f6368;">Generated: {now_str} | Total Units Tracked: {total_devs}</p>

  <div class="kpi-container">
    <div class="kpi-card"><div>Total Units</div><div class="kpi-val">{total_devs}</div></div>
    <div class="kpi-card"><div>Full Pass</div><div class="kpi-val val-pass">{pass_devs}</div></div>
    <div class="kpi-card"><div>Pass (Bypass)</div><div class="kpi-val val-bypass">{bypass_devs}</div></div>
    <div class="kpi-card"><div>Failed</div><div class="kpi-val val-fail">{fail_devs}</div></div>
    <div class="kpi-card"><div>Overall Yield</div><div class="kpi-val val-pass">{yield_pct:.1f}%</div></div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Serial Number</th>
        <th>Overall Verdict</th>
        <th>SMT Step 1 (Provisioning)</th>
        <th>SMT Step 2 (Functional)</th>
        <th>FATP Step 4 (QT0)</th>
        <th>Notes</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
</body>
</html>
"""
    with open(html_path, "w", encoding="utf-8") as fp:
      fp.write(html_content)


def main():
  parser = argparse.ArgumentParser(description="Merge Maui Factory Test Logs across Multi-PC Stations")
  parser.add_argument(
      "-i",
      "--input-dirs",
      nargs="+",
      default=[
          os.path.expanduser("~/diagnoseme_results"),
          "/tmp/diagnoseme_results",
      ],
      help="Directories containing JSON test result files",
  )
  parser.add_argument(
      "-o",
      "--output-dir",
      default=os.path.expanduser("~/diagnoseme_results/consolidated"),
      help="Directory to save merged reports (default: ~/diagnoseme_results/consolidated)",
  )
  args = parser.parse_args()

  files = find_all_result_files(args.input_dirs)
  print(f"Found {len(files)} result files across {args.input_dirs}...")

  raw_records = []
  for f in files:
    try:
      with open(f, "r", encoding="utf-8") as fp:
        d = json.load(fp)
      raw_records.append(extract_record_info(d, f))
    except Exception as e:
      print(f"Skipping {f}: {e}", file=sys.stderr)

  devices = merge_device_records(raw_records)
  print(f"Consolidated records for {len(devices)} unique Maui devices.")

  export_merged_reports(devices, args.output_dir)
  print(f"Merged reports exported to {args.output_dir}:")
  print(f"  • {args.output_dir}/factory_summary_dashboard.csv")
  print(f"  • {args.output_dir}/factory_summary_report.html")
  print(f"  • {args.output_dir}/FINAL_REPORT_<SERIAL>.json")


if __name__ == "__main__":
  main()
