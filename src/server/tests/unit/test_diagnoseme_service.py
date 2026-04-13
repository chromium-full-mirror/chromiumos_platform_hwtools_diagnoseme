# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for diagnoseme_service.py."""

import unittest
from unittest import mock

from server import diagnoseme_service
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


if __name__ == "__main__":
    unittest.main()
