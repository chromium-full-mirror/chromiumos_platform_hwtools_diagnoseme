# Copyright 2025 The Chromium OS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""RPC service providing functionality to provision and test Dolos devices."""

import logging

# pylint: disable=no-name-in-module
from server import diagnoseme_servod_pb2
from server import diagnoseme_servod_pb2_grpc


logging.getLogger().setLevel(logging.DEBUG)


class ServodRpcService(diagnoseme_servod_pb2_grpc.ServodRpcServiceServicer):
    """Provides RPC methods for interacting with Servo devices.

    This service implements the functions necessary to manage and access
    methods of a servod service.
    """

    def start_servod(self, unused_request, unused_context):
        """Start servod and return if successful or not."""
        response = diagnoseme_servod_pb2.StartServodResponse()
        return response

    def get_dut_hardware_id(self, unused_request, unused_context):
        """Call servod to get the information from the EC about the HWID."""
        response = diagnoseme_servod_pb2.GetHardwareIDResponse()
        response.hwid = "FAKE HWID"
        return response
