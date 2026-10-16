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
EDITION="maui-v1.1"
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
            echo "  --edition <id>      DiagnoseMe edition to package (default: maui-v1.1)"
            echo "  --tag <tag>         Docker image tag to package (default: the edition id)"
            echo "  --output <file>     ISO to write (default: custom-debian-preseed-<edition>.iso)"
            echo "  --maui-utils-tar <file>  developer override: bundle this local maui-utils.tar"
            echo "                      (docker save output of an unreleased build) instead of"
            echo "                      the stable release (Maui editions always bundle the"
            echo "                      stable maui-utils otherwise)"
            echo "  --use-local         Use existing local Docker images without pulling from cloud"
            echo "  --build-local       Rebuild Docker images from local workspace source code first"
            echo "  --skip-fw           Do not download Maui firmware from GCS (only bundle"
            echo "                      dist/firmware)"
            echo "  -h, --help          Show this message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

EDITION_INFO="python3 ../tools/edition_info.py"
${EDITION_INFO} validate "${EDITION}"
if [ -n "${MAUI_UTILS_TAR}" ]; then
    MAUI_UTILS_TAR="$(cd "${CALLER_DIR}" && realpath "${MAUI_UTILS_TAR}")"
fi
if [ -z "$DOCKER_TAG" ]; then
    DOCKER_TAG="${EDITION}"
fi
if [ -z "$NEW_ISO" ]; then
    NEW_ISO="custom-debian-preseed-${EDITION}.iso"
fi
# Tag the installed station runs (config.env DOCKER_TAG); the packaged images
# are also tagged with it, so e.g. --tag maui-v1.1-<commit> still works.
ISO_TAG="$(${EDITION_INFO} get "${EDITION}" docker_tags | cut -d' ' -f1)"
# Every station tag of the edition, e.g. "maui-v1.1 maui": stations installed
# before the editions still run DOCKER_TAG=maui.
ISO_TAGS="$(${EDITION_INFO} get "${EDITION}" docker_tags)"
echo "Packaging DiagnoseMe edition ${EDITION} (image tag :${DOCKER_TAG}) into ${NEW_ISO}"

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
cp preseed.cfg "$BUILD_DIR/"

echo "Embedding offline assets (Docker images :${DOCKER_TAG}, Maui firmware, Debian package)..."
OFFLINE_ASSETS_DIR="$BUILD_DIR/offline_assets"
mkdir -p "$OFFLINE_ASSETS_DIR/images" "$OFFLINE_ASSETS_DIR/deb"

# Copy the edition's local firmware components (editions.json "firmware",
# e.g. mcu+pdc for maui-v1.1) from dist/firmware if present.
for COMP in $(${EDITION_INFO} get "${EDITION}" firmware); do
    mkdir -p "$OFFLINE_ASSETS_DIR/firmware/${COMP}"
    if [ -d "../dist/firmware/${COMP}" ]; then
        cp -r "../dist/firmware/${COMP}/"* "$OFFLINE_ASSETS_DIR/firmware/${COMP}/" 2>/dev/null || true
    fi
done

# Fetch the stable firmware of the edition's components from GCS (the
# stable.<maui_device>.* markers; see tools/maui_fw_fetch.py).
FW_COMPONENTS="$(${EDITION_INFO} get "${EDITION}" firmware)"
if [ "$SKIP_FW" = true ]; then
    echo "Skipping the Maui firmware download (--skip-fw); using dist/firmware only."
elif [ -n "${FW_COMPONENTS}" ]; then
    # shellcheck disable=SC2086 # one argument per component
    python3 ../tools/maui_fw_fetch.py --dest-root "$OFFLINE_ASSETS_DIR/firmware" \
        --device "$(${EDITION_INFO} get "${EDITION}" maui_device)" ${FW_COMPONENTS}
fi

# Build local images if requested
if [ "$BUILD_LOCAL" = true ]; then
    echo "Rebuilding Docker images (:tag ${DOCKER_TAG}) from local workspace source..."
    pushd .. > /dev/null
    ./development_environment/build-diagnoseme.sh --edition "${EDITION}" \
        ${MAUI_UTILS_TAR:+--maui-utils-tar "${MAUI_UTILS_TAR}"}
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
    SAVE_IMGS=("${REG_IMG}" "${LOCAL_IMG}")
    for TAG in ${ISO_TAGS}; do
        if [ "${TAG}" != "${DOCKER_TAG}" ]; then
            STATION_IMG="us-docker.pkg.dev/chromeos-hw-tools/diagnoseme/${IMG}:${TAG}"
            docker tag "${LOCAL_IMG}" "${STATION_IMG}"
            SAVE_IMGS+=("${STATION_IMG}")
        fi
    done
    docker save "${SAVE_IMGS[@]}" | gzip > "$OFFLINE_ASSETS_DIR/images/${IMG}_${ISO_TAG}.tar.gz"
done

# Fetch the stable maui-utils image for Maui editions; it is bundled into
# the deb.
if [ "$(${EDITION_INFO} get "${EDITION}" maui_utils)" = "true" ]; then
    ../tools/fetch_maui_utils.sh ${MAUI_UTILS_TAR:+--tar "${MAUI_UTILS_TAR}"}
fi

# Build and copy Debian package
echo "Building and embedding DiagnoseMe Debian package..."
mkdir -p "$OFFLINE_ASSETS_DIR/deb"
docker build -t diagnoseme-deb-builder --build-arg EDITION="${EDITION}" -f ../development_environment/deb_builder/Dockerfile ..
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

# Specialise config.env for the edition like the deb builder does; preseed
# installs it as /etc/diagnoseme/config.env.
if [ -f "$BUILD_DIR/cli_tools/config.env" ]; then
    ${EDITION_INFO} render-config "${EDITION}" "$BUILD_DIR/cli_tools/config.env" \
        --bundled-maui-utils "$(cat ../dist/maui-utils/VERSION 2>/dev/null || true)"
fi

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
