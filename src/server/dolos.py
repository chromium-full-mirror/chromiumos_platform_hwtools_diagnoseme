# Copyright 2025 The Chromium OS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""RPC service providing functionality to provision and test Dolos devices."""
import logging
import pathlib
import re
import socket
import subprocess
import tempfile
import time

from doloscmd import console_lib
from google.cloud import storage
from paramiko import AuthenticationException
from paramiko import AutoAddPolicy
from paramiko import BadHostKeyException
from paramiko import SSHClient
from paramiko import SSHException

# pylint: disable=no-name-in-module
from server import diagnoseme_dolos_pb2
from server import diagnoseme_dolos_pb2_grpc


logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger().setLevel(logging.DEBUG)

# Location where dhcp leases are stored.
_DHCPD_LEASES = "/leases/dnsmasq.leases"

# fping, ping a range of IP addresses in parallel.
# -c is the number of packets to send.
# -p is the interval between ping packets to a single target.
# -i is the interval between sending ping packets.
FIND_DUT_CMD = [
    "cat %s | awk -F ' ' '{print $3}' | /usr/bin/fping -a -t200 -B1.0 -r2"
    % _DHCPD_LEASES
]

SUBNET_DUT_SEARCH_RE = r"(?P<ip>192\.168\.231\.[0-9][0-9]*[0-9]*).*"

FIRMWARE_CLOUD_BUCKET = "dolos-firmware"
BOX_FIRMWARE_SUBDIR = "box_firmware/"
EEPROM_FIRMWARE_SUBDIR = "cable_eeprom/"


class DolosRpcService(diagnoseme_dolos_pb2_grpc.DolosRpcServiceServicer):
    """Provides RPC methods for interacting with Dolos devices.

    This service implements the functions necessary to provision and test the Dolos
    box and cable for a specific device.

    Box and cable firmware is accessed in the cloud bucket and the standard Dolos
    console library is used to access the box and the cable.
    """

    def get_firmware_versions(self, unused_request, unused_context):
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

    def get_models(self, unused_request, unused_context):
        """Retrieves supported Dolos models.

        Queries a Google Cloud Storage bucket to determine the available Dolos models.

        Args:
            unused_request: An unused request object.
            unused_context: An unused context object.

        Returns:
            A ModelsResponse containing the supported Dolos models.
        """
        response = diagnoseme_dolos_pb2.ModelsResponse()
        storage_client = storage.Client.create_anonymous_client()
        for blob in storage_client.list_blobs(
            FIRMWARE_CLOUD_BUCKET, prefix=EEPROM_FIRMWARE_SUBDIR
        ):
            path = pathlib.PurePath(blob.name)
            if len(path.parts) != 3:
                continue
            model = path.parts[1]
            response.models.append(model)
        return response

    def update_firmware(self, request, unused_context):
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
        response = diagnoseme_dolos_pb2.FirmwareUpdateResponse()
        doloses = console_lib.DolosConsole.get_all_dolos_consoles()
        for dolos in doloses:
            result = diagnoseme_dolos_pb2.CommandExecutionResults()
            try:
                dolos.update_firmware(request.firmware_version)
                dolos.repair()
            except console_lib.DolosConsoleError as e:
                logging.exception("Failed to update firmware.")
                result.exit_code = 1
                result.stderr = str(e)

            response.result.append(result)
        return response

    def _get_active_network_devices(self):
        """Find the active DUT's with IP's issues by the DiagnoseMe box.

        DiagnoseMe has a private network (on the second NIC) for the DUT
        DHCP is running on that network.   Find a list of assigned IP
        addresses and remove any IP's that do not respond to ping.

        Returns:
            list of strings: The IP addresses of devices connected to the
            DiagnoseMe test network.
        """
        connected_ip_addresses = []
        process = subprocess.run(
            FIND_DUT_CMD, capture_output=True, shell=True, check=True
        )
        fping_result = process.stdout
        for line in fping_result.decode("utf-8").splitlines():
            match = re.match(SUBNET_DUT_SEARCH_RE, line)
            if match:
                connected_ip_addresses.append(match.group("ip"))
            else:
                logging.info("Ignoring %s no match", line)
        return list(set(connected_ip_addresses))

    def check_dolos_from_dut(self, unused_request, unused_context):
        """Checks Dolos status from the DUT perspective.

        Establishes an SSH connection to a connected DUT and executes a command
        to check the Dolos status, typically related to battery presence.

        Args:
            unused_request: An unused request object.
            unused_context: An unused context object.

        Returns:
            A CheckDolosFromDutResponse indicating the test status (PASS/FAIL)
            and any associated error messages.
        """
        response = diagnoseme_dolos_pb2.CheckDolosFromDutResponse()
        response.status = diagnoseme_dolos_pb2.TEST_STATUS.FAIL

        devices = self._get_active_network_devices()
        # None is an error as is more than one.
        if len(devices) != 1:
            logging.debug("Wrong number of DUT's detected: %s", devices)
            response.status = diagnoseme_dolos_pb2.TEST_STATUS.INFRA_ERROR
            response.error = (
                f"Expected 1 chromebook connected found {len(devices)} chromebooks."
            )
            return response

        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())

        try:
            client.connect(
                username="root",
                hostname=devices[0],
                key_filename="/home/diagnoseme/.ssh/testing_rsa",
                timeout=10,
            )
        except (
            BadHostKeyException,
            AuthenticationException,
            SSHException,
            socket.error,
        ) as e:
            logging.exception(e)
            response.error = "Failed to ssh into DUT - have you installed test image ?"
            return response

        unused_stdin, stdout, unused_stderr = client.exec_command("ectool battery")
        cmd_result = " ".join(stdout).replace("\n", " ")

        logging.info("Check from dut %s", cmd_result)

        battery_voltage_search = re.search(r"Present voltage\s*(\d+) mV", cmd_result)

        battery_present = False
        try:
            if battery_voltage_search and int(battery_voltage_search.group(1)) >= 3:
                battery_present = True
        except ValueError:
            logging.info(
                "Voltage is not a valid int (%s)", battery_voltage_search.group(1)
            )

        client.close()

        logging.info("Battery match %s", battery_voltage_search)
        logging.info("Battery flag is present %s", battery_present)

        if battery_present:
            response.status = diagnoseme_dolos_pb2.TEST_STATUS.PASS
        response.error = f"Battery flag is present {battery_present}"

        return response

    def check_dolos_from_host(self, unused_request, unused_context):
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
        response = diagnoseme_dolos_pb2.CheckDolosFromHostResponse()
        response.status = diagnoseme_dolos_pb2.TEST_STATUS.PASS

        doloses = console_lib.DolosConsole.get_all_dolos_consoles()
        dolos = doloses[0]
        dolos.open()
        dolos_status_map = dolos.get_status()

        response.dolos_serial_number = dolos.serial
        response.dolos_status = dolos.determine_status(dolos_status_map)
        if response.dolos_status != diagnoseme_dolos_pb2.DOLOS_STATUS.DOLOS_OK:
            response.status = diagnoseme_dolos_pb2.TEST_STATUS.FAIL

        return response

    def program_cable(self, request, unused_context):
        """Programs a Dolos cable.

        Downloads the appropriate firmware from Google Cloud Storage based on
        the provided model and uses the `dolos_program_cable` tool to program the
        cable.
        Performs a dolos repair ( resets the device ) after the update.

        Args:
            request: A ProgramCableRequest specifying the Dolos model.
            unused_context: An unused context object.

        Returns:
            A ProgramCableResponse containing the results of the programming operation.
        """
        response = diagnoseme_dolos_pb2.ProgramCableResponse()
        storage_client = storage.Client.create_anonymous_client()
        blobs = list(
            storage_client.list_blobs(
                FIRMWARE_CLOUD_BUCKET, prefix=EEPROM_FIRMWARE_SUBDIR + request.model
            )
        )
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            blobs[0].download_to_filename(tmp.name)
            cmd = [
                "/usr/local/bin/dolos_program_cable",
                "-y",
                tmp.name,
                "-v",
                "-d",
                "any",
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, check=True)
                logging.debug(
                    "Cable update %r, stdout: %r ,stderr: %r",
                    " ".join(cmd),
                    proc.stdout,
                    proc.stderr,
                )
                response.result.stderr = proc.stderr
                response.result.stdout = proc.stdout
                response.result.exit_code = proc.returncode
            except subprocess.CalledProcessError as err:
                logging.debug("Command: %s", err.cmd)
                logging.debug("stderr: %s", err.stderr)
                logging.debug("stdout: %s", err.stdout)
                logging.debug(str(err))
            finally:
                time.sleep(5)
                doloses = console_lib.DolosConsole.get_all_dolos_consoles()
                if doloses:
                    dolos = doloses[0]
                    dolos.open()
                    dolos.repair()
        return response

    def program_serial_number(self, request, unused_context):
        """Programs the serial number of a Dolos cable.

        Downloads the appropriate firmware from Google Cloud Storage based on the
        provided model and uses the `dolos_program_cable` tool to program the cable
        with the specified serial number.
        Performs a dolos repair ( resets the device ) after the update.

        Args:
            request: A ProgramSerialRequest specifying the Dolos model and new
                     serial number.
            unused_context: An unused context object.

        Returns:
            A ProgramSerialResponse containing the results of the programming operation.
        """
        response = diagnoseme_dolos_pb2.ProgramSerialResponse()
        storage_client = storage.Client.create_anonymous_client()
        blobs = list(
            storage_client.list_blobs(
                FIRMWARE_CLOUD_BUCKET, prefix=EEPROM_FIRMWARE_SUBDIR + request.model
            )
        )
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            blobs[0].download_to_filename(tmp.name)
            cmd = [
                "/usr/local/bin/dolos_program_cable",
                "-y",
                tmp.name,
                "-v",
                "-d",
                "any",
                "-ns",
                request.new_serial_number,
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, check=True)
                logging.debug(
                    "Serial update %r, stdout: %r ,stderr: %r",
                    " ".join(cmd),
                    proc.stdout,
                    proc.stderr,
                )
                response.result.stderr = proc.stderr
                response.result.stdout = proc.stdout
                response.result.exit_code = proc.returncode
            except subprocess.CalledProcessError as err:
                logging.debug("Command: %s", err.cmd)
                logging.debug("stderr: %s", err.stderr)
                logging.debug("stdout: %s", err.stdout)
                logging.debug(str(err))
            finally:
                time.sleep(5)
                doloses = console_lib.DolosConsole.get_all_dolos_consoles()
                if doloses:
                    dolos = doloses[0]
                    dolos.open()
                    dolos.repair()
        return response
