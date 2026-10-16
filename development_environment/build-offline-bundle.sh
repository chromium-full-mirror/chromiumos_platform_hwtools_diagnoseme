#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

EDITION="maui-v1.1"
DOCKER_TAG=""
OUTPUT_DIR=""
MAUI_UTILS_TAR=""
ZIP_OUTPUT=true
SKIP_DOCKER=false
SKIP_FW=false
SKIP_DEB=false
BUILD_LOCAL=false
USE_LOCAL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --edition)
            EDITION="$2"
            shift 2
            ;;
        --tag)
            DOCKER_TAG="$2"
            shift 2
            ;;
        --maui-utils-tar)
            MAUI_UTILS_TAR="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --skip-docker)
            SKIP_DOCKER=true
            shift
            ;;
        --skip-fw)
            SKIP_FW=true
            shift
            ;;
        --skip-deb)
            SKIP_DEB=true
            shift
            ;;
        --build-local)
            BUILD_LOCAL=true
            shift
            ;;
        --use-local|--no-pull|--skip-pull)
            USE_LOCAL=true
            shift
            ;;
        --no-zip)
            ZIP_OUTPUT=false
            shift
            ;;
        -h|--help)
            echo "Usage: build-offline-bundle.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --edition <id>      DiagnoseMe edition to bundle (default: maui-v1.1)"
            echo "  --tag <tag>         Docker image tag to export (default: the edition id)"
            echo "  --output-dir <dir>  Output directory (default: offline_update_bundle/<edition>)"
            echo "  --maui-utils-tar <file>  developer override: bundle this local maui-utils.tar"
            echo "                      (docker save output of an unreleased build) instead of"
            echo "                      the stable release (Maui editions always bundle the"
            echo "                      stable maui-utils otherwise)"
            echo "  --use-local         Use existing local Docker images without pulling from cloud"
            echo "  --build-local       Rebuild Docker images from local workspace source code first"
            echo "  --skip-docker       Skip saving Docker images"
            echo "  --skip-fw           Skip downloading Maui firmware from GCS (dist/firmware"
            echo "                      is still bundled)"
            echo "  --skip-deb          Skip building/copying Debian package"
            echo "  --no-zip            Do not generate a compressed .zip archive"
            echo "  -h, --help          Show this message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

EDITION_INFO="python3 ${PROJECT_ROOT}/tools/edition_info.py"
${EDITION_INFO} validate "${EDITION}"
if [ -n "${MAUI_UTILS_TAR}" ]; then
    MAUI_UTILS_TAR="$(realpath "${MAUI_UTILS_TAR}")"
fi
DOCKER_TAG="${DOCKER_TAG:-${EDITION}}"
OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/offline_update_bundle/${EDITION}}"
# Tag the station runs (config.env DOCKER_TAG), e.g. "maui" for maui-v1.1.
STATION_TAG="$(${EDITION_INFO} get "${EDITION}" docker_tags | cut -d' ' -f1)"
# Every station tag of the edition, e.g. "maui-v1.1 maui": stations installed
# before the editions still run DOCKER_TAG=maui.
STATION_TAGS="$(${EDITION_INFO} get "${EDITION}" docker_tags)"
FW_COMPONENTS="$(${EDITION_INFO} get "${EDITION}" firmware)"
BUNDLE_DIR="${OUTPUT_DIR}/diagnoseme-update"

echo "=================================================="
echo " Building DiagnoseMe Offline Update Bundle"
echo " Edition:      ${EDITION}"
echo " Target Tag:   ${DOCKER_TAG} (station tag :${STATION_TAG})"
echo " Destination:  ${BUNDLE_DIR}"
echo "=================================================="

rm -rf "${OUTPUT_DIR}"
mkdir -p "${BUNDLE_DIR}/images" "${BUNDLE_DIR}/deb"
for COMP in ${FW_COMPONENTS}; do
    mkdir -p "${BUNDLE_DIR}/firmware/${COMP}"
done

# 1. Export Docker Images
if [ "$SKIP_DOCKER" = false ]; then
    echo "[1/4] Resolving and Exporting Docker Images (:tag ${DOCKER_TAG})..."
    
    if [ "$BUILD_LOCAL" = true ]; then
        echo "  Rebuilding Docker images (:tag ${DOCKER_TAG}) from local workspace source..."
        pushd "${PROJECT_ROOT}" > /dev/null
        ./development_environment/build-diagnoseme.sh --edition "${EDITION}" \
            ${MAUI_UTILS_TAR:+--maui-utils-tar "${MAUI_UTILS_TAR}"}
        popd > /dev/null
    fi

    # Determine fast compression utility (multi-threaded pigz if available, else gzip)
    if command -v pigz > /dev/null 2>&1; then
        GZIP_CMD="pigz -p $(nproc 2>/dev/null || echo 4)"
        echo "  Using parallel compression: pigz ($(nproc 2>/dev/null || echo 4) cores)"
    else
        GZIP_CMD="gzip"
        echo "  Using standard compression: gzip"
    fi

    IMAGES=("ui" "server" "envoy")
    for IMG in "${IMAGES[@]}"; do
        REG_IMG="us-docker.pkg.dev/chromeos-hw-tools/diagnoseme/${IMG}:${DOCKER_TAG}"
        LOCAL_IMG="${IMG}:${DOCKER_TAG}"

        if [ "$BUILD_LOCAL" = false ] && [ "$USE_LOCAL" = false ]; then
            echo "  Pulling latest ${REG_IMG} from cloud..."
            if ! docker pull "${REG_IMG}"; then
                echo ""
                echo "ERROR: Failed to pull '${REG_IMG}' from Google Cloud Artifact Registry."
                echo "To fix:"
                echo "  1. Run: gcloud auth configure-docker us-docker.pkg.dev"
                echo "  2. Or if you want to package local images, pass --use-local or --build-local."
                exit 1
            fi
        fi

        if docker image inspect "${REG_IMG}" > /dev/null 2>&1; then
            docker tag "${REG_IMG}" "${LOCAL_IMG}" 2>/dev/null || true
        elif docker image inspect "${LOCAL_IMG}" > /dev/null 2>&1; then
            docker tag "${LOCAL_IMG}" "${REG_IMG}" 2>/dev/null || true
        elif docker image inspect "${IMG}:latest" > /dev/null 2>&1; then
            docker tag "${IMG}:latest" "${LOCAL_IMG}" 2>/dev/null || true
            docker tag "${IMG}:latest" "${REG_IMG}" 2>/dev/null || true
        else
            echo "ERROR: Docker image '${LOCAL_IMG}' not found locally."
            exit 1
        fi

        IMG_ID=$(docker image inspect --format '{{.Id}}' "${LOCAL_IMG}" 2>/dev/null | cut -d: -f2 | head -c 12 || echo "unknown")
        IMG_DATE=$(docker image inspect --format '{{.Created}}' "${LOCAL_IMG}" 2>/dev/null | cut -d. -f1 || echo "unknown")
        SAVE_IMGS=("${REG_IMG}" "${LOCAL_IMG}")
        for TAG in ${STATION_TAGS}; do
            if [ "${TAG}" != "${DOCKER_TAG}" ]; then
                STATION_IMG="us-docker.pkg.dev/chromeos-hw-tools/diagnoseme/${IMG}:${TAG}"
                docker tag "${LOCAL_IMG}" "${STATION_IMG}"
                SAVE_IMGS+=("${STATION_IMG}")
            fi
        done
        TAR_FILE="${BUNDLE_DIR}/images/${IMG}_${STATION_TAG}.tar.gz"
        echo "  -> Packaging ${LOCAL_IMG} (ID: ${IMG_ID}, Created: ${IMG_DATE}) -> ${TAR_FILE}..."
        docker save "${SAVE_IMGS[@]}" | ${GZIP_CMD} > "${TAR_FILE}"
    done
else
    echo "[1/4] Skipping Docker image export."
fi

# 2. Maui firmware: local dist/firmware, then the current versions from GCS
echo "[2/4] Preparing Maui firmware (copying local dist/firmware)..."
# The edition's firmware components (editions.json), e.g. mcu+pdc for
# v1.1 and mcu_app+mcu_full+pdc for v1.2.
for COMP in ${FW_COMPONENTS}; do
    if [ -d "${PROJECT_ROOT}/dist/firmware/${COMP}" ]; then
        cp -r "${PROJECT_ROOT}/dist/firmware/${COMP}/"* "${BUNDLE_DIR}/firmware/${COMP}/" 2>/dev/null || true
    fi
done
if [ "$SKIP_FW" = false ]; then
    echo "      Downloading the latest Maui firmware from GCS..."
    # Then the stable versions from gs://maui-firmware (the
    # stable.<maui_device>.* markers; see tools/maui_fw_fetch.py).
    if [ -n "${FW_COMPONENTS}" ]; then
        # shellcheck disable=SC2086 # one argument per component
        python3 "${PROJECT_ROOT}/tools/maui_fw_fetch.py" \
            --device "$(${EDITION_INFO} get "${EDITION}" maui_device)" \
            --dest-root "${BUNDLE_DIR}/firmware" ${FW_COMPONENTS}
    fi
else
    echo "      Skipping the Maui firmware download (--skip-fw)."
fi

# 3. Build / Package Debian Package
if [ "$SKIP_DEB" = false ]; then
    echo "[3/4] Building DiagnoseMe Debian package..."
    pushd "${PROJECT_ROOT}" > /dev/null
    # Maui editions bundle the stable maui-utils image into the deb.
    if [ "$(${EDITION_INFO} get "${EDITION}" maui_utils)" = "true" ]; then
        ./tools/fetch_maui_utils.sh ${MAUI_UTILS_TAR:+--tar "${MAUI_UTILS_TAR}"}
    fi
    docker build -t diagnoseme-deb-builder --build-arg EDITION="${EDITION}" \
        -f development_environment/deb_builder/Dockerfile .
    CONTAINER_ID=$(docker create diagnoseme-deb-builder)
    docker cp "${CONTAINER_ID}:/diagnoseme.deb" "${BUNDLE_DIR}/deb/diagnoseme_amd64.deb"
    docker rm "${CONTAINER_ID}" > /dev/null 2>&1 || true
    popd > /dev/null
    if [ ! -f "${BUNDLE_DIR}/deb/diagnoseme_amd64.deb" ]; then
        echo "ERROR: diagnoseme_amd64.deb was not generated."
        exit 1
    fi
else
    echo "[3/4] Skipping Debian package build."
fi

# 4. Generate install-update.sh and MANIFEST.json
echo "[4/4] Generating installation launcher and MANIFEST.json..."

cat <<'EOF' > "${BUNDLE_DIR}/install-update.sh"
#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (or via sudo): sudo ./install-update.sh"
    exit 1
fi

if command -v diagnoseme-offline-update > /dev/null 2>&1; then
    diagnoseme-offline-update "$DIR"
else
    # Fallback if diagnoseme-offline-update is not yet installed
    echo "Installing updated Debian package..."
    if [ -d "$DIR/deb" ]; then
        DEB_FILE=$(find "$DIR/deb" -name "*.deb" | head -n 1)
        if [ -n "$DEB_FILE" ]; then
            dpkg -i "$DEB_FILE" || apt-get install -f -y
        fi
    fi
    if command -v diagnoseme-offline-update > /dev/null 2>&1; then
        diagnoseme-offline-update "$DIR"
    else
        echo "Loading images..."
        for I in "$DIR"/images/*.tar.gz "$DIR"/images/*.tar; do
            [ -f "$I" ] && docker load -i "$I"
        done
        for COMP_DIR in "$DIR"/firmware/*/; do
            [ -d "$COMP_DIR" ] || continue
            COMP="$(basename "$COMP_DIR")"
            mkdir -p "/var/lib/diagnoseme/firmware/$COMP"
            cp -v "$COMP_DIR"* "/var/lib/diagnoseme/firmware/$COMP/" 2>/dev/null || true
        done
        echo "Starting DiagnoseMe..."
        start-diagnoseme --offline
    fi
fi
EOF
chmod +x "${BUNDLE_DIR}/install-update.sh"

# Compute SHA256 Checksums
python3 - <<EOF
import os, json, hashlib, datetime

bundle_dir = "${BUNDLE_DIR}"
manifest = {
    "version": "1.0.0",
    "build_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "edition": "${EDITION}",
    "docker_tag": "${STATION_TAG}",
    "files": {}
}

for root, _, filenames in os.walk(bundle_dir):
    for fn in sorted(filenames):
        if fn == "MANIFEST.json":
            continue
        full_path = os.path.join(root, fn)
        rel_path = os.path.relpath(full_path, bundle_dir)
        sha = hashlib.sha256()
        with open(full_path, "rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                sha.update(chunk)
        manifest["files"][rel_path] = sha.hexdigest()

with open(os.path.join(bundle_dir, "MANIFEST.json"), "w") as f:
    json.dump(manifest, f, indent=2)

print("MANIFEST.json generated with", len(manifest["files"]), "file entries.")
EOF

# 5. Compress to ZIP if requested
if [ "$ZIP_OUTPUT" = true ]; then
    ZIP_NAME="${OUTPUT_DIR}/diagnoseme-offline-bundle-${EDITION}-$(date +'%Y%m%d').zip"
    echo "Creating compressed ZIP archive: ${ZIP_NAME}..."
    pushd "${OUTPUT_DIR}" > /dev/null
    zip -r "${ZIP_NAME}" "diagnoseme-update"
    popd > /dev/null
    echo "Bundle archive ready: ${ZIP_NAME}"
fi

echo "=================================================="
echo " Offline update bundle generated successfully!"
echo " Ready to copy '${BUNDLE_DIR}' to a USB memory drive."
echo "=================================================="
