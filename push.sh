#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

docker tag ui:latest us-docker.pkg.dev/chromeos-hw-tools/diagnoseme/ui:${USER}
docker tag server:latest us-docker.pkg.dev/chromeos-hw-tools/diagnoseme/server:${USER}
docker tag envoy:latest us-docker.pkg.dev/chromeos-hw-tools/diagnoseme/envoy:${USER}
docker push us-docker.pkg.dev/chromeos-hw-tools/diagnoseme/envoy:${USER}
docker push us-docker.pkg.dev/chromeos-hw-tools/diagnoseme/ui:${USER}
docker push us-docker.pkg.dev/chromeos-hw-tools/diagnoseme/server:${USER}
