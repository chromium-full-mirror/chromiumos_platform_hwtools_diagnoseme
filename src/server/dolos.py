# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""RPC service providing functionality to provision and test Dolos devices."""

import csv
from io import StringIO
import logging
import pathlib
import tempfile
import time
from typing import Any
from typing import Optional

from doloscmd import console_lib
from doloscmd.error import DolosConsoleError
from google.cloud import storage

# pylint: disable=no-name-in-module,import-error
from server.generated import diagnoseme_dolos_pb2
from server.generated import diagnoseme_dolos_pb2_grpc


# pylint: enable=no-name-in-module,import-error


logger = logging.getLogger(__name__)

FIRMWARE_CLOUD_BUCKET = "dolos-firmware"
BOX_FIRMWARE_SUBDIR = "box_firmware/"
EEPROM_FIRMWARE_SUBDIR = "cable_eeprom/"
BOARD_MODEL_MAPPING_FILE = "cable_eeprom/mapping.csv"


class DolosRpcService(diagnoseme_dolos_pb2_grpc.DolosRpcServiceServicer):
    """Provides RPC methods for interacting with Dolos devices.

    This service implements the functions necessary to provision and test the Dolos
    box and cable for a specific device.

    Box and cable firmware is accessed in the cloud bucket and the standard Dolos
    console library is used to access the box and the cable.
    """

    def get_firmware_versions(
        self, unused_request: Any, unused_context: Any
    ) -> diagnoseme_dolos_pb2.FirmwareVersionsResponse:  # pylint: disable=no-member
        """Retrieves available Dolos firmware versions.

        Fetches a list of available firmware versions from a Google Cloud Storage
        bucket and identifies the default version.

        Args:
            unused_request: An unused request object.
            unused_context: An unused context object.

        Returns:
            A FirmwareVersionsResponse containing the available firmware versions
            and the default version.
        """
        # pylint: disable=no-member
        logger.info("Getting firmware versions")
        response = diagnoseme_dolos_pb2.FirmwareVersionsResponse()
        storage_client = storage.Client.create_anonymous_client()
        for blob in storage_client.list_blobs(
            FIRMWARE_CLOUD_BUCKET, prefix=BOX_FIRMWARE_SUBDIR
        ):
            path = pathlib.PurePath(blob.name)
            if len(path.parts) != 3:
                continue
            firmware_version = path.parts[1]
            if path.parts[2] == "default":
                response.default_firmware_version = firmware_version
            response.firmware_version.append(firmware_version)

        return response

    def get_models(
        self, unused_request: Any, unused_context: Any
    ) -> diagnoseme_dolos_pb2.ModelsResponse:  # pylint: disable=no-member
        """Retrieves supported Dolos models.

        Queries a Google Cloud Storage bucket to determine the available Dolos models.

        Args:
            unused_request: An unused request object.
            unused_context: An unused context object.

        Returns:
            A ModelsResponse containing the supported Dolos models.
        """
        # pylint: disable=no-member
        response = diagnoseme_dolos_pb2.ModelsResponse()
        storage_client = storage.Client.create_anonymous_client()
        models = []
        for blob in storage_client.list_blobs(
            FIRMWARE_CLOUD_BUCKET, prefix=EEPROM_FIRMWARE_SUBDIR
        ):
            path = pathlib.PurePath(blob.name)
            if len(path.parts) != 3:
                continue
            model = path.parts[1]
            models.append(model)
        response.models.extend(list(set(models)))
        return response

    def get_board_model_mapping(
        self, unused_request: Any, unused_context: Any
    ) -> diagnoseme_dolos_pb2.BoardModelMappingResponse:  # pylint: disable=no-member
        """Reads public csv file with board model mapping.

        Args:
            unused_request: An unused request object.
            unused_context: An unused context object.

        Returns:
            BoardModelMappingResponse which has the mapping.
        """
        # pylint: disable=no-member
        logger.debug("Generating board model mapping.")
        response = diagnoseme_dolos_pb2.BoardModelMappingResponse()
        storage_client = storage.Client.create_anonymous_client()
        bucket = storage_client.bucket(FIRMWARE_CLOUD_BUCKET)
        blob = storage.Blob(bucket=bucket, name=BOARD_MODEL_MAPPING_FILE)
        csv_data = StringIO(blob.download_as_string(storage_client).decode("utf-8"))
        reader = csv.reader(csv_data)
        for row in reader:
            board_model_mapping = diagnoseme_dolos_pb2.BoardModel()
            board_model_mapping.model = row[0]
            board_model_mapping.board = row[1]
            response.board_model_mappings.append(board_model_mapping)
        return response

    def update_firmware(
        self, request: Any, unused_context: Any
    ) -> diagnoseme_dolos_pb2.FirmwareUpdateResponse:  # pylint: disable=no-member
        """Updates the firmware on connected Dolos devices.

        Initiates a firmware update process on all detected Dolos consoles
        using the specified firmware version.  Performs a repair operation
        after the update.

        Args:
            request: A FirmwareUpdateRequest specifying the target firmware version.
            unused_context: An unused context object.

        Returns:
            A FirmwareUpdateResponse containing the results of the update operation
            for each Dolos device.
        """
        # pylint: disable=no-member
        response = diagnoseme_dolos_pb2.FirmwareUpdateResponse()
        doloses = console_lib.DolosConsole.get_all_dolos_consoles()
        for dolos in doloses:
            result = diagnoseme_dolos_pb2.CommandExecutionResults()
            try:
                dolos.update_firmware(request.firmware_version, request.bsl_mode)
                dolos.repair()
            except console_lib.DolosConsoleError as e:
                logger.exception("Failed to update firmware.")
                result.exit_code = 1
                result.stderr = str(e)

            response.result.append(result)
        return response

    def check_dolos_from_host(
        self, unused_request: Any, unused_context: Any
    ) -> diagnoseme_dolos_pb2.CheckDolosFromHostResponse:  # pylint: disable=no-member
        """Checks Dolos status from the host perspective.

        Queries a connected Dolos console for its status information, including
        serial number and overall status.

        Args:
            unused_request: An unused request object.
            unused_context: An unused context object.

        Returns:
            A CheckDolosFromHostResponse containing the Dolos serial number,
            status, and the overall test status.
        """
        # pylint: disable=no-member
        response = diagnoseme_dolos_pb2.CheckDolosFromHostResponse()
        response.status = diagnoseme_dolos_pb2.TEST_STATUS.FAIL

        doloses = console_lib.DolosConsole.get_all_dolos_consoles()
        if len(doloses) > 0:
            dolos = doloses[0]
            dolos.open()
            dolos_status_map = dolos.get_status()

            response.dolos_serial_number = dolos.serial
            response.dolos_status = dolos.determine_status(dolos_status_map)
            if response.dolos_status == diagnoseme_dolos_pb2.DOLOS_STATUS.DOLOS_OK:
                response.status = diagnoseme_dolos_pb2.TEST_STATUS.PASS
            response.error = diagnoseme_dolos_pb2.DOLOS_STATUS.Name(
                response.dolos_status
            )

        return response

    def program_cable(
        self, request: Any, unused_context: Any
    ) -> diagnoseme_dolos_pb2.ProgramCableResponse:  # pylint: disable=no-member
        """Programs a Dolos cable.

        Performs a dolos repair ( resets the device ) after the update.

        Args:
            request: A ProgramCableRequest specifying the Dolos hwid and
                     optional serial.
            unused_context: An unused context object.

        Returns:
            A ProgramCableResponse containing the results of the programming operation.
        """
        # pylint: disable=no-member
        response = diagnoseme_dolos_pb2.ProgramCableResponse()
        response.success = False

        doloses = console_lib.DolosConsole.get_all_dolos_consoles()
        if len(doloses) > 0:
            dolos = doloses[0]
            dolos.open()
            eeprom_data_filename: Optional[str] = None
            new_serial_number: Optional[str] = None

            if request.eeprom_data:
                with tempfile.NamedTemporaryFile(delete=False) as datafile:
                    datafile.write(request.eeprom_data.encode("utf-8"))
                    eeprom_data_filename = datafile.name

            if request.new_serial_number:
                new_serial_number = request.new_serial_number

            try:
                dolos.program_cable(
                    request.hwid,
                    file=eeprom_data_filename,
                    new_serial=new_serial_number,
                )
            except DolosConsoleError as e:
                response.error_message = str(e)
                logger.exception("Failed to program cable.")
                return response
            finally:
                if eeprom_data_filename:
                    pathlib.Path(eeprom_data_filename).unlink(missing_ok=True)

            time.sleep(5)

            try:
                dolos.repair()
                response.success = True

            except DolosConsoleError as e:
                response.error_message = str(e)
                logger.exception("Failed to reset the device.")
        else:
            response.error_message = "No Dolos console found"

        return response
