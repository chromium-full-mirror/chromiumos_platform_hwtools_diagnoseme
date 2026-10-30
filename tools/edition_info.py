#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Query the DiagnoseMe edition registry (editions.json) from shell scripts.

Examples:
  tools/edition_info.py list                      # every edition id
  tools/edition_info.py list --shipped            # shipped edition ids only
  tools/edition_info.py get maui-v1.1 landing     # single field
  tools/edition_info.py get servo-dolos docker_tags # edition id + aliases
  tools/edition_info.py get servo-dolos station_tag # config.env DOCKER_TAG
  tools/edition_info.py get maui-v1.1 deb_conflicts # other editions' debs
  tools/edition_info.py get maui-v1.1 iso_latest  # GCS name of the latest ISO
  tools/edition_info.py get servo-dolos realtek_tools # true|false
  tools/edition_info.py validate maui-v1.1        # exit 1 when unknown
  tools/edition_info.py validate --shipped "$E"   # exit 1 unless shipped
  tools/edition_info.py render-config maui-v1.1 config.env \
      --bundled-maui-utils 0.0.1-69-89b053b      # specialise a config.env
"""

import argparse
import json
import os
import shlex
import sys


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(REPO_ROOT, "editions.json")
# Base URL of the station UI (UI_URL = base + landing page).
STATION_UI_BASE_URL = "http://localhost:8888"
# Compose files of the Maui stations (config.env COMPOSE_FILE): the generic
# docker-compose.yaml plus the Maui override (Maui mounts and environment).
MAUI_COMPOSE_FILE = (
    "/etc/diagnoseme/docker-compose.yaml:/etc/diagnoseme/docker-compose-maui.yaml"
)
# Added for the editions with H2H firmware flashing (Maui v1.2): mounts the
# station's H2H firmware directory (MAUI_H2H_FW_DIR).
MAUI_H2H_COMPOSE_FILE = "/etc/diagnoseme/docker-compose-maui-h2h.yaml"


def load_editions(path=REGISTRY):
    """Returns the edition dictionary of editions.json."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["editions"]


# pylint: disable-next=too-many-return-statements
def get_field(editions, edition_id, field):
    """Returns a field of an edition formatted for shell consumption."""
    meta = editions[edition_id]
    if field == "docker_tags":
        return " ".join([edition_id] + meta.get("docker_tag_aliases", []))
    if field == "docker_aliases":
        return " ".join(meta.get("docker_tag_aliases", []))
    if field == "station_tag":
        # Image tag of the edition's stations (servo-dolos: release).
        return meta.get("station_tag") or edition_id
    if field == "deb_conflicts":
        # The deb packages of the other editions (deb Conflicts / Replaces).
        own = meta.get("deb_package", "")
        packages = {m.get("deb_package", "") for m in editions.values()}
        return ", ".join(sorted(packages - {own, ""}))
    if field == "iso_latest":
        # Only servo-dolos keeps the historical custom-debian-preseed-latest.iso.
        return (
            meta.get("iso_latest") or f"custom-debian-preseed-{edition_id}-latest.iso"
        )
    if field == "realtek_tools":
        # Servo v4p1 tooling: rtunicpg and the r8152 DKMS driver in the deb.
        return "true" if meta.get("realtek_tools") else "false"
    value = meta.get(field, "")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)


def station_config(editions, edition_id, bundled_maui_utils=""):
    """Returns the per-edition values of a station config.env.

    Args:
      editions: the edition dictionary of editions.json.
      edition_id: edition installed on the station.
      bundled_maui_utils: version of the maui-utils image bundled with the
        station package (dist/maui-utils/VERSION, the stable release);
        MAUI_UTILS_IMAGE is only set for Maui editions when it is given.

    The editions without Maui only get their identity: the generic template
    (config-generic.env) keeps the online defaults and has no Maui keys.
    """
    meta = editions[edition_id]
    values = {
        "DIAGNOSEME_EDITION": edition_id,
        "DOCKER_TAG": get_field(editions, edition_id, "station_tag"),
        "UI_URL": STATION_UI_BASE_URL + meta.get("landing", "/"),
        "STATION_ID": meta.get("station_id", ""),
    }
    if meta.get("maui_utils"):
        h2h = bool(meta.get("h2h_firmware"))
        values.update(
            {
                "COMPOSE_FILE": MAUI_COMPOSE_FILE
                + (f":{MAUI_H2H_COMPOSE_FILE}" if h2h else ""),
                "MAUI_H2H_FIRMWARE": "true" if h2h else "false",
                "FIRMWARE_COMPONENTS": get_field(editions, edition_id, "firmware"),
                "MAUI_DEVICE": meta.get("maui_device", ""),
                # Maui results directory (server DIAGNOSEME_RESULTS_DIR).
                "DIAGNOSEME_RESULTS_DIR": meta.get("results_dir", ""),
            }
        )
        if bundled_maui_utils:
            values["MAUI_UTILS_IMAGE"] = f"maui-utils:{bundled_maui_utils}"
    return values


def render_config(text, values):
    """Returns config.env |text| with the KEY= lines of |values| replaced.

    Keys missing from |text| are appended when they have a value (an empty
    value only clears an existing line); values are shell quoted because the
    stations source config.env.
    """
    lines = text.splitlines()
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0]
        if "=" in line and key in values:
            lines[i] = f"{key}={shlex.quote(values[key]) if values[key] else ''}"
            done.add(key)
    for key, value in values.items():
        if key not in done and value:
            lines.append(f"{key}={shlex.quote(value)}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    """Command line entry point; returns the exit status."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", default=REGISTRY)
    sub = parser.add_subparsers(dest="cmd", required=True)
    list_p = sub.add_parser("list")
    list_p.add_argument("--shipped", action="store_true")
    get_p = sub.add_parser("get")
    get_p.add_argument("edition")
    get_p.add_argument("field")
    val_p = sub.add_parser("validate")
    val_p.add_argument("edition")
    val_p.add_argument(
        "--shipped",
        action="store_true",
        help="also fail unless the edition is shipped (release builds)",
    )
    render_p = sub.add_parser(
        "render-config",
        help="specialise a station config.env for an edition (in place)",
    )
    render_p.add_argument("edition")
    render_p.add_argument("config_file")
    render_p.add_argument(
        "--bundled-maui-utils",
        default="",
        metavar="VERSION",
        help="version of the bundled maui-utils.tar (sets MAUI_UTILS_IMAGE)",
    )
    args = parser.parse_args(argv)

    editions = load_editions(args.registry)
    if args.cmd == "list":
        for edition_id, meta in editions.items():
            if not args.shipped or meta.get("shipped"):
                print(edition_id)
        return 0
    if args.edition not in editions:
        print(
            f"Unknown edition '{args.edition}'. "
            f"Known: {', '.join(sorted(editions))}",
            file=sys.stderr,
        )
        return 1
    if args.cmd == "validate" and args.shipped:
        if not editions[args.edition].get("shipped"):
            shipped = [e for e, m in editions.items() if m.get("shipped")]
            print(
                f"Edition '{args.edition}' is not shipped. "
                f"Shipped: {', '.join(shipped)}",
                file=sys.stderr,
            )
            return 1
    if args.cmd == "get":
        print(get_field(editions, args.edition, args.field))
    elif args.cmd == "render-config":
        values = station_config(editions, args.edition, args.bundled_maui_utils)
        with open(args.config_file, "r", encoding="utf-8") as f:
            text = f.read()
        with open(args.config_file, "w", encoding="utf-8") as f:
            f.write(render_config(text, values))
    return 0


if __name__ == "__main__":
    sys.exit(main())
