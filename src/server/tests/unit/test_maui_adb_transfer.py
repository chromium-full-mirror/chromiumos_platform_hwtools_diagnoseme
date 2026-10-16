# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for the Maui ADB transfer reliability test."""

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from server import maui_adb_transfer as adbt
from server.generated import maui_manufacturing_pb2
from server.maui_station2_service import MauiStation2Servicer


class FakeAdb:  # pylint: disable=too-many-instance-attributes
    """Fake `adb` with a DUT file store and a fake clock."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        devices=("DUT123",),
        rate_mb_s=50.0,
        corrupt_push="",
        corrupt_pull="",
        has_z=True,
        has_sha1sum=True,
        overshoot_s=0.0,
    ):
        self.devices = list(devices)
        self.rate = rate_mb_s * adbt.MB
        # Remote paths containing these strings are corrupted.
        self.corrupt_push = corrupt_push
        self.corrupt_pull = corrupt_pull
        self.has_z = has_z
        self.has_sha1sum = has_sha1sum
        # Transfers slower than their timeout still succeed this much late.
        self.overshoot_s = overshoot_s
        self.now = 0.0
        self.dut = {}
        self.pushed = {}
        self.calls = []

    def clock(self):
        return self.now

    def _transfer(self, size, timeout):
        seconds = size / self.rate
        if seconds > timeout:
            if self.overshoot_s:
                self.now += timeout + self.overshoot_s
                return
            self.now += timeout
            raise subprocess.TimeoutExpired("adb", timeout)
        self.now += seconds

    @staticmethod
    def _corrupt(data):
        return bytes([data[0] ^ 0xFF]) + data[1:]

    def _shell(self, args):
        out, rc = "", 0
        if args[1] == "sha1sum":
            if not self.has_sha1sum:
                return "/system/bin/sh: sha1sum: not found", 127
            out = "\n".join(
                f"{hashlib.sha1(self.dut[p]).hexdigest()}  {p}"
                for p in args[2:]
                if p in self.dut
            )
        elif args[1:3] == ["rm", "-f"]:
            for path in args[3:]:
                self.dut.pop(path, None)
        return out, rc

    def __call__(self, cmd, timeout=None, **unused_kwargs):
        self.calls.append(cmd)
        out, rc = "", 0
        if cmd[1:] == ["devices"]:
            out = "List of devices attached\n" + "".join(
                f"{d}\tdevice\n" for d in self.devices
            )
        elif cmd[1:] == ["help"]:
            out = (
                "push [--sync] [-z ALGORITHM] [-Z] LOCAL... REMOTE"
                if self.has_z
                else ""
            )
        else:
            args = [a for a in cmd[3:] if a != "-Z"]
            if args[0] == "push":
                with open(args[1], "rb") as f:
                    data = f.read()
                self._transfer(len(data), timeout)
                self.pushed[args[2]] = data
                if self.corrupt_push and self.corrupt_push in args[2]:
                    data = self._corrupt(data)
                self.dut[args[2]] = data
            elif args[0] == "pull":
                data = self.dut[args[1]]
                self._transfer(len(data), timeout)
                if self.corrupt_pull and self.corrupt_pull in args[1]:
                    data = self._corrupt(data)
                with open(args[2], "wb") as f:
                    f.write(data)
            elif args[0] == "shell":
                out, rc = self._shell(args)
        return subprocess.CompletedProcess(cmd, rc, stdout=out)


class NoPullFileAdb(FakeAdb):
    """FakeAdb whose `adb pull` succeeds without writing the file."""

    def __call__(self, cmd, timeout=None, **kwargs):
        if "pull" in cmd:
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="")
        return super().__call__(cmd, timeout=timeout, **kwargs)


def _calls(fake, verb):
    return [c for c in fake.calls if verb in c]


class TestAdbTransfer(unittest.TestCase):  # pylint: disable=too-many-public-methods
    """run_adb_transfer_test()."""

    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work)

    def _run(self, fake, **kwargs):
        kwargs.setdefault("size_mib", 2)
        kwargs.setdefault("extra_mib", 3)
        return adbt.run_adb_transfer_test(
            run=fake, clock=fake.clock, work_dir=self.work, **kwargs
        )

    def test_pass_two_files_measures_rates_and_cleans_up(self):
        fake = FakeAdb(rate_mb_s=50.0)
        with mock.patch.dict(os.environ, {adbt.DEADLINE_ENV: ""}):
            res = self._run(fake)
        self.assertTrue(res.success, res.log)
        self.assertEqual(res.adb_serial, "DUT123")
        self.assertEqual(res.file_size_bytes, 7 * adbt.MIB)
        self.assertEqual(
            [f.size_bytes for f in res.files], [2 * adbt.MIB, 5 * adbt.MIB]
        )
        for f in res.files:
            self.assertAlmostEqual(f.push_mb_s, 50.0)
            self.assertAlmostEqual(f.pull_mb_s, 50.0)
            self.assertEqual(f.host_sha1, f.dut_sha1)
            self.assertEqual(f.host_sha1, f.pulled_sha1)
        self.assertAlmostEqual(res.push_mb_s, 50.0)
        self.assertAlmostEqual(res.pull_mb_s, 50.0)
        self.assertAlmostEqual(res.push_s, 7 * adbt.MIB / (50.0 * adbt.MB))
        self.assertEqual(res.host_sha1, ", ".join(f.host_sha1 for f in res.files))
        self.assertEqual(res.host_sha1, res.dut_sha1)
        self.assertEqual(res.host_sha1, res.pulled_sha1)
        self.assertAlmostEqual(res.transfer_s, res.push_s + res.pull_s)
        self.assertEqual(res.deadline_s, adbt.DEFAULT_DEADLINE_S)
        self.assertGreaterEqual(res.total_s, res.transfer_s)
        self.assertIn("2 + 5 MiB", res.message)
        self.assertIn("Push file 2 (5 MiB): ", "\n".join(res.log))
        self.assertIn("Pull file 1 (2 MiB): ", "\n".join(res.log))
        self.assertEqual(fake.dut, {})  # Removed from the DUT.
        self.assertEqual(os.listdir(self.work), [])  # Host temp files removed.
        self.assertEqual(len(_calls(fake, "push")), 2)
        self.assertEqual(len(_calls(fake, "pull")), 2)
        self.assertIn("-Z", _calls(fake, "push")[0])
        # Both DUT copies are hashed by one sha1sum.
        (sha1sum,) = _calls(fake, "sha1sum")
        self.assertEqual(sorted(sha1sum[5:]), sorted(fake.pushed))

    def test_files_are_independent_and_the_second_is_larger(self):
        fake = FakeAdb()
        with mock.patch.object(adbt.random, "randint", return_value=7) as randint:
            res = self._run(fake, extra_mib=None)
        self.assertTrue(res.success, res.log)
        randint.assert_called_once_with(1, 16)
        first, second = (fake.pushed[r] for r in sorted(fake.pushed))
        self.assertEqual((len(first), len(second)), (2 * adbt.MIB, 9 * adbt.MIB))
        self.assertNotEqual(first, second[: len(first)])
        self.assertNotEqual(res.files[0].host_sha1, res.files[1].host_sha1)

    def test_old_adb_without_compression_flag(self):
        fake = FakeAdb(has_z=False)
        self.assertTrue(self._run(fake).success)
        self.assertNotIn("-Z", _calls(fake, "push")[0])

    def test_no_device_fails(self):
        res = self._run(FakeAdb(devices=()))
        self.assertFalse(res.success)
        self.assertIn("No ADB device", res.message)

    def test_preferred_serial(self):
        res = self._run(FakeAdb(devices=("A", "B")), preferred_serial="B")
        self.assertEqual(res.adb_serial, "B")

    def test_multiple_devices_warns(self):
        res = self._run(FakeAdb(devices=("A", "B")))
        self.assertTrue(res.success)
        self.assertEqual(res.adb_serial, "A")
        self.assertTrue(any("WARNING" in line for line in res.log))

    def test_push_corruption_detected_on_dut(self):
        fake = FakeAdb(corrupt_push="_2.bin")
        res = self._run(fake)
        self.assertFalse(res.success)
        self.assertIn("corrupted on push", res.message)
        self.assertIn("file 2", res.message)
        self.assertNotIn("file 1", res.message)
        self.assertEqual(_calls(fake, "pull"), [])  # Fails before pulling.
        self.assertEqual(fake.dut, {})

    def test_pull_corruption_detected(self):
        res = self._run(FakeAdb(corrupt_pull="_1.bin"))
        self.assertFalse(res.success)
        self.assertIn("corrupted on pull", res.message)
        self.assertIn("file 1", res.message)
        self.assertNotEqual(res.host_sha1, res.pulled_sha1)

    def test_without_dut_sha1sum_the_pulled_copies_decide(self):
        res = self._run(FakeAdb(has_sha1sum=False))
        self.assertTrue(res.success, res.log)
        self.assertEqual(res.dut_sha1, "")
        self.assertTrue(any("no DUT SHA-1" in line for line in res.log))
        res = self._run(FakeAdb(has_sha1sum=False, corrupt_push="_1.bin"))
        self.assertFalse(res.success)
        self.assertIn("corrupted on pull", res.message)

    def test_slow_link_hits_deadline(self):
        fake = FakeAdb(rate_mb_s=0.05)
        res = self._run(fake, deadline_s=30.0)
        self.assertFalse(res.success)
        self.assertIn("Push of file 1 did not finish within the 30 s", res.message)
        self.assertLessEqual(res.transfer_s, 30.0)
        self.assertEqual(fake.dut, {})
        self.assertEqual(os.listdir(self.work), [])

    def test_transfer_phase_over_the_deadline_fails(self):
        # The last pull ends after the deadline although adb reported success.
        fake = FakeAdb(rate_mb_s=1.0, overshoot_s=0.5)
        res = self._run(fake, deadline_s=12.0)
        self.assertFalse(res.success)
        self.assertIn("more than the 12 s deadline", res.message)
        self.assertGreater(res.transfer_s, 12.0)

    def test_generation_is_not_part_of_the_deadline(self):
        fake = FakeAdb(rate_mb_s=50.0)
        urandom = os.urandom

        def slow_urandom(n):
            fake.now += 10.0
            return urandom(n)

        # 2 + 3 MiB: one urandom chunk per file, 20 s in total.
        with mock.patch.object(adbt.os, "urandom", side_effect=slow_urandom):
            res = self._run(fake, extra_mib=1, deadline_s=1.0, generation_limit_s=25.0)
        self.assertTrue(res.success, res.log)
        self.assertLess(res.transfer_s, 1.0)
        self.assertGreaterEqual(res.total_s, 20.0)

    def test_generation_is_capped(self):
        fake = FakeAdb(rate_mb_s=50.0)
        urandom = os.urandom

        def slow_urandom(n):
            fake.now += 10.0
            return urandom(n)

        with mock.patch.object(adbt.os, "urandom", side_effect=slow_urandom):
            res = self._run(fake, extra_mib=1, generation_limit_s=15.0)
        self.assertFalse(res.success)
        self.assertIn("took longer than 15 s", res.message)
        self.assertEqual(_calls(fake, "push"), [])
        self.assertEqual(os.listdir(self.work), [])

    def test_default_sizes_fit_the_budget_at_50_mb_s(self):
        largest = (2 * adbt.DEFAULT_SIZE_MIB + adbt.EXTRA_MIB_RANGE[1]) * adbt.MIB
        transfer_s = 2 * largest / (50 * adbt.MB)
        self.assertLess(transfer_s, adbt.DEFAULT_DEADLINE_S * 0.7)
        self.assertEqual(adbt.DEFAULT_DEADLINE_S, 60.0)

    def test_pull_without_file_fails(self):
        res = self._run(NoPullFileAdb())
        self.assertFalse(res.success)
        self.assertIn("pull", res.message)

    def test_speed_is_only_logged_by_default(self):
        with mock.patch.dict(os.environ, {adbt.MIN_RATE_ENV: ""}):
            res = self._run(FakeAdb(rate_mb_s=10.0))
        self.assertTrue(res.success, res.log)

    def test_minimum_rate_is_enforced(self):
        res = self._run(FakeAdb(rate_mb_s=10.0), min_rate_mb_s=25.0)
        self.assertFalse(res.success)
        self.assertIn("too slow: 10.0 MB/s < 25.0 MB/s", res.message)
        self.assertEqual(
            self._run(FakeAdb(rate_mb_s=30.0), min_rate_mb_s=25.0).success, True
        )

    def test_size_and_deadline_from_environment(self):
        fake = FakeAdb(rate_mb_s=50.0)
        env = {adbt.SIZE_ENV: "3", adbt.DEADLINE_ENV: "12.5"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(adbt.get_size_mib(), 3)
            self.assertEqual(adbt.get_deadline_s(), 12.5)
            res = adbt.run_adb_transfer_test(
                run=fake, clock=fake.clock, work_dir=self.work, extra_mib=1
            )
        self.assertTrue(res.success, res.log)
        self.assertEqual(res.file_size_bytes, 7 * adbt.MIB)
        self.assertEqual(res.deadline_s, 12.5)
        self.assertTrue(any("deadline 12.5 s" in line for line in res.log))

    def test_invalid_environment_values_are_ignored(self):
        for size, deadline, rate in (
            ("0", "0", "-1"),
            ("abc", "-5", "fast"),
            ("nan", "inf", "nan"),
        ):
            env = {
                adbt.SIZE_ENV: size,
                adbt.DEADLINE_ENV: deadline,
                adbt.MIN_RATE_ENV: rate,
            }
            with mock.patch.dict(os.environ, env):
                self.assertEqual(adbt.get_size_mib(), adbt.DEFAULT_SIZE_MIB)
                self.assertEqual(adbt.get_deadline_s(), adbt.DEFAULT_DEADLINE_S)
                self.assertEqual(adbt.get_min_rate_mb_s(), adbt.MIN_RATE_MB_S)
        with mock.patch.dict(os.environ, {adbt.SIZE_ENV: "99999"}):
            self.assertEqual(adbt.get_size_mib(), adbt.MAX_SIZE_MIB)

    def test_minimum_rate_from_environment(self):
        with mock.patch.dict(os.environ, {adbt.MIN_RATE_ENV: "25"}):
            self.assertEqual(adbt.get_min_rate_mb_s(), 25.0)
            res = self._run(FakeAdb(rate_mb_s=10.0))
        self.assertFalse(res.success)
        self.assertTrue(any("minimum 25.0 MB/s" in line for line in res.log))
        with mock.patch.dict(os.environ, {adbt.MIN_RATE_ENV: "fast"}):
            self.assertEqual(adbt.get_min_rate_mb_s(), adbt.MIN_RATE_MB_S)

    def test_parse_sha1sum(self):
        out = (
            f"{'a' * 40}  /data/local/tmp/x_1.bin\n"
            f"{'b' * 40} */data/local/tmp/x_2.bin\n"
            "sha1sum: /data/local/tmp/x_3.bin: No such file or directory\n"
        )
        self.assertEqual(
            adbt.parse_sha1sum(out),
            {"/data/local/tmp/x_1.bin": "a" * 40, "/data/local/tmp/x_2.bin": "b" * 40},
        )


class TestStation2AdbTransferRpc(unittest.TestCase):
    """MauiStation2Servicer.run_adb_transfer_test()."""

    RESULT = adbt.AdbTransferResult(
        success=True,
        message="ADB transfer OK",
        adb_serial="DUT123",
        file_size_bytes=807 * adbt.MIB,
        push_s=16.0,
        pull_s=18.0,
        push_mb_s=52.9,
        pull_mb_s=47.0,
        host_sha1=f"{'a' * 40}, {'b' * 40}",
        dut_sha1=f"{'a' * 40}, {'b' * 40}",
        pulled_sha1=f"{'a' * 40}, {'b' * 40}",
        total_s=38.5,
        log=["line 1", "line 2"],
        transfer_s=35.0,
        deadline_s=60.0,
    )

    def setUp(self):
        self.servicer = MauiStation2Servicer()
        patcher = mock.patch(
            "server.maui_adb_transfer.run_adb_transfer_test", return_value=self.RESULT
        )
        self.mock_test = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("server.maui_station2_service.write_device_log")
        self.mock_log = patcher.start()
        self.addCleanup(patcher.stop)

    def test_maps_the_result_and_writes_the_log(self):
        req = maui_manufacturing_pb2.MauiAdbTransferTestRequest(
            serial_number="MAUI123", adb_serial="DUT123", size_mb=100
        )
        resp = self.servicer.run_adb_transfer_test(req, mock.MagicMock())
        self.mock_test.assert_called_once_with(preferred_serial="DUT123", size_mib=100)
        self.assertTrue(resp.success)
        self.assertEqual(resp.message, "ADB transfer OK")
        self.assertEqual(resp.adb_serial, "DUT123")
        self.assertEqual(resp.file_size_bytes, 807 * adbt.MIB)
        self.assertAlmostEqual(resp.push_mb_per_s, 52.9, places=3)
        self.assertAlmostEqual(resp.pull_mb_per_s, 47.0, places=3)
        self.assertAlmostEqual(resp.push_seconds, 16.0)
        self.assertAlmostEqual(resp.pull_seconds, 18.0)
        self.assertAlmostEqual(resp.total_seconds, 38.5)
        self.assertAlmostEqual(resp.transfer_seconds, 35.0)
        self.assertAlmostEqual(resp.deadline_seconds, 60.0)
        self.assertEqual(resp.host_sha1, f"{'a' * 40}, {'b' * 40}")
        self.assertEqual(resp.pulled_sha1, f"{'a' * 40}, {'b' * 40}")
        self.assertEqual(resp.log, "line 1\nline 2")
        serial, step, content = self.mock_log.call_args[0]
        self.assertEqual((serial, step), ("MAUI123", "adb_transfer_test"))
        self.assertTrue(content.startswith("ALL_PASSED=True\nRESULT: PASSED\n"))
        self.assertIn("Push: 16.00 s, 52.9 MB/s", content)
        self.assertIn("Transfer: 35.00 s (deadline 60 s)", content)
        self.assertTrue(content.endswith("line 1\nline 2"))

    def test_defaults_to_the_connected_unit(self):
        failed = adbt.AdbTransferResult(success=False, message="No ADB device")
        self.mock_test.return_value = failed
        req = maui_manufacturing_pb2.MauiAdbTransferTestRequest()
        with mock.patch.object(
            MauiStation2Servicer, "_find_unit_serial", return_value="H2H42"
        ), mock.patch.dict(os.environ, {adbt.SIZE_ENV: ""}):
            resp = self.servicer.run_adb_transfer_test(req, mock.MagicMock())
        self.mock_test.assert_called_once_with(
            preferred_serial="", size_mib=adbt.DEFAULT_SIZE_MIB
        )
        self.assertFalse(resp.success)
        serial, _, content = self.mock_log.call_args[0]
        self.assertEqual(serial, "H2H42")
        self.assertTrue(content.startswith("ALL_PASSED=False\nRESULT: FAILED\n"))

    def test_station_size_override(self):
        req = maui_manufacturing_pb2.MauiAdbTransferTestRequest(serial_number="M1")
        with mock.patch.dict(os.environ, {adbt.SIZE_ENV: "100"}):
            self.servicer.run_adb_transfer_test(req, mock.MagicMock())
        self.mock_test.assert_called_once_with(preferred_serial="", size_mib=100)


if __name__ == "__main__":
    unittest.main()
