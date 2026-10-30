# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for the station scripts of the deb (development_environment).

Covers the station config handling (config.env + config.local.env, the
migration of station settings on upgrade), the Maui compose override, the
edition check of the offline update, the maui-utils md5 check before
`docker load`, tools/check_deb.py and the shared ADB transfer test defaults
(code, config.env, start-diagnoseme and the Maui compose override agree).
"""

import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from unittest import mock

from server import maui_adb_transfer as adbt


# pylint: disable=missing-function-docstring


REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
DEV_ENV = os.path.join(REPO_ROOT, "development_environment")
DEB_BUILDER = os.path.join(DEV_ENV, "deb_builder")


_CHECK_DEB_SPEC = importlib.util.spec_from_file_location(
    "check_deb", os.path.join(REPO_ROOT, "tools", "check_deb.py")
)
check_deb = importlib.util.module_from_spec(_CHECK_DEB_SPEC)
_CHECK_DEB_SPEC.loader.exec_module(check_deb)


def _write(path, text, mode=0o644):
    """Writes |text| to |path| (creating directories)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(text))
    os.chmod(path, mode)
    return path


class ScriptTestCase(unittest.TestCase):
    """Common setup: a temporary directory."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def run_lib(self, shell, script, code, env=None):
        """Sources |script| for its functions only and runs |code|."""
        full_env = dict(os.environ, DIAGNOSEME_LIB_ONLY="1", **(env or {}))
        return subprocess.run(
            [shell, "-c", f'. "{script}"; {code}'],
            capture_output=True,
            text=True,
            env=full_env,
            check=False,
        )


class TestConfigMigration(ScriptTestCase):
    """postinst: station settings of an old config.env -> config.local.env."""

    POSTINST = os.path.join(DEB_BUILDER, "postinst")

    def migrate(self, old_text, new_text, local_text=None):
        old = _write(os.path.join(self.tmp, "old.env"), old_text)
        new = _write(os.path.join(self.tmp, "config.env"), new_text)
        local = os.path.join(self.tmp, "config.local.env")
        if local_text is not None:
            _write(local, local_text)
        proc = self.run_lib(
            "sh", self.POSTINST, f'migrate_station_config "{old}" "{new}" "{local}"'
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        if not os.path.exists(local):
            return None
        with open(local, encoding="utf-8") as f:
            return f.read()

    def source(self, *files, names=("STATION_ID",)):
        script = "; ".join(f'. "{f}"' for f in files)
        script += "; " + "; ".join(f'printf "%s\\n" "${{{n}-}}"' for n in names)
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=True
        ).stdout.splitlines()

    def test_odm_station_values_move_to_config_local_env(self):
        # config.env of an ODM B station (a8a85a8) with a changed station ID,
        # a results server and an ADB override.
        local = self.migrate(
            """\
            MODE=offline
            DOCKER_TAG=maui
            STATION_ID=LINE3_STATION_7
            RESULTS_SERVER="http://mes.local/hook?a=1&b='x'"
            MAUI_ADB_DEADLINE_S=60
            #MAUI_ADB_SIZE_MB=400
            MAUI_UTILS_IMAGE=maui-utils:old
            MAUI_DEVICE=maui_v11
            """,
            """\
            MODE=offline
            DOCKER_TAG=maui-v1.1
            STATION_ID=MAUI_V1_1_STATION
            RESULTS_SERVER=
            MAUI_UTILS_IMAGE=maui-utils:0.0.1-69-89b053b
            MAUI_DEVICE=maui_v11
            #MAUI_ADB_DEADLINE_S=40
            """,
        )
        self.assertIn("STATION_ID='LINE3_STATION_7'", local)
        self.assertIn("MAUI_ADB_DEADLINE_S='60'", local)
        # Package owned values and unset (commented) keys are not moved.
        self.assertNotIn("MAUI_UTILS_IMAGE", local)
        self.assertNotIn("MAUI_DEVICE", local)
        self.assertNotIn("MAUI_ADB_SIZE_MB", local)
        self.assertNotIn("DOCKER_TAG", local)
        self.assertEqual(
            self.source(
                os.path.join(self.tmp, "config.env"),
                os.path.join(self.tmp, "config.local.env"),
                names=("STATION_ID", "RESULTS_SERVER", "MAUI_ADB_DEADLINE_S"),
            ),
            ["LINE3_STATION_7", "http://mes.local/hook?a=1&b='x'", "60"],
        )

    def test_values_equal_to_the_new_defaults_are_not_moved(self):
        local = self.migrate(
            "STATION_ID=MAUI_V1_1_STATION\nRESULTS_SERVER=\n",
            "STATION_ID=MAUI_V1_1_STATION\nRESULTS_SERVER=\n",
        )
        self.assertIsNone(local)

    def test_old_package_default_station_ids_are_not_moved(self):
        # a8a85a8 shipped STATION_ID=MAUI_STATION_1 and the first editions
        # packages (also ODM A's Maui v1.2) MAUI_V1_1_STATION as defaults.
        for old_id, new_id in (
            ("MAUI_STATION_1", "MAUI_V1_1_STATION"),
            ("MAUI_V1_1_STATION", "MAUI_V1_2_STATION"),
        ):
            with self.subTest(old_id=old_id):
                local = self.migrate(
                    f"DIAGNOSEME_EDITION=maui-v1.2\nSTATION_ID={old_id}\n"
                    "MAUI_V12_APPROVED_FW=0.69-5324f43\n",
                    f"DIAGNOSEME_EDITION=maui-v1.2\nSTATION_ID={new_id}\n",
                )
                self.assertNotIn("STATION_ID", local)
                self.assertIn("MAUI_V12_APPROVED_FW='0.69-5324f43'", local)
                os.remove(os.path.join(self.tmp, "config.local.env"))

    def test_empty_values_are_never_written(self):
        local = self.migrate(
            "DOCKER_TAG=maui\nSTATION_ID=\nRESULTS_SERVER=\nMAUI_ADB_SIZE_MB=\n",
            "DIAGNOSEME_EDITION=maui-v1.1\nSTATION_ID=MAUI_V1_1_STATION\n"
            "RESULTS_SERVER=http://new\n",
        )
        self.assertIsNone(local)

    def test_edition_switch_moves_nothing(self):
        # servo-dolos -> maui-v1.1 (--switch-edition) and a pre-editions Maui
        # station (DOCKER_TAG=maui = maui-v1.1) moving to another edition.
        for old_text, new_text in (
            (
                "DIAGNOSEME_EDITION=servo-dolos\nSTATION_ID=\nRESULTS_SERVER=x\n",
                "DIAGNOSEME_EDITION=maui-v1.1\nSTATION_ID=MAUI_V1_1_STATION\n",
            ),
            (
                "DOCKER_TAG=maui\nSTATION_ID=LINE3\n",
                "DIAGNOSEME_EDITION=servo-dolos\nSTATION_ID=\n",
            ),
        ):
            with self.subTest(old=old_text):
                self.assertIsNone(self.migrate(old_text, new_text))

    def test_config_env_of_an_editions_package_is_not_migrated(self):
        # Since config.local.env exists, config.env only holds package
        # defaults: a repeat upgrade or a default that changes between
        # releases must not freeze the old default in config.local.env.
        local = self.migrate(
            "# Put station settings into /etc/diagnoseme/config.local.env.\n"
            "DIAGNOSEME_EDITION=maui-v1.1\nSTATION_ID=MAUI_V1_1_OLD\n"
            "MAUI_ADB_DEADLINE_S=30\n",
            "DIAGNOSEME_EDITION=maui-v1.1\nSTATION_ID=MAUI_V1_1_STATION\n",
        )
        self.assertIsNone(local)

    def test_shipped_config_templates_carry_the_marker(self):
        # The skip above relies on every shipped config.env naming
        # config.local.env.
        for name in ("config.env", "config-generic.env"):
            with open(os.path.join(DEB_BUILDER, name), encoding="utf-8") as f:
                self.assertIn("config.local.env", f.read(), name)

    def test_existing_config_local_env_wins(self):
        local = self.migrate(
            "STATION_ID=OLD\nRESULTS_SERVER=http://old\n",
            "STATION_ID=NEW\n",
            "STATION_ID=MINE\n",
        )
        self.assertEqual(local, "STATION_ID=MINE\nRESULTS_SERVER='http://old'\n")

    def test_no_old_config(self):
        proc = self.run_lib(
            "sh",
            self.POSTINST,
            f'migrate_station_config "{self.tmp}/none" "{self.tmp}/new" '
            f'"{self.tmp}/local"',
        )
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "local")))

    def test_preinst_saves_the_old_config(self):
        with open(os.path.join(DEB_BUILDER, "preinst"), encoding="utf-8") as f:
            preinst = f.read()
        self.assertIn('CONFIG_FILE="/etc/diagnoseme/config.env"', preinst)
        self.assertIn(
            'SAVED_CONFIG="/var/lib/diagnoseme/config.env.pre-upgrade"', preinst
        )
        with open(self.POSTINST, encoding="utf-8") as f:
            postinst = f.read()
        self.assertIn(
            'SAVED_CONFIG="/var/lib/diagnoseme/config.env.pre-upgrade"', postinst
        )
        self.assertIn('migrate_station_config "$SAVED_CONFIG"', postinst)


class TestStartDiagnoseme(ScriptTestCase):
    """start-diagnoseme: config loading and the maui-utils md5 check."""

    SCRIPT = os.path.join(DEB_BUILDER, "start-diagnoseme")

    def test_config_local_env_is_read_after_config_env(self):
        _write(os.path.join(self.tmp, "config.env"), "STATION_ID=A\nMODE=offline\n")
        _write(os.path.join(self.tmp, "config.local.env"), "STATION_ID=B\n")
        proc = self.run_lib(
            "bash",
            self.SCRIPT,
            'load_station_config; echo "$STATION_ID $MODE"',
            env={"DIAGNOSEME_CONFIG_DIR": self.tmp},
        )
        self.assertEqual(proc.stdout.strip(), "B offline", proc.stderr)

    def verify(self, data, manifest):
        utils_dir = os.path.join(self.tmp, "maui-utils")
        os.makedirs(utils_dir, exist_ok=True)
        with open(os.path.join(utils_dir, "maui-utils.tar"), "wb") as f:
            f.write(data)
        if manifest is not None:
            _write(os.path.join(utils_dir, "STABLE.json"), json.dumps(manifest))
        return self.run_lib(
            "bash",
            self.SCRIPT,
            "verify_maui_utils_tar",
            env={"DIAGNOSEME_MAUI_UTILS_DIR": utils_dir},
        )

    def test_matching_md5_is_loaded(self):
        proc = self.verify(b"image", {"md5": hashlib.md5(b"image").hexdigest()})
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_damaged_tar_is_not_loaded(self):
        proc = self.verify(b"broken", {"md5": hashlib.md5(b"image").hexdigest()})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("damaged", proc.stdout)

    def test_missing_or_invalid_manifest_fails_closed(self):
        self.assertNotEqual(self.verify(b"image", None).returncode, 0)
        self.assertNotEqual(self.verify(b"image", {"md5": "x"}).returncode, 0)

    def test_maui_directories_only_on_maui_stations(self):
        with open(self.SCRIPT, encoding="utf-8") as f:
            script = f.read()
        self.assertIn('if [ "$MAUI_STATION" = true ]; then', script)
        self.assertNotIn("maui_v11_results", script)
        self.assertIn("MAUI_ADB_MIN_LINK_MBPS", script)

    def test_h2h_firmware_dir_only_with_h2h_firmware(self):
        # M8: only Maui stations of an edition with H2H firmware flashing
        # (config.env MAUI_H2H_FIRMWARE=true, Maui v1.2) set, create and
        # mount the H2H firmware directory.
        with open(self.SCRIPT, encoding="utf-8") as f:
            script = f.read()
        self.assertEqual(script.count("MAUI_H2H_FW_DIR="), 1)
        main = script.split('if [ -n "${DIAGNOSEME_LIB_ONLY:-}" ]', 1)[1]
        maui_block = main.split('if [ "$MAUI_STATION" = true ]; then', 1)[1]
        self.assertIn('if [ "${MAUI_H2H_FIRMWARE:-false}" = true ]; then', maui_block)
        self.assertNotIn("diagnoseme_h2h_firmware", main.split(maui_block)[0])
        self.assertIn(
            'if [ "$MAUI_STATION" = true ] && [ "${MAUI_H2H_FIRMWARE:-false}" = true ]',
            main,
        )

    def test_create_h2h_fw_dir(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        proc = self.run_lib(
            "bash",
            self.SCRIPT,
            'D="$(station_home)/diagnoseme_h2h_firmware"; create_h2h_fw_dir "$D"; '
            'create_h2h_fw_dir "$D"; echo "$D"',
            env={"HOME": home, "SUDO_USER": ""},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        h2h_dir = proc.stdout.strip()
        if os.getuid() != 0:
            self.assertEqual(h2h_dir, os.path.join(home, "diagnoseme_h2h_firmware"))
        self.assertTrue(os.path.isdir(h2h_dir))


class TestAdbSettingsConsistency(unittest.TestCase):
    """One set of ADB transfer defaults, passed through by every station file."""

    @staticmethod
    def _read(*path):
        with open(os.path.join(*path), encoding="utf-8") as f:
            return f.read()

    def test_shared_defaults(self):
        # D2: every Maui edition uses the same tuning (40 s, 5 Gbps link).
        self.assertEqual(adbt.DEFAULT_DEADLINE_S, 40.0)
        self.assertEqual(adbt.MIN_LINK_MBPS, 5000)
        with open(os.path.join(REPO_ROOT, "editions.json"), encoding="utf-8") as f:
            registry = json.load(f)
        for edition_id, edition in registry["editions"].items():
            for key in edition:
                self.assertNotIn("adb", key.lower(), edition_id)

    def test_overrides_reach_the_server(self):
        defaults = {
            adbt.SIZE_ENV: adbt.DEFAULT_SIZE_MIB,
            adbt.DEADLINE_ENV: adbt.DEFAULT_DEADLINE_S,
            adbt.MIN_RATE_ENV: adbt.MIN_RATE_MB_S,
            adbt.MIN_LINK_ENV: adbt.MIN_LINK_MBPS,
        }
        compose = self._read(DEV_ENV, "docker-compose-maui.yaml")
        start = self._read(DEB_BUILDER, "start-diagnoseme")
        config = self._read(DEB_BUILDER, "config.env")
        for env, default in defaults.items():
            self.assertIn(f"- {env}\n", compose)
            self.assertIn(env, start)
            # config.env documents the code default of every override.
            self.assertIn(f"#{env}={default:g}\n", config)


class TestComposeFiles(ScriptTestCase):
    """Generic compose file without Maui mounts, the Maui override, stop."""

    def test_generic_compose_has_no_maui_mounts(self):
        with open(
            os.path.join(DEV_ENV, "docker-compose-prod.yaml"), encoding="utf-8"
        ) as f:
            text = f.read()
        for maui in ("maui", "firmware", "DIAGNOSEME_RESULTS_DIR", "devices"):
            self.assertNotIn(maui, text.replace("docker-compose-maui.yaml", ""))
        self.assertIn("${DOCKER_TAG:-release}", text)
        self.assertIn("${PULL_POLICY:-always}", text)

    def test_maui_override_has_the_maui_mounts(self):
        with open(
            os.path.join(DEV_ENV, "docker-compose-maui.yaml"), encoding="utf-8"
        ) as f:
            text = f.read()
        for mount in (
            "/var/lib/diagnoseme/firmware:/var/lib/diagnoseme/firmware:ro",
            "/var/lib/diagnoseme/maui-utils/staging:"
            "/var/lib/diagnoseme/maui-utils/staging",
            "/var/log/diagnoseme/devices:/var/log/diagnoseme/devices",
            "MAUI_ADB_MIN_LINK_MBPS",
        ):
            self.assertIn(mount, text)

    def test_dev_compose_mounts_maui_only_with_the_dev_override(self):
        # The development compose file of GEMINI.md creates no Maui folders;
        # docker-compose-dev-maui.yaml adds them for Maui development.
        with open(os.path.join(DEV_ENV, "docker-compose.yaml"), encoding="utf-8") as f:
            dev = f.read()
        body = dev[dev.index("services:") :].replace("docker-compose-dev-maui.yaml", "")
        for maui in ("maui", "DIAGNOSEME_RESULTS_DIR", "/var/log/diagnoseme/devices"):
            self.assertNotIn(maui, body)
        with open(
            os.path.join(DEV_ENV, "docker-compose-dev-maui.yaml"), encoding="utf-8"
        ) as f:
            override = f.read()
        for mount in (
            "/var/log/diagnoseme/devices:/var/log/diagnoseme/devices",
            "/var/lib/diagnoseme/maui-utils/staging:"
            "/var/lib/diagnoseme/maui-utils/staging",
            "MAUI_ADB_MIN_LINK_MBPS",
        ):
            self.assertIn(mount, override)

    def test_v12_overrides_reach_the_server(self):
        with open(
            os.path.join(DEV_ENV, "docker-compose-maui.yaml"), encoding="utf-8"
        ) as f:
            compose = f.read()
        with open(os.path.join(DEB_BUILDER, "start-diagnoseme"), encoding="utf-8") as f:
            start = f.read()
        with open(os.path.join(DEB_BUILDER, "config.env"), encoding="utf-8") as f:
            config = f.read()
        for env in (
            "MAUI_V12_APPROVED_FW",
            "MAUI_V12_HWREVS",
            "MAUI_ITE_NO_OUTPUT_TIMEOUT_S",
        ):
            self.assertIn(f"- {env}\n", compose)
            self.assertIn(env, start)
            self.assertIn(f"#{env}=", config)

    def test_h2h_override_mounts_the_h2h_firmware_dir(self):
        with open(
            os.path.join(DEV_ENV, "docker-compose-maui-h2h.yaml"), encoding="utf-8"
        ) as f:
            text = f.read()
        self.assertIn("/mnt/h2h_firmware:ro", text)
        self.assertIn("MAUI_H2H_FW_DIR=${MAUI_H2H_FW_DIR:-", text)
        # Neither the generic file nor the shared Maui override mount it.
        for name in ("docker-compose-prod.yaml", "docker-compose-maui.yaml"):
            with open(os.path.join(DEV_ENV, name), encoding="utf-8") as f:
                self.assertNotIn("h2h_firmware", f.read(), name)

    def test_stop_uses_the_compose_files_of_config_env(self):
        compose = _write(
            os.path.join(self.tmp, "docker-compose.yaml"), "services: {}\n"
        )
        maui = _write(
            os.path.join(self.tmp, "docker-compose-maui.yaml"), "services: {}\n"
        )
        _write(
            os.path.join(self.tmp, "config.env"),
            f"COMPOSE_FILE={compose}:{maui}\nDIAGNOSEME_RESULTS_DIR=/r\n",
        )
        bindir = os.path.join(self.tmp, "bin")
        log = os.path.join(self.tmp, "docker.log")
        _write(
            os.path.join(bindir, "docker"),
            f'#!/bin/bash\necho "$* R=$DIAGNOSEME_RESULTS_DIR" >> "{log}"\n',
            0o755,
        )
        env = dict(
            os.environ,
            DIAGNOSEME_CONFIG_DIR=self.tmp,
            PATH=f"{bindir}:{os.environ['PATH']}",
        )
        proc = subprocess.run(
            ["bash", os.path.join(DEB_BUILDER, "stop-diagnoseme")],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(log, encoding="utf-8") as f:
            self.assertEqual(f.read(), f"compose -f {compose} -f {maui} down R=/r\n")

    def test_stop_without_config_uses_the_generic_compose_file(self):
        compose = _write(
            os.path.join(self.tmp, "docker-compose.yaml"), "services: {}\n"
        )
        bindir = os.path.join(self.tmp, "bin")
        log = os.path.join(self.tmp, "docker.log")
        _write(
            os.path.join(bindir, "docker"),
            f'#!/bin/bash\necho "$*" >> "{log}"\n',
            0o755,
        )
        env = dict(
            os.environ,
            DIAGNOSEME_CONFIG_DIR=self.tmp,
            PATH=f"{bindir}:{os.environ['PATH']}",
        )
        subprocess.run(
            ["bash", os.path.join(DEB_BUILDER, "stop-diagnoseme")],
            capture_output=True,
            env=env,
            check=True,
        )
        with open(log, encoding="utf-8") as f:
            self.assertEqual(f.read(), f"compose -f {compose} down\n")


class TestDashboard(ScriptTestCase):
    """diagnoseme-dashboard reads the results directory of the station."""

    def load(self):
        loader = importlib.machinery.SourceFileLoader(
            "diagnoseme_dashboard", os.path.join(DEB_BUILDER, "diagnoseme-dashboard")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def test_results_dir_from_config_local_env(self):
        dashboard = self.load()
        config = _write(
            os.path.join(self.tmp, "config.env"), "DIAGNOSEME_RESULTS_DIR=/a\n"
        )
        local = _write(
            os.path.join(self.tmp, "config.local.env"), "DIAGNOSEME_RESULTS_DIR=/b\n"
        )
        env = {k: v for k, v in os.environ.items() if k != "DIAGNOSEME_RESULTS_DIR"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(dashboard.station_results_dir((config, local)), "/b")
            self.assertEqual(dashboard.station_results_dir((config,)), "/a")
            self.assertEqual(
                dashboard.station_results_dir((os.path.join(self.tmp, "none"),)),
                dashboard.DEFAULT_RESULTS_DIR,
            )

    def test_reads_the_edition_and_the_legacy_results(self):
        # S5: the edition's results directory (e.g. maui_v12_results) and the
        # ~/diagnoseme_results of older releases are both read.
        dashboard = self.load()
        results = os.path.join(self.tmp, "maui_v12_results")
        new = _write(os.path.join(results, "highlevel", "SN1_PASS.json"), "{}")
        home = os.path.join(self.tmp, "home")
        old = _write(
            os.path.join(home, "diagnoseme_results", "highlevel", "SN2_PASS.json"),
            "{}",
        )
        with mock.patch.object(dashboard, "RESULTS_DIR", results), mock.patch.dict(
            os.environ, {"HOME": home}
        ):
            files = dashboard.find_result_files()
        self.assertIn(new, files)
        self.assertIn(old, files)


class TestPreseed(ScriptTestCase):
    """The ISO preseed creates the results directory of the edition."""

    def test_results_dir_of_the_edition_is_created(self):
        with open(
            os.path.join(REPO_ROOT, "test_server", "preseed.cfg"), encoding="utf-8"
        ) as f:
            lines = [line for line in f if "DIAGNOSEME_RESULTS_DIR" in line]
        self.assertEqual(len(lines), 1)
        command = lines[0].strip().rstrip("\\").strip().replace("/target", self.tmp)
        _write(
            os.path.join(self.tmp, "etc", "diagnoseme", "config.env"),
            "MODE=offline\nDIAGNOSEME_RESULTS_DIR=/var/log/diagnoseme/maui_v12_results\n",
        )
        proc = subprocess.run(
            ["bash", "-c", f"in-target() {{ :; }}; {command}"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(
            os.path.isdir(
                os.path.join(self.tmp, "var", "log", "diagnoseme", "maui_v12_results")
            )
        )


class TestOfflineUpdateEditionCheck(ScriptTestCase):
    """diagnoseme-offline-update refuses bundles of another edition."""

    SCRIPT = os.path.join(DEB_BUILDER, "diagnoseme-offline-update")

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def check(
        self, manifest, config=None, installed=False, deb_package=None, switch=False
    ):
        manifest_path = _write(
            os.path.join(self.tmp, "MANIFEST.json"), json.dumps(manifest)
        )
        config_dir = os.path.join(self.tmp, "etc")
        os.makedirs(config_dir, exist_ok=True)
        if config is not None:
            _write(os.path.join(config_dir, "config.env"), config)
        status = "install ok installed" if installed else ""
        dpkg_query = _write(
            os.path.join(self.tmp, "dpkg-query"),
            f'#!/bin/sh\nprintf "{status}"\n',
            0o755,
        )
        dpkg_deb = _write(
            os.path.join(self.tmp, "dpkg-deb"),
            f'#!/bin/sh\nprintf "%s\\n" "{deb_package or ""}"\n',
            0o755,
        )
        deb = os.path.join(self.tmp, "x.deb") if deb_package else ""
        return self.run_lib(
            "bash",
            self.SCRIPT,
            f'SWITCH_EDITION={"true" if switch else "false"}; '
            f'check_bundle_edition "{manifest_path}" "{deb}"',
            env={
                "DIAGNOSEME_CONFIG_DIR": config_dir,
                "DPKG_QUERY": dpkg_query,
                "DPKG_DEB": dpkg_deb,
            },
        )

    MAUI = {"edition": "maui-v1.1", "deb_package": "diagnoseme-maui-v1.1"}

    def test_same_edition_is_applied(self):
        proc = self.check(
            self.MAUI,
            config="DIAGNOSEME_EDITION=maui-v1.1\n",
            deb_package="diagnoseme-maui-v1.1",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_old_odm_maui_station_counts_as_maui_v11(self):
        # a8a85a8 config.env: no DIAGNOSEME_EDITION, DOCKER_TAG=maui.
        proc = self.check(
            self.MAUI,
            config="MODE=offline\nDOCKER_TAG=maui\n",
            installed=True,
            deb_package="diagnoseme-maui-v1.1",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_other_edition_is_refused(self):
        proc = self.check(self.MAUI, config="DIAGNOSEME_EDITION=servo-dolos\n")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--switch-edition", proc.stdout)

    def test_old_servo_dolos_station_refuses_a_maui_bundle(self):
        # The 'diagnoseme' package of main, without config.env.
        proc = self.check(self.MAUI, installed=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("runs servo-dolos", proc.stdout)

    def test_switch_edition(self):
        proc = self.check(
            self.MAUI,
            config="DIAGNOSEME_EDITION=servo-dolos\n",
            deb_package="diagnoseme-maui-v1.1",
            switch=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("Switching the station", proc.stdout)

    def test_fresh_station_accepts_any_edition(self):
        proc = self.check(self.MAUI, deb_package="diagnoseme-maui-v1.1")
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_wrong_deb_package_is_refused_even_with_switch(self):
        proc = self.check(
            self.MAUI,
            config="DIAGNOSEME_EDITION=maui-v1.1\n",
            deb_package="diagnoseme",
            switch=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("expected 'diagnoseme-maui-v1.1'", proc.stdout)

    def test_bundle_without_edition_needs_switch(self):
        proc = self.check({"files": {}}, config="DIAGNOSEME_EDITION=maui-v1.1\n")
        self.assertNotEqual(proc.returncode, 0)
        proc = self.check(
            {"files": {}},
            config="DIAGNOSEME_EDITION=maui-v1.1\n",
            deb_package="diagnoseme-maui-v1.1",
            switch=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_bundle_manifest_names_edition_and_package(self):
        with open(
            os.path.join(DEV_ENV, "build-offline-bundle.sh"), encoding="utf-8"
        ) as f:
            text = f.read()
        self.assertIn('"edition": os.environ["EDITION"]', text)
        self.assertIn('"deb_package": os.environ["DEB_PACKAGE"]', text)


def _tar_bytes(files, compression="xz"):
    """Returns a tar.<compression> of |files| (name -> bytes or None = dir)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=f"w:{compression}") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"./{name}")
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _write_deb(path, control, data):
    """Writes a minimal deb (ar archive) with the given control/data files."""
    members = [
        ("debian-binary", b"2.0\n"),
        ("control.tar.xz", _tar_bytes(control)),
        ("data.tar.xz", _tar_bytes(data)),
    ]
    with open(path, "wb") as f:
        f.write(b"!<arch>\n")
        for name, content in members:
            header = f"{name:<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(content):<10}`\n"
            f.write(header.encode())
            f.write(content)
            if len(content) % 2:
                f.write(b"\n")


class TestOfflineUpdateFirmware(ScriptTestCase):
    """diagnoseme-offline-update: firmware components of the bundle."""

    SCRIPT = os.path.join(DEB_BUILDER, "diagnoseme-offline-update")

    def install(self, files):
        bundle = os.path.join(self.tmp, "bundle", "pdc")
        paths = [_write(os.path.join(bundle, name), text) for name, text in files]
        target = os.path.join(self.tmp, "firmware", "pdc")
        _write(os.path.join(target, "v19_old.bin"), "old ISO image")
        args = " ".join(f'"{p}"' for p in paths)
        proc = self.run_lib(
            "bash", self.SCRIPT, f'install_firmware_files "{target}" {args}'
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return sorted(os.listdir(target))

    def test_stable_release_replaces_the_component_directory(self):
        # ODM B: firmware/pdc of the a8a85a8 ISO has images without a
        # STABLE.json; a bundle with a stable release must not leave them next
        # to the new image (several images, no marker -> flashing refused).
        self.assertEqual(
            self.install([("v20_0325_100W.bin", "new"), ("STABLE.json", "{}")]),
            ["STABLE.json", "v20_0325_100W.bin"],
        )

    def test_files_without_stable_json_are_added(self):
        self.assertEqual(self.install([("v20.bin", "new")]), ["v19_old.bin", "v20.bin"])


class TestIsoFirstBoot(ScriptTestCase):
    """test_server/iso_setup/post-install-setup.sh and its preseed wiring."""

    TEST_SERVER = os.path.join(REPO_ROOT, "test_server")
    SCRIPT = os.path.join(TEST_SERVER, "iso_setup", "post-install-setup.sh")

    def run_setup(self, mode, installed, package="diagnoseme"):
        root = os.path.join(self.tmp, "root")
        bindir = os.path.join(self.tmp, "bin")
        log = os.path.join(self.tmp, "calls.log")
        status = "install ok installed" if installed else "unknown ok not-installed"
        # curl / gpg write their -o file.
        write_o = 'while [ $# -gt 0 ]; do [ "$1" = -o ] && echo x > "$2"; shift; done'
        fakes = {
            "sleep": "",
            "docker": "",
            "systemctl": "",
            "curl": write_o,
            "gpg": write_o,
            "apt-get": "",
            "dpkg-deb": f"echo {package}",
            "dpkg-query": f"printf '%s' '{status}'",
        }
        for name, body in fakes.items():
            _write(
                os.path.join(bindir, name),
                f'#!/bin/bash\necho "{name} $*" >> "{log}"\n{body}\n',
                0o755,
            )
        _write(os.path.join(root, "etc", "diagnoseme", "config.env"), f"MODE={mode}\n")
        _write(os.path.join(root, "var/lib/diagnoseme/deb/diagnoseme_amd64.deb"), "")
        os.makedirs(os.path.join(root, "home", "diagnoseme", "Desktop"))
        script = os.path.join(self.tmp, "post-install-setup.sh")
        with open(self.SCRIPT, encoding="utf-8") as src:
            _write(script, src.read(), 0o755)
        proc = subprocess.run(
            ["bash", script],
            capture_output=True,
            text=True,
            env=dict(
                os.environ,
                DIAGNOSEME_ROOT=root,
                PATH=f"{bindir}:{os.environ['PATH']}",
            ),
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(log, encoding="utf-8") as f:
            return root, f.read(), os.path.exists(script)

    def test_maui_station_stays_offline(self):
        root, calls, script_left = self.run_setup(
            "offline", True, "diagnoseme-maui-v1.1"
        )
        self.assertNotIn("apt-get", calls)
        self.assertNotIn("curl", calls)
        self.assertFalse(os.path.exists(os.path.join(root, "etc/apt/sources.list.d")))
        self.assertIn("dpkg-query -W -f=${Status} diagnoseme-maui-v1.1", calls)
        self.assertIn("systemctl disable diagnoseme-setup.service", calls)
        self.assertFalse(script_left)

    def test_online_station_gets_the_diagnoseme_deb_apt_source(self):
        root, calls, _ = self.run_setup("online", True)
        with open(
            os.path.join(root, "etc/apt/sources.list.d/diagnoseme.list"),
            encoding="utf-8",
        ) as f:
            self.assertEqual(
                f.read(),
                "deb [signed-by=/usr/share/keyrings/artifact-registry.gpg] "
                "https://us-apt.pkg.dev/projects/chromeos-hw-tools diagnoseme-deb "
                "main\n",
            )
        self.assertTrue(
            os.path.exists(
                os.path.join(root, "usr/share/keyrings/artifact-registry.gpg")
            )
        )
        self.assertIn("apt-get install -y diagnoseme", calls)
        self.assertIn("systemctl disable", calls)

    def test_missing_package_leaves_a_visible_marker(self):
        root, calls, script_left = self.run_setup(
            "offline", False, "diagnoseme-maui-v1.1"
        )
        marker = os.path.join(root, "var/log/diagnoseme/INSTALL_FAILED")
        with open(marker, encoding="utf-8") as f:
            self.assertIn("diagnoseme-maui-v1.1 is not installed", f.read())
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    root, "home/diagnoseme/Desktop/DIAGNOSEME_INSTALL_FAILED.txt"
                )
            )
        )
        # Not disabled: the check runs again on the next boot.
        self.assertNotIn("systemctl", calls)
        self.assertTrue(script_left)

    def test_preseed_installs_the_first_boot_setup(self):
        with open(os.path.join(self.TEST_SERVER, "preseed.cfg"), encoding="utf-8") as f:
            preseed = f.read()
        self.assertIn(
            "cp /cdrom/iso_setup/post-install-setup.sh "
            "/target/usr/local/bin/post-install-setup.sh",
            preseed,
        )
        # A failed deb install is logged (and reported at first boot).
        self.assertIn("/var/log/diagnoseme/deb-install.log", preseed)
        self.assertNotIn("apt-get install -f -y || true", preseed)
        # Browser autostart only for the online editions (servo-dolos).
        self.assertIn(
            'grep -q "^MODE=online" /target/etc/diagnoseme/config.env', preseed
        )
        self.assertIn(".config/autostart", preseed)
        with open(
            os.path.join(self.TEST_SERVER, "build_iso.sh"), encoding="utf-8"
        ) as f:
            build_iso = f.read()
        self.assertIn('"$SCRIPT_DIR/iso_setup/post-install-setup.sh"', build_iso)
        self.assertIn('"$SCRIPT_DIR/iso_setup/diagnoseme-browser.desktop"', build_iso)


class TestRealtekDriver(ScriptTestCase):
    """postinst / prerm: the r8152 DKMS driver of the realtek editions."""

    POSTINST = os.path.join(DEB_BUILDER, "postinst")
    PRERM = os.path.join(DEB_BUILDER, "prerm")

    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        self.log = os.path.join(self.tmp, "calls.log")
        for tool in ("dkms", "modprobe"):
            _write(
                os.path.join(self.bin, tool),
                f'#!/bin/sh\necho "{tool} $*" >> "{self.log}"\n',
                mode=0o755,
            )
        self.src = os.path.join(self.tmp, "src", "r8152-2.21.4")
        self.dkms_dir = os.path.join(self.tmp, "dkms", "r8152", "2.21.4")
        self.env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "R8152_SRC_DIR": self.src,
            "R8152_DKMS_DIR": self.dkms_dir,
        }

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            return f.read().splitlines()

    def setup_driver(self, realtek):
        proc = self.run_lib(
            "sh",
            self.POSTINST,
            f"REALTEK_TOOLS={realtek}; setup_realtek_driver",
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_realtek_edition_builds_the_driver(self):
        _write(os.path.join(self.src, "dkms.conf"), "PACKAGE_NAME=r8152\n")
        self.setup_driver("true")
        self.assertEqual(
            self.calls(),
            [
                "dkms add -m r8152 -v 2.21.4",
                "dkms install -m r8152 -v 2.21.4",
                "modprobe -r cdc_ether",
                "modprobe -r r8152",
            ],
        )

    def test_already_registered_driver_is_not_added_again(self):
        _write(os.path.join(self.src, "dkms.conf"), "PACKAGE_NAME=r8152\n")
        os.makedirs(self.dkms_dir)
        self.setup_driver("true")
        self.assertNotIn("dkms add -m r8152 -v 2.21.4", self.calls())
        self.assertIn("dkms install -m r8152 -v 2.21.4", self.calls())

    def test_other_editions_leave_dkms_and_modules_alone(self):
        self.setup_driver("false")
        self.assertEqual(self.calls(), [])

    def test_stale_registration_of_the_old_package_is_removed(self):
        # An ODM station moving from the old 'diagnoseme' package (r8152 DKMS
        # driver) to diagnoseme-maui-v1.x: the source is gone.
        os.makedirs(self.dkms_dir)
        self.setup_driver("false")
        self.assertEqual(self.calls(), ["dkms remove -m r8152 -v 2.21.4 --all"])

    def test_failing_or_missing_dkms_never_fails(self):
        os.makedirs(self.dkms_dir)
        _write(os.path.join(self.bin, "dkms"), "#!/bin/sh\nexit 3\n", mode=0o755)
        self.assertIn("dkms remove of r8152 2.21.4 failed", self.setup_driver("false"))
        os.remove(os.path.join(self.bin, "dkms"))
        self.env["PATH"] = self.bin + ":/usr/bin/nonexistent"
        proc = self.run_lib(
            "/bin/sh",
            self.POSTINST,
            "REALTEK_TOOLS=false; setup_realtek_driver",
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def run_prerm(self, realtek, action):
        prerm = os.path.join(self.tmp, "prerm")
        with open(self.PRERM, encoding="utf-8") as f:
            _write(prerm, f.read().replace("@REALTEK_TOOLS@", realtek), mode=0o755)
        proc = subprocess.run(
            ["sh", prerm, action],
            capture_output=True,
            text=True,
            env=dict(os.environ, **self.env),
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_prerm_removes_the_driver_of_a_removed_realtek_package(self):
        self.run_prerm("true", "remove")
        self.assertEqual(self.calls(), [])  # not registered
        os.makedirs(self.dkms_dir)
        self.run_prerm("true", "upgrade")
        self.assertEqual(self.calls(), [])
        self.run_prerm("false", "remove")
        self.assertEqual(self.calls(), [])
        self.run_prerm("true", "remove")
        self.assertEqual(self.calls(), ["dkms remove -m r8152 -v 2.21.4 --all"])
        _write(os.path.join(self.bin, "dkms"), "#!/bin/sh\nexit 3\n", mode=0o755)
        self.run_prerm("true", "remove")


class TestDebVersion(unittest.TestCase):
    """tools/deb_version.sh: 1.1.<YYYYMMDD>, above the old 1.0.<seconds>."""

    TOOL = os.path.join(REPO_ROOT, "tools", "deb_version.sh")

    def version(self, date=None):
        env = dict(os.environ)
        env.pop("DEB_VERSION_DATE", None)
        if date is not None:
            env["DEB_VERSION_DATE"] = date
        return subprocess.run(
            ["bash", self.TOOL], capture_output=True, text=True, env=env, check=False
        )

    def test_date_version(self):
        self.assertEqual(self.version("20261030").stdout, "1.1.20261030\n")
        proc = self.version()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout, r"^1\.1\.20[0-9]{6}\n$")
        self.assertNotEqual(self.version("2026-10-30").returncode, 0)

    @unittest.skipUnless(shutil.which("dpkg"), "needs dpkg")
    def test_sorts_above_the_old_versions(self):
        new = self.version("20261030").stdout.strip()
        for old in ("1.0.1792746882", "1.0.12", "1.0.0", "1.0.99991231"):
            proc = subprocess.run(
                ["dpkg", "--compare-versions", new, "gt", old], check=False
            )
            self.assertEqual(proc.returncode, 0, f"{new} gt {old}")
        later = self.version("20261031").stdout.strip()
        self.assertEqual(
            subprocess.run(
                ["dpkg", "--compare-versions", later, "gt", new], check=False
            ).returncode,
            0,
        )

    def test_every_deb_build_uses_it(self):
        for parts in (
            ("dockerfiles", "cloudbuild_release.yaml"),
            ("dockerfiles", "cloudbuild_cq.yaml"),
            ("development_environment", "deb_builder", "cloudbuild.yaml"),
            ("development_environment", "build-offline-bundle.sh"),
        ):
            with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as f:
                content = f.read()
            self.assertIn("deb_version.sh", content, parts)
            self.assertIn("DEB_VERSION=", content, parts)
            self.assertNotIn("date +%s", content, parts)


# Realtek tools of the servo-dolos deb (realtek_tools).
RTK_FILES = {
    "usr/local/bin/rtunicpg": b"",
    "usr/local/server/binfiles/EE8153BvB.cfg": b"",
    "usr/local/server/binfiles/EF8153BvB.cfg": b"",
    "usr/src/r8152-2.21.4/dkms.conf": b"",
}


class TestCheckDeb(ScriptTestCase):
    """tools/check_deb.py."""

    def setUp(self):
        super().setUp()
        self.editions = check_deb.edition_info.load_editions()

    def control(self, package, others, version="1.1.20261030", realtek=None):
        """Control files of the package; realtek defaults to its edition's."""
        if realtek is None:
            realtek = any(
                meta.get("realtek_tools")
                for meta in self.editions.values()
                if meta["deb_package"] == package
            )
        conflicts = ", ".join(["diagnoseme-station", *others])
        depends = "docker.io, docker-compose, "
        if realtek:
            depends += "dkms, build-essential, linux-headers-amd64, "
        depends += "libcurl4, python3"
        flag = f'REALTEK_TOOLS="{"true" if realtek else "false"}"\n'.encode()
        return {
            "control": (
                f"Package: {package}\nVersion: {version}\nConflicts: {conflicts}\n"
                f"Replaces: {conflicts}\nDepends: {depends}\n"
            ).encode(),
            "preinst": b"#!/bin/sh\n",
            "postinst": b'#!/bin/sh\nDIAGNOSEME_EDITION="x"\n' + flag,
            "prerm": b"#!/bin/sh\n" + flag,
        }

    def others(self, edition):
        return check_deb._package_list(  # pylint: disable=protected-access
            check_deb.edition_info.get_field(self.editions, edition, "deb_conflicts")
        )

    def servo_dolos_data(self, **extra):
        data = {
            "etc/diagnoseme/config.env": (
                b"DIAGNOSEME_EDITION=servo-dolos\nMODE=online\nDOCKER_TAG=release\n"
                b"PULL_POLICY=always\nAUTO_OPEN_BROWSER=false\n"
                b"UI_URL=http://localhost:8888/dolos-rework\nSTATION_ID=\n"
            ),
            "etc/diagnoseme/docker-compose.yaml": b"services: {}\n",
            "usr/share/applications/diagnoseme-web.desktop": b"",
        }
        data.update(RTK_FILES)
        data.update(extra)
        return data

    def run_check(self, edition, control, data, allow=False):
        deb = os.path.join(self.tmp, "x.deb")
        _write_deb(deb, control, data)
        return check_deb.check_deb(deb, edition, self.editions, allow)

    def test_valid_servo_dolos_deb(self):
        problems = self.run_check(
            "servo-dolos",
            self.control("diagnoseme", self.others("servo-dolos")),
            self.servo_dolos_data(),
        )
        self.assertEqual(problems, [])

    def test_servo_dolos_deb_with_maui_parts_is_rejected(self):
        data = self.servo_dolos_data(
            **{
                "etc/diagnoseme/docker-compose-maui.yaml": b"",
                "etc/diagnoseme/config.local.env": b"STATION_ID=X\n",
            }
        )
        data["etc/diagnoseme/config.env"] += b"MAUI_DEVICE=maui_v11\nMODE=offline\n"
        problems = self.run_check(
            "servo-dolos", self.control("diagnoseme-maui-v1.1", []), data
        )
        text = "\n".join(problems)
        for expected in (
            "Package is 'diagnoseme-maui-v1.1'",
            "Conflicts lacks",
            "config.local.env must never be shipped",
            "MODE='offline'",
            "has MAUI_DEVICE",
            "ships etc/diagnoseme/docker-compose-maui.yaml",
        ):
            self.assertIn(expected, text)

    def maui_data(self, tar=b"utils", md5=None, edition="maui-v1.1"):
        config = check_deb.edition_info.station_config(self.editions, edition, "1.0")
        config["MODE"] = "offline"
        data = {
            "etc/diagnoseme/config.env": "".join(
                f"{k}={shlex.quote(v)}\n" for k, v in config.items()
            ).encode(),
            "etc/diagnoseme/docker-compose-maui.yaml": b"",
            "usr/share/applications/start-diagnoseme.desktop": b"",
        }
        if self.editions[edition].get("h2h_firmware"):
            data["etc/diagnoseme/docker-compose-maui-h2h.yaml"] = b""
        if tar is not None:
            data["var/lib/diagnoseme/maui-utils/maui-utils.tar"] = tar
            data["var/lib/diagnoseme/maui-utils/STABLE.json"] = json.dumps(
                {"version": "1.0", "md5": md5 or hashlib.md5(tar).hexdigest()}
            ).encode()
        return data

    def test_valid_maui_deb(self):
        control = self.control("diagnoseme-maui-v1.1", self.others("maui-v1.1"))
        self.assertEqual(self.run_check("maui-v1.1", control, self.maui_data()), [])

    def test_valid_maui_v12_deb_and_the_h2h_override(self):
        control = self.control("diagnoseme-maui-v1.2", self.others("maui-v1.2"))
        data = self.maui_data(edition="maui-v1.2")
        config = data["etc/diagnoseme/config.env"].decode()
        self.assertIn("MAUI_H2H_FIRMWARE=true", config)
        self.assertIn("docker-compose-maui-h2h.yaml", config)
        self.assertEqual(self.run_check("maui-v1.2", control, data), [])
        del data["etc/diagnoseme/docker-compose-maui-h2h.yaml"]
        self.assertIn(
            "docker-compose-maui-h2h.yaml must be shipped",
            "\n".join(self.run_check("maui-v1.2", control, data)),
        )
        # Maui v1.1 has no H2H firmware flashing.
        control = self.control("diagnoseme-maui-v1.1", self.others("maui-v1.1"))
        data = self.maui_data()
        self.assertIn(
            "MAUI_H2H_FIRMWARE=false", data["etc/diagnoseme/config.env"].decode()
        )
        data["etc/diagnoseme/docker-compose-maui-h2h.yaml"] = b""
        self.assertIn(
            "docker-compose-maui-h2h.yaml must be shipped",
            "\n".join(self.run_check("maui-v1.1", control, data)),
        )

    def test_maui_deb_without_maui_utils(self):
        control = self.control("diagnoseme-maui-v1.1", self.others("maui-v1.1"))
        problems = self.run_check("maui-v1.1", control, self.maui_data(tar=None))
        self.assertIn("maui-utils.tar / STABLE.json is missing", "\n".join(problems))
        self.assertEqual(
            self.run_check("maui-v1.1", control, self.maui_data(tar=None), allow=True),
            [],
        )

    def test_maui_deb_with_damaged_maui_utils(self):
        control = self.control("diagnoseme-maui-v1.1", self.others("maui-v1.1"))
        problems = self.run_check("maui-v1.1", control, self.maui_data(md5="0" * 32))
        self.assertIn("does not match its STABLE.json md5", "\n".join(problems))

    def test_realtek_tools_only_in_realtek_editions(self):
        # servo-dolos (realtek_tools) must ship rtunicpg, the EEPROM files and
        # the r8152 DKMS driver and depend on dkms.
        data = self.servo_dolos_data()
        for name in RTK_FILES:
            del data[name]
        problems = "\n".join(
            self.run_check(
                "servo-dolos",
                self.control("diagnoseme", self.others("servo-dolos"), realtek=False),
                data,
            )
        )
        for expected in (
            "usr/local/bin/rtunicpg is missing",
            "usr/src/r8152-2.21.4/dkms.conf is missing",
            "usr/local/server/binfiles/EE8153B* is missing",
            "Depends lacks dkms",
            'postinst lacks REALTEK_TOOLS="true"',
        ):
            self.assertIn(expected, problems)
        # A Maui deb must not ship or depend on them.
        control = self.control(
            "diagnoseme-maui-v1.1", self.others("maui-v1.1"), realtek=True
        )
        data = self.maui_data()
        data.update(RTK_FILES)
        problems = "\n".join(self.run_check("maui-v1.1", control, data))
        for expected in (
            "Depends has dkms",
            "Depends has linux-headers-amd64",
            "an edition without Realtek tools ships usr/local/bin/rtunicpg",
            'prerm lacks REALTEK_TOOLS="false"',
        ):
            self.assertIn(expected, problems)

    def test_deb_version(self):
        control = self.control(
            "diagnoseme-maui-v1.1", self.others("maui-v1.1"), version="1.0.1792746882"
        )
        self.assertIn(
            "expected 1.1.<YYYYMMDD>",
            "\n".join(self.run_check("maui-v1.1", control, self.maui_data())),
        )
        del control["prerm"]
        self.assertIn(
            "DEBIAN/prerm is missing",
            "\n".join(self.run_check("maui-v1.1", control, self.maui_data())),
        )

    def test_command_line(self):
        deb = os.path.join(self.tmp, "x.deb")
        _write_deb(
            deb,
            self.control("diagnoseme", self.others("servo-dolos")),
            self.servo_dolos_data(),
        )
        self.assertEqual(check_deb.main([deb, "servo-dolos"]), 0)
        self.assertEqual(check_deb.main([deb, "maui-v1.1"]), 1)
        self.assertEqual(check_deb.main([deb, "bogus"]), 2)


if __name__ == "__main__":
    unittest.main()
