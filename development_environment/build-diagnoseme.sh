#!/bin/bash
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

export DOCKER_BUILDKIT=1
IMAGE="servod:dev"
set -ex

# Get the absolute path to the project root
PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

pushd "${PROJECT_ROOT}"

# Fetch RTK tools from GCS if missing
DIST_DIR="${PROJECT_ROOT}/dist"
mkdir -p "${DIST_DIR}"
RTK_FILES=("rtunicpg.tar.bz2" "r8152-2.21.4.tar.bz2")
for FILE in "${RTK_FILES[@]}"; do
    if [ ! -f "${DIST_DIR}/${FILE}" ]; then
        echo "Fetching ${FILE} from gs://rtk-tools/..."
        gsutil cp "gs://rtk-tools/${FILE}" "${DIST_DIR}/${FILE}"
    fi
done

rm -rf ./src/generated
docker build -t envoy:latest -f dockerfiles/envoy/Dockerfile .
docker build --output "./src/generated" --target copytohost -f dockerfiles/utilities/Dockerfile.protoc .
docker build -t server:latest -f dockerfiles/rpcserver/Dockerfile .
rm -rf ./src/ui/src/proto
mkdir -p ./src/ui/src/proto
cp ./src/generated/* ./src/ui/src/proto
docker build -t ui:latest -f dockerfiles/ui/Dockerfile .
rm -rf ./src/generated

popd
