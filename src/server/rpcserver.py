#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""The RPC server that encapsulates a set of services that diagnoseme front
end can use.

The set of hosted services depends on the DiagnoseMe edition the server image
was built for (DIAGNOSEME_EDITION, see server/editions.py):
    diagnoseme
    dolos
    servod (started only when an edition service needs it)
    servo_manufacturing (Servo v4.1 and Servo Micro)
    maui_manufacturing (Maui Step 1 flashing, PDC flashing, functional testing
        and device management)
Service modules are imported lazily so that an edition image only needs the
dependencies of its own services. On a Maui edition the Maui services are
required: if their modules fail to import or initialize, the server does not
start (a clear error instead of a UI whose requests all fail). Only the
development edition 'all' keeps them optional: the error is logged and the
other services still run.
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
from typing import Iterable
from typing import Optional
from typing import Sequence
from typing import Tuple

import grpc.aio
from grpc_reflection.v1alpha import reflection

from server import editions
from server.config import config


BACKEND_GRPC_SERVICE_PORT = config.BACKEND_GRPC_SERVICE_PORT

# Maui service key (see server/editions.py) -> (servicer module, servicer class).
# The servicer is registered for the gRPC service editions.GRPC_SERVICE_OF[key].
MAUI_SERVICES: Dict[str, Tuple[str, str]] = {
    editions.SERVICE_MAUI_STATION1: (
        "server.maui_station1_service",
        "MauiStation1Servicer",
    ),
    editions.SERVICE_MAUI_PDC: ("server.maui_station1_service", "MauiPdcServicer"),
    editions.SERVICE_MAUI_STATION2: (
        "server.maui_station2_service",
        "MauiStation2Servicer",
    ),
    editions.SERVICE_MAUI_DEVICE_MGMT: (
        "server.maui_station1_service",
        "MauiDeviceManagementServicer",
    ),
}


class MauiServicesUnavailableError(RuntimeError):
    """The Maui services of a Maui edition could not be registered."""


def maui_services_required(edition: editions.Edition) -> bool:
    """True if |edition| cannot run without its Maui services.

    Every edition with Maui services needs them, except the development
    edition editions.DEFAULT_EDITION ('all'), where they stay optional.
    """
    return edition.edition_id != editions.DEFAULT_EDITION and any(
        service in MAUI_SERVICES for service in edition.services
    )


def _enable_reflection(server, descriptor, service_name: str) -> None:
    """Enables gRPC server reflection for a single service."""
    reflection.enable_server_reflection(
        (
            descriptor.services_by_name[service_name].full_name,
            reflection.SERVICE_NAME,
        ),
        server,
    )


def register_maui_services(server, services: Optional[Iterable[str]] = None) -> bool:
    """Registers the Maui manufacturing services on |server|.

    Args:
        server: the gRPC server.
        services: Maui service keys (MAUI_SERVICES) to register; default: all.

    The Maui modules are imported lazily; an import or initialization failure
    (e.g. a missing dependency) is logged and registers none of them. The
    caller decides whether that stops the server (see maui_services_required).

    Returns:
        True if all requested Maui services were registered.
    """
    keys = list(MAUI_SERVICES if services is None else services)
    try:
        maui_pb2 = importlib.import_module("server.generated.maui_manufacturing_pb2")
        maui_pb2_grpc = importlib.import_module(
            "server.generated.maui_manufacturing_pb2_grpc"
        )
        # Create every servicer first so that a failure registers none of them.
        servicers = []
        for key in keys:
            module_name, class_name = MAUI_SERVICES[key]
            servicers.append(
                (
                    editions.GRPC_SERVICE_OF[key],
                    getattr(importlib.import_module(module_name), class_name)(),
                )
            )
    except Exception:  # pylint: disable=broad-exception-caught
        logging.exception("Maui manufacturing services unavailable.")
        return False

    for grpc_service, servicer in servicers:
        getattr(maui_pb2_grpc, f"add_{grpc_service}Servicer_to_server")(
            servicer, server
        )
        _enable_reflection(server, maui_pb2.DESCRIPTOR, grpc_service)
    return True


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


# pylint: enable=import-outside-toplevel,no-name-in-module,import-error
# pylint: enable=no-member


# Non-Maui service key (see server/editions.py) -> registration function. The
# Maui services (MAUI_SERVICES) are registered together by
# register_maui_services().
SERVICE_REGISTRARS: Dict[str, Callable[[Any, Dict[str, Any]], None]] = {
    editions.SERVICE_DIAGNOSEME: _register_diagnoseme,
    editions.SERVICE_DOLOS: _register_dolos,
    editions.SERVICE_SERVOD: _register_servod,
    editions.SERVICE_SERVO_V41: _register_servo_v41,
    editions.SERVICE_SERVO_MICRO: _register_servo_micro,
}


def _check_services(services: Sequence[str]) -> None:
    """Rejects unknown services and two services implementing one gRPC service."""
    registered: Dict[str, str] = {}  # gRPC service -> service key
    for service in services:
        if service not in SERVICE_REGISTRARS and service not in MAUI_SERVICES:
            raise ValueError(f"No registrar for service '{service}'")
        grpc_service = editions.GRPC_SERVICE_OF.get(service, service)
        if grpc_service in registered:
            raise ValueError(
                f"Services '{registered[grpc_service]}' and '{service}' both "
                f"implement the gRPC service '{grpc_service}'"
            )
        registered[grpc_service] = service


def register_services(
    server,
    services: Sequence[str],
    stack: contextlib.ExitStack,
    maui_required: bool = False,
) -> None:
    """Registers the given edition services on the gRPC server.

    Services needing a servod instance share a single ServodRpcService, whose
    lifetime is bound to `stack`. The Maui services are registered last and
    all together (see register_maui_services); if that fails and
    |maui_required|, MauiServicesUnavailableError is raised.
    """
    _check_services(services)
    shared: Dict[str, Any] = {}
    if editions.SERVICE_SERVOD in services:
        from server import servod  # pylint: disable=import-outside-toplevel

        shared["servod_service"] = stack.enter_context(servod.ServodRpcService())

    for service in services:
        if service in SERVICE_REGISTRARS:
            SERVICE_REGISTRARS[service](server, shared)
            logging.info("Registered service '%s'", service)

    maui_services = [service for service in services if service in MAUI_SERVICES]
    if not maui_services:
        return
    if register_maui_services(server, maui_services):
        logging.info("Registered services %s", ", ".join(maui_services))
    elif not maui_required:
        logging.warning("Continuing without the Maui manufacturing services.")
    else:
        message = (
            "The Maui manufacturing services could not be loaded (see the "
            "traceback above); this Maui edition cannot run without them. "
            "Not starting the server."
        )
        logging.critical("%s", message)
        raise MauiServicesUnavailableError(message)


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
            register_services(
                server,
                list(edition.services),
                stack,
                maui_required=maui_services_required(edition),
            )

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
