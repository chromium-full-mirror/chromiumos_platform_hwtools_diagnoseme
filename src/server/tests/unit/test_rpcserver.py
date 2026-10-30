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

    def test_maui_services_required_on_maui_editions_only(self):
        """Only Maui editions other than the dev edition 'all' need Maui."""
        for edition in rpcserver.editions.EDITIONS.values():
            has_maui = any(s in rpcserver.MAUI_SERVICES for s in edition.services)
            self.assertEqual(
                rpcserver.maui_services_required(edition),
                has_maui and edition.edition_id != "all",
                edition.edition_id,
            )
        self.assertTrue(
            rpcserver.maui_services_required(rpcserver.editions.EDITIONS["maui-v1.1"])
        )
        self.assertFalse(
            rpcserver.maui_services_required(rpcserver.editions.EDITIONS["all"])
        )

    def _register_with_failing_maui(self, services, maui_required):
        with mock.patch.object(
            rpcserver, "register_maui_services", return_value=False
        ), mock.patch.dict(
            rpcserver.SERVICE_REGISTRARS,
            {key: mock.Mock() for key in rpcserver.SERVICE_REGISTRARS},
        ):
            with rpcserver.contextlib.ExitStack() as stack:
                rpcserver.register_services(
                    mock.MagicMock(), services, stack, maui_required=maui_required
                )

    def test_maui_failure_stops_a_maui_edition(self):
        """S3: a Maui import failure stops startup on a Maui edition."""
        with self.assertRaisesRegex(
            rpcserver.MauiServicesUnavailableError, "Not starting the server"
        ), self.assertLogs(level="CRITICAL"):
            self._register_with_failing_maui(
                [
                    rpcserver.editions.SERVICE_DIAGNOSEME,
                    rpcserver.editions.SERVICE_MAUI_STATION1,
                ],
                maui_required=True,
            )

    def test_maui_failure_is_optional_without_required(self):
        """The dev edition keeps running without the Maui services."""
        with self.assertLogs(level="WARNING") as logs:
            self._register_with_failing_maui(
                [
                    rpcserver.editions.SERVICE_DIAGNOSEME,
                    rpcserver.editions.SERVICE_MAUI_STATION1,
                ],
                maui_required=False,
            )
        self.assertIn("Continuing without the Maui", "\n".join(logs.output))
        # No Maui service requested: nothing to fail.
        self._register_with_failing_maui(
            [rpcserver.editions.SERVICE_DIAGNOSEME], maui_required=True
        )

    def test_serve_stops_when_maui_fails_on_a_maui_edition(self):
        """serve() propagates the error for a Maui edition (no half-working UI)."""
        with mock.patch.dict(
            "os.environ", {"DIAGNOSEME_EDITION": "maui-v1.1"}
        ), mock.patch.object(rpcserver, "setup_logging"), mock.patch.object(
            rpcserver, "parse_arguments"
        ), mock.patch.object(
            rpcserver, "register_maui_services", return_value=False
        ), mock.patch.object(
            rpcserver.grpc.aio, "server"
        ) as mock_server:
            mock_server.return_value.stop = mock.AsyncMock()
            with self.assertRaises(rpcserver.MauiServicesUnavailableError):
                rpcserver.asyncio.run(rpcserver.serve())
            mock_server.return_value.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
