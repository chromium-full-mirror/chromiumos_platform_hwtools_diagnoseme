# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

######################################################################
FROM debian:trixie AS deb-builder

RUN apt-get update && apt-get install -y bzip2 dpkg-dev python3

# DiagnoseMe edition packaged by this deb (see editions.json). There is no
# default: every build names its edition (--build-arg EDITION=servo-dolos for
# the Servo / Dolos 'diagnoseme' package). Maui editions ("maui_utils": true)
# get the Maui station config, compose override, launchers, firmware and the
# bundled maui-utils image; the other editions keep the generic station
# behaviour (online, always pull, watchtower).
ARG EDITION
COPY editions.json tools/edition_info.py /edition/
RUN INFO="python3 /edition/edition_info.py --registry /edition/editions.json" \
    && if [ -z "${EDITION}" ]; then \
        echo "ERROR: build with --build-arg EDITION=<edition> (editions.json)" >&2; \
        exit 1; \
    fi \
    && ${INFO} validate "${EDITION}" \
    && ${INFO} get "${EDITION}" maui_utils > /edition/maui \
    && ${INFO} get "${EDITION}" realtek_tools > /edition/realtek \
    && ${INFO} get "${EDITION}" h2h_firmware > /edition/h2h \
    && ${INFO} get "${EDITION}" firmware > /edition/firmware \
    && ${INFO} get "${EDITION}" results_dir > /edition/results_dir \
    && ${INFO} get "${EDITION}" deb_package > /edition/deb_package \
    && ${INFO} get "${EDITION}" deb_conflicts > /edition/deb_conflicts

WORKDIR /pkg

# Create directory structure
RUN mkdir -p usr/local/bin \
             etc/diagnoseme \
             etc/modprobe.d \
             usr/share/applications \
             etc/skel/Desktop \
             var/log/diagnoseme/rpcserver \
             var/log/diagnoseme/nginx \
             var/log/diagnoseme/envoy \
             DEBIAN

# Copy offline firmware into package (only the components of the edition;
# this also creates their firmware/<component> directories, so only the Maui
# editions get /var/lib/diagnoseme/firmware). dist/firmware
# always exists (it holds a tracked .keep file), so builds without staged
# firmware (e.g. CQ) work and ship empty firmware directories.
COPY dist/firmware/ /tmp/firmware/
RUN for COMP in $(cat /edition/firmware); do \
        mkdir -p "var/lib/diagnoseme/firmware/${COMP}" \
        && if [ -d "/tmp/firmware/${COMP}" ]; then \
            find "/tmp/firmware/${COMP}" -maxdepth 1 -type f \
                -exec install -m 644 -t "var/lib/diagnoseme/firmware/${COMP}/" {} +; \
        fi; \
    done \
    && rm -rf /tmp/firmware

# Copy CLI helper scripts. The Maui firmware fetcher (tools/maui_fw_fetch.py)
# is installed as fetch-maui-firmware (used by start-diagnoseme online).
COPY development_environment/deb_builder/start-diagnoseme usr/local/bin/
COPY development_environment/deb_builder/stop-diagnoseme usr/local/bin/
COPY development_environment/deb_builder/diagnoseme-dashboard usr/local/bin/
COPY development_environment/deb_builder/diagnoseme-logs usr/local/bin/
COPY development_environment/deb_builder/diagnoseme-offline-update usr/local/bin/
COPY tools/maui_fw_fetch.py usr/local/bin/fetch-maui-firmware
COPY development_environment/deb_builder/maui-flash usr/local/bin/
RUN chmod 755 usr/local/bin/start-diagnoseme \
              usr/local/bin/stop-diagnoseme \
              usr/local/bin/diagnoseme-dashboard \
              usr/local/bin/diagnoseme-logs \
              usr/local/bin/diagnoseme-offline-update \
              usr/local/bin/fetch-maui-firmware \
              usr/local/bin/maui-flash \
    && ln -s maui-flash usr/local/bin/maui-ctl

# Copy docker-compose and the station config of the edition, specialised by
# tools/edition_info.py render-config: Maui editions get the Maui station
# config (config.env, selecting docker-compose-maui.yaml with the Maui mounts,
# and for H2H firmware flashing (Maui v1.2) docker-compose-maui-h2h.yaml),
# the other editions the generic online config (config-generic.env: online,
# always pull, watchtower, no browser start, no Maui keys). Station overrides
# live in /etc/diagnoseme/config.local.env, which is never shipped.
COPY development_environment/docker-compose-prod.yaml etc/diagnoseme/docker-compose.yaml
COPY development_environment/docker-compose-maui.yaml /tmp/docker-compose-maui.yaml
COPY development_environment/docker-compose-maui-h2h.yaml /tmp/docker-compose-maui-h2h.yaml
COPY development_environment/deb_builder/config.env /tmp/config-maui.env
COPY development_environment/deb_builder/config-generic.env /tmp/config-generic.env
# Bundle the stable maui-utils image (tools/fetch_maui_utils.sh: maui-utils.tar
# and its STABLE.json with version and md5) for the Maui editions;
# start-diagnoseme checks the md5 and loads it on offline stations. A Maui
# deb without it cannot flash, so the build fails unless
# ALLOW_NO_MAUI_UTILS=1 (development / CQ builds only).
ARG ALLOW_NO_MAUI_UTILS=0
COPY dist/maui-util[s]/ /tmp/maui-utils/
RUN MAUI_UTILS_VERSION="" \
    && if [ "$(cat /edition/maui)" = "true" ]; then \
        install -m 644 /tmp/config-maui.env etc/diagnoseme/config.env \
        && install -m 644 /tmp/docker-compose-maui.yaml etc/diagnoseme/docker-compose-maui.yaml \
        && if [ "$(cat /edition/h2h)" = "true" ]; then \
            install -m 644 /tmp/docker-compose-maui-h2h.yaml etc/diagnoseme/docker-compose-maui-h2h.yaml; \
        fi \
        && if [ -f /tmp/maui-utils/maui-utils.tar ] && [ -f /tmp/maui-utils/STABLE.json ]; then \
            MAUI_UTILS_VERSION="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' /tmp/maui-utils/STABLE.json)" \
            && mkdir -p var/lib/diagnoseme/maui-utils \
            && install -m 644 /tmp/maui-utils/maui-utils.tar /tmp/maui-utils/STABLE.json var/lib/diagnoseme/maui-utils/; \
        elif [ "${ALLOW_NO_MAUI_UTILS}" = "1" ]; then \
            echo "WARNING: building the ${EDITION} deb without maui-utils (ALLOW_NO_MAUI_UTILS=1); it cannot flash." >&2; \
        else \
            echo "ERROR: dist/maui-utils/maui-utils.tar or STABLE.json is missing; run tools/fetch_maui_utils.sh (or pass ALLOW_NO_MAUI_UTILS=1 for a development build)." >&2; \
            exit 1; \
        fi; \
    else \
        install -m 644 /tmp/config-generic.env etc/diagnoseme/config.env; \
    fi \
    && python3 /edition/edition_info.py --registry /edition/editions.json \
        render-config "${EDITION}" etc/diagnoseme/config.env \
        --bundled-maui-utils "${MAUI_UTILS_VERSION}" \
    && chmod 644 etc/diagnoseme/docker-compose.yaml etc/diagnoseme/config.env \
    && rm -rf /tmp/config-maui.env /tmp/config-generic.env /tmp/maui-utils /tmp/docker-compose-maui.yaml \
        /tmp/docker-compose-maui-h2h.yaml

# Copy desktop launchers. Maui editions get the Maui launchers (menu and
# desktop); the other editions only get a 'DiagnoseMe Web' menu entry.
COPY development_environment/deb_builder/desktop_launchers/ /tmp/maui_launchers/
COPY development_environment/deb_builder/generic_launchers/ /tmp/generic_launchers/
RUN if [ "$(cat /edition/maui)" = "true" ]; then \
        install -m 644 /tmp/maui_launchers/*.desktop usr/share/applications/ \
        && install -m 755 /tmp/maui_launchers/*.desktop etc/skel/Desktop/; \
    else \
        install -m 644 /tmp/generic_launchers/*.desktop usr/share/applications/; \
    fi \
    && rm -rf /tmp/maui_launchers /tmp/generic_launchers

# Realtek tools of the editions with Servo v4p1 tooling (editions.json
# realtek_tools): the rtunicpg RTL8153 programmer, its EEPROM/eFUSE config
# files and the r8152 driver source for DKMS (postinst builds it). The other
# editions build without dist/rtunicpg.tar.bz2 and dist/r8152-2.21.4.tar.bz2:
# the tracked dist/firmware/.keep makes the COPY valid when the optional
# ([2]) files are missing, with BuildKit and the legacy builder. A realtek
# edition fails without them (tools/fetch_rtk_tools.sh fetches them).
COPY dist/firmware/.keep dist/rtunicpg.tar.bz[2] dist/r8152-2.21.4.tar.bz[2] /tmp/rtk-dist/
RUN if [ "$(cat /edition/realtek)" = "true" ]; then \
        for F in rtunicpg.tar.bz2 r8152-2.21.4.tar.bz2; do \
            if [ ! -s "/tmp/rtk-dist/${F}" ]; then \
                echo "ERROR: dist/${F} is missing; the ${EDITION} deb needs the Realtek tools (run tools/fetch_rtk_tools.sh ${EDITION})." >&2; \
                exit 1; \
            fi; \
        done \
        && mkdir -p /tmp/rtk usr/local/server/binfiles usr/src/r8152-2.21.4 \
        && tar -xjf /tmp/rtk-dist/rtunicpg.tar.bz2 -C /tmp/rtk \
        && install -m 755 /tmp/rtk/*/rtunicpg-x86_64 usr/local/bin/rtunicpg \
        && install -m 644 /tmp/rtk/*/EF8153B* /tmp/rtk/*/EE8153B* usr/local/server/binfiles/ \
        && tar -xjf /tmp/rtk-dist/r8152-2.21.4.tar.bz2 -C usr/src/r8152-2.21.4 \
        && printf "PACKAGE_NAME=\"r8152\"\nPACKAGE_VERSION=\"2.21.4\"\nBUILT_MODULE_NAME[0]=\"r8152\"\nDEST_MODULE_LOCATION[0]=\"/updates\"\nAUTOINSTALL=\"yes\"" > usr/src/r8152-2.21.4/dkms.conf; \
    fi \
    && rm -rf /tmp/rtk /tmp/rtk-dist

# Block conflicting modules (every edition, as before the editions)
RUN printf "black""list cdc_ether\n" > etc/modprobe.d/diagnoseme-denylist.conf

# Deb version, from tools/deb_version.sh (1.1.<YYYYMMDD>); no default.
ARG DEB_VERSION
# Control file
# servo-dolos keeps the historical 'diagnoseme' package name so the installed
# Servo / Dolos stations keep updating with apt; the other editions get
# 'diagnoseme-<edition>' (editions.json deb_package) and conflict with each
# other and with 'diagnoseme' (one edition per station; an ODM station with
# the old Maui 'diagnoseme' package moves to diagnoseme-maui-v1.1 with the
# offline bundle).
# dpkg only takes over files of packages named in Replaces (a virtual package
# does not count), so every other edition's package (editions.json
# deb_package, including the legacy 'diagnoseme') is listed explicitly.
# Only the realtek editions depend on dkms, build-essential and the kernel
# headers (the r8152 DKMS build).
RUN if [ -z "${DEB_VERSION}" ]; then \
        echo "ERROR: build with --build-arg DEB_VERSION=\"\$(tools/deb_version.sh)\"" >&2; \
        exit 1; \
    fi \
    && PKG="$(cat /edition/deb_package)" && \
    OTHERS="$(cat /edition/deb_conflicts)" && \
    RTK_DEPENDS="" && \
    if [ "$(cat /edition/realtek)" = "true" ]; then \
        RTK_DEPENDS="dkms, build-essential, linux-headers-amd64, "; \
    fi && \
    printf "Package: ${PKG}\n\
Version: ${DEB_VERSION}\n\
Provides: diagnoseme-station\n\
Conflicts: diagnoseme-station${OTHERS:+, ${OTHERS}}\n\
Replaces: diagnoseme-station${OTHERS:+, ${OTHERS}}\n\
Architecture: amd64\n\
Maintainer: DiagnoseMe Team\n\
Depends: docker.io, docker-compose, ${RTK_DEPENDS}libcurl4, libxmlb2, libjcat1, ethtool, usbutils, python3\n\
Description: Diagnose Me hardware verification tool\n" > DEBIAN/control

# Post-inst script to setup DKMS, desktop shortcuts and permissions. It is told
# the edition, whether it is a Maui edition (only Maui stations get the Maui
# desktop shortcuts and the bundled maui-utils image), whether it has the
# Realtek tools (only those stations build the r8152 DKMS driver), its
# firmware components and its Maui results directory. prerm unregisters the
# r8152 DKMS driver when a realtek edition package is removed.
# preinst keeps a copy of the old config.env, whose station specific values
# postinst moves into config.local.env.
COPY development_environment/deb_builder/preinst DEBIAN/preinst
COPY development_environment/deb_builder/postinst DEBIAN/postinst
COPY development_environment/deb_builder/prerm DEBIAN/prerm
RUN sed -i -e "s|@EDITION@|${EDITION}|" \
           -e "s|@MAUI_EDITION@|$(cat /edition/maui)|" \
           -e "s|@REALTEK_TOOLS@|$(cat /edition/realtek)|" \
           -e "s|@FIRMWARE_COMPONENTS@|$(cat /edition/firmware)|" \
           -e "s|@RESULTS_DIR@|$(cat /edition/results_dir)|" \
           DEBIAN/postinst DEBIAN/prerm \
    && chmod 755 DEBIAN/preinst DEBIAN/postinst DEBIAN/prerm

# Build the deb
RUN dpkg-deb --build . /diagnoseme.deb

FROM busybox
COPY --from=deb-builder /diagnoseme.deb /
