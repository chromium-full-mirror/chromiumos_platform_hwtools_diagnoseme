#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Fetch the maui-utils Docker image used by the Maui editions.
#
# maui-utils releases live in gs://maui-firmware/docker/<version>/maui-utils.tar
# and the stable release is marked by docker/<version>/stable.dockertag.
# Builds always take the stable release (no version option). The download
# goes through tools/maui_fw_fetch.py (the same stable-marker resolution,
# paginated listing, numeric version order, hash check and atomic directory
# replacement as the firmware): it writes dist/maui-utils/maui-utils.tar and
# dist/maui-utils/STABLE.json {version, file, md5, source}, which the deb
# bundles so that start-diagnoseme can check the md5 before `docker load`.
# dist/maui-utils/VERSION repeats the version for the build scripts; --load
# loads the image as maui-utils:<version>.
#
# Developer override (unreleased local builds only): --tar <file> (or
# MAUI_UTILS_TAR=<file>) installs a local `docker save` tar instead; its
# version is the maui-utils:<version> tag inside the tar.
#
# Usage: tools/fetch_maui_utils.sh [--tar <maui-utils.tar>] [--load]

set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DIST_DIR="${PROJECT_ROOT}/dist"
OUT_DIR="${DIST_DIR}/maui-utils"
FETCH=(python3 "${PROJECT_ROOT}/tools/maui_fw_fetch.py" --dest-root "${DIST_DIR}")
VERSION=""
# A local maui-utils.tar (unreleased build) instead of the stable release.
TAR="${MAUI_UTILS_TAR:-}"
LOAD=false

usage() {
    echo "Usage: $0 [--tar <file>] [--load]"
    echo ""
    echo "Fetches the stable maui-utils release (docker/<version>/stable.dockertag)."
    echo "  --tar <file>  developer override: install this local maui-utils.tar (docker"
    echo "                save output of an unreleased build, tagged"
    echo "                maui-utils:<version>) instead. Also MAUI_UTILS_TAR=<file>."
    echo "  --load        docker load the image as maui-utils:<version>"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tar)
            TAR="$2"
            shift 2
            ;;
        --load)
            LOAD=true
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

if [ -n "${TAR}" ]; then
    if [ ! -f "${TAR}" ]; then
        echo "ERROR: maui-utils tar ${TAR} does not exist" >&2
        exit 1
    fi
    # Version of the image in the tar (RepoTags of the docker save manifest).
    VERSION=$(python3 -c '
import json, sys, tarfile
try:
    with tarfile.open(sys.argv[1]) as tar:
        manifest = json.load(tar.extractfile("manifest.json"))
except (OSError, KeyError, ValueError, tarfile.TarError):
    sys.exit(0)
tags = [t for m in manifest for t in (m.get("RepoTags") or [])]
versions = [t.split(":", 1)[1] for t in tags if t.startswith("maui-utils:")]
print(versions[0] if versions else "")
' "${TAR}")
    if [ -z "${VERSION}" ]; then
        echo "ERROR: no maui-utils:<version> tag in ${TAR}" >&2
        exit 1
    fi
    echo "WARNING: using the local maui-utils:${VERSION} from ${TAR} instead of the stable release" >&2
    if [ "$(realpath "${TAR}")" = "$(realpath -m "${OUT_DIR}/maui-utils.tar")" ]; then
        # Installing dist/maui-utils/maui-utils.tar onto itself: copy it out
        # first, the directory is replaced as a whole.
        TMP_TAR="$(mktemp -d)/maui-utils.tar"
        cp "${TAR}" "${TMP_TAR}"
        TAR="${TMP_TAR}"
    fi
    "${FETCH[@]}" --local-file "${TAR}" --local-version "${VERSION}" maui-utils
else
    # Stable release; skipped when STABLE.json already matches the bucket.
    "${FETCH[@]}" maui-utils
    VERSION=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' \
        "${OUT_DIR}/STABLE.json")
fi
echo "${VERSION}" > "${OUT_DIR}/VERSION"
echo "Using maui-utils version ${VERSION} ($(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["md5"])' "${OUT_DIR}/STABLE.json"))"

if [ "${LOAD}" = true ]; then
    LOADED=$(docker load -i "${OUT_DIR}/maui-utils.tar" | sed -n 's/^Loaded image[^:]*: //p' | tail -n 1)
    if [ -z "${LOADED}" ]; then
        echo "ERROR: could not determine the image loaded from maui-utils.tar" >&2
        exit 1
    fi
    docker tag "${LOADED}" "maui-utils:${VERSION}"
    echo "Loaded ${LOADED} as maui-utils:${VERSION}"
fi
