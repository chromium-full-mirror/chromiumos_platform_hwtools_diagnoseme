# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for servod.py."""

import unittest
from unittest import mock

# pylint: disable=no-member
# pylint: disable=no-name-in-module
# pylint: disable=import-error
from server import servod  # noqa: E402
from server.generated import diagnoseme_servod_pb2  # noqa: E402


class TestServodRpcService(unittest.TestCase):
    """Tests for ServodRpcService."""

    def setUp(self):
        self.servicer = servod.ServodRpcService()
        self.context = mock.MagicMock()

    @mock.patch("docker.from_env")
    def test_start_servod_success(self, mock_docker_env):
        """Test start_servod success."""
        mock_client = mock_docker_env.return_value
        mock_container = mock.MagicMock()
        mock_container.exec_run.return_value = (0, b"log output")
        mock_container.logs.return_value = b"servod logs"
        mock_client.containers.run.return_value = mock_container

        # Mock _get_image to avoid network calls
        with mock.patch.object(
            self.servicer, "_get_image", return_value="servod:latest"
        ):
            request = diagnoseme_servod_pb2.StartServodRequest(board="eve")
            response = self.servicer.start_servod(request, self.context)

        self.assertTrue(response.started)
        mock_client.containers.run.assert_called()

    @mock.patch("docker.from_env")
    def test_start_servod_recovery_noboard(self, mock_docker_env):
        """Test start_servod with recovery and noboard parameters."""
        mock_client = mock_docker_env.return_value
        mock_container = mock.MagicMock()
        mock_container.exec_run.return_value = (0, b"log output")
        mock_container.logs.return_value = b"servod logs"
        mock_client.containers.run.return_value = mock_container

        # Mock _get_image to avoid network calls
        with mock.patch.object(
            self.servicer, "_get_image", return_value="servod:latest"
        ):
            request = diagnoseme_servod_pb2.StartServodRequest(
                serial="12345", recovery=True, noboard=True
            )
            response = self.servicer.start_servod(request, self.context)

        self.assertTrue(response.started)
        mock_client.containers.run.assert_called()
        command_arg = mock_client.containers.run.call_args.kwargs["command"]
        self.assertIn("--recovery", command_arg[2])
        self.assertIn("--noboard", command_arg[2])
        self.assertNotIn("--board", command_arg[2])

    def test_run_dut_control_success(self):
        """Test run_dut_control success."""
        mock_container = mock.MagicMock()
        mock_container.exec_run.return_value = (0, b"control: value")
        self.servicer.cont = mock_container

        request = diagnoseme_servod_pb2.RunDutControlRequest(command="control")
        response = self.servicer.run_dut_control(request, self.context)

        self.assertEqual(response.error_code, 0)
        self.assertEqual(response.result, " value")


if __name__ == "__main__":
    unittest.main()
