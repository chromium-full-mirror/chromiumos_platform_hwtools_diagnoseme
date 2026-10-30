#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEB_BUILDER_DIR="${SCRIPT_DIR}/deb_builder"
REGISTRY="us-docker.pkg.dev/chromeos-hw-tools/diagnoseme"

DOCKER_TAG="maui"
OUTPUT_DIR="${PROJECT_ROOT}/offline_update_bundle"
ZIP_OUTPUT=true
SKIP_DOCKER=false
SKIP_FW=false
ALLOW_MISSING_FW=false
SKIP_DEB=false
BUILD_LOCAL=false
USE_LOCAL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)
            DOCKER_TAG="$2"
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
        --allow-missing-fw)
            ALLOW_MISSING_FW=true
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
            echo "  --tag <tag>         Docker image tag to export (default: maui)"
            echo "  --output-dir <dir>  Output directory for bundle (default: offline_update_bundle)"
            echo "  --use-local         Use existing local Docker images without pulling from cloud"
            echo "  --build-local       Rebuild Docker images from local workspace source code first"
            echo "  --skip-docker       Skip saving Docker images"
            echo "  --skip-fw           Skip downloading Maui firmware from GCS"
            echo "  --allow-missing-fw  Warn instead of failing when no MCU/PDC firmware was staged"
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

if [ -z "${OUTPUT_DIR}" ] || [ "${OUTPUT_DIR}" = "/" ]; then
    echo "ERROR: Refusing to use '${OUTPUT_DIR}' as output directory."
    exit 1
fi

rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"
# Use an absolute path: later steps change the working directory.
OUTPUT_DIR="$(cd "${OUTPUT_DIR}" && pwd)"
BUNDLE_DIR="${OUTPUT_DIR}/diagnoseme-update"

echo "=================================================="
echo " Building DiagnoseMe Offline Update Bundle"
echo " Target Tag:   ${DOCKER_TAG}"
echo " Destination:  ${BUNDLE_DIR}"
echo "=================================================="

mkdir -p "${BUNDLE_DIR}/images" "${BUNDLE_DIR}/deb" "${BUNDLE_DIR}/firmware/mcu" "${BUNDLE_DIR}/firmware/pdc"

# Makes sure the image <img>:<tag> exists under both its local and registry
# name, pulling it from the registry first unless local images are requested.
resolve_image() {
    local img="$1"
    local reg_img="${REGISTRY}/${img}:${DOCKER_TAG}"
    local local_img="${img}:${DOCKER_TAG}"

    if [ "$BUILD_LOCAL" = false ] && [ "$USE_LOCAL" = false ]; then
        echo "  Pulling latest ${reg_img} from cloud..."
        if ! docker pull "${reg_img}"; then
            echo ""
            echo "ERROR: Failed to pull '${reg_img}' from Google Cloud Artifact Registry."
            echo "To fix:"
            echo "  1. Run: gcloud auth configure-docker us-docker.pkg.dev"
            echo "  2. Or if you want to package local images, pass --use-local or --build-local."
            exit 1
        fi
    fi

    if docker image inspect "${reg_img}" > /dev/null 2>&1; then
        docker tag "${reg_img}" "${local_img}" 2>/dev/null || true
    elif docker image inspect "${local_img}" > /dev/null 2>&1; then
        docker tag "${local_img}" "${reg_img}" 2>/dev/null || true
    elif docker image inspect "${img}:latest" > /dev/null 2>&1; then
        docker tag "${img}:latest" "${local_img}" 2>/dev/null || true
        docker tag "${img}:latest" "${reg_img}" 2>/dev/null || true
    else
        echo "ERROR: Docker image '${local_img}' not found locally."
        exit 1
    fi
}

# 1. Export Docker Images
if [ "$SKIP_DOCKER" = false ]; then
    echo "[1/4] Resolving and Exporting Docker Images (:tag ${DOCKER_TAG})..."

    if [ "$BUILD_LOCAL" = true ]; then
        echo "  Rebuilding Docker images (:tag ${DOCKER_TAG}) from local workspace source..."
        "${PROJECT_ROOT}/development_environment/build-diagnoseme.sh" --tag "${DOCKER_TAG}"
    fi

    # Determine fast compression utility (multi-threaded pigz if available, else gzip)
    if command -v pigz > /dev/null 2>&1; then
        GZIP_CMD=(pigz -p "$(nproc 2>/dev/null || echo 4)")
        echo "  Using parallel compression: pigz ($(nproc 2>/dev/null || echo 4) cores)"
    else
        GZIP_CMD=(gzip)
        echo "  Using standard compression: gzip"
    fi

    for IMG in ui server envoy; do
        resolve_image "${IMG}"
        REG_IMG="${REGISTRY}/${IMG}:${DOCKER_TAG}"
        LOCAL_IMG="${IMG}:${DOCKER_TAG}"
        IMG_ID=$(docker image inspect --format '{{.Id}}' "${LOCAL_IMG}" 2>/dev/null | cut -d: -f2 | head -c 12 || echo "unknown")
        IMG_DATE=$(docker image inspect --format '{{.Created}}' "${LOCAL_IMG}" 2>/dev/null | cut -d. -f1 || echo "unknown")
        TAR_FILE="${BUNDLE_DIR}/images/${IMG}_${DOCKER_TAG}.tar.gz"
        echo "  -> Packaging ${LOCAL_IMG} (ID: ${IMG_ID}, Created: ${IMG_DATE}) -> ${TAR_FILE}..."
        docker save "${REG_IMG}" "${LOCAL_IMG}" | "${GZIP_CMD[@]}" > "${TAR_FILE}"
    done
else
    echo "[1/4] Skipping Docker image export."
fi

# 2. Fetch Maui Firmware from GCS
if [ "$SKIP_FW" = false ]; then
    echo "[2/4] Preparing Maui firmware (copying local / downloading latest stable from GCS)..."
    for FW_KIND in mcu pdc; do
        if [ -d "${PROJECT_ROOT}/dist/firmware/${FW_KIND}" ]; then
            cp -r "${PROJECT_ROOT}/dist/firmware/${FW_KIND}/"* "${BUNDLE_DIR}/firmware/${FW_KIND}/" 2>/dev/null || true
        fi
    done
    python3 "${DEB_BUILDER_DIR}/fetch-maui-firmware" --dest "${BUNDLE_DIR}/firmware" \
        || echo "WARNING: Downloading the latest stable Maui firmware failed."

    for FW_KIND in mcu pdc; do
        if [ -z "$(ls -A "${BUNDLE_DIR}/firmware/${FW_KIND}")" ]; then
            if [ "$ALLOW_MISSING_FW" = true ]; then
                echo "WARNING: No ${FW_KIND^^} firmware in ${BUNDLE_DIR}/firmware/${FW_KIND}."
            else
                echo "ERROR: No ${FW_KIND^^} firmware in ${BUNDLE_DIR}/firmware/${FW_KIND}."
                echo "Put the firmware into dist/firmware/${FW_KIND}/ or pass --skip-fw / --allow-missing-fw."
                exit 1
            fi
        fi
    done
else
    echo "[2/4] Skipping Maui firmware download."
fi

# 3. Build / Package Debian Package
if [ "$SKIP_DEB" = false ]; then
    echo "[3/4] Building DiagnoseMe Debian package (Maui station profile)..."
    pushd "${PROJECT_ROOT}" > /dev/null
    docker build --build-arg STATION_PROFILE=maui -t diagnoseme-deb-builder -f development_environment/deb_builder/Dockerfile .
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

# Ship the offline update tool with the bundle, so stations without (or with
# an older) diagnoseme package can apply it.
install -m 755 "${DEB_BUILDER_DIR}/diagnoseme-offline-update" "${BUNDLE_DIR}/diagnoseme-offline-update"

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

exec bash "$DIR/diagnoseme-offline-update" "$DIR"
EOF
chmod +x "${BUNDLE_DIR}/install-update.sh"

# Compute SHA256 Checksums
BUNDLE_DIR="${BUNDLE_DIR}" DOCKER_TAG="${DOCKER_TAG}" python3 - <<'EOF'
import datetime
import hashlib
import json
import os

bundle_dir = os.environ["BUNDLE_DIR"]
manifest = {
    "version": "1.0.0",
    "build_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "docker_tag": os.environ["DOCKER_TAG"],
    "files": {}
}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


for root, _, filenames in os.walk(bundle_dir):
    for fn in sorted(filenames):
        if fn == "MANIFEST.json":
            continue
        full_path = os.path.join(root, fn)
        rel_path = os.path.relpath(full_path, bundle_dir)
        manifest["files"][rel_path] = sha256_file(full_path)

with open(os.path.join(bundle_dir, "MANIFEST.json"), "w") as f:
    json.dump(manifest, f, indent=2)

print("MANIFEST.json generated with", len(manifest["files"]), "file entries.")
EOF

# 5. Compress to ZIP if requested
if [ "$ZIP_OUTPUT" = true ]; then
    ZIP_NAME="${OUTPUT_DIR}/diagnoseme-offline-bundle-${DOCKER_TAG}-$(date +'%Y%m%d').zip"
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
