# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for the Maui v1.2 functional test values (host-only cards).

The console transcripts are copies of real MCU App output from the v1.2
bring-up (b/567837469) and of the firmware print formats.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from server import maui_common
from server import maui_v12_device as device
from server import maui_v12_mcu_service as mcu_service
from server import maui_v12_profile as profile
from server import maui_v12_services
from server.generated import maui_manufacturing_pb2
from server.maui_console_tester import EXPECTED_HOST_GPIOS
from server.maui_console_tester import MauiConsoleTester
from server.maui_station2_service import MauiStation2Servicer


SN = "MAUIV12H26090104"

VERSION_059 = (
    "Maui version 0.59-838d947 (ite_freeze, built 2026-09-19 21:06:53 UTC) "
    "[MCUBOOT-SLOT0 (0x80038200)]"
)
VERSION_064 = (
    "Maui version 0.64-86c6fbb (main, built 2026-10-15 09:12:01 UTC) "
    "[MCUBOOT-SLOT0 (0x80038200)]"
)
VERSION_067 = (
    "Maui version 0.67-17241ef (main, built 2026-10-08 11:49:33 UTC) "
    "[MCUBOOT-SLOT0 (0x80038200)]"
)
VERSION_069 = (
    "Maui version 0.69-5324f43 (h2h-read-rpc, built 2026-10-13 08:45:17 UTC) "
    "[MCUBOOT-SLOT0 (0x80038200)]"
)

PDC_INFO_V12 = (
    "pdc info\r\nUsing TPS6699x device: tps6699x@20\r\nMode: APP1\r\n"
    "Version: e013200e\r\nApp Config Version: 00000325\r\nFW Version: 00000020\r\n"
    "maui$ "
)

# `gpio get` of FW 0.67 in the CC1 ("Normal") orientation (maui.log:63-96).
GPIO_CC1 = """gpio get
[              h2h_reset_l] O = 0
[              hub_reset_l] O = 0
[            pp5000_cutoff] O = 0
[               ec_mem_sel] O = 0
[              ec_mem_oe_l] O = 1
[        spi_ec_wp_mem_r_l] O = 0
[      spi_ec_hold_mem_r_l] O = 0
[        usb3_mux_hub_oe_l] O = 1
[         usb3_mux_hub_sel] O = 0
[        usb2_mux_hub_oe_l] O = 1
[         usb2_mux_hub_sel] O = 0
[      usb3_mux_dut_oe_r_l] O = 1
[       usb3_mux_dut_sel_r] O = 0
[      usb2_mux_dut_oe_r_l] O = 1
[       usb2_mux_dut_sel_r] O = 0
[     usb3_mux_host_oe_r_l] O = 1
[      usb3_mux_host_sel_r] O = 1
[           i2c_temp_alert] I = 0
[    h2h_host_access_led_l] I = 1
[h2h_dut_host_access_led_l] I = 0
[     h2h_dut_access_led_l] I = 0
[             pp1200_pg_od] I = 1
[             pp3300_pg_od] I = 1
[             pp1100_pg_od] I = 1
[         pp3300_ec_status] I = 0
[         hub_ss_up_status] I = 1
[         hub_hs_up_status] I = 1
[           hub_hs_suspend] I = 1
[           hub_ss_suspend] I = 1
[    pdc_hi_priority_irq_l] I = 0
[                pdc_gpio8] I = 1
[                pdc_gpio9] I = 1
[               pdc_gpio10] I = 1
[               pdc_gpio11] I = 1
maui$ """.replace(
    "\n", "\r\n"
)
# The same board flipped (CC2): only the USB3 host mux select differs.
GPIO_CC2 = GPIO_CC1.replace(
    "[      usb3_mux_host_sel_r] O = 1", "[      usb3_mux_host_sel_r] O = 0"
)


def _adc(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    cc1, cc2, chg=5021, dut=5, sbu1=5, sbu2=5, host=2519, pp5000=2516
):
    """Returns `adc status` output in the firmware format."""
    rows = (
        ("USB_HOST_CC1", cc1),
        ("USB_HOST_CC2", cc2),
        ("SBU1_DET", sbu1),
        ("SBU2_DET", sbu2),
        ("PPVAR_USB_CHG_VBUS", chg),
        ("PPVAR_USB_DUT_VBUS", dut),
        ("PP5000_USB_HOST_VBUS", host),
        ("PP5000", pp5000),
    )
    lines = ["adc status", "ADC values:"]
    for name, value in rows:
        if isinstance(value, str):
            lines.append(f"[{name:>20}] = {value}")
        else:
            lines.append(f"[{name:>20}] ={value: 5d} mV")
    return "\r\n".join(lines) + "\r\nmaui$ "


def _adc_status(output):
    """Parses |output| like the shared read_adc_status() does."""
    tester = device.MauiV12ConsoleTester("/dev/ttyACM0")
    with mock.patch.object(tester, "send_command", return_value=output):
        return tester.read_adc_status(ser=mock.MagicMock())


def _write_manifest(root, component, version):
    """Writes <root>/<component>/STABLE.json like the firmware fetcher."""
    os.makedirs(os.path.join(root, component), exist_ok=True)
    with open(os.path.join(root, component, "STABLE.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": version,
                "file": "maui_app_signed.bin",
                "md5": "0" * 32,
                "source": f"gs://maui-firmware/it82002-app-fw/{version}/",
            },
            f,
        )


def _firmware_root(test, **versions):
    """Returns a temporary firmware root with STABLE.json of |versions|."""
    tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
    test.addCleanup(tmp.cleanup)
    for component, version in versions.items():
        _write_manifest(tmp.name, component, version)
    return tmp.name


class TestMcuVersion(unittest.TestCase):
    """The MCU App version parser and the approved builds."""

    def setUp(self):
        self.default_roots = profile.FIRMWARE_ROOTS
        # Never read the firmware directories of the machine running the tests.
        patcher = mock.patch.object(profile, "FIRMWARE_ROOTS", ())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_parses_all_observed_builds(self):
        for line, build, branch in (
            (VERSION_059, "0.59-838d947", "ite_freeze"),
            (VERSION_067, "0.67-17241ef", "main"),
            (VERSION_069, "0.69-5324f43", "h2h-read-rpc"),
        ):
            version = profile.parse_mcu_version(f"version\r\n{line}\r\nmaui$ ")
            self.assertEqual(version.build_id, build)
            self.assertEqual(version.branch, branch)
            self.assertEqual(version.mode, "MCUBOOT-SLOT0")
            self.assertEqual(version.addr, "80038200")
            self.assertFalse(version.dirty)

    def test_detached_head_build_parses(self):
        line = VERSION_064.replace("(main,", "(HEAD,")
        self.assertEqual(profile.parse_mcu_version(line).branch, "HEAD")

    def test_stable_manifests_approve_their_builds(self):
        root = _firmware_root(self, mcu_app="0.69-5324f43", mcu_full="0.67-17241ef")
        self.assertEqual(
            profile.stable_mcu_versions([root]), ("0.69-5324f43", "0.67-17241ef")
        )
        check = profile.check_mcu_version(VERSION_069, environ={}, roots=[root])
        self.assertTrue(check.ok, check.message)
        self.assertEqual(check.build_id, "0.69-5324f43")
        self.assertEqual(check.message, "MCU App 0.69-5324f43 (approved build)")
        self.assertTrue(profile.check_mcu_version(VERSION_067, {}, [root]).ok)

    def test_unapproved_builds_fail_with_a_clear_message(self):
        root = _firmware_root(self, mcu_app="0.69-5324f43")
        check = profile.check_mcu_version(VERSION_064, environ={}, roots=[root])
        self.assertFalse(check.ok)
        self.assertEqual(
            check.message,
            "MCU App 0.64-86c6fbb is not an approved build (approved: 0.69-5324f43).",
        )
        self.assertFalse(profile.check_mcu_version(VERSION_059, {}, [root]).ok)
        self.assertFalse(profile.check_mcu_version(VERSION_067, {}, [root]).ok)

    def test_manifests_of_all_roots_are_merged(self):
        first = _firmware_root(self, mcu_app="0.69-5324F43")
        second = _firmware_root(self, mcu_app="0.69-5324f43", mcu_full="0.67-17241ef")
        self.assertEqual(
            profile.stable_mcu_versions([first, "/nonexistent", second]),
            ("0.69-5324f43", "0.67-17241ef"),
        )

    def test_broken_manifests_are_ignored(self):
        root = _firmware_root(self, mcu_app="latest", mcu_full="0.67-17241ef")
        path = os.path.join(root, "mcu_full", "STABLE.json")
        for content in ("{not json", '["0.67-17241ef"]', '{"file": "x.bin"}'):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            with self.assertLogs(level="WARNING"):
                self.assertEqual(profile.stable_mcu_versions([root]), (), content)

    def test_no_manifest_accepts_release_builds_with_a_note(self):
        for version, build in (
            (VERSION_059, "0.59-838d947"),
            (VERSION_069, "0.69-5324f43"),
        ):
            check = profile.check_mcu_version(version, environ={}, roots=[])
            self.assertTrue(check.ok, check.message)
            self.assertEqual(
                check.message,
                f"MCU App {build} (no bundled stable MCU firmware to compare)",
            )
        dirty = VERSION_069.replace("5324f43 (", "5324f43-dirty (")
        self.assertFalse(profile.check_mcu_version(dirty, environ={}, roots=[]).ok)

    def test_no_manifest_is_not_enough_when_approval_is_required(self):
        check = profile.check_mcu_version(
            VERSION_069, environ={}, roots=[], require_approval=True
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.build_id, "0.69-5324f43")
        self.assertIn(profile.NO_STABLE_FIRMWARE, check.message)
        self.assertIn("MAUI_V12_APPROVED_FW", check.message)
        root = _firmware_root(self, mcu_full="0.69-5324f43")
        self.assertTrue(
            profile.check_mcu_version(
                VERSION_069, environ={}, roots=[root], require_approval=True
            ).ok
        )

    def test_dirty_standalone_and_major_are_rejected(self):
        env = {"MAUI_V12_APPROVED_FW": "0.69-5324f43"}
        dirty = VERSION_069.replace("5324f43 (", "5324f43-dirty (")
        check = profile.check_mcu_version(dirty, env, [])
        self.assertFalse(check.ok)
        self.assertIn("0.69-5324f43-dirty is a dirty development build", check.message)

        standalone = VERSION_069.replace(
            "[MCUBOOT-SLOT0 (0x80038200)]", "[STANDALONE (0x80000000)]"
        )
        check = profile.check_mcu_version(standalone, env, [])
        self.assertFalse(check.ok)
        self.assertIn("expected MCUBOOT-SLOT0 (0x80038200)", check.message)

        wrong_slot = VERSION_069.replace("0x80038200", "0x80020000")
        self.assertFalse(profile.check_mcu_version(wrong_slot, env, []).ok)

        major = VERSION_069.replace("version 0.69", "version 1.69")
        check = profile.check_mcu_version(
            major, environ={"MAUI_V12_APPROVED_FW": "any"}
        )
        self.assertFalse(check.ok)
        self.assertIn("major version 1 (expected 0)", check.message)

    def test_unparsable_output(self):
        check = profile.check_mcu_version("Maui version 0.59.1-abc\r\nmaui$ ")
        self.assertFalse(check.ok)
        self.assertIn("Could not parse", check.message)
        self.assertIn("0.59.1-abc", check.message)
        self.assertEqual(check.build_id, "")
        self.assertIn("no 'Maui version' line", profile.check_mcu_version("").message)

    def test_env_ids_extend_the_manifests(self):
        root = _firmware_root(self, mcu_app="0.69-5324f43")
        env = {"MAUI_V12_APPROVED_FW": " 0.67-17241ef , 0.64-86C6FBB,,0.69-5324f43"}
        self.assertEqual(
            profile.approved_mcu_versions(env, [root]),
            ("0.69-5324f43", "0.67-17241ef", "0.64-86c6fbb"),
        )
        for version in (VERSION_069, VERSION_067, VERSION_064):
            self.assertTrue(profile.check_mcu_version(version, env, [root]).ok)
        self.assertFalse(profile.check_mcu_version(VERSION_059, env, [root]).ok)

    def test_env_ids_without_manifests(self):
        env = {"MAUI_V12_APPROVED_FW": "0.67-17241ef,0.64-86c6fbb"}
        self.assertTrue(profile.check_mcu_version(VERSION_064, env, []).ok)
        check = profile.check_mcu_version(VERSION_069, env, [], require_approval=True)
        self.assertFalse(check.ok)
        self.assertIn("(approved: 0.67-17241ef, 0.64-86c6fbb)", check.message)

    def test_env_any_accepts_every_well_formed_build(self):
        env = {"MAUI_V12_APPROVED_FW": "ANY"}
        root = _firmware_root(self, mcu_app="0.69-5324f43")
        self.assertIsNone(profile.approved_mcu_versions(env, [root]))
        check = profile.check_mcu_version(
            VERSION_059, env, [root], require_approval=True
        )
        self.assertTrue(check.ok)
        self.assertIn("MAUI_V12_APPROVED_FW=any", check.message)
        dirty = VERSION_064.replace("86c6fbb (", "86c6fbb-dirty (")
        self.assertFalse(profile.check_mcu_version(dirty, env, [root]).ok)

    def test_malformed_env_entries_are_ignored(self):
        env = {"MAUI_V12_APPROVED_FW": "0.69, latest, 0.67-17241ef"}
        with self.assertLogs(level="WARNING"):
            self.assertEqual(profile.approved_mcu_versions(env, []), ("0.67-17241ef",))
        env = {"MAUI_V12_APPROVED_FW": "latest"}
        with self.assertLogs(level="WARNING"):
            self.assertEqual(profile.approved_mcu_versions(env, []), ())
        self.assertEqual(profile.approved_mcu_versions({}, []), ())

    def test_hashes_are_compared_by_prefix(self):
        env = {"MAUI_V12_APPROVED_FW": "0.69-5324f43"}
        long_hash = VERSION_069.replace("5324f43 (", "5324f43a9 (")
        self.assertTrue(profile.check_mcu_version(long_hash, env, []).ok)
        other = VERSION_069.replace("5324f43 (", "5324f44 (")
        self.assertFalse(profile.check_mcu_version(other, env, []).ok)
        recount = VERSION_069.replace("0.69-", "0.70-")
        self.assertFalse(profile.check_mcu_version(recount, env, []).ok)

    def test_env_and_manifests_are_read_at_check_time(self):
        root = _firmware_root(self)
        with mock.patch.object(profile, "FIRMWARE_ROOTS", (root,)):
            with mock.patch.dict(
                "os.environ", {"MAUI_V12_APPROVED_FW": "0.69-5324f43"}
            ):
                self.assertFalse(profile.check_mcu_version(VERSION_064).ok)
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertTrue(profile.check_mcu_version(VERSION_064).ok)
                _write_manifest(root, "mcu_app", "0.69-5324f43")
                self.assertFalse(profile.check_mcu_version(VERSION_064).ok)
                self.assertTrue(profile.check_mcu_version(VERSION_069).ok)

    def test_firmware_list_and_manifests_share_the_roots(self):
        self.assertIs(mcu_service.FIRMWARE_ROOTS, self.default_roots)
        self.assertEqual(self.default_roots[0], "/var/lib/diagnoseme/firmware")

    def test_console_test_case_uses_the_approvals(self):
        tester = device.MauiV12ConsoleTester("/dev/ttyACM0")
        case = tester.test_cases[0]
        self.assertEqual((case.name, case.command), ("MCU Version Check", "version"))
        noisy = (
            "version\r\n[00:00:03.589,569] <inf> health: OS stable for 3s\r\n"
            f"{VERSION_069}\r\nmaui$ "
        )
        root = _firmware_root(self, mcu_app="0.69-5324f43")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                case.evaluate(noisy),
                (
                    True,
                    "MCU App 0.69-5324f43 (no bundled stable MCU firmware to compare)",
                ),
            )
            with mock.patch.object(profile, "FIRMWARE_ROOTS", (root,)):
                self.assertEqual(
                    case.evaluate(noisy),
                    (True, "MCU App 0.69-5324f43 (approved build)"),
                )
                self.assertFalse(case.evaluate(VERSION_064)[0])


class TestHwrev(unittest.TestCase):
    """Accepted hardware revisions."""

    def test_default_is_revision_3(self):
        self.assertEqual(profile.valid_hwrevs({}), (3,))
        self.assertEqual(profile.expected_hwrev_text({}), "3")

    def test_env_override(self):
        env = {"MAUI_V12_HWREVS": " 4, 3,x,8,-1,"}
        self.assertEqual(profile.valid_hwrevs(env), (3, 4))
        self.assertEqual(profile.expected_hwrev_text(env), "3 or 4")
        self.assertEqual(profile.valid_hwrevs({"MAUI_V12_HWREVS": "x"}), (3,))

    def test_tester_and_servicer_follow_the_env(self):
        tester = device.MauiV12ConsoleTester("/dev/ttyACM0")
        servicer = maui_v12_services.MauiV12Station2Servicer()
        with mock.patch.object(
            tester, "send_command", return_value="Hardware revision: 4\r\nmaui$ "
        ):
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertEqual(
                    tester.read_hwrev(ser=mock.MagicMock())[:2], (False, 4)
                )
                self.assertEqual(servicer._expected_hwrev_text(), "3")
            with mock.patch.dict("os.environ", {"MAUI_V12_HWREVS": "3,4"}):
                self.assertEqual(tester.read_hwrev(ser=mock.MagicMock())[:2], (True, 4))
                self.assertEqual(servicer._expected_hwrev_text(), "3 or 4")

    def test_init_failure_is_reported(self):
        tester = device.MauiV12ConsoleTester("/dev/ttyACM0")
        with mock.patch.object(
            tester, "send_command", return_value="Hardware revision: -1\r\nmaui$ "
        ):
            self.assertEqual(tester.read_hwrev(ser=mock.MagicMock())[:2], (False, -1))


class TestGpioTable(unittest.TestCase):
    """The v1.2 `gpio get` table of the host-only cards."""

    def test_table_is_the_connection_independent_subset(self):
        table = device.V12_EXPECTED_HOST_GPIOS
        low = ("h2h_reset_l", "hub_reset_l", "pp5000_cutoff", "ec_mem_sel")
        high = ("ec_mem_oe_l", "pp1200_pg_od", "pp3300_pg_od", "pp1100_pg_od")
        self.assertEqual(
            table,
            {**dict.fromkeys(low + ("i2c_temp_alert",), 0), **dict.fromkeys(high, 1)},
        )
        # The v1.1 table is unchanged.
        self.assertEqual(len(EXPECTED_HOST_GPIOS), 19)

    def test_observed_listings_pass_in_both_orientations(self):
        tester = device.MauiV12ConsoleTester("/dev/ttyACM0")
        for listing in (GPIO_CC1, GPIO_CC2):
            with mock.patch.object(
                tester, "send_command", return_value=listing
            ) as send:
                ok, parsed, mismatches, raw = tester.read_and_verify_gpios(
                    ser=mock.MagicMock()
                )
            send.assert_called_once()
            self.assertTrue(ok, mismatches)
            self.assertEqual(len(parsed), 34)
            self.assertEqual(raw, listing)

    def test_connection_dependent_gpios_are_not_checked(self):
        listing = GPIO_CC1
        for name, old, new in (
            ("usb3_mux_host_sel_r", "O = 1", "O = 0"),
            ("usb3_mux_dut_sel_r", "O = 0", "O = 1"),
            ("hub_hs_up_status", "I = 1", "I = 0"),
            ("hub_ss_suspend", "I = 1", "I = 0"),
            ("h2h_dut_access_led_l", "I = 0", "I = 1"),
            ("pp3300_ec_status", "I = 0", "I = 1"),
            ("pdc_gpio8", "I = 1", "I = 0"),
        ):
            line = f"[{name:>25}] "
            listing = listing.replace(line + old, line + new)
        self.assertNotEqual(listing, GPIO_CC1)
        self.assertEqual(self._verify(listing), (True, []))

    def test_mismatches_are_reported_clearly(self):
        listing = GPIO_CC1.replace(
            "[              ec_mem_oe_l] O = 1", "[              ec_mem_oe_l] O = 0"
        ).replace("[             pp3300_pg_od] I = 1\r\n", "")
        self.assertEqual(
            self._verify(listing),
            (
                False,
                [
                    "ec_mem_oe_l: expected 1, got 0",
                    "pp3300_pg_od: missing in 'gpio get'",
                ],
            ),
        )

    @staticmethod
    def _verify(listing):
        tester = device.MauiV12ConsoleTester("/dev/ttyACM0")
        with mock.patch.object(tester, "send_command", return_value=listing):
            ok, _, mismatches, _ = tester.read_and_verify_gpios(ser=mock.MagicMock())
        return ok, mismatches


class TestAdc(unittest.TestCase):
    """verify_host_adc(): `adc status` of the v1.2 host-only cards."""

    def _verify(self, output):
        return device.verify_host_adc(_adc_status(output))

    def test_all_bring_up_captures_pass(self):
        # (CC1, CC2, host VBUS, PP5000) of Maui.txt, update-fail_maui.log and
        # maui.log (CC1, CC2) of b/567837469; SBU 2-8 mV and CHG/DUT VBUS 0-8 mV
        # in all of them (no charger, no DUT).
        for cc1, cc2, host, pp5000, line in (
            (0, 1669, 2496, 2496, "CC2"),
            (8, 1708, 2542, 2545, "CC2"),
            (418, 5, 2519, 2516, "CC1"),
            (5, 416, 2510, 2496, "CC2"),
        ):
            values = {"sbu1": 2, "sbu2": 8, "dut": 0, "host": host, "pp5000": pp5000}
            status = _adc_status(_adc(cc1, cc2, chg=8, **values))
            self.assertEqual(
                device.verify_host_adc(status, charger_connected=False),
                (True, []),
                (cc1, cc2),
            )
            self.assertTrue(status["host_orientation"].startswith(line))
            # The same board with the charger plugged in (vSafe5V).
            self.assertEqual(
                self._verify(_adc(cc1, cc2, chg=5021, **values)), (True, [])
            )

    def test_host_cc_must_have_exactly_one_active_line(self):
        for cc1, cc2 in ((250, 150), (5, 2200), (1669, 0)):
            self.assertEqual(self._verify(_adc(cc1, cc2)), (True, []), (cc1, cc2))
        for cc1, cc2 in ((418, 416), (5, 5), (249, 5), (2201, 5), (418, 151)):
            ok, mismatches = self._verify(_adc(cc1, cc2))
            self.assertFalse(ok, (cc1, cc2))
            self.assertEqual(
                mismatches,
                [
                    f"Host CC: USB_HOST_CC1={cc1} mV, USB_HOST_CC2={cc2} mV "
                    "(expected exactly one in [250, 2200] mV and the other "
                    "<= 150 mV)"
                ],
            )

    def test_rails_use_expected_value_plus_minus_10_percent(self):
        for pp5000, host in ((2250, 2250), (2500, 2500), (2750, 2750)):
            self.assertEqual(
                self._verify(_adc(418, 5, pp5000=pp5000, host=host)), (True, [])
            )
        ok, mismatches = self._verify(_adc(418, 5, pp5000=2751, host=2249))
        self.assertFalse(ok)
        self.assertEqual(
            mismatches,
            [
                "PP5000: 2751 mV outside [2250, 2750] mV",
                "PP5000_USB_HOST_VBUS: 2249 mV outside [2250, 2750] mV",
            ],
        )
        self.assertFalse(self._verify(_adc(418, 5, pp5000=2249))[0])
        self.assertFalse(self._verify(_adc(418, 5, host=2751))[0])

    def test_expected_range(self):
        self.assertEqual(profile.expected_range(2500), (2250, 2750))
        self.assertEqual(profile.CHG_VBUS_5V_MV, (4500, 5500))
        self.assertEqual(profile.CHG_VBUS_20V_MV, (18000, 22000))

    def test_charger_vbus_is_5v_or_a_20v_contract(self):
        for chg in (4500, 5021, 5500, 18000, 20000, 22000):
            self.assertEqual(self._verify(_adc(418, 5, chg=chg)), (True, []), chg)
        for chg in (5, 536, 4499, 5501, 15000, 17999, 22001):
            ok, mismatches = self._verify(_adc(418, 5, chg=chg))
            self.assertFalse(ok, chg)
            self.assertEqual(
                mismatches,
                [
                    f"PPVAR_USB_CHG_VBUS: {chg} mV is neither 5 V [4500, 5500] "
                    "mV nor a 20 V contract [18000, 22000] mV (is the charger "
                    "plugged in?)"
                ],
            )

    def test_charger_vbus_without_charger_is_vsafe0v(self):
        status = _adc_status(_adc(418, 5, chg=800))
        self.assertEqual(
            device.verify_host_adc(status, charger_connected=False), (True, [])
        )
        status = _adc_status(_adc(418, 5, chg=5021))
        self.assertEqual(
            device.verify_host_adc(status, charger_connected=False),
            (False, ["PPVAR_USB_CHG_VBUS: 5021 mV above 800 mV (vSafe0V, no charger)"]),
        )

    def test_dut_port_must_be_idle(self):
        self.assertEqual(
            self._verify(_adc(418, 5, sbu1=100, sbu2=100, dut=800)), (True, [])
        )
        ok, mismatches = self._verify(_adc(418, 5, sbu1=350, sbu2=101, dut=801))
        self.assertFalse(ok)
        self.assertEqual(
            mismatches,
            [
                "SBU1_DET: 350 mV above 100 mV (no DUT)",
                "SBU2_DET: 101 mV above 100 mV (no DUT)",
                "PPVAR_USB_DUT_VBUS: 801 mV above 800 mV (vSafe0V, no DUT)",
            ],
        )

    def test_error_and_missing_channels_fail(self):
        ok, mismatches = self._verify(_adc("error (-5)", 416))
        self.assertFalse(ok)
        self.assertEqual(mismatches, ["USB_HOST_CC1: ADC read error"])
        output = _adc(418, 5).replace("[              PP5000] = 2516 mV\r\n", "")
        ok, mismatches = self._verify(output)
        self.assertFalse(ok)
        self.assertEqual(mismatches, ["PP5000: missing in 'adc status'"])
        self.assertEqual(
            device.verify_host_adc({"raw_output": "Port error"}),
            (False, ["'adc status' returned no readings"]),
        )

    def test_parse_adc_channels(self):
        channels = device.parse_adc_channels(
            _adc(-12, "error (-22)").replace("ADC", "\x1b[1;32mADC\x1b[0m")
        )
        self.assertEqual(channels["USB_HOST_CC1"], -12)
        self.assertIsNone(channels["USB_HOST_CC2"])
        self.assertEqual(channels["PP5000"], 2516)
        self.assertEqual(len(channels), 8)


class TestPdc(unittest.TestCase):
    """PDC info and status on v1.2."""

    def setUp(self):
        self.tester = device.MauiV12ConsoleTester("/dev/ttyACM0")

    def test_pdc_info_expects_the_v12_image(self):
        case = self.tester.test_cases[1]
        self.assertEqual(case.command, "pdc info")
        self.assertEqual(
            case.evaluate(PDC_INFO_V12),
            (True, "PDC Info verified (AppConfig: 00000325, FW: 00000020)"),
        )
        ok, msg = case.evaluate(
            PDC_INFO_V12.replace("00000325", "00000318").replace("00000020", "00000019")
        )
        self.assertFalse(ok)
        self.assertIn("expected 00000325, got 00000318", msg)
        self.assertIn("expected 00000020, got 00000019", msg)
        self.assertIsNone(MauiConsoleTester.EXPECTED_PDC_APP_CONFIG_VER)

    def _status(self, output, port="charger"):
        with mock.patch.object(self.tester, "send_command", return_value=output):
            return self.tester.read_pdc_port_status(port=port, ser=mock.MagicMock())

    def test_old_single_block_format(self):
        res = self._status(
            "pdc status charger\r\nPDC Status:\r\n  Plug Present:     No\r\n"
        )
        self.assertTrue(res["success"])
        self.assertFalse(res["plug_present"])
        self.assertEqual(res["contract_type"], "None")

    def test_new_two_port_format(self):
        output = (
            "pdc status all\r\n"
            "=== PDC Status: DUT (Port 1) ===\r\n"
            "  Plug Present:     No\r\n"
            "  Active Contract:  None\r\n"
            "  Measured VBUS:    0.046 V (46 mV)\r\n"
            "\r\n"
            "=== PDC Status: Charger (Port 2) ===\r\n"
            "  Plug Present:     Yes\r\n"
            "  Connection State: 6\r\n"
            "  Orientation:      Flipped (CC2)\r\n"
            "  Power Role:       Sink\r\n"
            "  Data Role:        UFP (Device)\r\n"
            "  EPR Mode Active:  No\r\n"
            "  Active Contract:\r\n"
            "    Raw PDO:        0x0801912c\r\n"
            "    Raw RDO:        0x1304b12c\r\n"
            "    Type:           Fixed Supply\r\n"
            "    Voltage:        5.000 V\r\n"
            "    Max Current:    3.00 A\r\n"
            "    Op Current:     3.00 A\r\n"
            "  Measured VBUS:    5.021 V (5021 mV)\r\n"
            "maui$ "
        )
        chg = self._status(output)
        self.assertTrue(chg["plug_present"])
        self.assertEqual(chg["contract_type"], "Fixed Supply")
        self.assertEqual(chg["voltage_v"], 5.0)
        self.assertEqual(chg["op_current_a"], 3.0)
        self.assertEqual(chg["measured_vbus_mv"], 5021)
        self.assertEqual(chg["orientation"], "Flipped (CC2)")
        dut = self._status(output, port="dut")
        self.assertFalse(dut["plug_present"])
        self.assertEqual(dut["measured_vbus_mv"], 46)
        self.assertEqual(dut["contract_type"], "None")

    def test_new_format_without_plug(self):
        res = self._status(
            "=== PDC Status: Charger (Port 2) ===\r\n  Plug Present:     No\r\n"
            "  Active Contract:  None\r\n  Measured VBUS:    0.005 V (5 mV)\r\n"
        )
        self.assertFalse(res["plug_present"])
        self.assertEqual(res["contract_type"], "None")
        self.assertEqual(res["measured_vbus_mv"], 5)


class TestUsbTopologyPolling(unittest.TestCase):
    """The v1.2 host-only cards wait for the USB3 hub to re-enumerate."""

    def setUp(self):
        self.servicer = maui_v12_services.MauiV12Station2Servicer()
        self.now = 0.0
        self.sleeps = []
        self.servicer._clock = lambda: self.now
        self.servicer._sleep = self._sleep

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def _usb(self, ss_hub=True):
        devices = [
            maui_common.UsbDeviceInfo(sys_path="a", vid=0x0451, pid=0x8142),
            maui_common.UsbDeviceInfo(sys_path="c", vid=0x18D1, pid=0x506D, serial=SN),
        ]
        if ss_hub:
            devices.append(
                maui_common.UsbDeviceInfo(sys_path="b", vid=0x0451, pid=0x8140)
            )
        return devices

    def _check(self, scan):
        app = device.McuConsole(
            dev_node="/dev/ttyACM0",
            usb_path="/sys/usb1/1-3/1-3.1",
            product=profile.PRODUCT_APP,
            stage=profile.STAGE_APP,
        )
        with mock.patch.object(
            maui_common, "scan_usb_devices", side_effect=scan
        ), mock.patch.object(device, "scan_mcu_consoles", return_value=[app]):
            return self.servicer._check_host_usb_topology()

    def test_returns_at_once_when_everything_is_present(self):
        self.assertEqual(self._check(self._usb), (True, True, True, True))
        self.assertEqual(self.sleeps, [])

    def test_waits_for_the_usb3_hub_to_come_back(self):
        self.assertEqual(
            self._check(lambda: self._usb(ss_hub=self.now >= 2.4)),
            (True, True, True, True),
        )
        self.assertEqual(set(self.sleeps), {profile.USB_TOPOLOGY_POLL_S})
        self.assertAlmostEqual(self.now, 2.5)

    def test_gives_up_after_the_settle_time(self):
        self.assertEqual(
            self._check(lambda: self._usb(ss_hub=False)), (True, False, True, True)
        )
        self.assertGreaterEqual(self.now, profile.USB_TOPOLOGY_SETTLE_S)
        self.assertLessEqual(
            self.now, profile.USB_TOPOLOGY_SETTLE_S + profile.USB_TOPOLOGY_POLL_S
        )


class TestBslGpio(unittest.TestCase):
    """BSL button test through the ec_bsl_invoke GPIO."""

    PRESSED = "gpio get ec_bsl_invoke\r\n[            ec_bsl_invoke] I = 1\r\nmaui$ "
    MISSING = "gpio get ec_bsl_invoke\r\nmaui$ "

    def test_parser_matches_the_firmware_line(self):
        tester = device.MauiV12ConsoleTester("/dev/ttyACM0")
        with mock.patch.object(tester, "send_command", return_value=self.PRESSED):
            self.assertEqual(
                tester.read_gpio("ec_bsl_invoke", ser=mock.MagicMock()),
                (1, self.PRESSED),
            )

    def test_gpio_missing(self):
        self.assertTrue(device.gpio_missing(self.MISSING, "ec_bsl_invoke"))
        self.assertTrue(
            device.gpio_missing(
                "\x1b[1;32mmaui$ \x1b[mgpio get ec_bsl_invoke\r\n\x1b[1;32mmaui$ ",
                "ec_bsl_invoke",
            )
        )
        self.assertFalse(device.gpio_missing(self.PRESSED, "ec_bsl_invoke"))
        self.assertFalse(
            device.gpio_missing(
                "gpio get ec_bsl_invoke\r\n[            ec_bsl_invoke] I = 0\r\n",
                "ec_bsl_invoke",
            )
        )
        self.assertFalse(device.gpio_missing("", "ec_bsl_invoke"))


@mock.patch("server.maui_station2_service.write_device_log")
@mock.patch("server.maui_station2_service.open_serial_port")
@mock.patch("server.maui_station2_service.os.path.exists", return_value=True)
class TestV12HostOnlyCards(unittest.TestCase):
    """Cards 1 and 2 of the functional suite on Maui v1.2."""

    CHARGER = (
        "=== PDC Status: Charger (Port 2) ===\r\n  Plug Present:     Yes\r\n"
        "    Type:           Fixed Supply\r\n    Voltage:        5.000 V\r\n"
        "  Measured VBUS:    5.021 V (5021 mV)\r\nmaui$ "
    )

    def setUp(self):
        self.servicer = maui_v12_services.MauiV12Station2Servicer()
        patcher = mock.patch.object(
            self.servicer,
            "_check_host_usb_topology",
            return_value=(True, True, True, True),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict("os.environ", {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        root = _firmware_root(self, mcu_app="0.69-5324f43")
        roots = mock.patch.object(profile, "FIRMWARE_ROOTS", (root,))
        roots.start()
        self.addCleanup(roots.stop)

    def _host_only(self, version=VERSION_069, adc=None, gpio=GPIO_CC1):
        replies = [
            f"version\r\n{version}\r\nmaui$ ",
            PDC_INFO_V12,
            self.CHARGER,
            adc or _adc(418, 5),
            "Hardware revision: 3\r\nmaui$ ",
            "Temperature = 35.00 'C\r\nmaui$ ",
            gpio,
        ]
        with mock.patch.object(
            device.MauiV12ConsoleTester, "send_command", side_effect=replies
        ):
            return self.servicer.run_host_only_tests(
                maui_manufacturing_pb2.MauiHostOnlyTestRequest(
                    serial_number=SN, device_port="/dev/ttyACM0"
                ),
                None,
            )

    def test_host_only_passes_with_v12_values(self, *unused_mocks):
        resp = self._host_only()
        self.assertTrue(resp.all_passed, resp.summary)
        self.assertEqual(resp.mcu_version_str, "MCU App 0.69-5324f43 (approved build)")
        self.assertEqual(resp.expected_hwrev, "3")
        self.assertEqual(resp.pdc_appconfig_ver, "00000325")
        self.assertEqual(resp.pdc_fw_ver, "00000020")
        self.assertTrue(resp.host_orientation.startswith("CC1"))
        self.assertEqual(
            resp.gpio_details,
            "All 9 GPIOs verified (connection-independent subset)",
        )
        self.assertTrue(resp.voltages_ok)

    def test_unapproved_firmware_fails(self, *unused_mocks):
        resp = self._host_only(version=VERSION_064)
        self.assertFalse(resp.all_passed)
        self.assertFalse(resp.mcu_version_ok)
        self.assertIn("0.64-86c6fbb is not an approved build", resp.summary)

    def test_gpios_do_not_depend_on_the_orientation(self, *unused_mocks):
        resp = self._host_only(adc=_adc(418, 5), gpio=GPIO_CC2)
        self.assertTrue(resp.all_passed, resp.summary)
        self.assertTrue(resp.gpios_ok)

    def test_gpio_mismatch_fails(self, *unused_mocks):
        gpio = GPIO_CC1.replace(
            "[             pp1100_pg_od] I = 1", "[             pp1100_pg_od] I = 0"
        )
        resp = self._host_only(gpio=gpio)
        self.assertFalse(resp.all_passed)
        self.assertFalse(resp.gpios_ok)
        self.assertIn("pp1100_pg_od: expected 1, got 0", resp.gpio_details)

    def test_no_bundled_stable_firmware(self, *unused_mocks):
        with mock.patch.object(profile, "FIRMWARE_ROOTS", ()):
            resp = self._host_only(version=VERSION_064)
        self.assertTrue(resp.all_passed, resp.summary)
        self.assertEqual(
            resp.mcu_version_str,
            "MCU App 0.64-86c6fbb (no bundled stable MCU firmware to compare)",
        )

    def test_adc_error_fails(self, *unused_mocks):
        resp = self._host_only(adc=_adc(418, 5, chg="error (-5)"))
        self.assertFalse(resp.voltages_ok)
        self.assertIn("PPVAR_USB_CHG_VBUS: ADC read error", resp.voltage_details)

    def test_host_replug_after_flip(self, *unused_mocks):
        replies = [
            f"version\r\n{VERSION_069}\r\nmaui$ ",
            PDC_INFO_V12,
            _adc(5, 1669),
            "Hardware revision: 3\r\nmaui$ ",
            "Temperature = 36.00 'C\r\nmaui$ ",
            GPIO_CC2,
        ]
        with mock.patch.object(
            device.MauiV12ConsoleTester, "send_command", side_effect=replies
        ):
            resp = self.servicer.test_host_replug(
                maui_manufacturing_pb2.MauiHostReplugTestRequest(
                    serial_number=SN,
                    device_port="/dev/ttyACM0",
                    previous_orientation="CC1 (418mV / 5mV)",
                ),
                None,
            )
        self.assertTrue(resp.all_passed, resp.message)
        self.assertTrue(resp.host_flip_ok)
        self.assertEqual(resp.expected_hwrev, "3")
        self.assertTrue(resp.gpios_ok)


class TestV11Unchanged(unittest.TestCase):
    """The shared hooks keep the Maui v1.1 behaviour."""

    def test_v11_hooks(self):
        servicer = MauiStation2Servicer()
        self.assertEqual(servicer._expected_hwrev_text(), "2")
        tester = mock.MagicMock()
        tester.read_and_verify_gpios.return_value = (True, {}, [], "")
        self.assertEqual(
            servicer._verify_gpios(tester, "ser", {"host_cc1_mv": 418}),
            (True, {}, [], ""),
        )
        tester.read_and_verify_gpios.assert_called_once_with(ser="ser")
        ok, mismatches = servicer._verify_voltages(
            {"pp5000_mv": 2500, "host_vbus_mv": 2500, "chg_vbus_mv": 5000}
        )
        self.assertEqual((ok, mismatches), (True, []))


if __name__ == "__main__":
    unittest.main()
