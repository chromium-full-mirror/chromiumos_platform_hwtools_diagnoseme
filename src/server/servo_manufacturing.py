#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Servo Manufacturing service."""
# pylint: disable=no-member
import logging
import re

from google.protobuf import empty_pb2
import grpc

# pylint: disable=no-name-in-module
from server import atmega_kb_programmer
from server import genesys_hub_programmer
from server import rtk_eth_programmer
from server import serial_programmer
from server import servo_manufacturing_pb2
from server import servo_manufacturing_pb2_grpc
from server import servo_programmer
from server import util
from server import v4p1_tester
from server.servo_console import ServoConsole
from server.servo_console import ServoConsoleError


class ServoV41ManufacturingServicer(
    servo_manufacturing_pb2_grpc.ServoV41ManufacturingServicer
):
    """Servo Manufacturing gRPC service."""

    BOARD = "servo_v4p1"

    # The VID/PID of the stm chip before programming/in DFU mode
    STM_DFU_VID = 0x0483
    STM_DFU_PID = 0xDF11

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
        logging.info("Programming Genesys Hub...")
        try:
            programmer = genesys_hub_programmer.GenesysHubProgrammer(force=False)
            programmer.program()
            return servo_manufacturing_pb2.ProgramGenesysHubResponse(
                success=True, message="Genesys Hub programmed successfully"
            )
        except genesys_hub_programmer.GenesysHubProgrammerError as e:
            logging.exception("Failed to program Genesys Hub")
            logs = util.get_logs_from_exception(e)
            return servo_manufacturing_pb2.ProgramGenesysHubResponse(
                success=False, message=str(e), logs=logs
            )

    def program_mcu(
        self,
        request: servo_manufacturing_pb2.ProgramMcuRequest,
        context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramMcuResponse:
        """Program the MCU."""
        del context  # Unused
        logging.info("Programming MCU with %s...", request.firmware_path)
        try:
            dfu_programmer = servo_programmer.ServoProgrammer(
                self.BOARD, dfu_vid=self.STM_DFU_VID, dfu_pid=self.STM_DFU_PID
            )
            dfu_programmer.program()

            return servo_manufacturing_pb2.ProgramMcuResponse(
                success=True,
                message=f"MCU programmed successfully with {request.firmware_path}",
            )
        except servo_programmer.ServoProgrammerError as e:
            logging.exception("Failed to program MCU")
            logs = util.get_logs_from_exception(e)
            return servo_manufacturing_pb2.ProgramMcuResponse(
                success=False, message=str(e), logs=logs
            )

    def program_serial(
        self,
        request: servo_manufacturing_pb2.ProgramSerialRequest,
        context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramSerialResponse:
        """Program the serial number."""
        del context  # Unused
        logging.info("Programming serial number: %s...", request.serial_number)
        try:
            programmer = serial_programmer.SerialProgrammer(request.serial_number)
            programmer.program()
            return servo_manufacturing_pb2.ProgramSerialResponse(
                success=True, message="Serial number programmed successfully"
            )
        except serial_programmer.SerialProgrammerError as e:
            logging.exception("Failed to program serial number")
            logs = util.get_logs_from_exception(e)
            return servo_manufacturing_pb2.ProgramSerialResponse(
                success=False, message=str(e), logs=logs
            )

    def program_ethernet(
        self,
        request: servo_manufacturing_pb2.ProgramEthernetRequest,
        context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramEthernetResponse:
        """Program the Ethernet MAC address."""
        del context  # Unused
        logging.info(
            "Programming Ethernet with MAC %s for serial %s...",
            request.mac_address,
            request.serial_number,
        )

        serial_path = util.discover_servo_v4p1_serial_path(request.serial_number)

        if not serial_path:
            # Final fallback to constructed path
            serial_path = (
                f"/dev/serial/by-id/usb-Google_LLC_Servo_V4p1_{request.serial_number}"
                "-if00-port0"
            )
            logging.warning("Discovery failed, using fallback path: %s", serial_path)

        servo_mcu_connector = ServoConsole(serial_path)

        try:
            programmer = rtk_eth_programmer.RTKEthProgrammer(force=False)
            programmer.program(request.mac_address, servo_mcu_connector)

            return servo_manufacturing_pb2.ProgramEthernetResponse(
                success=True,
                message=(
                    f"Ethernet MAC address {request.mac_address} "
                    "programmed successfully"
                ),
            )
        except (
            rtk_eth_programmer.RTKEthProgrammerError,
            ServoConsoleError,
        ) as e:
            logging.exception("Failed to program Ethernet")
            logs = util.get_logs_from_exception(e)
            return servo_manufacturing_pb2.ProgramEthernetResponse(
                success=False, message=str(e), logs=logs
            )

    def program_atmega(
        self,
        request: servo_manufacturing_pb2.ProgramAtmegaRequest,
        context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramAtmegaResponse:
        """Program the Atmega Keyboard Emulator."""
        del context  # Unused
        logging.info(
            "Programming Atmega for serial %s...",
            request.serial_number,
        )

        serial_path = util.discover_servo_v4p1_serial_path(request.serial_number)

        if not serial_path:
            return servo_manufacturing_pb2.ProgramAtmegaResponse(
                success=False,
                message="Serial console not found for Atmega programming",
            )

        with ServoConsole(serial_path) as console:
            try:
                kb_programmer = atmega_kb_programmer.AtmegaKBEmulatorProgrammer(
                    console=console, i2caddr=0x21, i2coffset=2
                )
                if kb_programmer.program():
                    return servo_manufacturing_pb2.ProgramAtmegaResponse(
                        success=True, message="Atmega programmed successfully"
                    )
                return servo_manufacturing_pb2.ProgramAtmegaResponse(
                    success=False, message="Failed to program Atmega Keyboard Emulator"
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                logging.exception("Failed to program Atmega")
                logs = util.get_logs_from_exception(e)
                return servo_manufacturing_pb2.ProgramAtmegaResponse(
                    success=False, message=str(e), logs=logs
                )

    async def run_initial_tests(
        self,
        request: servo_manufacturing_pb2.RunTestsRequest,
        context: grpc.ServicerContext,
    ) -> (
        servo_manufacturing_pb2.RunTestsResponse
    ):  # pylint: disable=invalid-overridden-method
        """Run tests that only require console and lsusb."""
        del context  # Unused
        logging.info(
            "Running initial manufacturing tests for serial %s...",
            request.serial_number,
        )

        serial_path = util.discover_servo_v4p1_serial_path(request.serial_number)

        if not serial_path:
            return servo_manufacturing_pb2.RunTestsResponse(
                all_passed=False,
                summary="Error: Serial console not found for initial testing",
            )

        tester = v4p1_tester.V4P1Tester(
            serial_number=request.serial_number, mac_address=request.mac_address
        )
        test_results = tester.run_initial_tests(serial_path)

        all_passed = all(result for _, result in test_results)
        summary_lines = [
            f"{name}: {'PASS' if passed else 'FAIL'}" for name, passed in test_results
        ]

        return servo_manufacturing_pb2.RunTestsResponse(
            all_passed=all_passed, summary="\n".join(summary_lines)
        )

    async def run_servod_tests(
        self,
        request: servo_manufacturing_pb2.RunTestsRequest,
        context: grpc.ServicerContext,
    ) -> (
        servo_manufacturing_pb2.RunTestsResponse
    ):  # pylint: disable=invalid-overridden-method
        """Run tests that require servod."""
        del context  # Unused
        logging.info(
            "Running servod-based manufacturing tests for serial %s...",
            request.serial_number,
        )

        tester = v4p1_tester.V4P1Tester(
            serial_number=request.serial_number, mac_address=request.mac_address
        )
        test_results = await tester.run_servod_tests()

        all_passed = all(result for _, result in test_results)
        summary_lines = [
            f"{name}: {'PASS' if passed else 'FAIL'}" for name, passed in test_results
        ]

        return servo_manufacturing_pb2.RunTestsResponse(
            all_passed=all_passed, summary="\n".join(summary_lines)
        )

    def validate_servo_serial(
        self,
        request: servo_manufacturing_pb2.ValidateServoSerialRequest,
        context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ValidationResponse:
        """Validate the Servo Serial number."""
        del context  # Unused
        logging.info("Validating Servo Serial: %s...", request.serial_number)
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
        logging.info("Validating Servo MAC Address: %s...", request.mac_address)
        is_valid = bool(self.MACADDR_RE.match(request.mac_address))
        error_message = "" if is_valid else "Invalid MAC address format"
        return servo_manufacturing_pb2.ValidationResponse(
            is_valid=is_valid, error=error_message
        )
