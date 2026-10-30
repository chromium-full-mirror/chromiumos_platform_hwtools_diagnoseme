#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Prints the version of the DiagnoseMe station debs: 1.1.<YYYYMMDD> (UTC
# build date). The single source of the deb version of the release, CQ,
# deb_builder/cloudbuild.yaml, offline bundle and ISO builds.
#
# 1.1, not 1.0: the apt repository already holds 'diagnoseme'
# 1.0.<unix seconds> (e.g. 1.0.1792746882), which sorts above
# 1.0.<YYYYMMDD> and would stop the apt upgrades of the stations. Two builds
# of the same day get the same version.
#
# DEB_VERSION_DATE=<YYYYMMDD> overrides the date (tests).

set -euo pipefail

DEB_VERSION_PREFIX="1.1"
BUILD_DATE="${DEB_VERSION_DATE:-$(date -u +%Y%m%d)}"
if [[ ! "${BUILD_DATE}" =~ ^[0-9]{8}$ ]]; then
    echo "ERROR: invalid deb version date '${BUILD_DATE}' (expected YYYYMMDD)." >&2
    exit 1
fi
echo "${DEB_VERSION_PREFIX}.${BUILD_DATE}"
