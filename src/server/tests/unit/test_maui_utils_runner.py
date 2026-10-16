# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for maui_utils_runner."""

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from server import maui_utils_runner


class TestMauiUtilsRunner(unittest.TestCase):
    """Tests for MauiUtilsRunner and MauiUtilsSession."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runner = maui_utils_runner.MauiUtilsRunner(
            image="maui-utils:1.2.3",
            staging_root=os.path.join(self.tmp.name, "stage"),
            docker_bin="/usr/bin/docker",
        )
        # Session exit removes the container; never call the real docker.
        patcher = mock.patch("subprocess.run")
        self.mock_run = patcher.start()
        self.addCleanup(patcher.stop)

    def test_image_from_env(self):
        with mock.patch.dict(os.environ, {"MAUI_UTILS_IMAGE": "maui-utils:9"}):
            self.assertEqual(maui_utils_runner.get_image(), "maui-utils:9")

    def test_image_defaults_to_the_newest_local_image(self):
        self.mock_run.return_value = mock.Mock(
            stdout=(
                "2026-08-18 12:04:06 +0000 UTC\tmaui-utils:0.0.1-57-59acb1f\n"
                "2026-10-15 09:00:00 +0000 UTC\tmaui-utils:0.0.1-69-89b053b\n"
                "2026-10-16 09:00:00 +0000 UTC\tmaui-utils:<none>\n"
            )
        )
        # An empty MAUI_UTILS_IMAGE (server image built without a version)
        # counts as unset.
        with mock.patch.dict(os.environ, {"MAUI_UTILS_IMAGE": ""}):
            self.assertEqual(
                maui_utils_runner.get_image(), "maui-utils:0.0.1-69-89b053b"
            )
        self.assertEqual(self.mock_run.call_args[0][0][1:3], ["images", "maui-utils"])
        self.mock_run.return_value = mock.Mock(stdout="")
        with mock.patch.dict(os.environ, {"MAUI_UTILS_IMAGE": ""}):
            self.assertEqual(
                maui_utils_runner.get_image(), maui_utils_runner.DEFAULT_IMAGE
            )
        self.mock_run.side_effect = OSError("no docker")
        self.assertEqual(maui_utils_runner.newest_local_image(), "")

    def test_command_layout(self):
        with self.runner.session() as session:
            cmd = session.command(
                maui_utils_runner.TOOL_FW_UPDATER,
                ["/mnt/firmware_dir/zephyr.txt", "--uart", "/dev/ttyUSB0"],
            )
            staging_dir = session.staging_dir
            name = session.container_name
        self.assertEqual(cmd[:3], ["/usr/bin/docker", "run", "--rm"])
        self.assertIn("--privileged", cmd)
        self.assertIn("PYTHONUNBUFFERED=1", cmd)
        self.assertEqual(cmd[cmd.index("--pull") + 1], "never")
        self.assertIn("/dev:/dev", cmd)
        self.assertIn("/run/udev:/run/udev:ro", cmd)
        self.assertIn(f"{staging_dir}:/mnt/firmware_dir", cmd)
        self.assertEqual(cmd[cmd.index("--name") + 1], name)
        idx = cmd.index("--entrypoint")
        self.assertEqual(
            cmd[idx + 1 :],
            [
                "fw-updater",
                "maui-utils:1.2.3",
                "/mnt/firmware_dir/zephyr.txt",
                "--uart",
                "/dev/ttyUSB0",
            ],
        )

    def test_unknown_tool_rejected(self):
        with self.runner.session() as session:
            with self.assertRaises(maui_utils_runner.MauiUtilsError):
                session.command("bash", ["-c", "true"])

    def test_stage_copies_file_and_cleans_up(self):
        fw = os.path.join(self.tmp.name, "pdc.bin")
        with open(fw, "wb") as f:
            f.write(b"\x01\x02")
        with self.runner.session() as session:
            container_path = session.stage(fw)
            staged = os.path.join(session.staging_dir, "pdc.bin")
            self.assertEqual(container_path, "/mnt/firmware_dir/pdc.bin")
            with open(staged, "rb") as f:
                self.assertEqual(f.read(), b"\x01\x02")
            staging_dir = session.staging_dir
        self.assertFalse(os.path.exists(staging_dir))

    def test_stage_missing_file(self):
        with self.runner.session() as session:
            with self.assertRaises(maui_utils_runner.MauiUtilsError):
                session.stage(os.path.join(self.tmp.name, "missing.bin"))

    def test_every_command_gets_a_fresh_container_name(self):
        with self.runner.session() as session:
            first = session.command(maui_utils_runner.TOOL_MAUI_FLASH, ["--help"])
            second = session.command(maui_utils_runner.TOOL_MAUI_FLASH, ["--help"])
        name1 = first[first.index("--name") + 1]
        name2 = second[second.index("--name") + 1]
        self.assertNotEqual(name1, name2)
        self.assertEqual(session.container_name, name2)

    def test_kill_removes_container(self):
        with self.runner.session() as session:
            session.command(maui_utils_runner.TOOL_MAUI_FLASH, ["--help"])
            session.kill()
            name = session.container_name
        self.assertEqual(
            self.mock_run.call_args_list[0][0][0],
            ["/usr/bin/docker", "rm", "-f", name],
        )

    def test_session_exit_removes_started_container(self):
        with self.assertRaises(RuntimeError):
            with self.runner.session() as session:
                session.command(maui_utils_runner.TOOL_MAUI_FLASH, ["--help"])
                raise RuntimeError("cancelled")
        self.assertEqual(
            self.mock_run.call_args[0][0],
            ["/usr/bin/docker", "rm", "-f", session.container_name],
        )

    def test_kill_without_command_is_noop(self):
        with self.runner.session() as session:
            session.kill()
        self.mock_run.assert_not_called()

    def test_explain_failure(self):
        out = (
            "docker: Error response from daemon: No such image: "
            "maui-utils:0.0.1-57-59acb1f."
        )
        hint = maui_utils_runner.explain_failure(out, "maui-utils:0.0.1-57-59acb1f")
        self.assertIn("not loaded on this station", hint)
        self.assertIn(maui_utils_runner.BUNDLED_TAR, hint)
        self.assertEqual(maui_utils_runner.explain_failure("Buffer empty"), "")


if __name__ == "__main__":
    unittest.main()
