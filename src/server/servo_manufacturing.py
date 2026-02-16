#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Servo Manufacturing service."""
import logging
import os
import re
import time

from google.protobuf import empty_pb2
import grpc

# pylint: enable=no-name-in-module,import-error
# pylint: disable=no-name-in-module,import-error
from server import atmega_kb_programmer
from server import diagnoseme_servod_pb2
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

    def __init__(self, servod_service=None):
        self._servod_service = servod_service

    def get_status(
        self, unused_request: empty_pb2.Empty, unused_context: grpc.ServicerContext
    ) -> servo_manufacturing_pb2.StatusResponse:  # pylint: disable=no-member
        """Get the status of the manufacturing service."""
        # pylint: disable=no-member
        return servo_manufacturing_pb2.StatusResponse(status="OK")

    def get_device_presence(
        self,
        unused_request: empty_pb2.Empty,
        unused_context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.DevicePresenceResponse:  # pylint: disable=no-member
        """Check for connected devices."""
        # pylint: disable=no-member
        return servo_manufacturing_pb2.DevicePresenceResponse(
            mcu_dfu_detected=util.is_usb_device_present(0x0483, [0xDF11]),
            servo_v4p1_detected=util.is_usb_device_present(0x18D1, [0x520D]),
            realtek_eth_detected=util.is_usb_device_present(0x0BDA, [0x8153]),
        )

    def program_genesys_hub(
        self,
        unused_request: servo_manufacturing_pb2.ProgramGenesysHubRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramGenesysHubResponse:  # pylint: disable=no-member
        """Program the Genesys Hub."""
        # pylint: disable=no-member
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
        request: servo_manufacturing_pb2.ProgramMcuRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramMcuResponse:  # pylint: disable=no-member
        """Program the MCU."""
        # pylint: disable=no-member
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
        request: servo_manufacturing_pb2.ProgramSerialRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramSerialResponse:  # pylint: disable=no-member
        """Program the serial number."""
        # pylint: disable=no-member
        logging.info("Programming serial number: %s...", request.serial_number)

        logging.info("Waiting for Servo V4.1 to enumerate...")
        if not util.wait_for_usb_devices([(0x18D1, [0x520D])], timeout=30.0):
            return servo_manufacturing_pb2.ProgramSerialResponse(
                success=False, message="Timeout waiting for Servo device"
            )
        # Give a small grace period for tty to enumerate
        time.sleep(2.0)

        try:
            programmer = serial_programmer.SerialProgrammer(request.serial_number)
            programmer.program()

            logging.info("Forcing re-enumeration of Servo V4.1 Hub...")
            if not util.reset_servo_v4p1_hub():
                logging.warning("Failed to force hub re-enumeration.")

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
        request: servo_manufacturing_pb2.ProgramEthernetRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramEthernetResponse:  # pylint: disable=no-member
        """Program the Ethernet MAC address."""
        # pylint: disable=no-member
        logging.info(
            "Programming Ethernet with MAC %s for serial %s...",
            request.mac_address,
            request.serial_number,
        )

        # Wait for Realtek (0bda:8153) and Atmega (03eb:2ff4 or 03eb:2042)
        # We check both LUFA and DFU for Atmega.
        logging.info("Waiting for DUT side USB devices to be enumerated...")
        found = util.wait_for_usb_devices(
            [
                (0x0BDA, [0x8153]),
                (0x03EB, [0x2FF4, 0x2042]),
            ],
            timeout=30.0,
        )

        if not found:
            return servo_manufacturing_pb2.ProgramEthernetResponse(  # pylint: disable=no-member
                success=False,
                message="Error: Realtek or Atmega USB device not found after 30s",
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

            return servo_manufacturing_pb2.ProgramEthernetResponse(  # pylint: disable=no-member
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
            return servo_manufacturing_pb2.ProgramEthernetResponse(  # pylint: disable=no-member
                success=False, message=str(e), logs=logs
            )

    def program_atmega(
        self,
        request: servo_manufacturing_pb2.ProgramAtmegaRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramAtmegaResponse:  # pylint: disable=no-member
        """Program the Atmega Keyboard Emulator."""
        logging.info(
            "Programming Atmega for serial %s...",
            request.serial_number,
        )

        serial_path = util.discover_servo_v4p1_serial_path(request.serial_number)

        if not serial_path:
            return servo_manufacturing_pb2.ProgramAtmegaResponse(  # pylint: disable=no-member
                success=False,
                message="Serial console not found for Atmega programming",
            )

        with ServoConsole(serial_path) as console:
            try:
                kb_programmer = atmega_kb_programmer.AtmegaKBEmulatorProgrammer(
                    console=console, i2caddr=0x21, i2coffset=2
                )
                if kb_programmer.program():
                    logging.info("Forcing re-enumeration of Servo V4.1 Host Hub...")
                    if not util.reset_servo_v4p1_hub():
                        logging.warning("Failed to reset Host Hub.")

                    time.sleep(5.0)

                    logging.info("Forcing re-enumeration of Servo V4.1 DUT Hub...")
                    if not util.reset_servo_v4p1_dut_hub():
                        logging.warning("Failed to reset DUT Hub.")

                    return servo_manufacturing_pb2.ProgramAtmegaResponse(  # pylint: disable=no-member
                        success=True, message="Atmega programmed successfully"
                    )
                return servo_manufacturing_pb2.ProgramAtmegaResponse(  # pylint: disable=no-member
                    success=False, message="Failed to program Atmega Keyboard Emulator"
                )
            except (
                ServoConsoleError,
                atmega_kb_programmer.AtmegaKBEmulatorProgrammerError,
            ) as e:
                logging.exception("Failed to program Atmega")
                logs = util.get_logs_from_exception(e)
                return servo_manufacturing_pb2.ProgramAtmegaResponse(  # pylint: disable=no-member
                    success=False, message=str(e), logs=logs
                )

    async def run_console_tests(
        self,
        request: servo_manufacturing_pb2.RunTestsRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> (
        servo_manufacturing_pb2.RunTestsResponse  # pylint: disable=no-member
    ):  # pylint: disable=invalid-overridden-method
        """Phase 1: Direct serial console tests (No servod)."""
        logging.info(
            "Running direct console tests for serial %s...",
            request.serial_number,
        )

        serial_path = util.discover_servo_v4p1_serial_path(request.serial_number)

        if not serial_path:
            return (
                servo_manufacturing_pb2.RunTestsResponse(  # pylint: disable=no-member
                    all_passed=False,
                    summary="Error: Serial console not found for direct testing",
                )
            )

        tester = v4p1_tester.V4P1Tester(
            serial_number=request.serial_number, mac_address=request.mac_address
        )
        test_results = tester.run_console_tests(serial_path)

        all_passed = all(result for _, result in test_results)
        summary_lines = [
            f"{name}: {'PASS' if passed else 'FAIL'}" for name, passed in test_results
        ]

        return servo_manufacturing_pb2.RunTestsResponse(  # pylint: disable=no-member
            all_passed=all_passed, summary="\n".join(summary_lines)
        )

    async def run_functional_tests(
        self,
        request: servo_manufacturing_pb2.RunTestsRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> (
        servo_manufacturing_pb2.RunTestsResponse  # pylint: disable=no-member
    ):  # pylint: disable=invalid-overridden-method
        """Phase 2: Tests that require servod in recovery mode."""
        logging.info(
            "Running functional tests (servod recovery) for serial %s...",
            request.serial_number,
        )

        if self._servod_service:
            # Start servod in recovery mode
            start_request = (
                diagnoseme_servod_pb2.StartServodRequest(  # pylint: disable=no-member
                    board=self.BOARD, serial=request.serial_number, recovery=True
                )
            )
            start_resp = self._servod_service.start_servod(start_request, None)
            if not start_resp.started:
                return servo_manufacturing_pb2.RunTestsResponse(  # pylint: disable=no-member
                    all_passed=False,
                    summary=(
                        "Error: Servod failed to start in recovery mode:\n"
                        f"{start_resp.console_output}"
                    ),
                )

        tester = v4p1_tester.V4P1Tester(
            serial_number=request.serial_number, mac_address=request.mac_address
        )
        try:
            test_results = await tester.run_functional_tests()
        finally:
            if self._servod_service:
                self._servod_service.stop_servod(None, None)

        all_passed = all(result for _, result in test_results)
        summary_lines = [
            f"{name}: {'PASS' if passed else 'FAIL'}" for name, passed in test_results
        ]

        return servo_manufacturing_pb2.RunTestsResponse(  # pylint: disable=no-member
            all_passed=all_passed, summary="\n".join(summary_lines)
        )

    async def run_integration_tests(
        self,
        request: servo_manufacturing_pb2.RunTestsRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> (
        servo_manufacturing_pb2.RunTestsResponse  # pylint: disable=no-member
    ):  # pylint: disable=invalid-overridden-method
        """Phase 3: Tests that require servod with a connected DUT."""
        logging.info(
            "Running integration tests (servod with DUT) for serial %s...",
            request.serial_number,
        )

        board = os.environ.get("TEST_DUT", self.BOARD)
        model = os.environ.get("TEST_MODEL")
        if self._servod_service:
            # Start servod normally (needs DUT)
            start_request = (
                diagnoseme_servod_pb2.StartServodRequest(  # pylint: disable=no-member
                    board=board,
                    model=model,
                    serial=request.serial_number,
                    recovery=False,
                )
            )
            start_resp = self._servod_service.start_servod(start_request, None)
            if not start_resp.started:
                return servo_manufacturing_pb2.RunTestsResponse(  # pylint: disable=no-member
                    all_passed=False,
                    summary=(
                        "Error: Servod failed to start for integration testing:\n"
                        f"{start_resp.console_output}"
                    ),
                )

        tester = v4p1_tester.V4P1Tester(
            serial_number=request.serial_number, mac_address=request.mac_address
        )
        try:
            test_results = await tester.run_integration_tests()
        finally:
            if self._servod_service:
                self._servod_service.stop_servod(None, None)

        all_passed = all(result for _, result in test_results)
        summary_lines = [
            f"{name}: {'PASS' if passed else 'FAIL'}" for name, passed in test_results
        ]

        return servo_manufacturing_pb2.RunTestsResponse(  # pylint: disable=no-member
            all_passed=all_passed, summary="\n".join(summary_lines)
        )

    def validate_servo_serial(
        self,
        request: servo_manufacturing_pb2.ValidateServoSerialRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ValidationResponse:  # pylint: disable=no-member
        """Validate the Servo Serial number."""
        logging.info("Validating Servo Serial: %s...", request.serial_number)
        is_valid = bool(self.SERIALNO_RE.match(request.serial_number))
        error_message = "" if is_valid else "Invalid serial number format"
        return servo_manufacturing_pb2.ValidationResponse(  # pylint: disable=no-member
            is_valid=is_valid, error=error_message
        )

    def validate_servo_mac_address(
        self,
        request: servo_manufacturing_pb2.ValidateServoMacAddressRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ValidationResponse:  # pylint: disable=no-member
        """Validate the Servo MAC address."""
        logging.info("Validating Servo MAC Address: %s...", request.mac_address)
        is_valid = bool(self.MACADDR_RE.match(request.mac_address))
        error_message = "" if is_valid else "Invalid MAC address format"
        return servo_manufacturing_pb2.ValidationResponse(  # pylint: disable=no-member
            is_valid=is_valid, error=error_message
        )
