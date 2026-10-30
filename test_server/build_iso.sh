#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
set -e

# All paths below are relative to test_server/ (except --maui-utils-tar).
CALLER_DIR="$(pwd)"
cd "$(dirname "${BASH_SOURCE[0]}")"

# Configuration
ISO_NAME="debian-13-amd64-netinst.iso"
# Pointing to a pinned, verified Debian 13 (Trixie) ISO hosted in our GCS bucket
ISO_GCS_PATH="gs://chromeos-hw-tools-assets/debian-13-trixie-verified.iso"
BUILD_DIR="iso_build"
NEW_ISO=""
BUNDLE_TMP_DIR="$BUILD_DIR/offline_bundle_tmp"
SCRIPT_DIR="$(pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEV_ENV_DIR="$PROJECT_ROOT/development_environment"
DEB_BUILDER_DIR="$DEV_ENV_DIR/deb_builder"
EDITION=""
MAUI_UTILS_TAR=""
DOCKER_TAG=""
BUILD_LOCAL=false
USE_LOCAL=false
SKIP_FW=false

# Parse options
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
        --output)
            NEW_ISO="$2"
            shift 2
            ;;
        --maui-utils-tar)
            MAUI_UTILS_TAR="$2"
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
            echo "  --edition <id>      DiagnoseMe edition to package (required, see editions.json)"
            echo "  --tag <tag>         Docker image tag to package (default: <edition>-<commit>,"
            echo "                      see build-offline-bundle.sh)"
            echo "  --output <file>     ISO to write (default: custom-debian-preseed-<edition>.iso)"
            echo "  --maui-utils-tar <file>  developer override: bundle this local maui-utils.tar"
            echo "                      (docker save output of an unreleased build) instead of"
            echo "                      the stable release (Maui editions always bundle the"
            echo "                      stable maui-utils otherwise)"
            echo "  --use-local         Use existing local Docker images without pulling from cloud"
            echo "  --build-local       Rebuild Docker images from local workspace source code first"
            echo "  --skip-fw           Build an ISO without downloading the edition's Maui firmware"
            echo "                      (only dist/firmware is bundled; by default the build fails"
            echo "                      if no firmware of a component could be staged)"
            echo "  -h, --help          Show this message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

EDITION_INFO=(python3 "$PROJECT_ROOT/tools/edition_info.py")
if [ -z "${EDITION}" ]; then
    echo "Error: --edition <id> is required (shipped: $("${EDITION_INFO[@]}" list --shipped | tr '\n' ' '))."
    exit 1
fi
"${EDITION_INFO[@]}" validate "${EDITION}"
if [ -n "${MAUI_UTILS_TAR}" ]; then
    MAUI_UTILS_TAR="$(cd "${CALLER_DIR}" && realpath "${MAUI_UTILS_TAR}")"
fi
if [ -z "$NEW_ISO" ]; then
    NEW_ISO="custom-debian-preseed-${EDITION}.iso"
fi
MAUI_EDITION="$("${EDITION_INFO[@]}" get "${EDITION}" maui_utils)"
echo "Packaging DiagnoseMe edition ${EDITION} (image tag :${DOCKER_TAG:-<edition>-<commit>}) into ${NEW_ISO}"

# 1. Download ISO
echo "Downloading pinned Debian testing netinst ISO from GCS..."
if [ ! -f "$ISO_NAME" ]; then
    gcloud storage cp "$ISO_GCS_PATH" "$ISO_NAME"
fi

# 2. Extract ISO
echo "Extracting ISO to $BUILD_DIR..."
# A previous Docker based build may have left root-owned files behind.
rm -f "$NEW_ISO" 2>/dev/null || true
rm -rf "$BUILD_DIR" 2>/dev/null || \
    docker run --rm -v "$(pwd):/work" debian:trixie-slim rm -rf "/work/$BUILD_DIR"
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

echo "Embedding offline assets (Docker images, Maui firmware, Debian package)..."
OFFLINE_ASSETS_DIR="$BUILD_DIR/offline_assets"
mkdir -p "$OFFLINE_ASSETS_DIR"

# Reuse the offline bundle builder for the images, firmware and deb of the
# edition, then keep only those parts (the ISO installs them via the preseed
# late_command). A Maui station ISO must contain the firmware, so the bundle
# builder fails when no firmware of a component could be staged unless
# --skip-fw is given.
BUNDLE_ARGS=(--edition "$EDITION" --output-dir "$BUNDLE_TMP_DIR" --no-zip)
if [ -n "$DOCKER_TAG" ]; then
    BUNDLE_ARGS+=(--tag "$DOCKER_TAG")
fi
if [ -n "$MAUI_UTILS_TAR" ]; then
    BUNDLE_ARGS+=(--maui-utils-tar "$MAUI_UTILS_TAR")
fi
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
for PART in images deb; do
    mv "$BUNDLE_TMP_DIR/diagnoseme-update/$PART" "$OFFLINE_ASSETS_DIR/"
done
# Only the Maui editions have firmware components.
if [ -d "$BUNDLE_TMP_DIR/diagnoseme-update/firmware" ]; then
    mv "$BUNDLE_TMP_DIR/diagnoseme-update/firmware" "$OFFLINE_ASSETS_DIR/"
fi
rm -rf "$BUNDLE_TMP_DIR"
echo "Embedded $(stat -c '%s bytes (%n)' "$OFFLINE_ASSETS_DIR/deb/diagnoseme_amd64.deb")"

# Copy Desktop Launchers directly to ISO root (Maui editions only; the deb
# installs the 'DiagnoseMe Web' menu entry of the other editions).
if [ "$MAUI_EDITION" = "true" ]; then
    echo "Embedding Desktop Launchers..."
    mkdir -p "$BUILD_DIR/desktop_launchers"
    cp -r "$DEB_BUILDER_DIR"/desktop_launchers/*.desktop "$BUILD_DIR/desktop_launchers/" 2>/dev/null || true
fi

# First boot setup (image load, package check, apt source and browser
# autostart of the online editions), installed by the preseed.
echo "Embedding the first boot setup..."
mkdir -p "$BUILD_DIR/iso_setup"
cp "$SCRIPT_DIR/iso_setup/post-install-setup.sh" "$SCRIPT_DIR/iso_setup/diagnoseme-browser.desktop" \
   "$BUILD_DIR/iso_setup/"

# Copy CLI tools directly to ISO root
echo "Embedding CLI tools..."
mkdir -p "$BUILD_DIR/cli_tools"
cp "$DEB_BUILDER_DIR/start-diagnoseme" \
   "$DEB_BUILDER_DIR/stop-diagnoseme" \
   "$DEB_BUILDER_DIR/diagnoseme-dashboard" \
   "$DEB_BUILDER_DIR/diagnoseme-logs" \
   "$DEB_BUILDER_DIR/diagnoseme-offline-update" \
   "$DEB_BUILDER_DIR/maui-flash" \
   "$DEV_ENV_DIR/docker-compose-prod.yaml" \
   "$BUILD_DIR/cli_tools/" 2>/dev/null || true
# The shared firmware fetcher, installed like the deb does.
cp "$PROJECT_ROOT/tools/maui_fw_fetch.py" "$BUILD_DIR/cli_tools/fetch-maui-firmware" 2>/dev/null || true
# maui-ctl is the same wrapper as maui-flash (it dispatches on its name).
cp "$DEB_BUILDER_DIR/maui-flash" "$BUILD_DIR/cli_tools/maui-ctl" 2>/dev/null || true

# Station config of the edition, specialised like the deb builder does
# (Maui config and compose override for the Maui editions, else the generic
# online config); preseed installs them into /etc/diagnoseme (the deb then
# installs the same files).
if [ "$MAUI_EDITION" = "true" ]; then
    cp "$DEB_BUILDER_DIR/config.env" "$BUILD_DIR/cli_tools/config.env"
    cp "$DEV_ENV_DIR/docker-compose-maui.yaml" "$BUILD_DIR/cli_tools/"
else
    cp "$DEB_BUILDER_DIR/config-generic.env" "$BUILD_DIR/cli_tools/config.env"
fi
MAUI_UTILS_VERSION=""
if [ "$MAUI_EDITION" = "true" ]; then
    MAUI_UTILS_VERSION="$(cat "$PROJECT_ROOT/dist/maui-utils/VERSION" 2>/dev/null || true)"
fi
"${EDITION_INFO[@]}" render-config "${EDITION}" "$BUILD_DIR/cli_tools/config.env" \
    --bundled-maui-utils "${MAUI_UTILS_VERSION}"

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
    # Mount the directory of the output ISO too: --output may point outside
    # test_server/ (an absolute path), which the container would not keep.
    mkdir -p "$(dirname "$NEW_ISO")"
    ISO_OUT_DIR="$(cd "$(dirname "$NEW_ISO")" && pwd)"
    ISO_OUT_NAME="$(basename "$NEW_ISO")"
    docker run --rm -v "$(pwd):/work" -v "${ISO_OUT_DIR}:/out" -w /work debian:trixie-slim sh -c "
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
        -o '/out/${ISO_OUT_NAME}' \
        '$BUILD_DIR' && \
      chown $(id -u):$(id -g) '/out/${ISO_OUT_NAME}'
    "
else
    echo "Error: Neither xorriso nor docker is available to build ISO."
    exit 1
fi

echo "Done! Custom ISO generated: $(cd "$(dirname "$NEW_ISO")" && pwd)/$(basename "$NEW_ISO")"
