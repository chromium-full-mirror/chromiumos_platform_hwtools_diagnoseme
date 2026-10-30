#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Fetch the Realtek tools of the editions with Servo v4p1 tooling
# (editions.json "realtek_tools": true) into dist/:
#   dist/rtunicpg.tar.bz2       rtunicpg RTL8153 Ethernet programmer and its
#                               EEPROM/eFUSE config files
#   dist/r8152-2.21.4.tar.bz2   r8152 driver source (DKMS) of the station deb
# Only those editions' debs (deb_builder/Dockerfile) and server images
# (Dockerfile.base rtk-fetcher, base-servo-v4p1 and the targets based on it)
# use them; the other editions build without them. Files already in dist/ are
# kept. The single fetcher of build-diagnoseme.sh, build-offline-bundle.sh
# (and so build_iso.sh) and the Cloud Build configs.
#
# Usage: tools/fetch_rtk_tools.sh <edition>...
# Does nothing when no edition needs the Realtek tools; fails when one does
# and a file can neither be found in dist/ nor downloaded.

set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DIST_DIR="${PROJECT_ROOT}/dist"
EDITION_INFO=(python3 "${PROJECT_ROOT}/tools/edition_info.py")
GCS_BUCKET="gs://rtk-tools"
# <file in dist/>:<object in ${GCS_BUCKET}>
RTK_FILES=(
    "rtunicpg.tar.bz2:rtunicpg-2.0.25.3.tar.bz2"
    "r8152-2.21.4.tar.bz2:r8152-2.21.4.tar.bz2"
)

usage() {
    echo "Usage: $0 <edition>..."
    echo ""
    echo "Fetches dist/rtunicpg.tar.bz2 and dist/r8152-2.21.4.tar.bz2 from ${GCS_BUCKET}"
    echo "when one of the editions has realtek_tools (editions.json)."
}

if [[ $# -eq 0 ]]; then
    usage >&2
    exit 2
fi
case "$1" in
    -h|--help)
        usage
        exit 0
        ;;
esac

NEEDED=()
for EDITION in "$@"; do
    "${EDITION_INFO[@]}" validate "${EDITION}"
    if [[ "$("${EDITION_INFO[@]}" get "${EDITION}" realtek_tools)" == "true" ]]; then
        NEEDED+=("${EDITION}")
    fi
done
if [[ ${#NEEDED[@]} -eq 0 ]]; then
    echo "No Realtek tools needed for: $*"
    exit 0
fi

mkdir -p "${DIST_DIR}"
FAILED=()
for ENTRY in "${RTK_FILES[@]}"; do
    FILE="${ENTRY%%:*}"
    OBJECT="${ENTRY#*:}"
    if [[ -s "${DIST_DIR}/${FILE}" ]]; then
        echo "Using dist/${FILE}"
        continue
    fi
    echo "Fetching ${GCS_BUCKET}/${OBJECT} -> dist/${FILE}..."
    if gcloud storage cp "${GCS_BUCKET}/${OBJECT}" "${DIST_DIR}/${FILE}.part" &&
        [[ -s "${DIST_DIR}/${FILE}.part" ]]; then
        mv "${DIST_DIR}/${FILE}.part" "${DIST_DIR}/${FILE}"
    else
        rm -f "${DIST_DIR}/${FILE}.part"
        FAILED+=("dist/${FILE}")
    fi
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo "ERROR: ${FAILED[*]} could not be fetched from ${GCS_BUCKET} (Realtek tools of ${NEEDED[*]})." >&2
    echo "Googlers: ask the owners of this repository for access to ${GCS_BUCKET}." >&2
    echo "Others: get the Realtek RTL8153 programming tool (rtunicpg) and the r8152 driver source from Realtek and copy them to ${FAILED[*]}." >&2
    exit 1
fi
