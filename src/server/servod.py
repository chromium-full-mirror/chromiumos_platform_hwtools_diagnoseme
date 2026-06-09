# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""RPC service providing functionality to provision and test Dolos devices."""

from datetime import datetime
import logging
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from typing import Optional

# pylint: disable=import-error
import docker

# pylint: enable=import-error
from google.protobuf import empty_pb2

from server.config import config

# pylint: disable=no-name-in-module,import-error
from server.generated import diagnoseme_servod_pb2
from server.generated import diagnoseme_servod_pb2_grpc


# pylint: enable=no-name-in-module,import-error


logger = logging.getLogger(__name__)


class ServodRpcService(diagnoseme_servod_pb2_grpc.ServodRpcServiceServicer):
    """Provides RPC methods for interacting with Servo devices.

    This service implements the functions necessary to manage and access
    methods of a servod service.
    """

    ARTIFACT_URL_TEMPLATE = "us-docker.pkg.dev/chromeos-hw-tools/servod/servod:%s"
    UPDATE_CHECKER_FILE = Path(tempfile.gettempdir()) / "start-servod-timestamp"

    def __init__(self):
        self.cont: Optional[docker.models.containers.Container] = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.cont:
            try:
                self.cont.kill()
            except docker.errors.APIError:
                logger.warning("Failed to kill container during exit.")

    def _update_check_timestamp(self) -> None:
        current_date = datetime.now().strftime("%Y-%m-%d")
        self.UPDATE_CHECKER_FILE.write_text(current_date, encoding="utf-8")

    def _needs_update_check(self) -> bool:
        if not self.UPDATE_CHECKER_FILE.exists():
            return True

        date = self.UPDATE_CHECKER_FILE.read_text(encoding="utf-8").strip()
        current_date = datetime.now().strftime("%Y-%m-%d")
        return date != current_date

    def _pull_newest_image(
        self,
        client: docker.DockerClient,
        image: str,
        allow_offline: bool,
        verbose: bool,
    ) -> None:
        try:
            resp = client.api.pull(image, stream=True, decode=True)
            for unused_update in resp:
                if verbose:
                    logger.debug("+ %s", unused_update)
                else:
                    # Use standard log for progress if not verbose.
                    pass
            self._update_check_timestamp()
        except docker.errors.APIError as e:
            if (
                e.is_server_error()
                and e.response is not None
                and b"unauthorized" in e.response.content
            ):
                logger.error("Unexpected authentication failure.")
                sys.exit(1)
            if not allow_offline:
                raise
            logger.info("Failed to check for new version, offline mode specified.")
        except docker.errors.DockerException:
            if not allow_offline:
                raise
            logger.info("Failed to check for new version, offline mode specified.")

    def _get_image(
        self,
        client: docker.DockerClient,
        channel: str = "release",
        force_update: bool = False,
    ) -> str:
        image = self.ARTIFACT_URL_TEMPLATE % channel
        if force_update or self._needs_update_check():
            logger.info(
                "Checking docker image is up to date and downloading updates as "
                "necessary."
            )
            # allow_offline=True, verbose=True by default for this flow
            self._pull_newest_image(client, image, allow_offline=True, verbose=True)
            logger.info("Image check complete.")
        else:
            logger.info(
                "Docker image version verified earlier today, no updates needed."
            )
        return image

    def _run_servod_container(
        self, client: docker.DockerClient, image: str, name: str, servod_params: str
    ) -> None:
        """Remove old container and run a new servod container."""
        try:
            old_cont = client.containers.get(name)
            old_cont.remove(force=True)
        except (docker.errors.APIError, docker.errors.NotFound):
            pass

        volumes = ["/dev:/dev"]
        logs_volume = f"{name}_log"
        volumes += [f"{logs_volume}:/var/log/servod_{config.SERVOD_GRPC_PORT}/"]

        nofile_limit = docker.types.Ulimit(name="nofile", soft=65535, hard=65535)
        command = ["bash", "/start_servod_dev.sh", servod_params]
        logger.info("Running servod container with command: %s", " ".join(command))
        self.cont = client.containers.run(
            image,
            remove=True,
            privileged=True,
            name=name,
            hostname=name,
            cap_add=["NET_ADMIN"],
            detach=True,
            volumes=volumes,
            ports={str(config.SERVOD_GRPC_PORT): str(config.SERVOD_GRPC_PORT)},
            command=["bash", "/start_servod_dev.sh", servod_params],
            ulimits=[nofile_limit],
        )

    def _construct_servod_params(self, request: Any) -> str:
        """Construct the servod command parameters from the request."""
        params = f"--port {config.SERVOD_GRPC_PORT} "
        if request.board:
            params += f"--board {request.board} "
        if request.model:
            params += f"--model {request.model} "
        if request.recovery:
            params += "--recovery "
        if request.noboard:
            params += "--noboard "
        return params

    def _wait_for_servod(self, response: Any) -> bool:
        """Wait for the servod instance to become active."""
        if self.cont is None:
            return False

        while True:
            try:
                (error_code, _) = self.cont.exec_run(
                    "servodtool instance wait-for-active --timeout 1 "
                    f"-p {config.SERVOD_GRPC_PORT}"
                )
            except docker.errors.APIError as e:
                response.console_output += str(e)
                return False

            try:
                log_lines = self.cont.logs()
                response.console_output = log_lines.decode("utf-8")
            except docker.errors.NotFound:
                logger.warning("Container not found while trying to fetch logs.")
                response.console_output += "Error: Container not found."
                return False

            if error_code == 0:
                return True

    def start_servod(
        self, request: Any, unused_context: Any
    ) -> diagnoseme_servod_pb2.StartServodResponse:  # pylint: disable=no-member
        """Start servod and return if successful or not."""
        # pylint: disable=no-member
        response = diagnoseme_servod_pb2.StartServodResponse()
        try:
            client = docker.from_env()
        except docker.errors.DockerException as e:
            response.error = str(e)
            return response

        logger.info("Starting servod")
        image = self._get_image(client)
        servod_params = self._construct_servod_params(request)

        container_name = "diagnoseme"
        name = f"{container_name}-docker_servod"

        self._run_servod_container(client, image, name, servod_params)

        if self.cont is None:
            logger.error("Servod container not initialized.")
            response.console_output += "Error: Servod container not initialized.\n"
            response.started = False
            return response

        response.started = self._wait_for_servod(response)
        return response

    def stop_servod(self, unused_request: Any, unused_context: Any) -> empty_pb2.Empty:
        """Stop and remove the servod container."""
        if self.cont:
            try:
                logger.info("Stopping servod container: %s", self.cont.name)
                self.cont.stop()
            except docker.errors.APIError:
                logger.warning("Failed to stop container.")
            finally:
                self.cont = None
        return empty_pb2.Empty()

    def get_dut_hardware_id(
        self, unused_request: Any, unused_context: Any
    ) -> diagnoseme_servod_pb2.GetHardwareIDResponse:  # pylint: disable=no-member
        """Call futility to get the information from the EC about the HWID."""
        # pylint: disable=no-member
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

    def run_dut_control(
        self, request: Any, unused_context: Any
    ) -> diagnoseme_servod_pb2.RunDutControlResponse:  # pylint: disable=no-member
        """Run a dut-control command inside the diagnoseme servod container."""
        # pylint: disable=no-member
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
