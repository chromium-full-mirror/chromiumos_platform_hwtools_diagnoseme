#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""The RPC server that encapsulates a set of services that diagnoseme front
end can use.

The set of hosted services depends on the DiagnoseMe edition the server image
was built for (DIAGNOSEME_EDITION, see server/editions.py). Service modules are
imported lazily so that an edition image only needs the dependencies of its
own services (e.g. the Maui images do not ship doloscmd or servod tooling).
"""

import argparse
import asyncio
from concurrent import futures
import contextlib
import importlib
import logging
import sys
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Tuple

import grpc.aio
from grpc_reflection.v1alpha import reflection

from server import editions
from server.config import config


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


def _enable_reflection(server, descriptor, service_name: str) -> None:
    """Enables gRPC server reflection for a single service."""
    reflection.enable_server_reflection(
        (
            descriptor.services_by_name[service_name].full_name,
            reflection.SERVICE_NAME,
        ),
        server,
    )


# pylint: disable=import-outside-toplevel,no-name-in-module,import-error
# pylint: disable=no-member


def _register_diagnoseme(server, _shared: Dict[str, Any]) -> None:
    from server import diagnoseme_service
    from server.generated import diagnoseme_pb2
    from server.generated.diagnoseme_pb2_grpc import (
        add_DiagnoseMeServiceServicer_to_server,
    )

    add_DiagnoseMeServiceServicer_to_server(
        diagnoseme_service.DiagnoseMeServicer(), server
    )
    _enable_reflection(server, diagnoseme_pb2.DESCRIPTOR, "DiagnoseMeService")


def _register_dolos(server, _shared: Dict[str, Any]) -> None:
    from server import dolos
    from server.generated import diagnoseme_dolos_pb2
    from server.generated.diagnoseme_dolos_pb2_grpc import (
        add_DolosRpcServiceServicer_to_server,
    )

    add_DolosRpcServiceServicer_to_server(dolos.DolosRpcService(), server)
    _enable_reflection(server, diagnoseme_dolos_pb2.DESCRIPTOR, "DolosRpcService")


def _register_servod(server, shared: Dict[str, Any]) -> None:
    from server.generated import diagnoseme_servod_pb2
    from server.generated.diagnoseme_servod_pb2_grpc import (
        add_ServodRpcServiceServicer_to_server,
    )

    add_ServodRpcServiceServicer_to_server(shared["servod_service"], server)
    _enable_reflection(server, diagnoseme_servod_pb2.DESCRIPTOR, "ServodRpcService")


def _register_servo_v41(server, shared: Dict[str, Any]) -> None:
    from server import servo_manufacturing
    from server.generated import servo_manufacturing_pb2
    from server.generated.servo_manufacturing_pb2_grpc import (
        add_ServoV41ManufacturingServicer_to_server,
    )

    add_ServoV41ManufacturingServicer_to_server(
        servo_manufacturing.ServoV41ManufacturingServicer(
            servod_service=shared["servod_service"]
        ),
        server,
    )
    _enable_reflection(
        server, servo_manufacturing_pb2.DESCRIPTOR, "ServoV41Manufacturing"
    )


def _register_servo_micro(server, shared: Dict[str, Any]) -> None:
    from server import servo_micro_manufacturing
    from server.generated import servo_manufacturing_pb2
    from server.generated.servo_manufacturing_pb2_grpc import (
        add_ServoMicroManufacturingServicer_to_server,
    )

    add_ServoMicroManufacturingServicer_to_server(
        servo_micro_manufacturing.ServoMicroManufacturingServicer(
            servod_service=shared["servod_service"]
        ),
        server,
    )
    _enable_reflection(
        server, servo_manufacturing_pb2.DESCRIPTOR, "ServoMicroManufacturing"
    )


def _maui_registrar(service: str) -> Callable[[Any, Dict[str, Any]], None]:
    """Returns the registration function of the Maui service |service|."""
    module_name, servicer_name = MAUI_SERVICERS[service]
    grpc_service = editions.GRPC_SERVICE_OF[service]

    def register(server, _shared: Dict[str, Any]) -> None:
        from server.generated import maui_manufacturing_pb2
        from server.generated import maui_manufacturing_pb2_grpc

        module = importlib.import_module(f"server.{module_name}")
        add_servicer = getattr(
            maui_manufacturing_pb2_grpc, f"add_{grpc_service}Servicer_to_server"
        )
        add_servicer(getattr(module, servicer_name)(), server)
        _enable_reflection(server, maui_manufacturing_pb2.DESCRIPTOR, grpc_service)

    return register


# pylint: enable=import-outside-toplevel,no-name-in-module,import-error
# pylint: enable=no-member


# Maui service key -> (server module, servicer class). The servicer is
# registered for the gRPC service editions.GRPC_SERVICE_OF[key].
MAUI_SERVICERS: Dict[str, Tuple[str, str]] = {
    editions.SERVICE_MAUI_STATION1: ("maui_station1_service", "MauiStation1Servicer"),
    editions.SERVICE_MAUI_PDC: ("maui_station1_service", "MauiPdcServicer"),
    editions.SERVICE_MAUI_STATION2: ("maui_station2_service", "MauiStation2Servicer"),
    editions.SERVICE_MAUI_DEVICE_MGMT: (
        "maui_station1_service",
        "MauiDeviceManagementServicer",
    ),
    editions.SERVICE_MAUI_V12_PDC: ("maui_v12_services", "MauiV12PdcServicer"),
    editions.SERVICE_MAUI_V12_STATION2: (
        "maui_v12_services",
        "MauiV12Station2Servicer",
    ),
    editions.SERVICE_MAUI_V12_DEVICE_MGMT: (
        "maui_v12_services",
        "MauiV12DeviceManagementServicer",
    ),
    editions.SERVICE_MAUI_MCU: ("maui_v12_mcu_service", "MauiMcuServicer"),
}

# Service key (see server/editions.py) -> registration function.
SERVICE_REGISTRARS: Dict[str, Callable[[Any, Dict[str, Any]], None]] = {
    editions.SERVICE_DIAGNOSEME: _register_diagnoseme,
    editions.SERVICE_DOLOS: _register_dolos,
    editions.SERVICE_SERVOD: _register_servod,
    editions.SERVICE_SERVO_V41: _register_servo_v41,
    editions.SERVICE_SERVO_MICRO: _register_servo_micro,
    **{service: _maui_registrar(service) for service in MAUI_SERVICERS},
}


def register_services(server, services: List[str], stack: contextlib.ExitStack) -> None:
    """Registers the given edition services on the gRPC server.

    Services needing a servod instance share a single ServodRpcService, whose
    lifetime is bound to `stack`.
    """
    shared: Dict[str, Any] = {}
    if editions.SERVICE_SERVOD in services:
        from server import servod  # pylint: disable=import-outside-toplevel

        shared["servod_service"] = stack.enter_context(servod.ServodRpcService())

    registered: Dict[str, str] = {}  # gRPC service -> service key
    for service in services:
        registrar = SERVICE_REGISTRARS.get(service)
        if registrar is None:
            raise ValueError(f"No registrar for service '{service}'")
        grpc_service = editions.GRPC_SERVICE_OF.get(service, service)
        if grpc_service in registered:
            raise ValueError(
                f"Services '{registered[grpc_service]}' and '{service}' both "
                f"implement the gRPC service '{grpc_service}'"
            )
        registered[grpc_service] = service
        registrar(server, shared)
        logging.info("Registered service '%s'", service)


async def serve():
    """Create instance of each service and run a server on a port for those services."""
    options = parse_arguments(sys.argv[1:])
    logging_severity = logging.INFO
    if options.verbose:
        logging_severity = logging.DEBUG
    setup_logging(logging_severity)

    edition = editions.get_active_edition()
    logging.info("Starting DiagnoseMe server, edition '%s'", edition.edition_id)

    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=500))
    try:
        with contextlib.ExitStack() as stack:
            register_services(server, list(edition.services), stack)

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
