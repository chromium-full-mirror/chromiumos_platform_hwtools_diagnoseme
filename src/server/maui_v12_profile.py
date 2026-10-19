# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Hardware profile of Maui v1.2 (ITE IT82002 MCU with native USB CDC-ACM).

Maui v1.1 (TI MSPM0 behind an FTDI bridge) keeps its constants in
maui_common.py / maui_station*_service.py; this module only describes v1.2.

Source of truth for the USB identities is the maui firmware (dc552bf):
every MCU boot stage enumerates as 18d1:5085 and the stages are told apart by
the iProduct string. Note that 18d1:5066 is the Ti50 CCD of the DUT and must
never be matched as a Maui device (the maui host tools wrongly use it).
"""

import dataclasses
import json
import logging
import os
import re
from typing import List
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple


# ---------------------------------------------------------------------------
# USB identities
# ---------------------------------------------------------------------------
GOOGLE_VID = 0x18D1

MCU_PID = 0x5085
PRODUCT_APP = "Maui Diagnostic Controller"
PRODUCT_RW_BOOTLOADER = "Maui RW Bootloader"
PRODUCT_RO_BOOTLOADER = "Maui RO Recovery Bootloader"

STAGE_APP = "app"
STAGE_RW_BOOTLOADER = "rw_bootloader"
STAGE_RO_BOOTLOADER = "ro_bootloader"
STAGE_UNKNOWN = "unknown"

PRODUCT_TO_STAGE = {
    PRODUCT_APP: STAGE_APP,
    PRODUCT_RW_BOOTLOADER: STAGE_RW_BOOTLOADER,
    PRODUCT_RO_BOOTLOADER: STAGE_RO_BOOTLOADER,
}

# Host-to-host bridge (programmed pre-SMT, carries the unit serial number).
H2H_PIDS = (0x506D, 0x5084)

# On-board hubs (Realtek on early boards, TI TUSB8041 on production boards).
HUB_USB2_VID_PIDS = ((0x0BDA, 0x5411), (0x0451, 0x8142))
HUB_USB3_VID_PIDS = ((0x0BDA, 0x0411), (0x0451, 0x8140))

# PIDs that must never be treated as Maui (Ti50 / Cr50 CCD of the DUT).
NEVER_MATCH_PIDS = (0x5066, 0x5014, 0x504A, 0x520D)

# ---------------------------------------------------------------------------
# Serial number
# ---------------------------------------------------------------------------
# Unit serial format printed on the housing label, e.g. MAUIV12H26010001.
SERIAL_RE = re.compile(r"^MAUIV12H\d{8}$")
# Default MCU NVS serial of a freshly flashed board.
UNINITIALIZED_SERIAL = "Uninitialized"
# NVS_SERIAL_MAX_LEN in the firmware is 32 including the terminator.
SERIAL_MAX_LEN = 31

# Re-enumeration after `reboot`: wait, then poll for the grace period.
REENUM_INITIAL_DELAY_S = 2.0
REENUM_GRACE_S = 5.0
REENUM_POLL_INTERVAL_S = 0.25

# ---------------------------------------------------------------------------
# Functional test thresholds
# ---------------------------------------------------------------------------
# `version` prints e.g. "Maui version 0.69-5324f43 (h2h-read-rpc, built
# 2026-10-13 08:45:17 UTC) [MCUBOOT-SLOT0 (0x80038200)]" (firmware/version.sh).
MCU_VERSION_RE = re.compile(
    r"Maui version (?P<major>\d+)\.(?P<count>\d+)-(?P<hash>[0-9a-f]{7,40})"
    r"(?P<dirty>-dirty)?"
    r" \((?P<branch>[^,()]+), built"
    r" (?P<built>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\)"
    r" \[(?P<mode>MCUBOOT-SLOT0|STANDALONE) \(0x(?P<addr>[0-9a-f]{8})\)\]"
)
MCU_VERSION_MAJOR = "0"
MCU_BOOT_MODE = "MCUBOOT-SLOT0"
MCU_APP_SLOT_ADDR = "80038200"
# "<major>.<count>-<hash>", the build id used in the allowlist.
MCU_VERSION_ID_RE = re.compile(r"^(\d+)\.(\d+)-([0-9a-f]{7,40})$")
# The commit count does not order builds (a newer build can have a lower
# count), so the tests accept the approved MCU App builds: the "stable"
# versions the firmware fetcher wrote next to the station images
# (<root>/<component>/STABLE.json) plus MAUI_V12_APPROVED_FW.
STABLE_MANIFEST = "STABLE.json"
NO_STABLE_FIRMWARE = "no bundled stable MCU firmware to compare"
# Station override (config.env): comma separated build ids, or "any" to accept
# every well-formed release build.
APPROVED_FW_ENV = "MAUI_V12_APPROVED_FW"
APPROVE_ANY_BUILD = "any"

# `hwrev` prints "Hardware revision: <n>" (straps ID3..ID1, MSB first).
# TODO(maui-v1.2): 3 was sampled on one unit only; confirm it for all lots.
VALID_HWREVS = (3,)
# Station override (config.env): comma separated hardware revisions.
HWREVS_ENV = "MAUI_V12_HWREVS"
MAX_HWREV = 7

# `gpio get` levels checked by the host-only cards: only GPIOs that do not
# depend on the connection state (cable orientation, DUT, charger, USB link /
# suspend, LEDs, mux selects). Identical in every bring-up capture (FW 0.59
# and 0.67, both orientations). Outputs show the level the firmware last
# commanded, not a pin readback. (v1.1 keeps EXPECTED_HOST_GPIOS.)
EXPECTED_HOST_GPIOS = {
    "h2h_reset_l": 0,  # PL27A1 running.
    "hub_reset_l": 0,
    "pp5000_cutoff": 0,
    "ec_mem_sel": 0,  # The PL27A1 owns its SPI flash.
    "ec_mem_oe_l": 1,  # SPI mux buffer enabled, else the PL27A1 cannot boot.
    "i2c_temp_alert": 0,
    "pp1200_pg_od": 1,  # Power good.
    "pp3300_pg_od": 1,
    "pp1100_pg_od": 1,
}

# `adc status` limits of the host-only cards. Every "expected value" check
# uses the nominal value +-10 % (VOLTAGE_TOLERANCE_PCT); the idle and CC
# thresholds below are not expected-value checks.
# PP5000 and the host VBUS are read through 1:2 dividers (5 V reads about
# 2500 mV); CHG/DUT VBUS are reported in mV of the rail by the firmware.
VOLTAGE_TOLERANCE_PCT = 10


def expected_range(
    nominal_mv: int, tolerance_pct: int = VOLTAGE_TOLERANCE_PCT
) -> Tuple[int, int]:
    """Returns (low, high) mV of |nominal_mv| +- |tolerance_pct| %."""
    delta = nominal_mv * tolerance_pct // 100
    return nominal_mv - delta, nominal_mv + delta


PP5000_MV = expected_range(2500)  # 5 V rail through 1:2: 2250-2750 mV.
HOST_VBUS_MV = expected_range(2500)  # Host VBUS 5 V through 1:2: 2250-2750 mV.
# Exactly one host CC line sees the host Rp (about 420 mV for a default-USB
# Rp, about 1700 mV for a 3.0 A Rp); the other one stays idle.
HOST_CC_ACTIVE_MV = (250, 2200)
HOST_CC_IDLE_MAX_MV = 150
# Nothing is connected to the DUT port during the host-only cards.
SBU_IDLE_MAX_MV = 100
VSAFE0V_MAX_MV = 800  # Idle DUT / CHG VBUS (vSafe0V).
# CHG VBUS with the charger plugged in: 5 V without a PD contract, or a
# 20 V PD contract (both +-10 %).
CHG_VBUS_5V_MV = expected_range(5000)  # 4500-5500 mV.
CHG_VBUS_20V_MV = expected_range(20000)  # 18000-22000 mV.

# `pdc info` of the PDC image flashed on v1.2 boards (v20_0325_100W).
EXPECTED_PDC_APP_CONFIG_VER = "00000325"
EXPECTED_PDC_FW_VER = "00000020"

# The App drops and re-enumerates the USB3 hub about 2.4 s after plug-in
# (host mux), so the host-only cards poll the USB topology for a while.
USB_TOPOLOGY_SETTLE_S = 5.0
USB_TOPOLOGY_POLL_S = 0.25

# BSL button: read through the ec_bsl_invoke GPIO (GPJ6) instead of the v1.1
# BSL ADC channel. Firmware without that GPIO node prints nothing for it.
BSL_GPIO_NAME = "ec_bsl_invoke"
BSL_GPIO_PRESSED = 1
BSL_GPIO_RELEASED = 0
# Guided 3-phase check (one console session, see maui_v12_bsl):
# 1. Not touched: every sample of BSL_IDLE_S must read released (catches a
#    floating line, e.g. without the R0714 pull-down).
# 2. Press and hold: a press within BSL_PRESS_TIMEOUT_S, then pressed for
#    BSL_HOLD_S of consecutive samples.
# 3. Release: released within BSL_RELEASE_TIMEOUT_S, then for
#    BSL_RELEASED_STABLE_S of consecutive samples.
BSL_POLL_INTERVAL_S = 0.12
BSL_IDLE_S = 2.0
BSL_PRESS_TIMEOUT_S = 10.0
BSL_HOLD_S = 1.5
BSL_RELEASE_TIMEOUT_S = 5.0
BSL_RELEASED_STABLE_S = 1.0

# Console timeouts (CDC-ACM is fast; keep the v1.1 values).
CONSOLE_TIMEOUT_S = 0.5
# Before every command, pending console output is read until the console was
# idle for CONSOLE_DRAIN_IDLE_S (at most CONSOLE_DRAIN_MAX_S).
CONSOLE_DRAIN_IDLE_S = 0.05
CONSOLE_DRAIN_MAX_S = 0.5
CONSOLE_DRAIN_POLL_S = 0.01

# Directories searched for station firmware (see deb_builder/start-diagnoseme).
FIRMWARE_ROOTS = (
    "/var/lib/diagnoseme/firmware",
    "/usr/local/server/firmware",
    "/usr/share/diagnoseme/firmware",
)

# ---------------------------------------------------------------------------
# Firmware / flashing
# ---------------------------------------------------------------------------
PDC_MAX_SLOTS = 5
# Firmware component directories below /var/lib/diagnoseme/firmware, filled
# from gs://maui-firmware by tools/maui_fw_fetch.py (bucket paths: its
# COMPONENTS table).
FW_COMPONENT_MCU_APP = "mcu_app"
# The v1.2 PDC firmware is the same binary as v1.1 (gs://maui-firmware/pdc-fw).
FW_COMPONENT_PDC = "pdc"
# Whole IT82002 eflash image (RO + RW bootloader, App, NVS), flashed with
# itecomdbgr over an external USB-UART bridge. It must cover the whole 1 MiB
# eflash, so a full flash also erases the MCU NVS (serial number).
FW_COMPONENT_MCU_FULL = "mcu_full"
MCU_FULL_IMAGE_SIZE = 0x100000


def stage_for_product(product: str) -> str:
    """Returns the MCU boot stage for a USB iProduct string."""
    return PRODUCT_TO_STAGE.get((product or "").strip(), STAGE_UNKNOWN)


def is_valid_serial(serial: str) -> bool:
    """Returns True for a well-formed v1.2 unit serial number."""
    return bool(SERIAL_RE.match(serial or ""))


@dataclasses.dataclass(frozen=True)
class McuVersion:  # pylint: disable=too-many-instance-attributes
    """The parsed `version` line of the MCU App."""

    major: str
    count: str
    git_hash: str
    dirty: bool
    branch: str
    built: str
    mode: str
    addr: str

    @property
    def build_id(self) -> str:
        """E.g. '0.69-5324f43' ('-dirty' appended for dirty builds)."""
        suffix = "-dirty" if self.dirty else ""
        return f"{self.major}.{self.count}-{self.git_hash}{suffix}"


@dataclasses.dataclass(frozen=True)
class McuVersionCheck:
    """Outcome of check_mcu_version()."""

    ok: bool
    message: str
    version: Optional[McuVersion] = None

    @property
    def build_id(self) -> str:
        """Build id of the parsed version, '' if it could not be parsed."""
        return self.version.build_id if self.version else ""


def parse_mcu_version(output: str) -> Optional[McuVersion]:
    """Parses the output of `version`; None if no well-formed line is found."""
    m = MCU_VERSION_RE.search(output or "")
    if not m:
        return None
    return McuVersion(
        major=m.group("major"),
        count=m.group("count"),
        git_hash=m.group("hash"),
        dirty=bool(m.group("dirty")),
        branch=m.group("branch"),
        built=m.group("built"),
        mode=m.group("mode"),
        addr=m.group("addr"),
    )


def _env_items(environ: Optional[Mapping[str, str]], name: str) -> List[str]:
    """Returns the non-empty comma separated items of environment variable |name|."""
    env = os.environ if environ is None else environ
    return [item.strip() for item in env.get(name, "").split(",") if item.strip()]


def _manifest_version(path: str) -> str:
    """Returns the build id in STABLE.json |path|, '' if missing or invalid."""
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as e:
        logging.warning("Ignoring unreadable %s: %s", path, e)
        return ""
    version = manifest.get("version") if isinstance(manifest, dict) else None
    build_id = str(version or "").strip().lower()
    if not MCU_VERSION_ID_RE.match(build_id):
        logging.warning("Ignoring %s: malformed version %r.", path, version)
        return ""
    return build_id


def stable_mcu_versions(roots: Optional[Sequence[str]] = None) -> Tuple[str, ...]:
    """Returns the build ids of the stable MCU firmware bundled on the station.

    Reads "version" of <root>/mcu_app/STABLE.json and <root>/mcu_full/STABLE.json
    (written by the firmware fetcher next to the images) below every firmware
    root (default: FIRMWARE_ROOTS, the directories of the firmware list).
    """
    versions: List[str] = []
    for root in FIRMWARE_ROOTS if roots is None else roots:
        for component in (FW_COMPONENT_MCU_APP, FW_COMPONENT_MCU_FULL):
            build_id = _manifest_version(os.path.join(root, component, STABLE_MANIFEST))
            if build_id and build_id not in versions:
                versions.append(build_id)
    return tuple(versions)


def approved_mcu_versions(
    environ: Optional[Mapping[str, str]] = None,
    roots: Optional[Sequence[str]] = None,
) -> Optional[Tuple[str, ...]]:
    """Returns the approved MCU App build ids; None if any build is accepted.

    Approved are the bundled stable builds (stable_mcu_versions) and the build
    ids of MAUI_V12_APPROVED_FW; "any" there accepts every well-formed release
    build. An empty tuple means that there is nothing to compare with.
    """
    items = [item.lower() for item in _env_items(environ, APPROVED_FW_ENV)]
    if APPROVE_ANY_BUILD in items:
        return None
    approved = list(stable_mcu_versions(roots))
    for item in items:
        if not MCU_VERSION_ID_RE.match(item):
            logging.warning("Ignoring malformed %s entry '%s'.", APPROVED_FW_ENV, item)
        elif item not in approved:
            approved.append(item)
    return tuple(approved)


def _is_same_build(version: McuVersion, build_id: str) -> bool:
    """True if |version| is the build |build_id| (hashes compared by prefix)."""
    m = MCU_VERSION_ID_RE.match(build_id)
    if not m:
        return False
    major, count, git_hash = m.groups()
    length = min(len(git_hash), len(version.git_hash))
    return (major, count) == (version.major, version.count) and (
        git_hash[:length] == version.git_hash[:length]
    )


def check_mcu_version(  # pylint: disable=too-many-return-statements
    output: str,
    environ: Optional[Mapping[str, str]] = None,
    roots: Optional[Sequence[str]] = None,
    require_approval: bool = False,
) -> McuVersionCheck:
    """Checks the output of `version` against the approved MCU App builds.

    Requires a well-formed, clean release build of major version 0 running
    from the MCUboot App slot and, if there are approved builds (see
    approved_mcu_versions), one of them. Without approved builds every such
    build passes with a note, unless |require_approval| is set (the H2H SPI
    check never touches the bus with an unverified build).
    """
    version = parse_mcu_version(output)
    if version is None:
        line = next(
            (ln.strip() for ln in (output or "").splitlines() if "Maui version" in ln),
            "",
        )
        return McuVersionCheck(
            False,
            "Could not parse the MCU App version from 'version'"
            + (f" ('{line}')." if line else " (no 'Maui version' line)."),
        )
    build = version.build_id
    if version.dirty:
        return McuVersionCheck(
            False, f"MCU App {build} is a dirty development build.", version
        )
    if version.major != MCU_VERSION_MAJOR:
        return McuVersionCheck(
            False,
            f"MCU App {build} has major version {version.major} "
            f"(expected {MCU_VERSION_MAJOR}).",
            version,
        )
    if version.mode != MCU_BOOT_MODE or version.addr != MCU_APP_SLOT_ADDR:
        return McuVersionCheck(
            False,
            f"MCU App {build} runs as {version.mode} (0x{version.addr}), "
            f"expected {MCU_BOOT_MODE} (0x{MCU_APP_SLOT_ADDR}).",
            version,
        )
    approved = approved_mcu_versions(environ, roots)
    if approved is None:
        return McuVersionCheck(
            True,
            f"MCU App {build} (any release build accepted: "
            f"{APPROVED_FW_ENV}={APPROVE_ANY_BUILD})",
            version,
        )
    if not approved:
        if require_approval:
            return McuVersionCheck(
                False,
                f"MCU App {build} cannot be verified ({NO_STABLE_FIRMWARE}; "
                f"approve it with {APPROVED_FW_ENV} in config.env).",
                version,
            )
        return McuVersionCheck(True, f"MCU App {build} ({NO_STABLE_FIRMWARE})", version)
    if any(_is_same_build(version, build_id) for build_id in approved):
        return McuVersionCheck(True, f"MCU App {build} (approved build)", version)
    return McuVersionCheck(
        False,
        f"MCU App {build} is not an approved build (approved: {', '.join(approved)}).",
        version,
    )


def valid_hwrevs(environ: Optional[Mapping[str, str]] = None) -> Tuple[int, ...]:
    """Returns the accepted `hwrev` values (MAUI_V12_HWREVS overrides)."""
    revs = set()
    for item in _env_items(environ, HWREVS_ENV):
        if item.isdigit() and int(item) <= MAX_HWREV:
            revs.add(int(item))
        else:
            logging.warning("Ignoring invalid %s entry '%s'.", HWREVS_ENV, item)
    return tuple(sorted(revs)) or VALID_HWREVS


def expected_hwrev_text(environ: Optional[Mapping[str, str]] = None) -> str:
    """Returns the accepted hardware revisions as shown to the operator."""
    return " or ".join(str(rev) for rev in valid_hwrevs(environ))
