#!/usr/bin/env python3
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Servo Manufacturing service."""

import re

from google.protobuf import empty_pb2
import grpc

# pylint: disable=no-name-in-module
from server import servo_manufacturing_pb2
from server import servo_manufacturing_pb2_grpc


class ServoV41ManufacturingServicer(
    servo_manufacturing_pb2_grpc.ServoV41ManufacturingServicer
):
    """Servo Manufacturing gRPC service."""

    RE = (
        r"^(SERVOV4P1-)?"  # device type
        r"[CGSA](-)?"  # supplier
        r"[0-9]{2}"  # YY
        r"(0[1-9]|1[0-2])"  # MM
        r"(0[1-9]|[12][0-9]|3[0-1])"  # DD
        r"[0-9]{4,5}$"  # serialno suffix
    )
    # The serial number is either the standard RE or one of the legacy serial
    # numbers.
    SERIALNO_RE = re.compile(RE)

    # Regex to validate mac address input.
    # Note: the mac address regex lives here since it's universal. The regex for
    # serial numbers lives inside each manager, as they change per device.
    MACADDR_RE = re.compile("^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$")

    def get_status(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> servo_manufacturing_pb2.StatusResponse:
        """Get the status of the manufacturing service."""
        del request, context  # Unused
        return servo_manufacturing_pb2.StatusResponse(status="OK")

    def program_genesys_hub(
        self,
        request: servo_manufacturing_pb2.ProgramGenesysHubRequest,
        context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramGenesysHubResponse:
        """Program the Genesys Hub."""
        del request, context  # Unused
        print("Programming Genesys Hub...")
        return servo_manufacturing_pb2.ProgramGenesysHubResponse(
            success=True, message="Genesys Hub programmed successfully"
        )

    def program_mcu(
        self,
        request: servo_manufacturing_pb2.ProgramMcuRequest,
        context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramMcuResponse:
        """Program the MCU."""
        del context  # Unused
        print(f"Programming MCU with {request.firmware_path}...")
        return servo_manufacturing_pb2.ProgramMcuResponse(
            success=True,
            message=f"MCU programmed successfully with {request.firmware_path}",
        )

    def program_ethernet(
        self,
        request: servo_manufacturing_pb2.ProgramEthernetRequest,
        context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramEthernetResponse:
        """Program the Ethernet MAC address."""
        del context  # Unused
        print(f"Programming Ethernet with MAC {request.mac_address}...")
        return servo_manufacturing_pb2.ProgramEthernetResponse(
            success=True,
            message=(
                f"Ethernet MAC address {request.mac_address} programmed successfully"
            ),
        )

    def run_tests(
        self,
        request: servo_manufacturing_pb2.RunTestsRequest,
        context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.RunTestsResponse:
        """Run the specified tests."""
        del context  # Unused
        test_list = ", ".join(request.tests_to_run)
        print(f"Running tests: {test_list}...")
        return servo_manufacturing_pb2.RunTestsResponse(
            all_passed=True, summary=f"Tests {test_list} passed"
        )

    def validate_servo_serial(
        self,
        request: servo_manufacturing_pb2.ValidateServoSerialRequest,
        context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ValidationResponse:
        """Validate the Servo Serial number."""
        del context  # Unused
        print(f"Validating Servo Serial: {request.serial_number}...")
        is_valid = bool(self.SERIALNO_RE.match(request.serial_number))
        error_message = "" if is_valid else "Invalid serial number format"
        return servo_manufacturing_pb2.ValidationResponse(
            is_valid=is_valid, error=error_message
        )

    def validate_servo_mac_address(
        self,
        request: servo_manufacturing_pb2.ValidateServoMacAddressRequest,
        context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ValidationResponse:
        """Validate the Servo MAC address."""
        del context  # Unused
        print(f"Validating Servo MAC Address: {request.mac_address}...")
        is_valid = bool(self.MACADDR_RE.match(request.mac_address))
        error_message = "" if is_valid else "Invalid MAC address format"
        return servo_manufacturing_pb2.ValidationResponse(
            is_valid=is_valid, error=error_message
        )
