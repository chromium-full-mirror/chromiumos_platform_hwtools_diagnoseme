# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for genesys_hub_programmer.py."""

from pathlib import Path
import subprocess
import unittest
from unittest import mock

from server import genesys_hub_programmer


class TestGenesysHubProgrammer(unittest.TestCase):
    """Tests for GenesysHubProgrammer."""

    def setUp(self):
        self.programmer = genesys_hub_programmer.GenesysHubProgrammer()

    @mock.patch("shutil.which")
    @mock.patch("server.util.run_command")
    def test_verify_success(self, mock_run, mock_which):
        """Test verify returns True on successful version match."""
        mock_which.return_value = "/usr/bin/fwupdtool"
        mock_run.return_value = mock.Mock(stdout="Current version: 64.18", returncode=0)

        self.assertTrue(self.programmer.verify())
        mock_run.assert_called_once_with(
            self.programmer.READ_CMD,
            cwd="/usr/bin",
        )

    @mock.patch("shutil.which")
    @mock.patch("server.util.run_command")
    def test_verify_mismatch(self, mock_run, mock_which):
        """Test verify returns False on version mismatch."""
        mock_which.return_value = "/usr/bin/fwupdtool"
        mock_run.return_value = mock.Mock(stdout="Current version: 64.17", returncode=0)

        self.assertFalse(self.programmer.verify())

    @mock.patch("shutil.which")
    @mock.patch("server.util.run_command")
    def test_verify_no_match(self, mock_run, mock_which):
        """Test verify returns False if version not found in output."""
        mock_which.return_value = "/usr/bin/fwupdtool"
        mock_run.return_value = mock.Mock(stdout="Some other output", returncode=0)

        self.assertFalse(self.programmer.verify())

    @mock.patch("shutil.which")
    def test_verify_binary_not_found(self, mock_which):
        """Test verify raises error if programmer binary is missing."""
        mock_which.return_value = None
        with self.assertRaises(genesys_hub_programmer.GenesysHubProgrammerError):
            self.programmer.verify()

    @mock.patch("shutil.which")
    @mock.patch("server.util.run_command")
    def test_verify_subprocess_error(self, mock_run, mock_which):
        """Test verify raises error if subprocess fails."""
        mock_which.return_value = "/usr/bin/fwupdtool"
        mock_run.side_effect = subprocess.CalledProcessError(1, "cmd", stderr="error")
        with self.assertRaises(genesys_hub_programmer.GenesysHubProgrammerError):
            self.programmer.verify()

    @mock.patch("shutil.which")
    @mock.patch("server.genesys_hub_programmer.GenesysHubProgrammer.verify")
    def test_program_already_verified(self, mock_verify, mock_which):
        """Test program does nothing if already verified."""
        mock_which.return_value = "/usr/bin/fwupdtool"
        mock_verify.return_value = True
        self.assertTrue(self.programmer.program())
        mock_verify.assert_called_once()

    @mock.patch("shutil.which")
    @mock.patch("os.path.exists")
    @mock.patch("server.util.find_binfile")
    @mock.patch("server.util.run_command")
    @mock.patch("server.genesys_hub_programmer.GenesysHubProgrammer.verify")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_program_success(
        self, mock_verify, mock_run, mock_find, mock_exists, mock_which
    ):
        """Test successful programming."""
        mock_verify.side_effect = [False, True]
        mock_which.return_value = "/usr/bin/fwupdtool"
        mock_find.return_value = Path("/path/to/fw.cab")
        mock_exists.return_value = True
        mock_run.return_value = mock.Mock(stdout="Success", returncode=0)

        self.assertTrue(self.programmer.program())

        # Verify write command was called
        expected_cmd = list(self.programmer.WRITE_CMD) + ["/path/to/fw.cab"]
        mock_run.assert_called_once_with(
            expected_cmd,
            cwd="/usr/bin",
        )

    @mock.patch("shutil.which")
    @mock.patch("os.path.exists")
    @mock.patch("server.util.find_binfile")
    @mock.patch("server.util.run_command")
    @mock.patch("server.genesys_hub_programmer.GenesysHubProgrammer.verify")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_program_force(
        self, mock_verify, mock_run, mock_find, mock_exists, mock_which
    ):
        """Test programming with force=True."""
        self.programmer.force = True
        mock_verify.return_value = False
        mock_which.return_value = "/usr/bin/fwupdtool"
        mock_find.return_value = Path("/path/to/fw.cab")
        mock_exists.return_value = True
        mock_run.return_value = mock.Mock(stdout="Success", returncode=0)

        self.assertTrue(self.programmer.program())

        # Verify force flag was added
        expected_cmd = list(self.programmer.WRITE_CMD) + ["--force", "/path/to/fw.cab"]
        mock_run.assert_called_once_with(
            expected_cmd,
            cwd="/usr/bin",
        )

    @mock.patch("shutil.which")
    @mock.patch("os.path.exists")
    @mock.patch("server.util.find_binfile")
    @mock.patch("server.util.run_command")
    @mock.patch("server.genesys_hub_programmer.GenesysHubProgrammer.verify")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_program_fail_code_1(
        self, mock_verify, mock_run, mock_find, mock_exists, mock_which
    ):
        """Test program returns False if write fails with code 1."""
        mock_verify.return_value = False
        mock_which.return_value = "/usr/bin/fwupdtool"
        mock_find.return_value = Path("/path/to/fw.cab")
        mock_exists.return_value = True
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "cmd", stderr="already at version"
        )

        self.assertFalse(self.programmer.program())

    @mock.patch("shutil.which")
    @mock.patch("os.path.exists")
    @mock.patch("server.util.find_binfile")
    @mock.patch("server.util.run_command")
    @mock.patch("server.genesys_hub_programmer.GenesysHubProgrammer.verify")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_program_fail_other_code(
        self, mock_verify, mock_run, mock_find, mock_exists, mock_which
    ):
        """Test program raises error if write fails with other code."""
        mock_verify.return_value = False
        mock_which.return_value = "/usr/bin/fwupdtool"
        mock_find.return_value = Path("/path/to/fw.cab")
        mock_exists.return_value = True
        mock_run.side_effect = subprocess.CalledProcessError(
            2, "cmd", stderr="fatal error"
        )

        with self.assertRaises(genesys_hub_programmer.GenesysHubProgrammerError):
            self.programmer.program()

    @mock.patch("shutil.which")
    @mock.patch("os.path.exists")
    @mock.patch("server.util.find_binfile")
    @mock.patch("server.genesys_hub_programmer.GenesysHubProgrammer.verify")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_program_fw_not_found(
        self, mock_verify, mock_find, mock_exists, mock_which
    ):
        """Test program raises error if firmware file is missing."""
        mock_verify.return_value = False
        mock_which.return_value = "/usr/bin/fwupdtool"
        mock_find.return_value = None
        mock_exists.return_value = False

        with self.assertRaises(genesys_hub_programmer.GenesysHubProgrammerError):
            self.programmer.program()


if __name__ == "__main__":
    unittest.main()
