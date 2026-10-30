#!/bin/bash
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

export DOCKER_BUILDKIT=1
REGISTRY="us-docker.pkg.dev/chromeos-hw-tools/diagnoseme"
# Local development builds default to the unshipped "all" edition (every
# page and service, tagged :all); release, deb, bundle and ISO builds always
# name their edition.
EDITION="all"
MAUI_UTILS_VERSION=""
MAUI_UTILS_TAR=""
# Optional extra tag for the ui/server/envoy images, in addition to the
# edition tags (e.g. a release tag for the offline bundle and ISO builders).
EXTRA_TAG=""

usage() {
    echo "Usage: build-diagnoseme.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --edition <id>           DiagnoseMe edition to build (default: all)."
    echo "                           Known editions are listed in editions.json. The"
    echo "                           images are tagged <img>:<tag> and ${REGISTRY}/<img>:<tag>"
    echo "                           for the edition id and its aliases (servo-dolos: also release)."
    echo "                           Maui editions always use the stable maui-utils"
    echo "                           release (gs://maui-firmware/docker/)."
    echo "  --maui-utils-tar <file>  developer override: local maui-utils.tar (docker"
    echo "                           save output of an unreleased build) to use"
    echo "                           instead of the stable release."
    echo "  --tag <tag>              Also tag ui/server/envoy as <img>:<tag> and ${REGISTRY}/<img>:<tag>"
    echo "  -h, --help               Show this message"
}

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
        --tag)
            EXTRA_TAG="$2"
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

set -ex

# Get the absolute path to the project root
PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

pushd "${PROJECT_ROOT}"

python3 tools/edition_info.py validate "${EDITION}"
# Edition id plus aliases (e.g. servo-dolos release), and the optional extra tag.
read -r -a DOCKER_TAGS <<< "$(python3 tools/edition_info.py get "${EDITION}" docker_tags)"
if [ -n "${EXTRA_TAG}" ]; then
    DOCKER_TAGS+=("${EXTRA_TAG}")
fi

# The Realtek tools (Servo v4p1 tooling, editions.json realtek_tools) of the
# server image; the other editions do not need them.
DIST_DIR="${PROJECT_ROOT}/dist"
tools/fetch_rtk_tools.sh "${EDITION}"

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

# Intermediate images are named per edition (server-base:build-<edition>,
# ...), so builds of different editions can run in parallel.
BUILD_TAG="build-${EDITION}"

rm -rf ./src/generated_tmp
rm -rf ./src/generated
docker build -t "envoy:${BUILD_TAG}" -f dockerfiles/envoy/Dockerfile .
docker build --output "./src/generated_tmp" --target copytohost -f dockerfiles/utilities/Dockerfile.protoc .

# Handle Python generated files
rm -rf ./src/server/generated/* 2>/dev/null || true
mkdir -p ./src/server/generated
cp -rf ./src/generated_tmp/python/server/generated/* ./src/server/generated/

# Handle TypeScript generated files
rm -rf ./src/ui/src/proto
mkdir -p ./src/ui/src/proto
cp -r ./src/generated_tmp/typescript/server/* ./src/ui/src/proto/

docker build -t "server-base:${BUILD_TAG}" --target "base-${EDITION}" \
    -f dockerfiles/rpcserver/Dockerfile.base .
docker build -t "server:${BUILD_TAG}" --build-arg EDITION="${EDITION}" \
    --build-arg BASE_IMAGE="server-base:${BUILD_TAG}" \
    --build-arg MAUI_UTILS_VERSION="${MAUI_UTILS_VERSION}" \
    -f dockerfiles/rpcserver/Dockerfile .

mkdir -p ./src/ui/src/assets
echo "{\"buildDate\": \"$(date -u +'%Y-%m-%dT%H:%M:%SZ')\", \"commitSha\": \"$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')\", \"edition\": \"${EDITION}\"}" > ./src/ui/src/assets/build_info.json

docker build -t "ui:${BUILD_TAG}" --build-arg EDITION="${EDITION}" -f dockerfiles/ui/Dockerfile .
rm -rf ./src/generated_tmp

# Tag images with the edition id (plus aliases, e.g. servo-dolos -> :release), the
# optional extra tag and the matching registry tags.
for IMG in ui server envoy; do
    for TAG in "${DOCKER_TAGS[@]}"; do
        docker tag "${IMG}:${BUILD_TAG}" "${IMG}:${TAG}"
        docker tag "${IMG}:${BUILD_TAG}" "${REGISTRY}/${IMG}:${TAG}"
    done
    # :latest of the development docker-compose.yaml and push.sh: the last
    # finished build (only tagged here, never read by a build step).
    docker tag "${IMG}:${BUILD_TAG}" "${IMG}:latest"
done

popd
