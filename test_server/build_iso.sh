#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
set -e

# Configuration
ISO_NAME="debian-13-amd64-netinst.iso"
# Pointing to a pinned, verified Debian 13 (Trixie) ISO hosted in our GCS bucket
ISO_GCS_PATH="gs://chromeos-hw-tools-assets/debian-13-trixie-verified.iso"
BUILD_DIR="iso_build"
NEW_ISO="custom-debian-preseed.iso"
DOCKER_TAG="maui"
BUILD_LOCAL=false
USE_LOCAL=false

# Parse options
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)
            DOCKER_TAG="$2"
            shift 2
            ;;
        --build-local)
            BUILD_LOCAL=true
            shift
            ;;
        --use-local|--skip-pull|--no-pull)
            USE_LOCAL=true
            shift
            ;;
        -h|--help)
            echo "Usage: ./build_iso.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --tag <tag>         Docker image tag to package (default: maui)"
            echo "  --use-local         Use existing local Docker images without pulling from cloud"
            echo "  --build-local       Rebuild Docker images from local workspace source code first"
            echo "  -h, --help          Show this message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# 1. Download ISO
echo "Downloading pinned Debian testing netinst ISO from GCS..."
if [ ! -f "$ISO_NAME" ]; then
    gcloud storage cp "$ISO_GCS_PATH" "$ISO_NAME"
fi

# 2. Extract ISO
echo "Extracting ISO to $BUILD_DIR..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
if command -v bsdtar > /dev/null 2>&1; then
    bsdtar -C "$BUILD_DIR" -xf "$ISO_NAME"
elif command -v 7z > /dev/null 2>&1; then
    7z x -o"$BUILD_DIR" -y "$ISO_NAME"
elif command -v docker > /dev/null 2>&1; then
    docker run --rm -v "$(pwd):/work" -w /work debian:trixie-slim sh -c "apt-get update && apt-get install -y libarchive-tools && bsdtar -C '$BUILD_DIR' -xf '$ISO_NAME'"
else
    echo "Error: Neither bsdtar, 7z, nor docker is available to extract ISO."
    exit 1
fi
chmod -R +w "$BUILD_DIR"

# 3. Inject Preseed and Offline Assets
echo "Injecting preseed.cfg..."
cp preseed.cfg "$BUILD_DIR/"

echo "Embedding offline assets (Docker images :${DOCKER_TAG}, Maui firmware, Debian package)..."
OFFLINE_ASSETS_DIR="$BUILD_DIR/offline_assets"
mkdir -p "$OFFLINE_ASSETS_DIR/images" "$OFFLINE_ASSETS_DIR/deb" "$OFFLINE_ASSETS_DIR/firmware/mcu" "$OFFLINE_ASSETS_DIR/firmware/pdc"

# Copy local firmware from dist/firmware if present
if [ -d "../dist/firmware/mcu" ]; then
    cp -r ../dist/firmware/mcu/* "$OFFLINE_ASSETS_DIR/firmware/mcu/" 2>/dev/null || true
fi
if [ -d "../dist/firmware/pdc" ]; then
    cp -r ../dist/firmware/pdc/* "$OFFLINE_ASSETS_DIR/firmware/pdc/" 2>/dev/null || true
fi

# Fetch latest stable Maui firmware from GCS (updates if online)
python3 - <<'EOF'
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET

GCS_BASE_URL = "https://storage.googleapis.com/maui-firmware"
OFFLINE_DIR = "iso_build/offline_assets"

def resolve_and_download(comp_dir, tag_suffix, file_ext, dest_dir):
    list_url = f"{GCS_BASE_URL}?prefix={comp_dir}/"
    try:
        req = urllib.request.Request(list_url, headers={"User-Agent": "DiagnoseMe-ISOBuilder"})
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_content = response.read()
        root = ET.fromstring(xml_content)
        
        target_dir_key = None
        for child in root:
            if child.tag.endswith("Contents"):
                for sub in child:
                    if sub.tag.endswith("Key") and sub.text and sub.text.endswith(f"/stable{tag_suffix}"):
                        target_dir_key = os.path.dirname(sub.text)
                        break
                if target_dir_key:
                    break

        if not target_dir_key:
            print(f"Note: 'stable' tag not found in {comp_dir}. Skipping.")
            return

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
            return

        bin_url = f"{GCS_BASE_URL}/{bin_key}"
        bin_filename = os.path.basename(bin_key)
        out_path = os.path.join(dest_dir, bin_filename)
        os.makedirs(dest_dir, exist_ok=True)
        
        print(f"Downloading {bin_url} -> {out_path}...")
        with urllib.request.urlopen(bin_url, timeout=30) as r, open(out_path, "wb") as f:
            f.write(r.read())
        print(f"Downloaded: {bin_filename}")

    except Exception as e:
        print(f"Warning: Could not fetch firmware for {comp_dir}: {e}")

resolve_and_download("mcu-fw", ".mcufw", ".txt", f"{OFFLINE_DIR}/firmware/mcu")
resolve_and_download("pdc-fw", ".pdcfw", ".bin", f"{OFFLINE_DIR}/firmware/pdc")
EOF

# Build local images if requested
if [ "$BUILD_LOCAL" = true ]; then
    echo "Rebuilding Docker images (:tag ${DOCKER_TAG}) from local workspace source..."
    pushd .. > /dev/null
    ./development_environment/build-diagnoseme.sh
    popd > /dev/null
fi

# Copy/save Docker images
echo "Resolving Docker images (:tag ${DOCKER_TAG})..."
for IMG in ui server envoy; do
    REG_IMG="us-docker.pkg.dev/chromeos-hw-tools/diagnoseme/${IMG}:${DOCKER_TAG}"
    LOCAL_IMG="${IMG}:${DOCKER_TAG}"

    if [ "$BUILD_LOCAL" = false ] && [ "$USE_LOCAL" = false ]; then
        echo "Pulling latest ${REG_IMG} from cloud..."
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
    echo "  -> Packaging ${LOCAL_IMG} (ID: ${IMG_ID}, Created: ${IMG_DATE})"
    docker save "${REG_IMG}" "${LOCAL_IMG}" | gzip > "$OFFLINE_ASSETS_DIR/images/${IMG}_${DOCKER_TAG}.tar.gz"
done

# Build and copy Debian package
echo "Building and embedding DiagnoseMe Debian package..."
mkdir -p "$OFFLINE_ASSETS_DIR/deb"
docker build -t diagnoseme-deb-builder -f ../development_environment/deb_builder/Dockerfile ..
CONTAINER_ID=$(docker create diagnoseme-deb-builder)
docker cp "${CONTAINER_ID}:/diagnoseme.deb" "$OFFLINE_ASSETS_DIR/deb/diagnoseme_amd64.deb"
docker rm "${CONTAINER_ID}" > /dev/null 2>&1
if [ ! -f "$OFFLINE_ASSETS_DIR/deb/diagnoseme_amd64.deb" ]; then
    echo "Error: diagnoseme_amd64.deb was not generated."
    exit 1
fi
echo "Embedded $(stat -c '%s bytes (%n)' "$OFFLINE_ASSETS_DIR/deb/diagnoseme_amd64.deb")"

# Copy Desktop Launchers directly to ISO root
echo "Embedding Desktop Launchers..."
mkdir -p "$BUILD_DIR/desktop_launchers"
cp -r ../development_environment/deb_builder/desktop_launchers/*.desktop "$BUILD_DIR/desktop_launchers/" 2>/dev/null || true

# Copy CLI tools directly to ISO root
echo "Embedding CLI tools..."
mkdir -p "$BUILD_DIR/cli_tools"
cp ../development_environment/deb_builder/start-diagnoseme \
   ../development_environment/deb_builder/stop-diagnoseme \
   ../development_environment/deb_builder/diagnoseme-dashboard \
   ../development_environment/deb_builder/diagnoseme-logs \
   ../development_environment/deb_builder/diagnoseme-offline-update \
   ../development_environment/deb_builder/maui-flash \
   ../development_environment/deb_builder/maui-ctl \
   ../development_environment/deb_builder/config.env \
   ../development_environment/docker-compose-prod.yaml \
   "$BUILD_DIR/cli_tools/" 2>/dev/null || true

# 4. Modify Boot Menu to Auto-Load Preseed
echo "Modifying GRUB boot parameters..."
# For UEFI (GRUB)
sed -i 's|--- quiet|auto=true priority=high preseed/file=/cdrom/preseed.cfg --- quiet|g' "$BUILD_DIR/boot/grub/grub.cfg"
# For BIOS (ISOLINUX)
if [ -f "$BUILD_DIR/isolinux/txt.cfg" ]; then
    sed -i 's|--- quiet|auto=true priority=high preseed/file=/cdrom/preseed.cfg --- quiet|g' "$BUILD_DIR/isolinux/txt.cfg"
fi

# 5. Rebuild ISO
echo "Rebuilding ISO to $NEW_ISO..."
# Generate md5 checksums
cd "$BUILD_DIR"
find . -type f -exec md5sum {} \; > md5sum.txt
cd ..

# Build ISO utilizing xorriso (standard for modern debian hybrid ISOs)
if command -v xorriso > /dev/null 2>&1; then
    xorriso -as mkisofs \
      -r -V "Custom Debian Install" \
      -J -joliet-long \
      -b isolinux/isolinux.bin \
      -c isolinux/boot.cat \
      -no-emul-boot -boot-load-size 4 -boot-info-table \
      -eltorito-alt-boot \
      -e boot/grub/efi.img \
      -no-emul-boot -isohybrid-gpt-basdat -isohybrid-apm-hfsplus \
      -o "$NEW_ISO" \
      "$BUILD_DIR"
elif command -v docker > /dev/null 2>&1; then
    echo "Host xorriso not found. Running xorriso via Docker..."
    docker run --rm -v "$(pwd):/work" -w /work debian:trixie-slim sh -c "
      apt-get update -qq && apt-get install -y -qq xorriso > /dev/null && \
      xorriso -as mkisofs \
        -r -V 'Custom Debian Install' \
        -J -joliet-long \
        -b isolinux/isolinux.bin \
        -c isolinux/boot.cat \
        -no-emul-boot -boot-load-size 4 -boot-info-table \
        -eltorito-alt-boot \
        -e boot/grub/efi.img \
        -no-emul-boot -isohybrid-gpt-basdat -isohybrid-apm-hfsplus \
        -o '$NEW_ISO' \
        '$BUILD_DIR'
    "
else
    echo "Error: Neither xorriso nor docker is available to build ISO."
    exit 1
fi

echo "Done! Custom ISO generated: $NEW_ISO"
