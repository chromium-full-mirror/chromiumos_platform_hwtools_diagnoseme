# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""RPC service providing functionality to provision and test Dolos devices."""

from datetime import datetime
import logging
import os
import re
import sys
import tempfile

# pylint: disable=import-error
import docker

# pylint: disable=no-name-in-module
from server import diagnoseme_servod_pb2
from server import diagnoseme_servod_pb2_grpc


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
file_handler = logging.FileHandler(f"/var/log/{__name__}.log")
logger.addHandler(file_handler)


class ServodRpcService(diagnoseme_servod_pb2_grpc.ServodRpcServiceServicer):
    """Provides RPC methods for interacting with Servo devices.

    This service implements the functions necessary to manage and access
    methods of a servod service.
    """

    ARTIFACT_URL_TEMPLATE = "us-docker.pkg.dev/chromeos-hw-tools/servod/servod:%s"
    UPDATE_CHECKER_FILE = os.path.join(tempfile.gettempdir(), "start-servod-timestamp")

    def __init__(self):
        self.cont = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.cont:
            self.cont.kill()

    def _update_check_timestamp(self):
        with open(self.UPDATE_CHECKER_FILE, "w", encoding="utf-8") as file:
            current_date = datetime.now().strftime("%Y-%m-%d")
            file.write(current_date)

    def _needs_update_check(self):
        if os.path.exists(self.UPDATE_CHECKER_FILE) is False:
            return True

        with open(self.UPDATE_CHECKER_FILE, "r", encoding="utf-8") as file:
            date = file.read().strip()
            current_date = datetime.now().strftime("%Y-%m-%d")
            if date == current_date:
                return False
            return True

    def _pull_newest_image(self, client, image, allow_offline, verbose):
        try:
            resp = client.api.pull(image, stream=True, decode=True)
            for unused_update in resp:
                if verbose:
                    print(f"+ {unused_update}")
                else:
                    print("+", end="", flush=True)
            print("", flush=True)
            self._update_check_timestamp()
        except docker.errors.APIError as e:
            if (
                e.is_server_error()
                and e.response is not None
                and str(e.response.content).find("unauthorized") > 0
            ):
                print("Unexpected authentication failure.")
                sys.exit(1)
            if not allow_offline:
                raise
            print("Failed to check for new version, offline mode specified.")
        except docker.errors.DockerException:
            if not allow_offline:
                raise
            print("Failed to check for new version, offline mode specified.")

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def _get_image(
        self, client, allow_offline, force_update, verbose, channel="release"
    ):
        image = self.ARTIFACT_URL_TEMPLATE % channel
        if force_update is True or self._needs_update_check():
            logging.info(
                (
                    "Checking docker image is up to date and downloading updates as"
                    " necessary."
                )
            )
            self._pull_newest_image(client, image, allow_offline, verbose)
            logging.info("Image check complete.")
        else:
            logging.info(
                "Docker image version verified earlier today, no updates needed."
            )
        return image

    def _run_servod_container(self, client, image, name, servod_params):
        """Remove old container and run a new servod container."""
        try:
            self.cont = client.containers.get(name)
            self.cont.remove(force=True)
        except (docker.errors.APIError, docker.errors.NotFound):
            pass

        volumes = ["/dev:/dev"]
        logs_volume = f"{name}_log"
        volumes += [f"{logs_volume}:/var/log/servod_9999/"]

        nofile_limit = docker.types.Ulimit(name="nofile", soft=65535, hard=65535)
        self.cont = client.containers.run(
            image,
            remove=True,
            privileged=True,
            name=name,
            hostname=name,
            cap_add=["NET_ADMIN"],
            detach=True,
            volumes=volumes,
            ports={"9999": "9999"},
            command=["bash", "/start_servod_dev.sh", servod_params],
            ulimits=[nofile_limit],
        )

    # pylint: disable=too-many-locals
    def start_servod(self, request, unused_context):
        """Start servod and return if successful or not."""
        response = diagnoseme_servod_pb2.StartServodResponse()
        try:
            client = docker.from_env()
        except docker.errors.DockerException as e:
            response.error = str(e)
            return response

        print("Started")
        image = self._get_image(client, True, False, True)
        servod_params = "--port 9999 "
        if request.board:
            servod_params += f"--board {request.board} "
        if request.model:
            servod_params += f"--model {request.model} "

        container_name = "diagnoseme"
        name = f"{container_name}-docker_servod"

        self._run_servod_container(client, image, name, servod_params)

        if self.cont is None:
            logger.error("Servod container not initialized.")
            response.console_output += "Error: Servod container not initialized.\n"
            response.started = False
            return response

        started = False
        while not started:
            try:
                (error_code, output) = self.cont.exec_run(
                    "servodtool instance wait-for-active --timeout 1 -p 9999"
                )
                del output  # All information necessary is in the error code.
            except docker.errors.APIError as e:
                response.console_output += str(e)
                break

            if error_code == 0:
                log_lines = None
                try:
                    log_lines = self.cont.logs()
                    response.console_output += log_lines.decode("utf-8")
                    started = True
                except docker.errors.NotFound:
                    logger.warning("Container not found while trying to fetch logs.")
                    response.console_output += "Error: Container not found."
                    started = False
                    break
            else:
                log_lines = None
                try:
                    log_lines = self.cont.logs()
                    response.console_output += log_lines.decode("utf-8")
                except docker.errors.NotFound:
                    logger.warning("Container not found while trying to fetch logs.")
                    response.console_output += "Error: Container not found."
                    started = False
                    break

        response.started = started
        return response

    def get_dut_hardware_id(self, unused_request, unused_context):
        """Call futility to get the information from the EC about the HWID."""
        response = diagnoseme_servod_pb2.GetHardwareIDResponse()
        if self.cont is None:
            logger.error("Servod container not initialized.")
            response.console_output = "Error: Servod container not initialized."
            response.error_code = 9999
            return response
        (error_code, output) = self.cont.exec_run("futility gbb --hwid --get --servo")
        logger.debug("futility returns: %s", output.decode("utf-8"))
        logger.debug("futility error code: %s", error_code)
        response.error_code = error_code

        hwid_regex = r"hardware_id:\s(.*)"
        if error_code == 0:
            hwid_match = re.search(hwid_regex, output.decode("utf-8"))
            if hwid_match:
                response.hwid = hwid_match.group(1)
            else:
                logger.info("No string match in futility output.")
        response.console_output = output.decode("utf-8")
        return response

    def run_dut_control(self, request, unused_context):
        """Run a dut-control command inside the diagnoseme servod container."""
        response = diagnoseme_servod_pb2.RunDutControlResponse()
        if self.cont is None:
            logger.error("Servod container not initialized.")
            response.error_code = 9999
            response.result = "Unable to access servod container, may have exited."
            return response
        cmd = ["dut-control", request.command]
        (error_code, output) = self.cont.exec_run(cmd)
        logger.debug("%s exit code: %s", cmd, error_code)
        logger.debug("%s output: %s", cmd, output)
        response.error_code = error_code
        if error_code == 0:
            result = output.decode("utf-8").strip().split(":")
            if len(result) > 1 and result[1]:
                response.result = result[1]
        return response
