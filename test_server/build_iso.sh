#!/bin/bash
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
set -e

# Configuration
ISO_NAME="debian-13-amd64-netinst.iso"
# Pointing to the stable release current directory for Debian 13 (Trixie)
ISO_URL="https://cdimage.debian.org/cdimage/release/13.3.0/amd64/iso-cd/debian-13.3.0-amd64-netinst.iso"
BUILD_DIR="iso_build"
NEW_ISO="custom-debian-preseed.iso"

# 1. Download ISO
echo "Downloading Debian testing netinst ISO..."
if [ ! -f "$ISO_NAME" ]; then
    wget -O "$ISO_NAME" "$ISO_URL"
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
