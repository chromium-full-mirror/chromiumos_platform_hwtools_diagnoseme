# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""DiagnoseMe editions (build-time product flavors) - server side registry.

Every shipped DiagnoseMe image is built for exactly one edition. The edition
is baked into the server image as the DIAGNOSEME_EDITION environment variable
(see dockerfiles/rpcserver/Dockerfile) and decides which gRPC services are
registered by rpcserver.py.

Keep in sync with:
  * editions.json (repository root, consumed by build/release scripts),
  * src/ui/src/app/editions/*.edition.ts (UI menu and routes),
  * src/ui/angular.json build configurations.
tests/unit/test_editions.py verifies the consistency.
"""

import dataclasses
import os
from typing import Dict
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

# Maui service key -> gRPC service (maui_manufacturing.proto) it implements;
# rpcserver.py registers the servicers from it. Several keys may implement
# the same gRPC service (one per hardware revision), but an edition may only
# contain one of them.
GRPC_SERVICE_OF = {
    SERVICE_MAUI_STATION1: "MauiStation1InitialFlashing",
    SERVICE_MAUI_PDC: "MauiPdcFlashing",
    SERVICE_MAUI_STATION2: "MauiStation2FunctionalTesting",
    SERVICE_MAUI_DEVICE_MGMT: "MauiDeviceManagement",
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
)

# Edition used when DIAGNOSEME_EDITION is not set (local development).
DEFAULT_EDITION = "all"


@dataclasses.dataclass(frozen=True)
class Edition:
    """Server side description of a DiagnoseMe edition."""

    edition_id: str
    title: str
    services: Tuple[str, ...]
    maui_hw_rev: str = ""
    shipped: bool = True


EDITIONS: Dict[str, Edition] = {
    "dolos": Edition(
        edition_id="dolos",
        title="Dolos",
        services=(SERVICE_DIAGNOSEME, SERVICE_DOLOS),
    ),
    "servo-v4p1": Edition(
        edition_id="servo-v4p1",
        title="Servo v4p1",
        services=(SERVICE_DIAGNOSEME, SERVICE_SERVOD, SERVICE_SERVO_V41),
    ),
    "servo-micro": Edition(
        edition_id="servo-micro",
        title="Servo Micro",
        services=(SERVICE_DIAGNOSEME, SERVICE_SERVOD, SERVICE_SERVO_MICRO),
    ),
    "maui-v1.1": Edition(
        edition_id="maui-v1.1",
        title="Maui v1.1",
        services=(
            SERVICE_DIAGNOSEME,
            SERVICE_MAUI_STATION1,
            SERVICE_MAUI_PDC,
            SERVICE_MAUI_STATION2,
            SERVICE_MAUI_DEVICE_MGMT,
        ),
        maui_hw_rev="v1.1",
    ),
    "all": Edition(
        edition_id="all",
        title="All (development)",
        services=ALL_SERVICES,
        maui_hw_rev="v1.1",
        shipped=False,
    ),
}


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
