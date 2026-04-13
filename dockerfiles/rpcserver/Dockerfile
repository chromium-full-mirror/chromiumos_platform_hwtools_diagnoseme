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

COPY --from=serverbuild /build/dist/ /install_tmp/
WORKDIR /install_tmp
RUN python3 -m pip install *.whl --no-deps --no-index --break-system-packages
WORKDIR /usr/local/server
RUN rm -rf /install_tmp

CMD cp -r /usr/local/server/binfiles_shadow/* /usr/local/server/binfiles/ && rpcserver -v
