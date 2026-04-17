# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

######################################################################
FROM debian:trixie AS rtk-tools-builder
RUN apt-get update && apt-get install -y bzip2
WORKDIR /build
COPY dist/rtunicpg.tar.bz2 .
RUN tar -xvf rtunicpg.tar.bz2

######################################################################
FROM debian:trixie AS deb-builder

RUN apt-get update && apt-get install -y dpkg-dev

WORKDIR /pkg

# Create directory structure
RUN mkdir -p usr/local/bin \
             etc/diagnoseme \
             usr/local/server/binfiles \
             etc/modprobe.d \
             DEBIAN

# Copy start script
COPY development_environment/deb_builder/start-diagnoseme usr/local/bin/
RUN chmod 755 usr/local/bin/start-diagnoseme

# Copy docker-compose
COPY development_environment/docker-compose-prod.yaml etc/diagnoseme/docker-compose.yaml

# Copy Realtek tools
COPY --from=rtk-tools-builder /build/*/rtunicpg-x86_64 usr/local/bin/rtunicpg
COPY --from=rtk-tools-builder /build/*/EF8153B* usr/local/server/binfiles/
COPY --from=rtk-tools-builder /build/*/EE8153B* usr/local/server/binfiles/

# Block conflicting modules
RUN printf "black""list cdc_ether\n" > etc/modprobe.d/diagnoseme-denylist.conf

# Copy Realtek driver source for DKMS
COPY dist/r8152.tar.bz2 /tmp/
RUN mkdir -p /tmp/r8152-2.21.4 && tar -xvf /tmp/r8152.tar.bz2 -C /tmp/r8152-2.21.4
RUN mkdir -p usr/src/r8152-2.21.4
RUN cp /tmp/r8152-2.21.4/Makefile /tmp/r8152-2.21.4/r8152.c /tmp/r8152-2.21.4/compatibility.h usr/src/r8152-2.21.4/
RUN printf "PACKAGE_NAME=\"r8152\"\nPACKAGE_VERSION=\"2.21.4\"\nBUILT_MODULE_NAME[0]=\"r8152\"\nDEST_MODULE_LOCATION[0]=\"/updates\"\nAUTOINSTALL=\"yes\"" > usr/src/r8152-2.21.4/dkms.conf

ARG DEB_VERSION="1.0.0"
# Control file
RUN printf "Package: diagnoseme\n\
Version: ${DEB_VERSION}\n\
Architecture: amd64\n\
Maintainer: DiagnoseMe Team\n\
Depends: docker.io, docker-compose, dkms, build-essential, linux-headers-amd64, libcurl4, libxmlb2, libjcat1, ethtool, usbutils\n\
Description: Diagnose Me hardware verification tool\n" > DEBIAN/control

# Post-inst script to setup DKMS and permissions
RUN printf "#!/bin/sh\n\
set -e\n\
echo \"Setting up Realtek driver via DKMS...\"\n\
dkms add -m r8152 -v 2.21.4 || true\n\
dkms install -m r8152 -v 2.21.4 || echo \"Warning: DKMS install failed. Headers may be missing.\"\n\
\n\
echo \"Unloading conflicting modules...\"\n\
modprobe -r cdc_ether || true\n\
modprobe -r r8152 || true\n\
\n\
echo \"Configuring Docker permissions...\"\n\
getent group docker >/dev/null || groupadd docker\n\
if [ -n \"\$SUDO_USER\" ]; then\n\
    echo \"Adding user '\$SUDO_USER' to docker group...\"\n\
    usermod -aG docker \"\$SUDO_USER\"\n\
else\n\
    echo \"Warning: Could not detect sudo user. Please run 'sudo usermod -aG docker \$USER' manually.\"\n\
fi\n\
\n\
echo \"Setting up permissions...\"\n\
chmod 755 /usr/local/bin/start-diagnoseme\n\
chmod 755 /usr/local/bin/rtunicpg\n\
" > DEBIAN/postinst
RUN chmod 755 DEBIAN/postinst

# Build the deb
RUN dpkg-deb --build . /diagnoseme.deb

FROM busybox
COPY --from=deb-builder /diagnoseme.deb /
