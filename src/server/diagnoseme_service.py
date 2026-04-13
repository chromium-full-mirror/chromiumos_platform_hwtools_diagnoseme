# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Diagnose Me service."""

import collections
import logging
import os

import grpc

from server.generated import diagnoseme_pb2
from server.generated import diagnoseme_pb2_grpc


class DiagnoseMeServicer(diagnoseme_pb2_grpc.DiagnoseMeServiceServicer):
    """Diagnose Me Servicer class."""

    # pylint: disable=too-few-public-methods

    def get_logs(
        self,
        request: diagnoseme_pb2.GetLogsRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> diagnoseme_pb2.GetLogsResponse:  # pylint: disable=no-member
        """Get the last N lines of the rpcserver.log."""
        # pylint: disable=no-member
        line_count = request.line_count if request.line_count > 0 else 500
        log_file = "/var/log/rpcserver/rpcserver.log"
        try:
            if not os.path.exists(log_file):
                return diagnoseme_pb2.GetLogsResponse(log_content="Log file not found")
            with open(log_file, "r", encoding="utf-8") as f:
                content = "".join(collections.deque(f, maxlen=line_count))
                return diagnoseme_pb2.GetLogsResponse(log_content=content)
        except OSError as e:
            logging.exception("Failed to read logs")
            return diagnoseme_pb2.GetLogsResponse(
                log_content=f"Error reading logs: {str(e)}"
            )
