#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Servo Micro Manufacturing service."""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any
from typing import List
from typing import Optional
from typing import Tuple
import urllib.error
import urllib.request

from google.protobuf import empty_pb2
import grpc

from server import serial_programmer
from server import servo_programmer
from server import util

# pylint: enable=no-name-in-module,import-error
# pylint: disable=no-name-in-module,import-error,duplicate-code
from server.generated import diagnoseme_servod_pb2
from server.generated import servo_manufacturing_pb2
from server.generated import servo_manufacturing_pb2_grpc


class ServoMicroManufacturingServicer(
    servo_manufacturing_pb2_grpc.ServoMicroManufacturingServicer
):
    """Servo Micro Manufacturing gRPC service."""

    BOARD = "servo_micro"

    # The VID/PID of the stm chip before programming/in DFU mode
    STM_DFU_VID = 0x0483
    STM_DFU_PID = 0xDF11

    # The VID/PID of the servo device once programmed
    SERVO_VID = 0x18D1
    SERVO_PID = 0x501A

    RE = (
        r"^(MICRO-)?"  # device type
        r"[CGS](-)?"  # supplier
        r"[0-9]{2}"  # YY
        r"(0[1-9]|1[0-2])"  # MM
        r"(0[1-9]|[12][0-9]|3[0-1])"  # DD
        r"[0-9]{4}$"  # serialno suffix
    )

    LEGACY_RES = [r"^S[MN][CN][PDQ][0-9]{5}$", r"^CMO653-00166-04[A-Z0-9]+$"]

    SERIALNO_RE = re.compile("|".join(f"({e})" for e in LEGACY_RES + [RE]))

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
    ) -> (
        servo_manufacturing_pb2.MicroDevicePresenceResponse
    ):  # pylint: disable=no-member
        """Check for connected devices."""
        # pylint: disable=no-member
        return servo_manufacturing_pb2.MicroDevicePresenceResponse(
            mcu_dfu_detected=util.is_usb_device_present(
                self.STM_DFU_VID, [self.STM_DFU_PID]
            ),
            servo_micro_detected=util.is_usb_device_present(
                self.SERVO_VID, [self.SERVO_PID]
            ),
        )

    def submit_provisioning_results(
        self,
        request: servo_manufacturing_pb2.SubmitMicroProvisioningResultsRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> (
        servo_manufacturing_pb2.SubmitProvisioningResultsResponse
    ):  # pylint: disable=no-member
        """Submit the results to a configured webhook."""
        # pylint: disable=no-member
        results_server = os.environ.get("RESULTS_SERVER")
        if not results_server:
            logging.info("RESULTS_SERVER is not configured, skipping submission.")
            return servo_manufacturing_pb2.SubmitProvisioningResultsResponse(
                success=True, message="Results server not configured."
            )

        payload = {
            "serial_number": request.serial_number,
            "mcu_programming": {
                "success": request.mcu_programming.success,
                "retry_count": request.mcu_programming.retry_count,
                "failure_logs": list(request.mcu_programming.failure_logs),
            },
            "serial_programming": {
                "success": request.serial_programming.success,
                "retry_count": request.serial_programming.retry_count,
                "failure_logs": list(request.serial_programming.failure_logs),
            },
            "testing": {
                "success": request.testing.success,
                "retry_count": request.testing.retry_count,
                "failure_logs": list(request.testing.failure_logs),
            },
        }

        try:
            req = urllib.request.Request(
                results_server,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10.0) as response:
                if response.status in (200, 201, 202, 204):
                    return servo_manufacturing_pb2.SubmitProvisioningResultsResponse(
                        success=True, message="Results submitted successfully."
                    )
                return servo_manufacturing_pb2.SubmitProvisioningResultsResponse(
                    success=False,
                    message=f"Results server returned status {response.status}",
                )
        except urllib.error.URLError as e:
            logging.exception("Failed to submit provisioning results.")
            return servo_manufacturing_pb2.SubmitProvisioningResultsResponse(
                success=False, message=f"Failed to submit results: {str(e)}"
            )

    def _wait_for_usb_device(
        self,
        device_name: str,
        usb_id: Tuple[int, List[int]],
        response_type: Any,
        grace_period: float = 2.0,
    ) -> Optional[Any]:
        """Waits for a USB device to enumerate and returns a response on timeout."""
        logging.info("Waiting for %s to enumerate...", device_name)
        if not util.wait_for_usb_devices([usb_id], timeout=30.0):
            return response_type(
                success=False, message=f"Timeout waiting for {device_name}"
            )
        time.sleep(grace_period)
        return None

    def program_mcu(
        self,
        request: servo_manufacturing_pb2.ProgramMcuRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> servo_manufacturing_pb2.ProgramMcuResponse:  # pylint: disable=no-member
        """Program the MCU."""
        # pylint: disable=no-member
        logging.info("Programming MCU with %s...", request.firmware_path)

        timeout_resp = self._wait_for_usb_device(
            "MCU in DFU mode",
            (self.STM_DFU_VID, [self.STM_DFU_PID]),
            servo_manufacturing_pb2.ProgramMcuResponse,
        )
        if timeout_resp:
            return timeout_resp

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

        timeout_resp = self._wait_for_usb_device(
            "Servo device",
            (self.SERVO_VID, [self.SERVO_PID]),
            servo_manufacturing_pb2.ProgramSerialResponse,
        )
        if timeout_resp:
            return timeout_resp

        try:
            programmer = serial_programmer.SerialProgrammer(
                request.serial_number,
                board=self.BOARD,
                target_if="if03",
            )
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

    async def run_tests(
        self,
        request: servo_manufacturing_pb2.RunTestsRequest,  # pylint: disable=no-member
        unused_context: grpc.ServicerContext,
    ) -> (
        servo_manufacturing_pb2.RunTestsResponse  # pylint: disable=no-member
    ):  # pylint: disable=invalid-overridden-method
        """Run tests to verify the programming."""
        logging.info(
            "Running tests for servo micro with serial %s...", request.serial_number
        )

        all_passed = False
        summary_lines = []

        if self._servod_service:
            # Start servod normally
            start_request = (
                diagnoseme_servod_pb2.StartServodRequest(  # pylint: disable=no-member
                    board=self.BOARD,
                    serial=request.serial_number,
                    recovery=False,
                )
            )
            start_resp = await asyncio.to_thread(
                self._servod_service.start_servod, start_request, None
            )
            if not start_resp.started:
                summary_lines.append(
                    f"Error: Servod failed to start:\n{start_resp.console_output}"
                )
                return servo_manufacturing_pb2.RunTestsResponse(  # pylint: disable=no-member
                    all_passed=False,
                    summary="\n".join(summary_lines),
                )

            try:
                # Test basic communication by querying serialname
                cmd_request = diagnoseme_servod_pb2.RunDutControlRequest(  # pylint: disable=no-member
                    command="serialname"
                )
                cmd_resp = await asyncio.to_thread(
                    self._servod_service.run_dut_control, cmd_request, None
                )
                if (
                    cmd_resp.error_code == 0
                    and request.serial_number in cmd_resp.result
                ):
                    summary_lines.append("serialname: PASS")
                    all_passed = True
                else:
                    summary_lines.append(
                        f"serialname: FAIL (output: {cmd_resp.result})"
                    )
            except Exception as e:  # pylint: disable=broad-except
                logging.exception("Test execution failed")
                summary_lines.append(f"serialname: FAIL ({e})")
            finally:
                await asyncio.to_thread(self._servod_service.stop_servod, None, None)
        else:
            summary_lines.append("Error: servod_service not provided")

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
