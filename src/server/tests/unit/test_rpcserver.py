# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for rpcserver.py."""

import logging
import unittest
from unittest import mock

# pylint: disable=no-member
# pylint: disable=no-name-in-module
# pylint: disable=import-error
from server import rpcserver


class TestRpcServer(unittest.TestCase):
    """Tests for rpcserver.py."""

    @mock.patch("logging.FileHandler")
    @mock.patch("logging.getLogger")
    def test_setup_logging(self, mock_get_logger, mock_file_handler):
        """Test setup_logging logic."""
        mock_logger = mock_get_logger.return_value
        rpcserver.setup_logging(logging.DEBUG)

        mock_file_handler.assert_called_with(
            "/var/log/rpcserver/rpcserver.log", mode="w"
        )
        # Check that addHandler was called on the logger returned by getLogger()
        mock_logger.addHandler.assert_called()
        mock_logger.setLevel.assert_called_with(logging.DEBUG)

    def test_parse_arguments(self):
        """Test argument parsing."""
        args = rpcserver.parse_arguments(["--verbose"])
        self.assertTrue(args.verbose)

        args = rpcserver.parse_arguments([])
        self.assertFalse(args.verbose)

    def test_register_maui_services(self):
        """All Maui services are registered when the modules import cleanly."""
        server = mock.MagicMock()
        self.assertTrue(rpcserver.register_maui_services(server))
        self.assertGreaterEqual(
            server.add_generic_rpc_handlers.call_count, len(rpcserver.MAUI_SERVICES)
        )

    def test_register_maui_services_failure_is_contained(self):
        """A Maui import failure is logged and registers nothing."""
        server = mock.MagicMock()
        with mock.patch(
            "server.rpcserver.importlib.import_module",
            side_effect=ImportError("no serial"),
        ), self.assertLogs(level="ERROR"):
            self.assertFalse(rpcserver.register_maui_services(server))
        server.add_generic_rpc_handlers.assert_not_called()


if __name__ == "__main__":
    unittest.main()
