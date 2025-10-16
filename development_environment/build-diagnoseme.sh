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
