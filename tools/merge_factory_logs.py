#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Consolidation and merging utility for multi-PC Maui factory test logs.

Ingests test result files from /var/log/diagnoseme/maui_v11_results/ plus the
legacy ~/diagnoseme_results/ and /tmp/diagnoseme_results/ directories when
present (or the directories given with -i) across:
  1. SMT Step 1 (Provisioning: MCU + PDC + FTDI)
  2. SMT Step 2 (Functional: Host + DUT + Charger)
  3. FATP Step 4 (QT0: Barcode + Quick Test Suite)

Consolidates per-device serial records (keeping the latest record of each
phase by completion_time) and generates, in an output directory outside the
inputs (default ~/diagnoseme_consolidated, override with -o):
  - FINAL_REPORT_<SERIAL>.json: Consolidated per-unit JSON records
  - factory_summary_dashboard.csv: CSV table of all manufactured devices
  - factory_summary_report.html: Visual HTML manufacturing report
"""

import argparse
import csv
import glob
import html
import json
import os
import re
import sys
import time
from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Tuple


DEFAULT_INPUT_DIR = "/var/log/diagnoseme/maui_v11_results"
# Result directories of older DiagnoseMe releases, read when present.
LEGACY_INPUT_DIRS = ("~/diagnoseme_results", "/tmp/diagnoseme_results")
# Outside all default input directories.
DEFAULT_OUTPUT_DIR = "~/diagnoseme_consolidated"

# Generated files that must never be read back as input records.
_GENERATED_PREFIXES = ("final_results", "factory_summary", "FINAL_REPORT_")

# (record key, phase tag, report label, report JSON key)
PHASES = (
    ("smt_step1", "SMT_STEP1", "SMT Step 1", "smt_step1_provisioning"),
    ("smt_step2", "SMT_STEP2", "SMT Step 2", "smt_step2_functional"),
    ("fatp_qt0", "FATP_QT0", "FATP QT0", "fatp_step4_qt0"),
)


def default_input_dirs() -> List[str]:
    """The results directory plus the legacy result directories that exist."""
    dirs = [DEFAULT_INPUT_DIR]
    for legacy in LEGACY_INPUT_DIRS:
        path = os.path.expanduser(legacy)
        if os.path.isdir(path):
            dirs.append(path)
    return dirs


def _is_within(path: str, directory: str) -> bool:
    directory = os.path.realpath(directory)
    return os.path.commonpath([path, directory]) == directory


def find_all_result_files(
    input_dirs: List[str], exclude_dirs: Iterable[str] = ()
) -> List[str]:
    """Searches for all JSON result files across input directories.

    Files inside exclude_dirs (e.g. the output directory) and generated report
    files are skipped.
    """
    excluded = [d for d in exclude_dirs if d]
    seen = set()
    files = []
    for d in input_dirs:
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "**", "*.json"), recursive=True):
            r_path = os.path.realpath(path)
            base = os.path.basename(r_path)
            if r_path in seen or base.startswith(_GENERATED_PREFIXES):
                continue
            if any(_is_within(r_path, ex) for ex in excluded):
                continue
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
            parts = os.path.basename(filepath).replace(".json", "").split("_")
            if parts and parts[0] != "BATCH":
                serial = parts[0]

    station_id = (data.get("station_id") or data.get("phase") or "").upper()
    phase = (data.get("phase") or "").upper()
    stages = data.get("stage_statuses", {})
    overall_status = (data.get("overall_status") or "UNKNOWN").upper()
    # Missing completion time stays empty (never "now"), so it cannot win the latest-record selection.
    comp_time = data.get("completion_time") or ""

    # Classify phase
    if "STEP1" in station_id or "PROVISIONING" in station_id or phase == "SMT_STEP1":
        phase_tag = "SMT_STEP1"
    elif "FATP" in station_id or "FATP" in phase or data.get("scanned_barcode"):
        phase_tag = "FATP_QT0"
    elif (
        "FUNCTIONAL" in station_id
        or "SMT" in phase
        or "host_dut_verification" in stages
        or "host_only_verification" in stages
    ):
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


def _record_order(record: Dict[str, Any]) -> Tuple[str, str]:
    """Sort key: completion_time (ISO-8601 UTC sorts lexically), then source file for determinism."""
    return record["completion_time"], record["source_file"]


def merge_device_records(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Groups records by device serial number, keeping the latest record of each phase."""
    devices: Dict[str, Dict[str, Any]] = {}
    phase_keys = {tag: key for key, tag, _, _ in PHASES}

    for r in records:
        ser = r["serial_number"]
        dev = devices.setdefault(
            ser,
            {
                "serial_number": ser,
                **{key: None for key, _, _, _ in PHASES},
                "all_records": [],
            },
        )
        dev["all_records"].append(r)
        key = phase_keys.get(r["phase_tag"])
        if key and (dev[key] is None or _record_order(r) >= _record_order(dev[key])):
            dev[key] = r

    return devices


def evaluate_final_verdict(dev_entry: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Computes the overall final verdict across available manufacturing phases."""
    verdicts = []
    notes = []

    for key, _, phase_name, _ in PHASES:
        rec = dev_entry[key]
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


def _phase_status(dev: Dict[str, Any], key: str) -> str:
    return dev[key]["overall_status"] if dev[key] else "N/A"


def _latest_timestamp(dev: Dict[str, Any]) -> str:
    return max((r["completion_time"] for r in dev["all_records"]), default="")


_HTML_TEMPLATE = """<!DOCTYPE html>
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
      {rows}
    </tbody>
  </table>
</body>
</html>
"""

_BADGE_CLASSES = {
    "PASS": "badge-pass",
    "PASS_WITH_BYPASS": "badge-bypass",
    "FAIL": "badge-fail",
}


def _write_device_json_reports(
    devices: Dict[str, Dict[str, Any]], output_dir: str, now_str: str
) -> None:
    for ser, dev in devices.items():
        final_verdict, notes = evaluate_final_verdict(dev)
        report_data = {
            "serial_number": ser,
            "generated_at": now_str,
            "final_verdict": final_verdict,
            "verdict_notes": notes,
            "phases": {
                report_key: dev[key]["raw_data"] if dev[key] else None
                for key, _, _, report_key in PHASES
            },
        }
        safe_ser = re.sub(r"[^A-Za-z0-9_.-]", "_", ser)
        out_json = os.path.join(output_dir, f"FINAL_REPORT_{safe_ser}.json")
        with open(out_json, "w", encoding="utf-8") as fp:
            json.dump(report_data, fp, indent=2)


def _write_csv_report(devices: Dict[str, Dict[str, Any]], output_dir: str) -> None:
    csv_path = os.path.join(output_dir, "factory_summary_dashboard.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "Serial Number",
                "Final Verdict",
                "SMT Step 1 (Provisioning)",
                "SMT Step 2 (Functional)",
                "FATP Step 4 (QT0)",
                "Last Timestamp",
                "Notes",
            ]
        )
        for ser, dev in sorted(devices.items()):
            final_verdict, notes = evaluate_final_verdict(dev)
            writer.writerow(
                [ser, final_verdict]
                + [_phase_status(dev, key) for key, _, _, _ in PHASES]
                + [_latest_timestamp(dev), " | ".join(notes)]
            )


def _write_html_report(
    devices: Dict[str, Dict[str, Any]], output_dir: str, now_str: str
) -> None:
    counts = {"PASS": 0, "PASS_WITH_BYPASS": 0, "FAIL": 0}
    rows_html = []
    for ser, dev in sorted(devices.items()):
        final_verdict, notes = evaluate_final_verdict(dev)
        if final_verdict in counts:
            counts[final_verdict] += 1
        phase_cells = "".join(
            f"\n          <td>{html.escape(_phase_status(dev, key))}</td>"
            for key, _, _, _ in PHASES
        )
        rows_html.append(
            f"""
        <tr>
          <td><strong>{html.escape(ser)}</strong></td>
          <td><span class="badge {_BADGE_CLASSES.get(final_verdict, 'badge-na')}">{html.escape(final_verdict)}</span></td>{phase_cells}
          <td><small>{html.escape(' | '.join(notes))}</small></td>
        </tr>
      """
        )

    total_devs = len(devices)
    yield_pct = (
        ((counts["PASS"] + counts["PASS_WITH_BYPASS"]) / total_devs * 100.0)
        if total_devs
        else 0.0
    )
    html_content = _HTML_TEMPLATE.format(
        now_str=html.escape(now_str),
        total_devs=total_devs,
        pass_devs=counts["PASS"],
        bypass_devs=counts["PASS_WITH_BYPASS"],
        fail_devs=counts["FAIL"],
        yield_pct=yield_pct,
        rows="".join(rows_html),
    )
    with open(
        os.path.join(output_dir, "factory_summary_report.html"), "w", encoding="utf-8"
    ) as fp:
        fp.write(html_content)


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
    if write_json:
        _write_device_json_reports(devices, output_dir, now_str)
    if write_csv:
        _write_csv_report(devices, output_dir)
    if write_html:
        _write_html_report(devices, output_dir, now_str)


def main(argv: Optional[List[str]] = None):
    """Command line entry point."""
    parser = argparse.ArgumentParser(
        description="Merge Maui Factory Test Logs across Multi-PC Stations"
    )
    parser.add_argument(
        "-i",
        "--input-dirs",
        nargs="+",
        default=None,
        help=(
            "Directories containing JSON test result files (default: "
            f"{DEFAULT_INPUT_DIR} plus {' and '.join(LEGACY_INPUT_DIRS)} when present)"
        ),
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to save merged reports (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args(argv)
    args.input_dirs = [
        os.path.expanduser(d) for d in (args.input_dirs or default_input_dirs())
    ]
    args.output_dir = os.path.expanduser(args.output_dir)

    files = find_all_result_files(args.input_dirs, exclude_dirs=[args.output_dir])
    print(f"Found {len(files)} result files across {args.input_dirs}...")

    raw_records = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                d = json.load(fp)
        except (OSError, ValueError) as e:
            print(f"Skipping {f}: {e}", file=sys.stderr)
            continue
        if not isinstance(d, dict):
            print(f"Skipping {f}: not a result record", file=sys.stderr)
            continue
        raw_records.append(extract_record_info(d, f))

    devices = merge_device_records(raw_records)
    print(f"Consolidated records for {len(devices)} unique Maui devices.")

    export_merged_reports(devices, args.output_dir)
    print(f"Merged reports exported to {args.output_dir}:")
    print(f"  • {args.output_dir}/factory_summary_dashboard.csv")
    print(f"  • {args.output_dir}/factory_summary_report.html")
    print(f"  • {args.output_dir}/FINAL_REPORT_<SERIAL>.json")


if __name__ == "__main__":
    main()
