# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Centralized configuration for DiagnoseMe server."""

import os


# pylint: disable=too-few-public-methods
class Config:
    """Configuration settings for DiagnoseMe."""

    # Server configuration
    BACKEND_GRPC_SERVICE_PORT = int(os.environ.get("BACKEND_GRPC_SERVICE_PORT", "6002"))
    SERVOD_GRPC_PORT = int(os.environ.get("SERVOD_GRPC_PORT", "9999"))

    # Hardware identifiers
    SERVO_V4P1_VID = os.environ.get("SERVO_V4P1_VID", "18d1")
    SERVO_V4P1_PID = os.environ.get("SERVO_V4P1_PID", "520d")

    GENESYS_HUB_VID = os.environ.get("GENESYS_HUB_VID", "05e3")
    GENESYS_HUB_PID = os.environ.get("GENESYS_HUB_PID", "0610")
    GENESYS_HUB_PID3 = os.environ.get("GENESYS_HUB_PID3", "0626")

    CYPRESS_HUB_VID = os.environ.get("CYPRESS_HUB_VID", "04b4")
    CYPRESS_HUB_PID = os.environ.get("CYPRESS_HUB_PID", "6502")
    CYPRESS_HUB_PID2 = os.environ.get("CYPRESS_HUB_PID2", "6500")
    CYPRESS_HUB_PID3 = os.environ.get("CYPRESS_HUB_PID3", "6504")

    # Paths
    BINFILES_DIR = os.environ.get("BINFILES_DIR", "/usr/local/server/binfiles")
    BINFILES_SHADOW_DIR = os.environ.get(
        "BINFILES_SHADOW_DIR", "/usr/local/server/binfiles_shadow"
    )
    GENESYS_FW_DIR = os.environ.get("GENESYS_FW_DIR", "/usr/local/genesys/")
    SERVO_V4P1_FW_DIR = os.environ.get("SERVO_V4P1_FW_DIR", "/usr/local/servo_v4p1/")
    SERVO_MICRO_FW_DIR = os.environ.get("SERVO_MICRO_FW_DIR", "/usr/local/servo_micro/")

    # Timeouts and retries
    DEFAULT_MAC_RETRY_ATTEMPTS = int(os.environ.get("DEFAULT_MAC_RETRY_ATTEMPTS", "10"))
    DEFAULT_MAC_RETRY_INTERVAL = float(
        os.environ.get("DEFAULT_MAC_RETRY_INTERVAL", "1.0")
    )


config = Config()
