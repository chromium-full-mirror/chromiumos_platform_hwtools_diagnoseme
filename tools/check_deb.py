#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Checks a DiagnoseMe station deb against its edition (editions.json).

Used by the CQ and release builds after the deb build:

  tools/check_deb.py diagnoseme.deb servo-dolos
  tools/check_deb.py --allow-no-maui-utils diagnoseme.deb maui-v1.1

Checks the control file (Package, Conflicts / Replaces of every other
edition's package), the maintainer scripts and the shipped station config:
the editions without Maui get the generic online config (MODE=online,
PULL_POLICY=always, no browser start, no Maui keys, no Maui compose file,
firmware or maui-utils), the Maui editions the Maui config with the Maui
compose override and the bundled maui-utils image (md5 checked against its
STABLE.json). config.local.env (station overrides) must never be shipped.
Only the editions with realtek_tools ship rtunicpg, the RTL8153 EEPROM files
and the r8152 DKMS driver and depend on dkms; the version is the
1.1.<YYYYMMDD> of tools/deb_version.sh.
Only the Python standard library is used (no dpkg needed).

Exit status: 0 when the deb is fine, 1 when a check failed, 2 for usage
errors.
"""

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import sys
import tarfile
from typing import Dict
from typing import List


TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_DIR)
import edition_info  # pylint: disable=wrong-import-position


CONFIG_PATH = "etc/diagnoseme/config.env"
LOCAL_CONFIG_PATH = "etc/diagnoseme/config.local.env"
MAUI_COMPOSE_PATH = "etc/diagnoseme/docker-compose-maui.yaml"
MAUI_H2H_COMPOSE_PATH = "etc/diagnoseme/docker-compose-maui-h2h.yaml"
MAUI_UTILS_DIR = "var/lib/diagnoseme/maui-utils"
# Realtek tools of the editions with realtek_tools (Servo v4p1 tooling).
RTUNICPG_PATH = "usr/local/bin/rtunicpg"
R8152_DKMS_CONF = "usr/src/r8152-2.21.4/dkms.conf"
RTK_PREFIXES = ("usr/src/r8152", "usr/local/server/binfiles", RTUNICPG_PATH)
RTK_BINFILES = ("usr/local/server/binfiles/EE8153B", "usr/local/server/binfiles/EF8153B")
# Depends of the r8152 DKMS build (realtek editions only).
RTK_DEPENDS = ("dkms", "build-essential", "linux-headers-amd64")
# tools/deb_version.sh: 1.1.<YYYYMMDD>.
DEB_VERSION_RE = re.compile(r"^1\.1\.[0-9]{8}$")
# Keys of the Maui config that the generic config must not have.
MAUI_ONLY_KEYS = (
    "COMPOSE_FILE",
    "FIRMWARE_COMPONENTS",
    "MAUI_DEVICE",
    "DIAGNOSEME_RESULTS_DIR",
    "SYNC_MAUI_FIRMWARE",
)


def read_ar(path: str) -> Dict[str, bytes]:
    """Returns the members of the ar archive (deb) at |path|."""
    members = {}
    with open(path, "rb") as f:
        if f.read(8) != b"!<arch>\n":
            raise ValueError(f"{path} is not a deb (ar) archive")
        while True:
            header = f.read(60)
            if len(header) < 60:
                break
            name = header[:16].decode().strip().rstrip("/")
            size = int(header[48:58].decode().strip())
            members[name] = f.read(size)
            if size % 2:
                f.read(1)
    return members


def _tar_member(members: Dict[str, bytes], prefix: str) -> tarfile.TarFile:
    """Opens the <prefix>.tar.* member of a deb."""
    for name, data in members.items():
        if name.startswith(prefix + ".tar"):
            return tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    raise ValueError(f"the deb has no {prefix}.tar member")


def _norm(name: str) -> str:
    """Returns a tar member name without its leading './'."""
    return name[2:] if name.startswith("./") else name


def parse_control(text: str) -> Dict[str, str]:
    """Returns the fields of a DEBIAN/control file."""
    fields = {}
    for line in text.splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


def parse_config(text: str) -> Dict[str, str]:
    """Returns the KEY=value assignments of a config.env (shell quoting)."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parts = shlex.split(value) if value else [""]
        values[key] = parts[0] if parts else ""
    return values


def _package_list(value: str) -> List[str]:
    """Returns the package names of a Conflicts / Replaces field."""
    return [p.strip() for p in value.split(",") if p.strip()]


def check_deb(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    path: str, edition_id: str, editions: Dict, allow_no_maui_utils: bool = False
) -> List[str]:
    """Returns the problems of the deb at |path| (empty when it is fine)."""
    meta = editions[edition_id]
    maui = bool(meta.get("maui_utils"))
    problems = []
    members = read_ar(path)

    with _tar_member(members, "control") as control_tar:
        control_files = {
            _norm(m.name): control_tar.extractfile(m).read()
            for m in control_tar.getmembers()
            if m.isfile()
        }
    control = parse_control(control_files.get("control", b"").decode())
    expected_package = meta["deb_package"]
    if control.get("Package") != expected_package:
        problems.append(
            f"Package is {control.get('Package')!r}, expected {expected_package!r}"
        )
    others = _package_list(
        edition_info.get_field(editions, edition_id, "deb_conflicts")
    )
    for field in ("Conflicts", "Replaces"):
        listed = _package_list(control.get(field, ""))
        missing = sorted(set(others) - set(listed))
        if missing:
            problems.append(f"{field} lacks {', '.join(missing)}")
        if expected_package in listed:
            problems.append(f"{field} names the package itself")
    if not DEB_VERSION_RE.match(control.get("Version", "")):
        problems.append(
            f"Version is {control.get('Version')!r}, expected 1.1.<YYYYMMDD> "
            "(tools/deb_version.sh)"
        )
    realtek = bool(meta.get("realtek_tools"))
    depends = [d.split()[0] for d in _package_list(control.get("Depends", ""))]
    for dep in RTK_DEPENDS:
        if realtek and dep not in depends:
            problems.append(f"Depends lacks {dep} (r8152 DKMS driver)")
        if not realtek and dep in depends:
            problems.append(f"Depends has {dep}, but the edition has no Realtek tools")
    for script in ("preinst", "postinst", "prerm"):
        if script not in control_files:
            problems.append(f"DEBIAN/{script} is missing")
    for script in ("postinst", "prerm"):
        text = control_files.get(script, b"").decode()
        if re.search(r"@[A-Z_]+@", text):
            problems.append(f"{script} has unreplaced @...@ placeholders")
        expected_flag = f'REALTEK_TOOLS="{"true" if realtek else "false"}"'
        if script in control_files and expected_flag not in text:
            problems.append(f"{script} lacks {expected_flag}")

    with _tar_member(members, "data") as data_tar:
        files: Dict[str, tarfile.TarInfo] = {
            _norm(m.name).rstrip("/"): m for m in data_tar.getmembers()
        }

        def read(name: str) -> bytes:
            return data_tar.extractfile(files[name]).read()

        def md5_of(name: str) -> str:
            digest = hashlib.md5(usedforsecurity=False)
            with data_tar.extractfile(files[name]) as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        if LOCAL_CONFIG_PATH in files:
            problems.append(f"{LOCAL_CONFIG_PATH} must never be shipped")
        if realtek:
            for name in (RTUNICPG_PATH, R8152_DKMS_CONF):
                if name not in files:
                    problems.append(f"{name} is missing (Realtek tools)")
            for prefix in RTK_BINFILES:
                if not any(f.startswith(prefix) for f in files):
                    problems.append(f"{prefix}* is missing (Realtek tools)")
        else:
            shipped_rtk = sorted(f for f in files if f.startswith(RTK_PREFIXES))
            if shipped_rtk:
                problems.append(
                    "an edition without Realtek tools ships " + ", ".join(shipped_rtk[:3])
                )
        if CONFIG_PATH not in files:
            return problems + [f"{CONFIG_PATH} is missing"]
        config = parse_config(read(CONFIG_PATH).decode())
        expect = {
            "DIAGNOSEME_EDITION": edition_id,
            "DOCKER_TAG": edition_info.get_field(editions, edition_id, "station_tag"),
        }
        if maui:
            expect.update(
                {
                    "MODE": "offline",
                    "COMPOSE_FILE": edition_info.station_config(editions, edition_id)[
                        "COMPOSE_FILE"
                    ],
                    "MAUI_H2H_FIRMWARE": (
                        "true" if meta.get("h2h_firmware") else "false"
                    ),
                    "FIRMWARE_COMPONENTS": edition_info.get_field(
                        editions, edition_id, "firmware"
                    ),
                    "MAUI_DEVICE": meta.get("maui_device", ""),
                    "DIAGNOSEME_RESULTS_DIR": meta.get("results_dir", ""),
                }
            )
        else:
            expect.update(
                {
                    "MODE": "online",
                    "PULL_POLICY": "always",
                    "AUTO_OPEN_BROWSER": "false",
                }
            )
        for key, value in expect.items():
            if config.get(key) != value:
                problems.append(
                    f"config.env {key}={config.get(key)!r}, expected {value!r}"
                )
        launchers = sorted(
            os.path.basename(f)
            for f in files
            if f.startswith("usr/share/applications/") and f.endswith(".desktop")
        )
        if maui:
            if MAUI_COMPOSE_PATH not in files:
                problems.append(f"{MAUI_COMPOSE_PATH} is missing")
            # Only the editions with H2H firmware flashing mount its directory.
            if bool(meta.get("h2h_firmware")) != (MAUI_H2H_COMPOSE_PATH in files):
                problems.append(
                    f"{MAUI_H2H_COMPOSE_PATH} must be shipped exactly by the "
                    "editions with h2h_firmware"
                )
            if "start-diagnoseme.desktop" not in launchers:
                problems.append("the Maui launchers are missing")
            tar_path = f"{MAUI_UTILS_DIR}/maui-utils.tar"
            manifest_path = f"{MAUI_UTILS_DIR}/STABLE.json"
            if tar_path in files and manifest_path in files:
                manifest = json.loads(read(manifest_path))
                image = f"maui-utils:{manifest.get('version', '')}"
                if config.get("MAUI_UTILS_IMAGE") != image:
                    problems.append(
                        f"config.env MAUI_UTILS_IMAGE="
                        f"{config.get('MAUI_UTILS_IMAGE')!r}, expected {image!r}"
                    )
                if md5_of(tar_path) != manifest.get("md5"):
                    problems.append("maui-utils.tar does not match its STABLE.json md5")
            elif not allow_no_maui_utils:
                problems.append("the bundled maui-utils.tar / STABLE.json is missing")
        else:
            for key in config:
                if key.startswith("MAUI_") or key in MAUI_ONLY_KEYS:
                    problems.append(f"config.env of a non-Maui edition has {key}")
            for name in (
                MAUI_COMPOSE_PATH,
                MAUI_H2H_COMPOSE_PATH,
                MAUI_UTILS_DIR,
                "var/lib/diagnoseme/firmware",
            ):
                if name in files:
                    problems.append(f"a non-Maui edition ships {name}")
            if launchers != ["diagnoseme-web.desktop"]:
                problems.append(f"unexpected launchers {launchers}")
    return problems


def main(argv=None) -> int:
    """Command line entry point; returns the exit status."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("deb")
    parser.add_argument("edition")
    parser.add_argument("--registry", default=edition_info.REGISTRY)
    parser.add_argument(
        "--allow-no-maui-utils",
        action="store_true",
        help="a Maui deb may lack maui-utils (ALLOW_NO_MAUI_UTILS=1 builds)",
    )
    args = parser.parse_args(argv)
    editions = edition_info.load_editions(args.registry)
    if args.edition not in editions:
        print(f"Unknown edition '{args.edition}'", file=sys.stderr)
        return 2
    try:
        problems = check_deb(args.deb, args.edition, editions, args.allow_no_maui_utils)
    except (OSError, ValueError, tarfile.TarError, KeyError) as e:
        problems = [str(e)]
    if problems:
        for problem in problems:
            print(f"check_deb: {args.deb} ({args.edition}): {problem}", file=sys.stderr)
        return 1
    print(f"check_deb: {args.deb} is a valid {args.edition} package")
    return 0


if __name__ == "__main__":
    sys.exit(main())
