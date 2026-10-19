# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests of the Maui final records (stage verdicts, label_verified)."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from server import maui_station1_service as station1
from server import maui_v12_services
from server.generated import maui_manufacturing_pb2


SN = "MAUIV12H26090104"

PASS_LOG = "ALL_PASSED=True\nRESULT: PASSED\nCompleted successfully\n"
# Partner-like stage log of the Host + DUT card with the charger.
STAGE1_PASS = (
    "[2026-10-19 07:39:31]\nALL_PASSED=True\n"
    "[Stage 1: With Charger] Charger Port: PASS (Connected) | DUT 20V Contract: "
    "20.0V @ 3.25A (65.0W, VBUS=20636mV, PASS) | Serial Match: PASS ("
    f"{SN}) | H2H 5Gbps: PASS (5000M) | CCD Consoles: EC=PASS, GSC=PASS\n"
    "DUT USB enumeration: 18d1:506d speed=5000\n"
)
STAGE_PASS = "ALL_PASSED=True\n[Stage] DUT: PASS\n"
STAGE_FAIL = "ALL_PASSED=False\n[Stage 2: SBU Flip] SBU Flip: FAIL (not inverted)\n"
STAGE_BYPASS = (
    "=== Host + DUT ===\nSTATUS=BYPASSED\nRESULT: BYPASSED\nBypassed by operator.\n"
)
SERIAL_PAGE_LOG = (
    "=== Maui v1.2 Enumeration, Serial & Barcode Check ===\n"
    "ALL_PASSED={passed}\nRESULT: {result}\n"
    "\n--- Barcode ---\nScanned barcode: {sn}\n"
    "Barcode matches H2H and MCU: {match}\n"
)

# Logs of the partner SMT functional run (v1.2, no host_dut_tests.log).
PARTNER_SMT_LOGS = {
    "mcu_serial_provisioning.log": PASS_LOG,
    "h2h_spi_check.log": PASS_LOG,
    "host_only_tests.log": PASS_LOG,
    "bsl_button_test.log": PASS_LOG,
    "reset_button_test.log": PASS_LOG,
    "host_replug_test.log": PASS_LOG,
    "host_dut_stage1_with_charger.log": STAGE1_PASS,
    "host_dut_stage2_sbu_flip.log": STAGE_PASS,
    "host_dut_stage3_no_charger.log": STAGE_PASS,
    "adb_transfer_test.log": PASS_LOG,
    "smt_functional_summary.log": "OVERALL RESULT: PASS\n",
}


class _LogDirTest(unittest.TestCase):
    """Fake /tmp/<serial>_diagnoseme_logs and ~/diagnoseme_results."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home)
        for target in (
            "server.maui_station1_service.get_device_log_dir",
            "server.maui_common.get_device_log_dir",
        ):
            patcher = mock.patch(target, return_value=self.dir)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Keep the result export inside the test directories.
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch(
            "server.maui_station1_service.glob.glob",
            side_effect=lambda pattern: [],
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        makedirs = os.makedirs

        def test_dirs_only(path, *args, **kwargs):
            if not str(path).startswith((self.dir, self.home)):
                raise PermissionError(path)
            return makedirs(path, *args, **kwargs)

        patcher = mock.patch.object(os, "makedirs", side_effect=test_dirs_only)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, logs):
        for name, content in logs.items():
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
                f.write(content)

    def analyze(self, mapping=maui_v12_services.V12_STAGE_LOG_MAPPING):
        return station1.analyze_device_manufacturing_logs(SN, mapping)


class TestHostDutFromStageLogs(_LogDirTest):
    """host_dut_verification from host_dut_stage*.log."""

    def test_partner_logs_pass(self):
        self.write(PARTNER_SMT_LOGS)
        statuses, overall, infos, _ = self.analyze()
        self.assertEqual(statuses["host_dut_verification"], "PASSED")
        self.assertEqual(statuses["label_verified"], "NOT_RUN")
        self.assertEqual(overall, "PASS")
        names = [i.log_name for i in infos]
        self.assertNotIn("host_dut_tests.log", names)
        stage_names = [
            "host_dut_stage1_with_charger.log",
            "host_dut_stage2_sbu_flip.log",
            "host_dut_stage3_no_charger.log",
        ]
        # The stage logs take the place of host_dut_tests.log (card order).
        start = names.index("host_only_tests.log") + 1
        self.assertEqual(names[start : start + 3], stage_names)
        for info in infos:
            if info.log_name in stage_names:
                self.assertEqual(info.stage_verdict, "PASSED")
                self.assertTrue(info.is_present)
                self.assertGreater(info.file_size_bytes, 0)

    def test_any_failed_stage_fails(self):
        self.write({**PARTNER_SMT_LOGS, "host_dut_stage2_sbu_flip.log": STAGE_FAIL})
        statuses, overall, infos, _ = self.analyze()
        self.assertEqual(statuses["host_dut_verification"], "FAILED")
        self.assertEqual(overall, "FAIL")
        verdicts = {i.log_name: i.stage_verdict for i in infos}
        self.assertEqual(verdicts["host_dut_stage2_sbu_flip.log"], "FAILED")
        self.assertEqual(verdicts["host_dut_stage1_with_charger.log"], "PASSED")

    def test_failure_wins_over_bypass(self):
        self.write(
            {
                "host_dut_stage1_with_charger.log": STAGE_BYPASS,
                "host_dut_stage2_sbu_flip.log": STAGE_FAIL,
            }
        )
        statuses, _, _, _ = self.analyze()
        self.assertEqual(statuses["host_dut_verification"], "FAILED")

    def test_bypassed_stage_gives_pass_with_bypass(self):
        self.write({**PARTNER_SMT_LOGS, "host_dut_stage3_no_charger.log": STAGE_BYPASS})
        statuses, overall, infos, _ = self.analyze()
        self.assertEqual(statuses["host_dut_verification"], "BYPASSED")
        self.assertEqual(overall, "PASS_WITH_BYPASS")
        verdicts = {i.log_name: i.stage_verdict for i in infos}
        self.assertEqual(verdicts["host_dut_stage3_no_charger.log"], "BYPASSED")

    def test_partial_stage_logs_pass(self):
        self.write({"host_dut_stage1_with_charger.log": STAGE1_PASS})
        statuses, _, _, _ = self.analyze()
        self.assertEqual(statuses["host_dut_verification"], "PASSED")

    def test_without_stage_logs_not_run(self):
        self.write({"host_only_tests.log": PASS_LOG})
        statuses, _, infos, _ = self.analyze()
        self.assertEqual(statuses["host_dut_verification"], "NOT_RUN")
        names = [i.log_name for i in infos]
        self.assertIn("host_dut_tests.log", names)

    def test_v11_card_order_variants(self):
        self.write(
            {
                "host_dut_stage1_no_charger.log": STAGE_PASS,
                "host_dut_stage2_with_charger.log": STAGE_PASS,
                "host_dut_stage3_sbu_flip.log": STAGE_PASS,
            }
        )
        statuses, _, infos, _ = self.analyze(station1.V11_STAGE_LOG_MAPPING)
        self.assertEqual(statuses["host_dut_verification"], "PASSED")
        self.assertIn("host_dut_stage3_sbu_flip.log", [i.log_name for i in infos])

    def test_host_dut_tests_log_stays_authoritative_but_stage_failure_wins(self):
        self.write({"host_dut_tests.log": PASS_LOG})
        statuses, _, infos, _ = self.analyze()
        self.assertEqual(statuses["host_dut_verification"], "PASSED")
        self.write({"host_dut_stage2_sbu_flip.log": STAGE_FAIL})
        statuses, _, infos, _ = self.analyze()
        self.assertEqual(statuses["host_dut_verification"], "FAILED")
        verdicts = {i.log_name: i.stage_verdict for i in infos}
        self.assertEqual(verdicts["host_dut_tests.log"], "FAILED")
        self.assertEqual(verdicts["host_dut_stage2_sbu_flip.log"], "FAILED")


class TestLabelVerified(_LogDirTest):
    """label_verified only from the barcode page."""

    def submit(
        self, servicer_cls=maui_v12_services.MauiV12DeviceManagementServicer, **kwargs
    ):
        req = maui_manufacturing_pb2.MauiFinalSubmissionRequest(
            serial_number=SN, operator_id="OP_FACTORY", **kwargs
        )
        resp = servicer_cls().submit_final_manufacturing_results(req, mock.MagicMock())
        self.assertTrue(resp.success, resp.message)
        with open(
            os.path.join(self.dir, "final_results.json"), "r", encoding="utf-8"
        ) as f:
            return json.load(f)

    def test_smt_functional_with_matching_serials_is_not_run(self):
        self.write(PARTNER_SMT_LOGS)
        # As sent by the partner run (before the UI fix): label_verified=True.
        for label_verified in (True, False):
            data = self.submit(
                station_id="STATION_SMT_FUNCTIONAL",
                label_verified=label_verified,
                host_dut_passed=True,
                adb_serial=SN,
                ftdi_serial=SN,
            )
            statuses = data["stage_statuses"]
            self.assertEqual(statuses["label_verified"], "NOT_RUN")
            self.assertEqual(statuses["host_dut_verification"], "PASSED")
            self.assertEqual(data["overall_status"], "PASS")
            self.assertTrue(data["endpoints"]["serials_matched"])
            names = [f["name"] for f in data["log_files"]]
            self.assertIn("host_dut_stage2_sbu_flip.log", names)

    def test_v11_smt_functional_is_not_run(self):
        self.write({"host_only_tests.log": PASS_LOG})
        data = self.submit(
            servicer_cls=station1.MauiDeviceManagementServicer,
            station_id="STATION_SMT_FUNCTIONAL",
            label_verified=True,
            adb_serial=SN,
            ftdi_serial=SN,
        )
        self.assertEqual(data["stage_statuses"]["label_verified"], "NOT_RUN")
        self.assertEqual(data["overall_status"], "PASS")

    def test_serial_page_log_pass(self):
        self.write(
            {
                "serial_label_check.log": SERIAL_PAGE_LOG.format(
                    passed=True, result="PASSED", sn=SN, match="PASS"
                )
            }
        )
        statuses, _, _, _ = self.analyze()
        self.assertEqual(statuses["label_verified"], "PASSED")
        self.assertEqual(statuses["serial_label_check"], "PASSED")

    def test_serial_page_barcode_mismatch_fails(self):
        self.write(
            {
                "serial_label_check.log": SERIAL_PAGE_LOG.format(
                    passed=False, result="FAILED", sn="WRONG", match="FAIL"
                )
            }
        )
        data = self.submit(
            station_id="STATION_V12_SERIAL_LABEL",
            label_verified=False,
            adb_serial=SN,
            ftdi_serial=SN,
            scanned_barcode=SN,
        )
        self.assertEqual(data["stage_statuses"]["label_verified"], "FAILED")
        self.assertEqual(data["overall_status"], "FAIL")

    def test_label_verification_log(self):
        self.write({"label_verification.log": "Label Verification Result: PASSED\n"})
        self.assertEqual(self.analyze()[0]["label_verified"], "PASSED")
        self.write(
            {
                "label_verification.log": (
                    "Label Verification Result: FAILED\nMessage: Verification FAILED "
                    "(Sticker Mismatch)\n"
                )
            }
        )
        self.assertEqual(self.analyze()[0]["label_verified"], "FAILED")

    def test_fatp_submission_with_scanned_barcode(self):
        self.write({"host_only_tests.log": PASS_LOG})
        data = self.submit(
            station_id="STATION_FATP_QT0",
            label_verified=True,
            adb_serial=SN,
            ftdi_serial=SN,
            scanned_barcode=SN,
        )
        self.assertEqual(data["stage_statuses"]["label_verified"], "PASSED")
        self.assertTrue(data["endpoints"]["serials_matched"])

    def test_fatp_scanned_barcode_mismatch_without_log_fails(self):
        data = self.submit(
            station_id="STATION_FATP_QT0",
            label_verified=False,
            adb_serial=SN,
            ftdi_serial=SN,
            scanned_barcode="WRONG",
        )
        self.assertEqual(data["stage_statuses"]["label_verified"], "FAILED")


if __name__ == "__main__":
    unittest.main()
