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


REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
TOOL = os.path.join(REPO_ROOT, "tools", "edition_info.py")
CONFIG_TEMPLATE = os.path.join(
    REPO_ROOT, "development_environment", "deb_builder", "config.env"
)

_spec = importlib.util.spec_from_file_location("edition_info", TOOL)
edition_info = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(edition_info)

REGISTRY = {
    "editions": {
        "dolos": {
            "title": "Dolos",
            "landing": "/dolos-rework",
            "shipped": True,
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
            "firmware": ["mcu", "pdc"],
            "station_id": "MAUI_V1_1_STATION",
            "docker_tag_aliases": ["maui"],
            "deb_package": "diagnoseme",
        },
        "all": {
            "title": "All (development)",
            "landing": "/maui/v1.1/provisioning",
            "shipped": False,
            "maui_utils": True,
            "firmware": ["mcu", "pdc"],
            "station_id": "DEV_STATION",
            "deb_package": "diagnoseme-dev",
        },
    }
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
            self.run_tool("list")[1].split(), ["dolos", "maui-v1.1", "all"]
        )
        self.assertEqual(
            self.run_tool("list", "--shipped")[1].split(), ["dolos", "maui-v1.1"]
        )

    def test_get_formats_values_for_shell(self):
        self.assertEqual(self.run_tool("get", "maui-v1.1", "firmware")[1], "mcu pdc\n")
        self.assertEqual(self.run_tool("get", "maui-v1.1", "maui_utils")[1], "true\n")
        self.assertEqual(self.run_tool("get", "dolos", "maui_utils")[1], "false\n")
        self.assertEqual(
            self.run_tool("get", "maui-v1.1", "docker_tags")[1], "maui-v1.1 maui\n"
        )
        self.assertEqual(self.run_tool("get", "dolos", "docker_tags")[1], "dolos\n")
        self.assertEqual(self.run_tool("get", "dolos", "firmware")[1], "\n")
        self.assertEqual(self.run_tool("get", "dolos", "no_such_field")[1], "\n")

    def test_deb_conflicts_lists_the_other_editions_packages(self):
        self.assertEqual(
            self.run_tool("get", "maui-v1.1", "deb_conflicts")[1],
            "diagnoseme-dev, diagnoseme-dolos\n",
        )
        self.assertEqual(
            self.run_tool("get", "dolos", "deb_conflicts")[1],
            "diagnoseme, diagnoseme-dev\n",
        )

    def test_validate(self):
        self.assertEqual(self.run_tool("validate", "maui-v1.1")[0], 0)
        status, _, err = self.run_tool("validate", "bogus")
        self.assertEqual(status, 1)
        self.assertIn("Unknown edition 'bogus'", err)
        self.assertEqual(self.run_tool("get", "bogus", "title")[0], 1)

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
            ],
        )

    def test_non_maui_edition_keeps_maui_utils_unset(self):
        path = self.render("dolos", "--bundled-maui-utils", "1.0")
        self.assertEqual(
            _source(path, "DOCKER_TAG", "FIRMWARE_COMPONENTS", "MAUI_UTILS_IMAGE"),
            ["dolos", "", ""],
        )
        with open(path, encoding="utf-8") as f:
            self.assertIn("\nFIRMWARE_COMPONENTS=\n", f.read())

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
        self.assertLessEqual(changed, {"DOCKER_TAG"})

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
