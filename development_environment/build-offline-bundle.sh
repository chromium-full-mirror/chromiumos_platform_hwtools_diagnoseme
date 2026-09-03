#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DOCKER_TAG="maui"
OUTPUT_DIR="${PROJECT_ROOT}/offline_update_bundle"
ZIP_OUTPUT=true
SKIP_DOCKER=false
SKIP_FW=false
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

BUNDLE_DIR="${OUTPUT_DIR}/diagnoseme-update"

echo "=================================================="
echo " Building DiagnoseMe Offline Update Bundle"
echo " Target Tag:   ${DOCKER_TAG}"
echo " Destination:  ${BUNDLE_DIR}"
echo "=================================================="

rm -rf "${OUTPUT_DIR}"
mkdir -p "${BUNDLE_DIR}/images" "${BUNDLE_DIR}/deb" "${BUNDLE_DIR}/firmware/mcu" "${BUNDLE_DIR}/firmware/pdc"

# 1. Export Docker Images
if [ "$SKIP_DOCKER" = false ]; then
    echo "[1/4] Resolving and Exporting Docker Images (:tag ${DOCKER_TAG})..."
    
    if [ "$BUILD_LOCAL" = true ]; then
        echo "  Rebuilding Docker images (:tag ${DOCKER_TAG}) from local workspace source..."
        pushd "${PROJECT_ROOT}" > /dev/null
        ./development_environment/build-diagnoseme.sh
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
        TAR_FILE="${BUNDLE_DIR}/images/${IMG}_${DOCKER_TAG}.tar.gz"
        echo "  -> Packaging ${LOCAL_IMG} (ID: ${IMG_ID}, Created: ${IMG_DATE}) -> ${TAR_FILE}..."
        docker save "${REG_IMG}" "${LOCAL_IMG}" | ${GZIP_CMD} > "${TAR_FILE}"
    done
else
    echo "[1/4] Skipping Docker image export."
fi

# 2. Fetch Maui Firmware from GCS
if [ "$SKIP_FW" = false ]; then
    echo "[2/4] Preparing Maui firmware (copying local / downloading latest stable from GCS)..."
    if [ -d "${PROJECT_ROOT}/dist/firmware/mcu" ]; then
        cp -r "${PROJECT_ROOT}/dist/firmware/mcu/"* "${BUNDLE_DIR}/firmware/mcu/" 2>/dev/null || true
    fi
    if [ -d "${PROJECT_ROOT}/dist/firmware/pdc" ]; then
        cp -r "${PROJECT_ROOT}/dist/firmware/pdc/"* "${BUNDLE_DIR}/firmware/pdc/" 2>/dev/null || true
    fi
    python3 - <<'EOF'
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET

GCS_BASE_URL = "https://storage.googleapis.com/maui-firmware"
OUTPUT_DIR = os.environ.get("BUNDLE_DIR", "offline_update_bundle/diagnoseme-update")

def resolve_and_download(comp_dir, tag_suffix, file_ext, dest_dir):
    list_url = f"{GCS_BASE_URL}?prefix={comp_dir}/"
    print(f"Resolving 'stable' in {list_url}...")
    try:
        req = urllib.request.Request(list_url, headers={"User-Agent": "DiagnoseMe-BundleBuilder"})
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_content = response.read()
        root = ET.fromstring(xml_content)
        
        target_dir_key = None
        for child in root:
            if child.tag.endswith("Contents"):
                for sub in child:
                    if sub.tag.endswith("Key") and sub.text:
                        if sub.text.endswith(f"/stable{tag_suffix}"):
                            target_dir_key = os.path.dirname(sub.text)
                            break
                if target_dir_key:
                    break

        if not target_dir_key:
            print(f"WARNING: 'stable' tag marker not found in {comp_dir}. Skipping.")
            return

        print(f"Resolved 'stable' {comp_dir} -> {target_dir_key}")
        # Find the binary in target dir
        sub_list_url = f"{GCS_BASE_URL}?prefix={target_dir_key}/"
        with urllib.request.urlopen(urllib.request.Request(sub_list_url), timeout=15) as response:
            sub_root = ET.fromstring(response.read())

        bin_key = None
        for child in sub_root:
            if child.tag.endswith("Contents"):
                for sub in child:
                    if sub.tag.endswith("Key") and sub.text and sub.text.endswith(file_ext):
                        bin_key = sub.text
                        break
                if bin_key:
                    break

        if not bin_key:
            print(f"WARNING: Binary ending in {file_ext} not found in {target_dir_key}.")
            return

        bin_url = f"{GCS_BASE_URL}/{bin_key}"
        bin_filename = os.path.basename(bin_key)
        out_path = os.path.join(dest_dir, bin_filename)
        os.makedirs(dest_dir, exist_ok=True)
        
        print(f"Downloading {bin_url} -> {out_path}...")
        with urllib.request.urlopen(bin_url, timeout=30) as r, open(out_path, "wb") as f:
            f.write(r.read())
        print(f"Downloaded successfully: {bin_filename}")

    except Exception as e:
        print(f"WARNING: Failed to download firmware for {comp_dir}: {e}")

resolve_and_download("mcu-fw", ".mcufw", ".txt", f"{OUTPUT_DIR}/firmware/mcu")
resolve_and_download("pdc-fw", ".pdcfw", ".bin", f"{OUTPUT_DIR}/firmware/pdc")
EOF
else
    echo "[2/4] Skipping Maui firmware download."
fi

# 3. Build / Package Debian Package
if [ "$SKIP_DEB" = false ]; then
    echo "[3/4] Building DiagnoseMe Debian package..."
    pushd "${PROJECT_ROOT}" > /dev/null
    docker build -t diagnoseme-deb-builder -f development_environment/deb_builder/Dockerfile . || {
        echo "WARNING: Docker build for deb package failed. Checking for existing .deb..."
    }
    CONTAINER_ID=$(docker create diagnoseme-deb-builder || true)
    if [ -n "$CONTAINER_ID" ]; then
        docker cp "${CONTAINER_ID}:/diagnoseme.deb" "${BUNDLE_DIR}/deb/diagnoseme_amd64.deb" || true
        docker rm "${CONTAINER_ID}" > /dev/null 2>&1 || true
    fi
    popd > /dev/null
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
        mkdir -p /var/lib/diagnoseme/firmware/mcu /var/lib/diagnoseme/firmware/pdc
        [ -d "$DIR/firmware/mcu" ] && cp -v "$DIR"/firmware/mcu/* /var/lib/diagnoseme/firmware/mcu/ 2>/dev/null || true
        [ -d "$DIR/firmware/pdc" ] && cp -v "$DIR"/firmware/pdc/* /var/lib/diagnoseme/firmware/pdc/ 2>/dev/null || true
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
    "docker_tag": "${DOCKER_TAG}",
    "files": {}
}

for root, _, filenames in os.walk(bundle_dir):
    for fn in sorted(filenames):
        if fn == "MANIFEST.json":
            continue
        full_path = os.path.join(root, fn)
        rel_path = os.path.relpath(full_path, bundle_dir)
        with open(full_path, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        manifest["files"][rel_path] = h

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
