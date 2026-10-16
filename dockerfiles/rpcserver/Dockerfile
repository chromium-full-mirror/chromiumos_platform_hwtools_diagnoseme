# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

ARG BASE_IMAGE=server-base:latest
FROM ${BASE_IMAGE} AS base

######################################################################
FROM python:3-trixie AS serverbuild

RUN apt-get update --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    python3-build

WORKDIR /build/server

COPY src/server/*.py .
COPY src/server/generated/ ./generated/

WORKDIR /build
COPY src/server/pyproject.toml .
ENV PYTHONPATH=/usr/lib/python3/dist-packages
RUN python3 -m build

#####################################################################
FROM base

# DiagnoseMe edition (see editions.json); selects the registered services.
ARG EDITION=all
ENV DIAGNOSEME_EDITION=${EDITION}
# maui-utils image (Maui host tools) that local builds fetched (the stable
# release, see tools/fetch_maui_utils.sh and src/server/maui_utils_runner.py).
# Stations pass MAUI_UTILS_IMAGE from config.env; when it is empty the server
# uses the newest local maui-utils image.
ARG MAUI_UTILS_VERSION=
ENV MAUI_UTILS_IMAGE=${MAUI_UTILS_VERSION:+maui-utils:}${MAUI_UTILS_VERSION}

COPY --from=serverbuild /build/dist/ /install_tmp/
WORKDIR /install_tmp
RUN python3 -m pip install *.whl --no-deps --no-index --break-system-packages
WORKDIR /usr/local/server
RUN rm -rf /install_tmp

# Copy offline firmware into /usr/local/server/firmware and
# /var/lib/diagnoseme/firmware (one directory per component of dist/firmware).
RUN mkdir -p /usr/local/server/firmware /var/lib/diagnoseme/firmware \
             /root/diagnoseme_results /var/log/rpcserver
COPY dist/firmware/ /usr/local/server/firmware/
RUN cp -r /usr/local/server/firmware/* /var/lib/diagnoseme/firmware/ 2>/dev/null || true

# binfiles_shadow is empty on non-servo editions; never block the server start.
CMD cp -r /usr/local/server/binfiles_shadow/. /usr/local/server/binfiles/ 2>/dev/null; rpcserver -v
