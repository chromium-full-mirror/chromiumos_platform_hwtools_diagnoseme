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

# 1. Download ISO
echo "Downloading pinned Debian testing netinst ISO from GCS..."
if [ ! -f "$ISO_NAME" ]; then
    gcloud storage cp "$ISO_GCS_PATH" "$ISO_NAME"
fi

# 2. Extract ISO
echo "Extracting ISO to $BUILD_DIR..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
bsdtar -C "$BUILD_DIR" -xf "$ISO_NAME"
chmod -R +w "$BUILD_DIR"

# 3. Inject Preseed
echo "Injecting preseed.cfg..."
cp preseed.cfg "$BUILD_DIR/"

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

echo "Done! Custom ISO generated: $NEW_ISO"
