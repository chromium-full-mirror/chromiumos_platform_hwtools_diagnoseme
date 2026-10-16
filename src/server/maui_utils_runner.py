# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Runs Maui host tools (maui-flash, maui-ctl, fw-updater) in maui-utils.

DiagnoseMe does not ship the maui repository. The Maui host tools are taken
from the maui-utils Docker image published by the maui project
(gs://maui-firmware/docker/<version>/maui-utils.tar) and started through the
host docker socket that is mounted into the server container:

  docker run --rm --privileged -v /dev:/dev -v /run/udev:/run/udev:ro \\
      -v <host staging dir>:/mnt/firmware_dir --entrypoint <tool> \\
      maui-utils:<version> <args>

Because the docker daemon runs on the host, bind mount sources must be host
paths. Firmware files are therefore staged into STAGING_ROOT, which lives in
/tmp; /tmp is shared between the host and the server container (see
docker-compose*.yaml).

The runner only builds the command line and stages files. Callers execute it
with subprocess / asyncio subprocess exactly like a local tool, so existing
output parsing, timeouts and retries keep working unchanged.
"""

import contextlib
import logging
import os
import shutil
import subprocess
from typing import Iterator
from typing import List
from typing import Optional
from typing import Sequence
import uuid


# Last resort when MAUI_UTILS_IMAGE is empty and no maui-utils image is
# loaded. Stations set MAUI_UTILS_IMAGE (config.env) to the bundled stable
# maui-utils; local builds bake it into the server image.
DEFAULT_IMAGE = "maui-utils:stable"
# Mount point of the staging directory inside the maui-utils container.
CONTAINER_FW_DIR = "/mnt/firmware_dir"
# Host-shared directory used to hand firmware files to maui-utils.
STAGING_ROOT = "/tmp/diagnoseme_maui_utils"
# maui-utils image bundled with the Maui station packages.
BUNDLED_TAR = "/var/lib/diagnoseme/maui-utils/maui-utils.tar"

TOOL_MAUI_FLASH = "maui-flash"
TOOL_MAUI_CTL = "maui-ctl"
TOOL_FW_UPDATER = "fw-updater"
KNOWN_TOOLS = (TOOL_MAUI_FLASH, TOOL_MAUI_CTL, TOOL_FW_UPDATER)


class MauiUtilsError(Exception):
    """Raised when maui-utils cannot be used."""


def newest_local_image(docker_bin: Optional[str] = None) -> str:
    """Returns the most recently created local maui-utils:<tag> image, or ''."""
    try:
        proc = subprocess.run(
            [
                docker_bin or get_docker_bin(),
                "images",
                "maui-utils",
                "--format",
                "{{.CreatedAt}}\t{{.Repository}}:{{.Tag}}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    images = [
        line.split("\t", 1)
        for line in (proc.stdout or "").splitlines()
        if "\t" in line and not line.endswith(":<none>")
    ]
    return max(images)[1] if images else ""


def get_image() -> str:
    """Returns the maui-utils image reference configured for this server.

    MAUI_UTILS_IMAGE wins (config.env / server image); otherwise the newest
    local maui-utils image, else DEFAULT_IMAGE.
    """
    image = os.environ.get("MAUI_UTILS_IMAGE", "").strip()
    return image or newest_local_image() or DEFAULT_IMAGE


def get_docker_bin() -> str:
    """Returns the docker CLI used to start maui-utils."""
    return shutil.which("docker") or "docker"


class MauiUtilsSession:
    """A staging directory plus command builder for one tool invocation.

    Use through MauiUtilsRunner.session(); the staging directory is removed
    when the context exits.
    """

    def __init__(self, image: str, staging_dir: str, docker_bin: str):
        self.image = image
        self.staging_dir = staging_dir
        self.docker_bin = docker_bin
        # Name of the container of the most recent command(), so a hung run
        # can be killed: killing the docker CLI does not stop the container.
        # Every command() gets a fresh name; callers build a new command for
        # a retry instead of re-running the old one, so a container that is
        # still being removed cannot cause a name conflict.
        self.container_name = ""

    def stage(self, path: str) -> str:
        """Copies a local file into the staging dir.

        Args:
          path: path of the file inside the server container.

        Returns:
          The path of the file inside the maui-utils container.
        """
        if not os.path.isfile(path):
            raise MauiUtilsError(f"File to stage does not exist: {path}")
        name = os.path.basename(path)
        shutil.copy2(path, os.path.join(self.staging_dir, name))
        return f"{CONTAINER_FW_DIR}/{name}"

    def command(self, tool: str, args: Sequence[str]) -> List[str]:
        """Returns the docker command line running |tool| with |args|."""
        if tool not in KNOWN_TOOLS:
            raise MauiUtilsError(f"Unknown maui-utils tool: {tool}")
        self.container_name = f"diagnoseme-maui-utils-{uuid.uuid4().hex[:12]}"
        return [
            self.docker_bin,
            "run",
            "--rm",
            "-i",
            # maui-utils is loaded from the bundled tar, never pulled.
            "--pull",
            "never",
            "--name",
            self.container_name,
            # stdout is a pipe: keep Python tools line buffered so progress
            # output is streamed instead of arriving at exit.
            "-e",
            "PYTHONUNBUFFERED=1",
            "--privileged",
            "-v",
            "/dev:/dev",
            "-v",
            "/run/udev:/run/udev:ro",
            "-v",
            f"{self.staging_dir}:{CONTAINER_FW_DIR}",
            "--entrypoint",
            tool,
            self.image,
            *args,
        ]

    def kill(self) -> None:
        """Force-removes the container of this session (e.g. after a timeout)."""
        if not self.container_name:
            return
        try:
            subprocess.run(
                [self.docker_bin, "rm", "-f", self.container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logging.warning(
                "Failed to remove maui-utils container %s: %s",
                self.container_name,
                e,
            )


def explain_failure(output: str, image: Optional[str] = None) -> str:
    """Returns an operator hint when |output| shows a missing maui-utils image."""
    if "No such image" not in output and "Unable to find image" not in output:
        return ""
    image = image or get_image()
    return (
        f"The maui-utils image '{image}' is not loaded on this station. Run "
        f"'start-diagnoseme' again or 'sudo docker load -i {BUNDLED_TAR}'."
    )


class MauiUtilsRunner:
    """Factory of maui-utils sessions."""

    # pylint: disable=too-few-public-methods

    def __init__(
        self,
        image: Optional[str] = None,
        staging_root: str = STAGING_ROOT,
        docker_bin: Optional[str] = None,
    ):
        self.image = image or get_image()
        self.staging_root = staging_root
        self.docker_bin = docker_bin or get_docker_bin()

    @contextlib.contextmanager
    def session(self) -> Iterator[MauiUtilsSession]:
        """Yields a session with a fresh host-shared staging directory."""
        os.makedirs(self.staging_root, exist_ok=True)
        staging_dir = os.path.join(self.staging_root, uuid.uuid4().hex)
        os.makedirs(staging_dir)
        session = MauiUtilsSession(self.image, staging_dir, self.docker_bin)
        try:
            yield session
        finally:
            # Never leave a container running on the staging dir (e.g. when
            # the caller was cancelled); `docker rm -f` of a container that
            # already exited is a no-op.
            session.kill()
            shutil.rmtree(staging_dir, ignore_errors=True)
