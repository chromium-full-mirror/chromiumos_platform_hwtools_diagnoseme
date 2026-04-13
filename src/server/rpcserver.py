#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""The RPC server that encapsulates a set of services that diagnoseme front
end can use.
Current hosted services:
    dolos
    servod
    servo_manufacturing
"""

import argparse
import asyncio
from concurrent import futures
import logging
import sys

import grpc.aio
from grpc_reflection.v1alpha import reflection

from server import diagnoseme_service
from server import dolos
from server import servo_manufacturing
from server import servo_micro_manufacturing
from server import servod
from server.config import config

# pylint: disable=no-name-in-module,import-error
from server.generated import diagnoseme_dolos_pb2
from server.generated import diagnoseme_pb2
from server.generated import diagnoseme_servod_pb2
from server.generated import servo_manufacturing_pb2
from server.generated.diagnoseme_dolos_pb2_grpc import (
    add_DolosRpcServiceServicer_to_server,
)
from server.generated.diagnoseme_pb2_grpc import (
    add_DiagnoseMeServiceServicer_to_server,
)
from server.generated.diagnoseme_servod_pb2_grpc import (
    add_ServodRpcServiceServicer_to_server,
)
from server.generated.servo_manufacturing_pb2_grpc import (
    add_ServoMicroManufacturingServicer_to_server,
)
from server.generated.servo_manufacturing_pb2_grpc import (
    add_ServoV41ManufacturingServicer_to_server,
)


# pylint: enable=no-name-in-module,import-error


BACKEND_GRPC_SERVICE_PORT = config.BACKEND_GRPC_SERVICE_PORT


def setup_logging(level):
    """Enable the correct level of logging.

    Args:
        level (int): One of the predefined logging levels, e.g logging.DEBUG
    """
    logging.getLogger().handlers = []
    logging.getLogger().setLevel(level)
    fh = logging.FileHandler("/var/log/rpcserver/rpcserver.log", mode="w")
    fh.setLevel(level)
    # create formatter and add it to the handlers
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    fh.setFormatter(formatter)
    logging.getLogger().addHandler(fh)


def parse_arguments(argv):
    """Creates the argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Turn on debug logging."
    )
    return parser.parse_args(argv)


async def serve():
    """Create instance of each service and run a server on a port for those services."""
    options = parse_arguments(sys.argv[1:])
    logging_severity = logging.INFO
    if options.verbose:
        logging_severity = logging.DEBUG
    setup_logging(logging_severity)

    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=500))
    try:
        add_DolosRpcServiceServicer_to_server(dolos.DolosRpcService(), server)
        service_name = (
            diagnoseme_dolos_pb2.DESCRIPTOR.services_by_name[  # pylint: disable=no-member
                "DolosRpcService"
            ].full_name,
            reflection.SERVICE_NAME,
        )
        reflection.enable_server_reflection(service_name, server)

        add_DiagnoseMeServiceServicer_to_server(
            diagnoseme_service.DiagnoseMeServicer(), server
        )
        service_name = (
            diagnoseme_pb2.DESCRIPTOR.services_by_name[  # pylint: disable=no-member
                "DiagnoseMeService"
            ].full_name,
            reflection.SERVICE_NAME,
        )
        reflection.enable_server_reflection(service_name, server)

        with servod.ServodRpcService() as servod_service:
            add_ServodRpcServiceServicer_to_server(servod_service, server)
            service_name = (
                diagnoseme_servod_pb2.DESCRIPTOR.services_by_name[  # pylint: disable=no-member
                    "ServodRpcService"
                ].full_name,
                reflection.SERVICE_NAME,
            )
            reflection.enable_server_reflection(service_name, server)

            add_ServoV41ManufacturingServicer_to_server(
                servo_manufacturing.ServoV41ManufacturingServicer(
                    servod_service=servod_service
                ),
                server,
            )
            service_name = (
                servo_manufacturing_pb2.DESCRIPTOR.services_by_name[  # pylint: disable=no-member
                    "ServoV41Manufacturing"
                ].full_name,
                reflection.SERVICE_NAME,
            )
            reflection.enable_server_reflection(service_name, server)

            add_ServoMicroManufacturingServicer_to_server(
                servo_micro_manufacturing.ServoMicroManufacturingServicer(
                    servod_service=servod_service
                ),
                server,
            )
            service_name = (
                servo_manufacturing_pb2.DESCRIPTOR.services_by_name[  # pylint: disable=no-member
                    "ServoMicroManufacturing"
                ].full_name,
                reflection.SERVICE_NAME,
            )
            reflection.enable_server_reflection(service_name, server)

        serving_port = BACKEND_GRPC_SERVICE_PORT
        server.add_insecure_port(f"0.0.0.0:{serving_port}")

        async def run_grpc_server():
            await server.start()
            logging.info("Starting server port %s", serving_port)
            await server.wait_for_termination()

        await asyncio.gather(
            run_grpc_server(),
        )

    finally:
        await server.stop(0)


def run_service():
    """Alternative main used by pyproject.toml for packaging."""
    asyncio.run(serve())


if __name__ == "__main__":
    asyncio.run(serve())
