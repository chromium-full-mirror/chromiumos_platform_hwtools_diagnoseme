# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""DiagnoseMe editions (build-time product flavors) - server side registry.

Every shipped DiagnoseMe image is built for exactly one edition. The edition
is baked into the server image as the DIAGNOSEME_EDITION environment variable
(see dockerfiles/rpcserver/Dockerfile) and decides which gRPC services are
registered by rpcserver.py.

The edition list, titles, shipped flags and Maui hardware revisions come from
editions.json (repository root), the single source of truth that the build
scripts read too. The server image carries a copy of it
(DIAGNOSEME_EDITIONS_JSON); in a source checkout the repository file is used.
This module only adds the gRPC services of every edition (SERVICES_OF).
tests/unit/test_editions.py verifies that both, the Angular edition files,
angular.json and Dockerfile.base stay consistent.
"""

import dataclasses
import json
import os
from typing import Dict
from typing import Optional
from typing import Tuple


# Service keys understood by rpcserver.py.
SERVICE_DIAGNOSEME = "diagnoseme"
SERVICE_DOLOS = "dolos"
SERVICE_SERVOD = "servod"
SERVICE_SERVO_V41 = "servo_v41"
SERVICE_SERVO_MICRO = "servo_micro"
SERVICE_MAUI_STATION1 = "maui_station1"
SERVICE_MAUI_PDC = "maui_pdc"
SERVICE_MAUI_STATION2 = "maui_station2"
SERVICE_MAUI_DEVICE_MGMT = "maui_device_mgmt"
# Maui v1.2 implementations of the shared Maui gRPC services. They register
# the same gRPC services as their v1.1 counterparts, so an edition may only
# contain one implementation of each.
SERVICE_MAUI_V12_PDC = "maui_v12_pdc"
SERVICE_MAUI_V12_STATION2 = "maui_v12_station2"
SERVICE_MAUI_V12_DEVICE_MGMT = "maui_v12_device_mgmt"
SERVICE_MAUI_MCU = "maui_mcu"

# Maui service key -> gRPC service (maui_manufacturing.proto) it implements;
# rpcserver.py registers the servicers from it. Several keys may implement
# the same gRPC service (one per hardware revision), but an edition may only
# contain one of them.
GRPC_SERVICE_OF = {
    SERVICE_MAUI_STATION1: "MauiStation1InitialFlashing",
    SERVICE_MAUI_PDC: "MauiPdcFlashing",
    SERVICE_MAUI_V12_PDC: "MauiPdcFlashing",
    SERVICE_MAUI_STATION2: "MauiStation2FunctionalTesting",
    SERVICE_MAUI_V12_STATION2: "MauiStation2FunctionalTesting",
    SERVICE_MAUI_DEVICE_MGMT: "MauiDeviceManagement",
    SERVICE_MAUI_V12_DEVICE_MGMT: "MauiDeviceManagement",
    SERVICE_MAUI_MCU: "MauiMcuService",
}

ALL_SERVICES = (
    SERVICE_DIAGNOSEME,
    SERVICE_DOLOS,
    SERVICE_SERVOD,
    SERVICE_SERVO_V41,
    SERVICE_SERVO_MICRO,
    SERVICE_MAUI_STATION1,
    SERVICE_MAUI_PDC,
    SERVICE_MAUI_STATION2,
    SERVICE_MAUI_DEVICE_MGMT,
    SERVICE_MAUI_V12_PDC,
    SERVICE_MAUI_V12_STATION2,
    SERVICE_MAUI_V12_DEVICE_MGMT,
    SERVICE_MAUI_MCU,
)

# Services of the development edition: every service, with the Maui v1.1
# implementation of the shared Maui services (plus the v1.2-only MCU service).
DEV_SERVICES = tuple(
    s
    for s in ALL_SERVICES
    if s
    not in (
        SERVICE_MAUI_V12_PDC,
        SERVICE_MAUI_V12_STATION2,
        SERVICE_MAUI_V12_DEVICE_MGMT,
    )
)

_MAUI_V11_SERVICES = (
    SERVICE_DIAGNOSEME,
    SERVICE_MAUI_STATION1,
    SERVICE_MAUI_PDC,
    SERVICE_MAUI_STATION2,
    SERVICE_MAUI_DEVICE_MGMT,
)

# gRPC services of every edition of editions.json (the only edition data that
# is not in editions.json).
SERVICES_OF: Dict[str, Tuple[str, ...]] = {
    # Every Servo / Dolos service, as before the editions.
    "servo-dolos": (
        SERVICE_DIAGNOSEME,
        SERVICE_DOLOS,
        SERVICE_SERVOD,
        SERVICE_SERVO_V41,
        SERVICE_SERVO_MICRO,
    ),
    "dolos": (SERVICE_DIAGNOSEME, SERVICE_DOLOS),
    "servo-v4p1": (SERVICE_DIAGNOSEME, SERVICE_SERVOD, SERVICE_SERVO_V41),
    "servo-micro": (SERVICE_DIAGNOSEME, SERVICE_SERVOD, SERVICE_SERVO_MICRO),
    "maui-v1.1": _MAUI_V11_SERVICES,
    "maui-v1.2": (
        SERVICE_DIAGNOSEME,
        SERVICE_MAUI_V12_PDC,
        SERVICE_MAUI_V12_STATION2,
        SERVICE_MAUI_V12_DEVICE_MGMT,
        SERVICE_MAUI_MCU,
    ),
    "all": DEV_SERVICES,
}

# Edition used when DIAGNOSEME_EDITION is not set (a server started from a
# source checkout). Every image names its edition explicitly.
DEFAULT_EDITION = "all"

# Environment variable naming the editions.json of the server image.
EDITIONS_JSON_ENV = "DIAGNOSEME_EDITIONS_JSON"
# editions.json of a source checkout (src/server/editions.py -> repo root).
_REPO_EDITIONS_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "editions.json",
)


@dataclasses.dataclass(frozen=True)
class Edition:
    """Server side description of a DiagnoseMe edition."""

    edition_id: str
    title: str
    services: Tuple[str, ...]
    maui_hw_rev: str = ""
    shipped: bool = True
    # Firmware components the edition's releases must bundle with a valid
    # STABLE.json (editions.json firmware_stable_required).
    firmware_stable_required: Tuple[str, ...] = ()


def editions_json_path() -> str:
    """Returns the editions.json to read (image copy, else the repository's)."""
    return os.environ.get(EDITIONS_JSON_ENV, "").strip() or _REPO_EDITIONS_JSON


def load_editions(path: Optional[str] = None) -> Dict[str, Edition]:
    """Returns the editions of editions.json with their services.

    Raises:
      ValueError: editions.json is missing or invalid, or an edition has no
        services row in SERVICES_OF (or a row has no edition).
    """
    path = path or editions_json_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            registry = json.load(f)["editions"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ValueError(f"Cannot read the DiagnoseMe editions from {path}: {e}") from e
    if set(registry) != set(SERVICES_OF):
        raise ValueError(
            f"{path} and server/editions.py SERVICES_OF list different editions: "
            f"{sorted(set(registry) ^ set(SERVICES_OF))}"
        )
    return {
        edition_id: Edition(
            edition_id=edition_id,
            title=meta["title"],
            services=SERVICES_OF[edition_id],
            maui_hw_rev=meta.get("maui_hw_rev", ""),
            shipped=bool(meta.get("shipped", False)),
            firmware_stable_required=tuple(meta.get("firmware_stable_required", ())),
        )
        for edition_id, meta in registry.items()
    }


EDITIONS: Dict[str, Edition] = load_editions()


def get_edition_id() -> str:
    """Returns the edition id this server was built for."""
    return os.environ.get("DIAGNOSEME_EDITION", "").strip() or DEFAULT_EDITION


def get_active_edition() -> Edition:
    """Returns the active edition, raising ValueError for unknown ids."""
    edition_id = get_edition_id()
    if edition_id not in EDITIONS:
        raise ValueError(
            f"Unknown DIAGNOSEME_EDITION '{edition_id}'. "
            f"Known editions: {', '.join(sorted(EDITIONS))}"
        )
    return EDITIONS[edition_id]
