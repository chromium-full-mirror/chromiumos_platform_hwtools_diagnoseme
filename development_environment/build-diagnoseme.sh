#!/bin/bash
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

export DOCKER_BUILDKIT=1
IMAGE="servod:dev"
set -ex

SOURCE="${BASH_SOURCE[0]}"
while [ -L "${SOURCE}" ]; do # resolve $SOURCE until the file is no longer a symlink
  DIR=$( cd -P "$( dirname "${SOURCE}" )" >/dev/null 2>&1 && pwd )
  SOURCE=$(readlink "${SOURCE}")
done
DIR=$( cd -P "${DIR}/$( dirname "${SOURCE}" )" >/dev/null 2>&1 && pwd )

pushd "${DIR}/../"

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
