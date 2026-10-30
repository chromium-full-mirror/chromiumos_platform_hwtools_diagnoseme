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
             usr/share/applications \
             etc/skel/Desktop \
             var/lib/diagnoseme/firmware/mcu \
             var/lib/diagnoseme/firmware/pdc \
             var/log/diagnoseme/rpcserver \
             var/log/diagnoseme/nginx \
             var/log/diagnoseme/envoy \
             DEBIAN

# Copy offline firmware into package. dist/firmware always exists (it holds a
# tracked .keep file), so builds without staged firmware (e.g. Servo/Dolos CQ)
# work and ship empty firmware directories.
COPY dist/firmware/ /tmp/firmware/
RUN for KIND in mcu pdc; do \
        if [ -d "/tmp/firmware/${KIND}" ]; then \
            find "/tmp/firmware/${KIND}" -maxdepth 1 -type f \
                -exec install -m 644 -t "var/lib/diagnoseme/firmware/${KIND}/" {} +; \
        fi; \
    done \
    && rm -rf /tmp/firmware

# Copy CLI helper scripts
COPY development_environment/deb_builder/start-diagnoseme usr/local/bin/
COPY development_environment/deb_builder/stop-diagnoseme usr/local/bin/
COPY development_environment/deb_builder/diagnoseme-dashboard usr/local/bin/
COPY development_environment/deb_builder/diagnoseme-logs usr/local/bin/
COPY development_environment/deb_builder/diagnoseme-offline-update usr/local/bin/
COPY development_environment/deb_builder/fetch-maui-firmware usr/local/bin/
COPY development_environment/deb_builder/maui-flash usr/local/bin/
RUN chmod 755 usr/local/bin/start-diagnoseme \
              usr/local/bin/stop-diagnoseme \
              usr/local/bin/diagnoseme-dashboard \
              usr/local/bin/diagnoseme-logs \
              usr/local/bin/diagnoseme-offline-update \
              usr/local/bin/fetch-maui-firmware \
              usr/local/bin/maui-flash \
    && ln -s maui-flash usr/local/bin/maui-ctl

# Copy docker-compose and, for Maui station packages, the Maui station config.
# The generic package (Servo/Dolos, default) ships no config.env, so
# start-diagnoseme keeps its generic defaults (online, :release, watchtower).
ARG STATION_PROFILE="generic"
COPY development_environment/docker-compose-prod.yaml etc/diagnoseme/docker-compose.yaml
COPY development_environment/deb_builder/config.env /tmp/config.env
RUN chmod 644 etc/diagnoseme/docker-compose.yaml \
    && if [ "${STATION_PROFILE}" = "maui" ]; then \
        install -m 644 /tmp/config.env etc/diagnoseme/config.env; \
    elif [ "${STATION_PROFILE}" != "generic" ]; then \
        echo "Unknown STATION_PROFILE '${STATION_PROFILE}' (expected generic or maui)" >&2; \
        exit 1; \
    fi \
    && rm -f /tmp/config.env

# Copy desktop launchers. Maui station packages get the Maui launchers (menu
# and desktop); the generic package only gets a 'DiagnoseMe Web' menu entry.
COPY development_environment/deb_builder/desktop_launchers/ /tmp/maui_launchers/
COPY development_environment/deb_builder/generic_launchers/ /tmp/generic_launchers/
RUN mkdir -p usr/share/applications \
    && if [ "${STATION_PROFILE}" = "maui" ]; then \
        mkdir -p etc/skel/Desktop \
        && install -m 644 /tmp/maui_launchers/*.desktop usr/share/applications/ \
        && install -m 755 /tmp/maui_launchers/*.desktop etc/skel/Desktop/; \
    else \
        install -m 644 /tmp/generic_launchers/*.desktop usr/share/applications/; \
    fi \
    && rm -rf /tmp/maui_launchers /tmp/generic_launchers

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
RUN printf "Package: diagnoseme\n\
Version: ${DEB_VERSION}\n\
Architecture: amd64\n\
Maintainer: DiagnoseMe Team\n\
Depends: docker.io, docker-compose, dkms, build-essential, linux-headers-amd64, libcurl4, libxmlb2, libjcat1, ethtool, usbutils, python3\n\
Description: Diagnose Me hardware verification tool\n" > DEBIAN/control

# Post-inst script to setup DKMS, desktop shortcuts and permissions. It is told
# the station profile, so that only Maui stations get the Maui desktop
# shortcuts.
COPY development_environment/deb_builder/postinst DEBIAN/postinst
RUN sed -i "s/@STATION_PROFILE@/${STATION_PROFILE}/" DEBIAN/postinst \
    && chmod 755 DEBIAN/postinst

# Build the deb
RUN dpkg-deb --build . /diagnoseme.deb

FROM busybox
COPY --from=deb-builder /diagnoseme.deb /
