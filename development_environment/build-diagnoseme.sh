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

rm -rf ./src/generated_tmp
rm -rf ./src/generated
docker build -t envoy:latest -f dockerfiles/envoy/Dockerfile .
docker build --output "./src/generated_tmp" --target copytohost -f dockerfiles/utilities/Dockerfile.protoc .

# Handle Python generated files
rm -rf ./src/server/generated
mkdir -p ./src/server/generated
cp -r ./src/generated_tmp/python/server/generated/* ./src/server/generated/

# Handle TypeScript generated files
rm -rf ./src/ui/src/proto
mkdir -p ./src/ui/src/proto
cp -r ./src/generated_tmp/typescript/server/* ./src/ui/src/proto/

docker build -t server-base:latest -f dockerfiles/rpcserver/Dockerfile.base .
docker build -t server:latest -f dockerfiles/rpcserver/Dockerfile .

mkdir -p ./src/ui/src/assets
echo "{\"buildDate\": \"$(date -u +'%Y-%m-%dT%H:%M:%SZ')\", \"commitSha\": \"$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')\"}" > ./src/ui/src/assets/build_info.json

docker build -t ui:latest -f dockerfiles/ui/Dockerfile .
rm -rf ./src/generated_tmp

popd
