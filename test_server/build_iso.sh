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
BUNDLE_TMP_DIR="$BUILD_DIR/offline_bundle_tmp"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV_ENV_DIR="$(cd "$SCRIPT_DIR/../development_environment" && pwd)"
DEB_BUILDER_DIR="$DEV_ENV_DIR/deb_builder"
DOCKER_TAG="maui"
BUILD_LOCAL=false
USE_LOCAL=false
SKIP_FW=false

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
        --skip-fw)
            SKIP_FW=true
            shift
            ;;
        -h|--help)
            echo "Usage: ./build_iso.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --tag <tag>         Docker image tag to package (default: maui)"
            echo "  --use-local         Use existing local Docker images without pulling from cloud"
            echo "  --build-local       Rebuild Docker images from local workspace source code first"
            echo "  --skip-fw           Build an ISO without Maui firmware (by default the build"
            echo "                      fails if no MCU/PDC firmware could be staged)"
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
cp "$SCRIPT_DIR/preseed.cfg" "$BUILD_DIR/"

echo "Embedding offline assets (Docker images :${DOCKER_TAG}, Maui firmware, Debian package)..."
OFFLINE_ASSETS_DIR="$BUILD_DIR/offline_assets"
mkdir -p "$OFFLINE_ASSETS_DIR"

# Reuse the offline bundle builder for the images, firmware and deb, then keep
# only those parts (the ISO installs them via the preseed late_command).
# A Maui station ISO must contain the firmware, so the bundle builder fails
# when no MCU/PDC firmware could be staged unless --skip-fw is given.
BUNDLE_ARGS=(--tag "$DOCKER_TAG" --output-dir "$BUNDLE_TMP_DIR" --no-zip)
if [ "$SKIP_FW" = true ]; then
    BUNDLE_ARGS+=(--skip-fw)
fi
if [ "$BUILD_LOCAL" = true ]; then
    BUNDLE_ARGS+=(--build-local)
fi
if [ "$USE_LOCAL" = true ]; then
    BUNDLE_ARGS+=(--use-local)
fi
"$DEV_ENV_DIR/build-offline-bundle.sh" "${BUNDLE_ARGS[@]}"
for PART in images deb firmware; do
    mv "$BUNDLE_TMP_DIR/diagnoseme-update/$PART" "$OFFLINE_ASSETS_DIR/"
done
rm -rf "$BUNDLE_TMP_DIR"
echo "Embedded $(stat -c '%s bytes (%n)' "$OFFLINE_ASSETS_DIR/deb/diagnoseme_amd64.deb")"

# Copy Desktop Launchers directly to ISO root
echo "Embedding Desktop Launchers..."
mkdir -p "$BUILD_DIR/desktop_launchers"
cp -r "$DEB_BUILDER_DIR"/desktop_launchers/*.desktop "$BUILD_DIR/desktop_launchers/" 2>/dev/null || true

# Copy CLI tools directly to ISO root
echo "Embedding CLI tools..."
mkdir -p "$BUILD_DIR/cli_tools"
cp "$DEB_BUILDER_DIR/start-diagnoseme" \
   "$DEB_BUILDER_DIR/stop-diagnoseme" \
   "$DEB_BUILDER_DIR/diagnoseme-dashboard" \
   "$DEB_BUILDER_DIR/diagnoseme-logs" \
   "$DEB_BUILDER_DIR/diagnoseme-offline-update" \
   "$DEB_BUILDER_DIR/fetch-maui-firmware" \
   "$DEB_BUILDER_DIR/maui-flash" \
   "$DEB_BUILDER_DIR/config.env" \
   "$DEV_ENV_DIR/docker-compose-prod.yaml" \
   "$BUILD_DIR/cli_tools/" 2>/dev/null || true
# maui-ctl is the same wrapper as maui-flash (it dispatches on its name).
cp "$DEB_BUILDER_DIR/maui-flash" "$BUILD_DIR/cli_tools/maui-ctl" 2>/dev/null || true

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
