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

RUN apt-get update && apt-get install -y dpkg-dev python3

# DiagnoseMe edition packaged by this deb (see editions.json).
ARG EDITION=maui-v1.1
COPY editions.json tools/edition_info.py /edition/

WORKDIR /pkg

# Create directory structure
RUN mkdir -p usr/local/bin \
             usr/lib/diagnoseme \
             etc/diagnoseme \
             usr/local/server/binfiles \
             etc/modprobe.d \
             usr/share/applications \
             etc/skel/Desktop \
             var/lib/diagnoseme/firmware \
             var/log/diagnoseme/rpcserver \
             var/log/diagnoseme/nginx \
             var/log/diagnoseme/envoy \
             DEBIAN

# Copy offline firmware into package (only the components of the edition;
# this also creates their firmware/<component> directories).
COPY dist/firmware/ /tmp/firmware/
RUN for COMP in $(python3 /edition/edition_info.py --registry /edition/editions.json get "${EDITION}" firmware); do \
        mkdir -p "var/lib/diagnoseme/firmware/${COMP}" && \
        cp -r "/tmp/firmware/${COMP}/." "var/lib/diagnoseme/firmware/${COMP}/" 2>/dev/null || true; \
    done && \
    find var/lib/diagnoseme/firmware -type f -exec chmod 644 {} +

# Copy CLI helper scripts
COPY development_environment/deb_builder/start-diagnoseme usr/local/bin/
COPY development_environment/deb_builder/stop-diagnoseme usr/local/bin/
COPY development_environment/deb_builder/diagnoseme-dashboard usr/local/bin/
COPY development_environment/deb_builder/diagnoseme-logs usr/local/bin/
COPY development_environment/deb_builder/diagnoseme-offline-update usr/local/bin/
COPY development_environment/deb_builder/maui-flash usr/local/bin/
COPY development_environment/deb_builder/maui-ctl usr/local/bin/
# Firmware fetcher of the online mode (start-diagnoseme).
COPY tools/maui_fw_fetch.py usr/lib/diagnoseme/
RUN chmod 755 usr/local/bin/start-diagnoseme \
              usr/local/bin/stop-diagnoseme \
              usr/local/bin/diagnoseme-dashboard \
              usr/local/bin/diagnoseme-logs \
              usr/local/bin/diagnoseme-offline-update \
              usr/local/bin/maui-flash \
              usr/local/bin/maui-ctl \
              usr/lib/diagnoseme/maui_fw_fetch.py

# Copy docker-compose and default config
COPY development_environment/docker-compose-prod.yaml etc/diagnoseme/docker-compose.yaml
COPY development_environment/deb_builder/config.env etc/diagnoseme/config.env
# Bundle the stable maui-utils image (fetched by tools/fetch_maui_utils.sh)
# for the Maui editions; start-diagnoseme loads it on offline stations.
COPY dist/maui-util[s]/ /tmp/maui-utils/
# Specialise config.env for the edition (the ISO builder does the same).
RUN INFO="python3 /edition/edition_info.py --registry /edition/editions.json" && \
    ${INFO} validate "${EDITION}" && \
    MAUI_UTILS_VERSION="" && \
    if [ "$(${INFO} get "${EDITION}" maui_utils)" = "true" ] && [ -f /tmp/maui-utils/maui-utils.tar ]; then \
        mkdir -p var/lib/diagnoseme/maui-utils && \
        install -m 644 /tmp/maui-utils/maui-utils.tar var/lib/diagnoseme/maui-utils/maui-utils.tar && \
        MAUI_UTILS_VERSION="$(cat /tmp/maui-utils/VERSION)"; \
    fi && \
    ${INFO} render-config "${EDITION}" etc/diagnoseme/config.env \
        --bundled-maui-utils "${MAUI_UTILS_VERSION}" && \
    ${INFO} get "${EDITION}" deb_package > /edition/deb_package
RUN chmod 644 etc/diagnoseme/docker-compose.yaml etc/diagnoseme/config.env

# Copy desktop launchers
COPY development_environment/deb_builder/desktop_launchers/*.desktop usr/share/applications/
COPY development_environment/deb_builder/desktop_launchers/*.desktop etc/skel/Desktop/
RUN chmod 644 usr/share/applications/*.desktop
RUN chmod 755 etc/skel/Desktop/*.desktop

# Copy Realtek tools
COPY --from=rtk-tools-builder /build/*/rtunicpg-x86_64 usr/local/bin/rtunicpg
COPY --from=rtk-tools-builder /build/*/EF8153B* usr/local/server/binfiles/
COPY --from=rtk-tools-builder /build/*/EE8153B* usr/local/server/binfiles/
RUN chmod 755 usr/local/bin/rtunicpg && chmod 644 usr/local/server/binfiles/*

# Block conflicting modules
RUN printf "black""list cdc_ether\n" > etc/modprobe.d/diagnoseme-denylist.conf

# Copy Realtek driver source for DKMS
COPY dist/r8152-2.21.4.tar.bz2 /tmp/
RUN mkdir -p usr/src/r8152-2.21.4 && tar -xvf /tmp/r8152-2.21.4.tar.bz2 -C usr/src/r8152-2.21.4
RUN printf "PACKAGE_NAME=\"r8152\"\nPACKAGE_VERSION=\"2.21.4\"\nBUILT_MODULE_NAME[0]=\"r8152\"\nDEST_MODULE_LOCATION[0]=\"/updates\"\nAUTOINSTALL=\"yes\"" > usr/src/r8152-2.21.4/dkms.conf

ARG DEB_VERSION="1.0.0"
# Control file
# Maui v1.1 keeps the historical 'diagnoseme' package name so installed stations
# keep updating; other editions get 'diagnoseme-<edition>' and conflict with
# each other (one edition per station).
# dpkg only takes over files of packages named in Replaces (a virtual package
# does not count), so every other edition's package (editions.json
# deb_package, including the legacy 'diagnoseme') is listed explicitly.
RUN PKG="$(cat /edition/deb_package)" && \
    OTHERS="$(python3 /edition/edition_info.py --registry /edition/editions.json get "${EDITION}" deb_conflicts)" && \
    printf "Package: ${PKG}\n\
Version: ${DEB_VERSION}\n\
Provides: diagnoseme-station\n\
Conflicts: diagnoseme-station${OTHERS:+, ${OTHERS}}\n\
Replaces: diagnoseme-station${OTHERS:+, ${OTHERS}}\n\
Architecture: amd64\n\
Maintainer: DiagnoseMe Team\n\
Depends: docker.io, docker-compose, dkms, build-essential, linux-headers-amd64, libcurl4, libxmlb2, libjcat1, ethtool, usbutils, python3\n\
Description: Diagnose Me hardware verification tool\n" > DEBIAN/control

# Post-inst script to setup DKMS, desktop shortcuts and permissions
RUN FW_DIRS="$(for COMP in $(python3 /edition/edition_info.py --registry /edition/editions.json get "${EDITION}" firmware); do \
        printf ' /var/lib/diagnoseme/firmware/%s' "${COMP}"; done)" && \
    printf "#!/bin/sh\n\
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
echo \"Setting up desktop shortcuts...\"\n\
if [ -d /home/diagnoseme ]; then\n\
    mkdir -p /home/diagnoseme/Desktop\n\
    cp -u /usr/share/applications/start-diagnoseme*.desktop /usr/share/applications/stop-diagnoseme.desktop /usr/share/applications/diagnoseme-*.desktop /home/diagnoseme/Desktop/ 2>/dev/null || true\n\
    chmod +x /home/diagnoseme/Desktop/*.desktop 2>/dev/null || true\n\
    chown -R diagnoseme:diagnoseme /home/diagnoseme/Desktop 2>/dev/null || true\n\
fi\n\
\n\
echo \"Setting up permissions...\"\n\
chmod 755 /usr/local/bin/start-diagnoseme\n\
chmod 755 /usr/local/bin/stop-diagnoseme\n\
chmod 755 /usr/local/bin/diagnoseme-dashboard\n\
chmod 755 /usr/local/bin/diagnoseme-logs\n\
chmod 755 /usr/local/bin/diagnoseme-offline-update\n\
chmod 755 /usr/local/bin/maui-flash\n\
chmod 755 /usr/local/bin/maui-ctl\n\
chmod 755 /usr/local/bin/rtunicpg\n\
mkdir -p /var/lib/diagnoseme/firmware${FW_DIRS}\n\
if [ -f /var/lib/diagnoseme/maui-utils/maui-utils.tar ]; then\n\
    chmod 644 /var/lib/diagnoseme/maui-utils/maui-utils.tar\n\
    if docker info > /dev/null 2>&1; then\n\
        echo \"Loading the bundled maui-utils image...\"\n\
        docker load -i /var/lib/diagnoseme/maui-utils/maui-utils.tar > /dev/null || echo \"Warning: loading maui-utils failed; start-diagnoseme retries.\"\n\
    fi\n\
fi\n\
mkdir -p /var/log/diagnoseme/rpcserver /var/log/diagnoseme/nginx /var/log/diagnoseme/envoy\n\
chmod -R 777 /var/log/diagnoseme 2>/dev/null || true\n\
if [ -d /home/diagnoseme ]; then\n\
    mkdir -p /home/diagnoseme/diagnoseme_results/highlevel /home/diagnoseme/diagnoseme_results/detailed\n\
    chmod -R 777 /home/diagnoseme/diagnoseme_results 2>/dev/null || true\n\
    chown -R diagnoseme:diagnoseme /home/diagnoseme/diagnoseme_results 2>/dev/null || true\n\
fi\n\
" > DEBIAN/postinst
RUN chmod 755 DEBIAN/postinst

# Build the deb
RUN dpkg-deb --build . /diagnoseme.deb

FROM busybox
COPY --from=deb-builder /diagnoseme.deb /
