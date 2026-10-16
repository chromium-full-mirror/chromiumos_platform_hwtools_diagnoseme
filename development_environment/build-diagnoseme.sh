#!/bin/bash
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

export DOCKER_BUILDKIT=1
set -e

usage() {
    echo "Usage: $0 [--edition <id>] [--maui-utils-tar <file>]"
    echo ""
    echo "  --edition <id>               DiagnoseMe edition to build (default: all)."
    echo "                               Known editions are listed in editions.json."
    echo "                               Maui editions always use the stable maui-utils"
    echo "                               release (gs://maui-firmware/docker/)."
    echo "  --maui-utils-tar <file>      developer override: local maui-utils.tar (docker"
    echo "                               save output of an unreleased build) to use"
    echo "                               instead of the stable release."
}

EDITION="all"
MAUI_UTILS_VERSION=""
MAUI_UTILS_TAR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --edition)
            EDITION="$2"
            shift 2
            ;;
        --maui-utils-tar)
            MAUI_UTILS_TAR="$2"
            shift 2
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

set -x

# Get the absolute path to the project root
PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

pushd "${PROJECT_ROOT}"

python3 tools/edition_info.py validate "${EDITION}"
# shellcheck disable=SC2207
DOCKER_TAGS=($(python3 tools/edition_info.py get "${EDITION}" docker_tags))

# Fetch RTK tools from GCS if missing
DIST_DIR="${PROJECT_ROOT}/dist"
mkdir -p "${DIST_DIR}" "${DIST_DIR}/firmware"
RTK_FILES=("rtunicpg.tar.bz2" "r8152-2.21.4.tar.bz2")
for FILE in "${RTK_FILES[@]}"; do
    if [ ! -f "${DIST_DIR}/${FILE}" ]; then
        if [ "$FILE" == "rtunicpg.tar.bz2" ]; then
            echo "Fetching rtunicpg-2.0.25.3.tar.bz2 from gs://rtk-tools/..."
            if ! gcloud storage cp "gs://rtk-tools/rtunicpg-2.0.25.3.tar.bz2" "${DIST_DIR}/${FILE}"; then
                echo "WARNING: Failed to fetch rtunicpg-2.0.25.3.tar.bz2 from gs://rtk-tools/."
                echo "Googler, speak to the owners of this repo on how to get access to the Realtek programming tools."
                echo "Non Googlers, you need to get the software from Realtek for programming ethernet chips and copy that file to the local directory as rtunicpg.tar.bz2"
            fi
        else
            echo "Fetching ${FILE} from gs://rtk-tools/..."
            gcloud storage cp "gs://rtk-tools/${FILE}" "${DIST_DIR}/${FILE}" || true
        fi
    fi
done

# Maui editions run their flashing tools in the stable maui-utils image; the
# server image is pointed at it (other editions leave MAUI_UTILS_IMAGE empty).
if [ "$(python3 tools/edition_info.py get "${EDITION}" maui_utils)" = "true" ]; then
    MAUI_UTILS_ARGS=(--load)
    if [ -n "${MAUI_UTILS_TAR}" ]; then
        MAUI_UTILS_ARGS+=(--tar "${MAUI_UTILS_TAR}")
    fi
    tools/fetch_maui_utils.sh "${MAUI_UTILS_ARGS[@]}"
    MAUI_UTILS_VERSION=$(cat "${DIST_DIR}/maui-utils/VERSION")
fi

rm -rf ./src/generated_tmp
rm -rf ./src/generated
docker build -t envoy:latest -f dockerfiles/envoy/Dockerfile .
docker build --output "./src/generated_tmp" --target copytohost -f dockerfiles/utilities/Dockerfile.protoc .

# Handle Python generated files
rm -rf ./src/server/generated/* 2>/dev/null || true
mkdir -p ./src/server/generated
cp -rf ./src/generated_tmp/python/server/generated/* ./src/server/generated/

# Handle TypeScript generated files
rm -rf ./src/ui/src/proto
mkdir -p ./src/ui/src/proto
cp -r ./src/generated_tmp/typescript/server/* ./src/ui/src/proto/

docker build -t server-base:latest --target "base-${EDITION}" \
    -f dockerfiles/rpcserver/Dockerfile.base .
docker build -t server:latest --build-arg EDITION="${EDITION}" \
    --build-arg MAUI_UTILS_VERSION="${MAUI_UTILS_VERSION}" \
    -f dockerfiles/rpcserver/Dockerfile .

mkdir -p ./src/ui/src/assets
echo "{\"buildDate\": \"$(date -u +'%Y-%m-%dT%H:%M:%SZ')\", \"commitSha\": \"$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')\", \"edition\": \"${EDITION}\"}" > ./src/ui/src/assets/build_info.json

docker build -t ui:latest --build-arg EDITION="${EDITION}" -f dockerfiles/ui/Dockerfile .
rm -rf ./src/generated_tmp

# Tag images with the edition id (plus aliases, e.g. maui-v1.1 -> :maui) and
# the matching registry tags.
for IMG in ui server envoy; do
    for TAG in "${DOCKER_TAGS[@]}"; do
        docker tag "${IMG}:latest" "${IMG}:${TAG}" 2>/dev/null || true
        docker tag "${IMG}:latest" "us-docker.pkg.dev/chromeos-hw-tools/diagnoseme/${IMG}:${TAG}" 2>/dev/null || true
    done
done

popd
