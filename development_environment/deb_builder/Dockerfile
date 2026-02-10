# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

######################################################################
FROM gcr.io/google.com/cloudsdktool/google-cloud-cli:alpine AS rtk-fetcher
WORKDIR /fetch
COPY "dist/rtunicpg-2.0.25.3.tar(24).bz2*" "dist/r8152-2.21.4.tar.bz2*" ./
RUN if [ ! -f "rtunicpg-2.0.25.3.tar(24).bz2" ]; then \
        gsutil cp "gs://rtk-tools/rtunicpg-2.0.25.3.tar(24).bz2" .; \
    fi && \
    if [ ! -f r8152-2.21.4.tar.bz2 ]; then \
        gsutil cp gs://rtk-tools/r8152-2.21.4.tar.bz2 .; \
    fi

######################################################################
FROM debian:trixie AS rtk-tools-builder
RUN apt-get update && apt-get install -y bzip2
WORKDIR /build
COPY --from=rtk-fetcher "/fetch/rtunicpg-2.0.25.3.tar(24).bz2" rtunicpg.tar.bz2
RUN tar -xvf rtunicpg.tar.bz2

######################################################################
FROM debian:trixie AS deb-builder

RUN apt-get update && apt-get install -y dpkg-dev

WORKDIR /pkg

# Create directory structure
RUN mkdir -p usr/local/bin \
             etc/diagnoseme \
             usr/local/server/binfiles \
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

# Copy Realtek driver source for DKMS (r8152-dkms will be a dependency or nested)
# For simplicity in this example, we'll assume r8152-dkms is built separately or
# we include the logic here.
COPY --from=rtk-fetcher /fetch/r8152-2.21.4.tar.bz2 /tmp/
RUN tar -xvf /tmp/r8152-2.21.4.tar.bz2 -C /tmp/
RUN mkdir -p usr/src/r8152-2.21.4
RUN cp /tmp/r8152-2.21.4/Makefile /tmp/r8152-2.21.4/r8152.c /tmp/r8152-2.21.4/compatibility.h usr/src/r8152-2.21.4/
RUN printf "PACKAGE_NAME=\"r8152\"\nPACKAGE_VERSION=\"2.21.4\"\nBUILT_MODULE_NAME[0]=\"r8152\"\nDEST_MODULE_LOCATION[0]=\"/updates\"\nAUTOINSTALL=\"yes\"" > usr/src/r8152-2.21.4/dkms.conf

# Control file
RUN printf "Package: diagnoseme\n\
Version: 1.0.0\n\
Architecture: amd64\n\
Maintainer: DiagnoseMe Team\n\
Depends: docker.io, dkms, build-essential, libcurl4, libxmlb2, libjcat1, ethtool, usbutils\n\
Description: Diagnose Me hardware verification tool\n" > DEBIAN/control

# Post-inst script to setup DKMS and permissions
RUN printf "#!/bin/sh\n\
set -e\n\
echo \"Setting up Realtek driver via DKMS...\"\n\
dkms add -m r8152 -v 2.21.4 || true\n\
dkms install -m r8152 -v 2.21.4 || echo \"Warning: DKMS install failed. Headers may be missing.\"\n\
echo \"Setting up permissions...\"\n\
chmod 755 /usr/local/bin/start-diagnoseme\n\
chmod 755 /usr/local/bin/rtunicpg\n\
" > DEBIAN/postinst
RUN chmod 755 DEBIAN/postinst

# Build the deb
RUN dpkg-deb --build . /diagnoseme.deb

FROM scratch
COPY --from=deb-builder /diagnoseme.deb /
