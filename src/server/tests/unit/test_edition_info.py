# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for tools/edition_info.py (editions.json queries for shell scripts)."""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


# pylint: disable=missing-function-docstring


REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
TOOL = os.path.join(REPO_ROOT, "tools", "edition_info.py")
CONFIG_TEMPLATE = os.path.join(
    REPO_ROOT, "development_environment", "deb_builder", "config.env"
)
# config.env template of the editions without Maui (deb builder).
GENERIC_CONFIG_TEMPLATE = os.path.join(
    REPO_ROOT, "development_environment", "deb_builder", "config-generic.env"
)

_spec = importlib.util.spec_from_file_location("edition_info", TOOL)
edition_info = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(edition_info)

REGISTRY = {
    "editions": {
        "servo-dolos": {
            "title": "Servo / Dolos",
            "landing": "/dolos-rework",
            "shipped": True,
            "maui_utils": False,
            "realtek_tools": True,
            "firmware": [],
            "station_id": "",
            "docker_tag_aliases": ["release"],
            "station_tag": "release",
            "deb_package": "diagnoseme",
            "iso_latest": "custom-debian-preseed-latest.iso",
        },
        "dolos": {
            "title": "Dolos",
            "landing": "/dolos-rework",
            "shipped": False,
            "maui_utils": False,
            "firmware": [],
            "station_id": "DOLOS_STATION",
            "deb_package": "diagnoseme-dolos",
        },
        "maui-v1.1": {
            "title": "Maui v1.1",
            "landing": "/maui/v1.1/provisioning",
            "shipped": True,
            "maui_utils": True,
            "maui_device": "maui_v11",
            "maui_hw_rev": "v1.1",
            "firmware": ["mcu", "pdc"],
            "results_dir": "/var/log/diagnoseme/maui_v11_results",
            "station_id": "MAUI_V1_1_STATION",
            "deb_package": "diagnoseme-maui-v1.1",
        },
        "all": {
            "title": "All (development)",
            "landing": "/dolos-rework",
            "shipped": False,
            "maui_utils": True,
            "maui_hw_rev": "v1.1",
            "firmware": ["mcu", "pdc"],
            "results_dir": "/var/log/diagnoseme/maui_v11_results",
            "station_id": "DEV_STATION",
            "deb_package": "diagnoseme-dev",
        },
    }
}


def _config_keys(path):
    """Returns the KEY of every (uncommented) KEY=value line of |path|."""
    with open(path, encoding="utf-8") as f:
        return {
            line.split("=", 1)[0]
            for line in f.read().splitlines()
            if "=" in line and not line.startswith("#")
        }


def _source(path, *names):
    """Returns the values of |names| after `source path` in bash."""
    script = f'source "{path}"; ' + "; ".join(
        f'printf "%s\\0" "${{{name}}}"' for name in names
    )
    out = subprocess.run(
        ["bash", "-c", script], check=True, capture_output=True, text=True
    ).stdout
    return out.split("\0")[: len(names)]


class EditionInfoTestCase(unittest.TestCase):
    """Runs edition_info.main() against a temporary registry."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.registry = os.path.join(self.tmp, "editions.json")
        with open(self.registry, "w", encoding="utf-8") as f:
            json.dump(REGISTRY, f)

    def run_tool(self, *argv):
        """Runs main(); returns (exit status, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = edition_info.main(["--registry", self.registry, *argv])
        return status, out.getvalue(), err.getvalue()


class TestQueries(EditionInfoTestCase):
    """Tests for list / get / validate."""

    def test_list(self):
        self.assertEqual(
            self.run_tool("list")[1].split(),
            ["servo-dolos", "dolos", "maui-v1.1", "all"],
        )
        self.assertEqual(
            self.run_tool("list", "--shipped")[1].split(), ["servo-dolos", "maui-v1.1"]
        )

    def test_get_formats_values_for_shell(self):
        self.assertEqual(self.run_tool("get", "maui-v1.1", "firmware")[1], "mcu pdc\n")
        self.assertEqual(self.run_tool("get", "maui-v1.1", "maui_utils")[1], "true\n")
        self.assertEqual(self.run_tool("get", "dolos", "maui_utils")[1], "false\n")
        self.assertEqual(
            self.run_tool("get", "maui-v1.1", "docker_tags")[1], "maui-v1.1\n"
        )
        self.assertEqual(
            self.run_tool("get", "servo-dolos", "docker_tags")[1],
            "servo-dolos release\n",
        )
        self.assertEqual(
            self.run_tool("get", "servo-dolos", "docker_aliases")[1], "release\n"
        )
        self.assertEqual(self.run_tool("get", "maui-v1.1", "docker_aliases")[1], "\n")
        self.assertEqual(self.run_tool("get", "dolos", "docker_tags")[1], "dolos\n")
        # servo-dolos stations keep following :release; the others the id.
        self.assertEqual(
            self.run_tool("get", "servo-dolos", "station_tag")[1], "release\n"
        )
        self.assertEqual(
            self.run_tool("get", "maui-v1.1", "station_tag")[1], "maui-v1.1\n"
        )
        self.assertEqual(self.run_tool("get", "dolos", "firmware")[1], "\n")
        # Servo v4p1 tooling (rtunicpg, r8152 DKMS driver); false when unset.
        self.assertEqual(
            self.run_tool("get", "servo-dolos", "realtek_tools")[1], "true\n"
        )
        self.assertEqual(
            self.run_tool("get", "maui-v1.1", "realtek_tools")[1], "false\n"
        )
        self.assertEqual(self.run_tool("get", "dolos", "no_such_field")[1], "\n")

    def test_iso_latest(self):
        # Only servo-dolos owns the historical custom-debian-preseed-latest.iso.
        self.assertEqual(
            self.run_tool("get", "servo-dolos", "iso_latest")[1],
            "custom-debian-preseed-latest.iso\n",
        )
        self.assertEqual(
            self.run_tool("get", "maui-v1.1", "iso_latest")[1],
            "custom-debian-preseed-maui-v1.1-latest.iso\n",
        )

    def test_deb_conflicts_lists_the_other_editions_packages(self):
        # maui-v1.1 conflicts with / replaces the Servo / Dolos 'diagnoseme'
        # (and the old Maui 'diagnoseme' of the ODM stations).
        self.assertEqual(
            self.run_tool("get", "maui-v1.1", "deb_conflicts")[1],
            "diagnoseme, diagnoseme-dev, diagnoseme-dolos\n",
        )
        self.assertEqual(
            self.run_tool("get", "servo-dolos", "deb_conflicts")[1],
            "diagnoseme-dev, diagnoseme-dolos, diagnoseme-maui-v1.1\n",
        )

    def test_validate(self):
        self.assertEqual(self.run_tool("validate", "maui-v1.1")[0], 0)
        status, _, err = self.run_tool("validate", "bogus")
        self.assertEqual(status, 1)
        self.assertIn("Unknown edition 'bogus'", err)
        self.assertEqual(self.run_tool("get", "bogus", "title")[0], 1)
        self.assertEqual(self.run_tool("validate", "")[0], 1)

    def test_validate_shipped(self):
        self.assertEqual(self.run_tool("validate", "--shipped", "servo-dolos")[0], 0)
        self.assertEqual(self.run_tool("validate", "--shipped", "maui-v1.1")[0], 0)
        status, _, err = self.run_tool("validate", "--shipped", "all")
        self.assertEqual(status, 1)
        self.assertIn("not shipped", err)
        self.assertEqual(self.run_tool("validate", "--shipped", "dolos")[0], 1)
        self.assertEqual(self.run_tool("validate", "all")[0], 0)

    def test_real_registry_is_valid(self):
        editions = edition_info.load_editions()
        self.assertIn("maui-v1.1", editions)
        for edition_id in editions:
            config = edition_info.station_config(editions, edition_id)
            self.assertTrue(config["UI_URL"].startswith("http://localhost:8888/"))

    def test_command_line(self):
        proc = subprocess.run(
            [sys.executable, TOOL, "--registry", self.registry, "get", "all", "title"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.stdout, "All (development)\n")


class TestRenderConfig(EditionInfoTestCase):
    """Tests for render-config (station config.env specialisation)."""

    def render(self, edition, *extra):
        """Renders a copy of the config.env template; returns its path."""
        path = os.path.join(self.tmp, f"{edition}.env")
        shutil.copy(CONFIG_TEMPLATE, path)
        status, _, _ = self.run_tool("render-config", edition, path, *extra)
        self.assertEqual(status, 0)
        return path

    def test_maui_edition(self):
        path = self.render("maui-v1.1", "--bundled-maui-utils", "0.0.1-69-89b053b")
        self.assertEqual(
            _source(
                path,
                "DIAGNOSEME_EDITION",
                "DOCKER_TAG",
                "UI_URL",
                "STATION_ID",
                "FIRMWARE_COMPONENTS",
                "MAUI_UTILS_IMAGE",
                "MODE",
                "MAUI_DEVICE",
                "DIAGNOSEME_RESULTS_DIR",
            ),
            [
                "maui-v1.1",
                "maui-v1.1",
                "http://localhost:8888/maui/v1.1/provisioning",
                "MAUI_V1_1_STATION",
                "mcu pdc",
                "maui-utils:0.0.1-69-89b053b",
                "offline",
                "maui_v11",
                "/var/log/diagnoseme/maui_v11_results",
            ],
        )

    def test_maui_edition_selects_the_maui_compose_override(self):
        path = self.render("maui-v1.1")
        self.assertEqual(
            _source(path, "COMPOSE_FILE"),
            [
                "/etc/diagnoseme/docker-compose.yaml:"
                "/etc/diagnoseme/docker-compose-maui.yaml"
            ],
        )

    def test_maui_template_documents_the_shared_adb_defaults(self):
        # One set of ADB defaults for every Maui edition: 40 s, 5 Gbps.
        with open(CONFIG_TEMPLATE, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("#MAUI_ADB_DEADLINE_S=40\n", text)
        self.assertIn("#MAUI_ADB_MIN_LINK_MBPS=5000\n", text)
        self.assertIn("config.local.env", text)

    def test_non_maui_station_config_has_no_maui_keys(self):
        editions = edition_info.load_editions(self.registry)
        for edition_id in ("servo-dolos", "dolos"):
            config = edition_info.station_config(editions, edition_id, "1.0")
            self.assertEqual(
                set(config),
                {"DIAGNOSEME_EDITION", "DOCKER_TAG", "UI_URL", "STATION_ID"},
            )

    def test_servo_dolos_gets_the_generic_online_config(self):
        path = os.path.join(self.tmp, "servo-dolos.env")
        shutil.copy(GENERIC_CONFIG_TEMPLATE, path)
        status, _, _ = self.run_tool(
            "render-config", "servo-dolos", path, "--bundled-maui-utils", "1.0"
        )
        self.assertEqual(status, 0)
        self.assertEqual(
            _source(
                path,
                "DIAGNOSEME_EDITION",
                "DOCKER_TAG",
                "UI_URL",
                "STATION_ID",
                "MODE",
                "PULL_POLICY",
                "AUTO_OPEN_BROWSER",
            ),
            [
                "servo-dolos",
                "release",
                "http://localhost:8888/dolos-rework",
                "",
                "online",
                "always",
                "false",
            ],
        )
        # Online defaults only: no Maui keys, no offline settings and no
        # RESULTS_SERVER (a value from the environment keeps working).
        self.assertEqual(
            _config_keys(path),
            {
                "DIAGNOSEME_EDITION",
                "MODE",
                "DOCKER_TAG",
                "PULL_POLICY",
                "AUTO_OPEN_BROWSER",
                "UI_URL",
                "STATION_ID",
            },
        )

    def test_results_dir_of_maui_editions(self):
        editions = edition_info.load_editions()
        for edition_id, meta in editions.items():
            config = edition_info.station_config(editions, edition_id)
            if meta.get("maui_utils"):
                self.assertTrue(
                    config["DIAGNOSEME_RESULTS_DIR"].startswith("/var/log/diagnoseme/"),
                    edition_id,
                )
            else:
                self.assertNotIn("DIAGNOSEME_RESULTS_DIR", config, edition_id)

    def test_empty_values_are_not_appended(self):
        text = edition_info.render_config(
            "MODE=online\nFIRMWARE_COMPONENTS='mcu pdc'\n",
            {"FIRMWARE_COMPONENTS": "", "MAUI_DEVICE": "", "STATION_ID": "S"},
        )
        self.assertEqual(text, "MODE=online\nFIRMWARE_COMPONENTS=\nSTATION_ID=S\n")

    def test_without_bundled_maui_utils(self):
        path = self.render("all")
        self.assertEqual(_source(path, "MAUI_UTILS_IMAGE"), [""])

    def test_only_specialised_lines_change(self):
        path = self.render("maui-v1.1")
        with open(CONFIG_TEMPLATE, encoding="utf-8") as f:
            template = f.read().splitlines()
        with open(path, encoding="utf-8") as f:
            rendered = f.read().splitlines()
        self.assertEqual(len(rendered), len(template))
        changed = {t.split("=", 1)[0] for t, r in zip(template, rendered) if t != r}
        editions = edition_info.load_editions(self.registry)
        self.assertLessEqual(
            changed, set(edition_info.station_config(editions, "maui-v1.1"))
        )

    def test_missing_keys_are_appended(self):
        text = edition_info.render_config(
            "# comment\nMODE=offline\n", {"STATION_ID": "S 1", "UI_URL": "u"}
        )
        self.assertEqual(text, "# comment\nMODE=offline\nSTATION_ID='S 1'\nUI_URL=u\n")

    def test_comments_are_not_replaced(self):
        text = edition_info.render_config(
            "# STATION_ID=old\nSTATION_ID=old\n", {"STATION_ID": "new"}
        )
        self.assertEqual(text, "# STATION_ID=old\nSTATION_ID=new\n")


if __name__ == "__main__":
    unittest.main()
