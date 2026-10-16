#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Promote a released DiagnoseMe edition build to the station update channel.
#
# Release builds (dockerfiles/cloudbuild_release.yaml) push images tagged
# <edition>-<commit>. Stations pull :<edition> (and legacy aliases such as
# :maui for maui-v1.1, see editions.json). This script moves those tags to the
# given commit for the ui, server and envoy images.
#
# Usage: tools/promote.sh --edition <id> --commit <sha> [--dry-run]

set -euo pipefail

REGISTRY="us-docker.pkg.dev/chromeos-hw-tools/diagnoseme"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
EDITION=""
COMMIT=""
DRY_RUN=false

usage() {
    echo "Usage: $0 --edition <id> --commit <sha> [--dry-run]"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --edition)
            EDITION="$2"
            shift 2
            ;;
        --commit)
            COMMIT="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

if [ -z "${EDITION}" ] || [ -z "${COMMIT}" ]; then
    usage
    exit 1
fi

python3 "${SCRIPT_DIR}/edition_info.py" validate "${EDITION}"
if [ "$(python3 "${SCRIPT_DIR}/edition_info.py" get "${EDITION}" shipped)" != "true" ]; then
    echo "Edition '${EDITION}' is not shipped and cannot be promoted."
    exit 1
fi
# shellcheck disable=SC2207
TAGS=($(python3 "${SCRIPT_DIR}/edition_info.py" get "${EDITION}" docker_tags))

for IMG in ui server envoy; do
    SRC="${REGISTRY}/${IMG}:${EDITION}-${COMMIT}"
    for TAG in "${TAGS[@]}"; do
        echo "Tagging ${SRC} as ${REGISTRY}/${IMG}:${TAG}"
        if [ "${DRY_RUN}" = false ]; then
            gcloud artifacts docker tags add "${SRC}" "${REGISTRY}/${IMG}:${TAG}"
        fi
    done
done
