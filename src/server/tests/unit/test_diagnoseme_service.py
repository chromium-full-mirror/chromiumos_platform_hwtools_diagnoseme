# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for diagnoseme_service.py."""

import os
import unittest
from unittest import mock

from server import diagnoseme_service
from server import editions
from server.generated import diagnoseme_pb2


class TestDiagnoseMeServicer(unittest.TestCase):
    """Tests for DiagnoseMeServicer."""

    def setUp(self):
        self.servicer = diagnoseme_service.DiagnoseMeServicer()
        self.context = mock.MagicMock()

    @mock.patch("os.path.exists")
    def test_get_logs_file_not_found(self, mock_exists):
        """Test get_logs when log file is missing."""
        mock_exists.return_value = False
        request = diagnoseme_pb2.GetLogsRequest(  # pylint: disable=no-member
            line_count=10
        )
        response = self.servicer.get_logs(request, self.context)
        self.assertEqual(response.log_content, "Log file not found")

    @mock.patch("os.path.exists")
    @mock.patch(
        "builtins.open", new_callable=mock.mock_open, read_data="line1\nline2\nline3\n"
    )
    def test_get_logs_success(self, mock_file, mock_exists):
        """Test get_logs success with specific line count."""
        mock_exists.return_value = True
        request = diagnoseme_pb2.GetLogsRequest(  # pylint: disable=no-member
            line_count=2
        )
        response = self.servicer.get_logs(request, self.context)
        # deque(maxlen=2) of [line1, line2, line3] should give [line2, line3]
        self.assertEqual(response.log_content, "line2\nline3\n")
        mock_file.assert_called_once_with(
            "/var/log/rpcserver/rpcserver.log", "r", encoding="utf-8"
        )

    def _station_config(self, env):
        """Calls get_station_config with only |env| in the environment."""
        request = diagnoseme_pb2.GetStationConfigRequest()  # pylint: disable=no-member
        with mock.patch.dict(os.environ, env, clear=True):
            return self.servicer.get_station_config(request, self.context)

    def test_get_station_config(self):
        """The station config describes the edition of the server image."""
        config = self._station_config(
            {"DIAGNOSEME_EDITION": "maui-v1.1", "STATION_ID": "MAUI_V1_1_STATION"}
        )
        edition = editions.EDITIONS["maui-v1.1"]
        self.assertEqual(config.edition, "maui-v1.1")
        self.assertEqual(config.title, edition.title)
        self.assertEqual(list(config.services), list(edition.services))
        self.assertEqual(config.maui_hw_rev, "v1.1")
        self.assertEqual(config.station_id, "MAUI_V1_1_STATION")

    def test_get_station_config_defaults(self):
        """Without DIAGNOSEME_EDITION / STATION_ID: dev edition, no station."""
        config = self._station_config({})
        self.assertEqual(config.edition, editions.DEFAULT_EDITION)
        self.assertEqual(config.maui_hw_rev, editions.EDITIONS["all"].maui_hw_rev)
        self.assertEqual(config.station_id, "")

    def test_get_station_config_non_maui_edition(self):
        """Non-Maui editions report no Maui hardware revision."""
        config = self._station_config({"DIAGNOSEME_EDITION": "dolos"})
        self.assertEqual(config.edition, "dolos")
        self.assertEqual(config.maui_hw_rev, "")
        self.assertNotIn(editions.SERVICE_MAUI_PDC, config.services)

    def test_get_station_config_unknown_edition(self):
        """An unknown edition is an error (the server refuses to start too)."""
        with self.assertRaises(ValueError):
            self._station_config({"DIAGNOSEME_EDITION": "bogus"})


if __name__ == "__main__":
    unittest.main()
