#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Fetch the maui-utils Docker image used by the Maui editions.
#
# maui-utils releases live in gs://maui-firmware/docker/<version>/maui-utils.tar
# and the stable release is marked by docker/<version>/stable.dockertag.
# Builds always take the stable release (no version option); the script
# downloads it to dist/maui-utils/maui-utils.tar, writes
# dist/maui-utils/VERSION and optionally loads it as maui-utils:<version>.
#
# Developer override (unreleased local builds only): --tar <file> (or
# MAUI_UTILS_TAR=<file>) copies a local `docker save` tar instead; its
# version is the maui-utils:<version> tag inside the tar.
#
# Usage: tools/fetch_maui_utils.sh [--tar <maui-utils.tar>] [--load]

set -euo pipefail

# The bucket is publicly readable; plain HTTPS avoids a gcloud login.
GCS_HTTP="https://storage.googleapis.com/maui-firmware"
PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUT_DIR="${PROJECT_ROOT}/dist/maui-utils"
VERSION=""
# A local maui-utils.tar (unreleased build) instead of the stable release.
TAR="${MAUI_UTILS_TAR:-}"
LOAD=false

usage() {
    echo "Usage: $0 [--tar <file>] [--load]"
    echo ""
    echo "Fetches the stable maui-utils release (docker/<version>/stable.dockertag)."
    echo "  --tar <file>  developer override: copy this local maui-utils.tar (docker"
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
else
    VERSION=$(curl -fsSL "${GCS_HTTP}?prefix=docker/" | python3 -c '
import re, sys
keys = re.findall(r"<Key>docker/([^/<]+)/stable\.dockertag</Key>", sys.stdin.read())
print(sorted(keys)[-1] if keys else "")
')
    if [ -z "${VERSION}" ]; then
        echo "ERROR: No docker/<version>/stable.dockertag marker found in ${GCS_HTTP}" >&2
        exit 1
    fi
fi
echo "Using maui-utils version ${VERSION}${TAR:+ from ${TAR}}"

mkdir -p "${OUT_DIR}"
if [ -n "${TAR}" ]; then
    if [ "$(realpath "${TAR}")" != "$(realpath -m "${OUT_DIR}/maui-utils.tar")" ]; then
        cp "${TAR}" "${OUT_DIR}/maui-utils.tar.tmp"
        mv "${OUT_DIR}/maui-utils.tar.tmp" "${OUT_DIR}/maui-utils.tar"
    fi
    echo "${VERSION}" > "${OUT_DIR}/VERSION"
elif [ ! -f "${OUT_DIR}/maui-utils.tar" ] || [ "$(cat "${OUT_DIR}/VERSION" 2>/dev/null)" != "${VERSION}" ]; then
    curl -fSL "${GCS_HTTP}/docker/${VERSION}/maui-utils.tar" -o "${OUT_DIR}/maui-utils.tar.tmp"
    mv "${OUT_DIR}/maui-utils.tar.tmp" "${OUT_DIR}/maui-utils.tar"
    echo "${VERSION}" > "${OUT_DIR}/VERSION"
fi

if [ "${LOAD}" = true ]; then
    LOADED=$(docker load -i "${OUT_DIR}/maui-utils.tar" | sed -n 's/^Loaded image[^:]*: //p' | tail -n 1)
    if [ -z "${LOADED}" ]; then
        echo "ERROR: could not determine the image loaded from maui-utils.tar" >&2
        exit 1
    fi
    docker tag "${LOADED}" "maui-utils:${VERSION}"
    echo "Loaded ${LOADED} as maui-utils:${VERSION}"
fi
