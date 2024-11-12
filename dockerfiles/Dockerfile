# syntax=docker/dockerfile:1.4
# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

######################################################################

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y \
    python3 \
    python3-pytest \
    python3-pytest-cov \
    python3-pytest-forked \
    python3-pytest-mock \
    python3-pytest-xdist

WORKDIR /diagnoseme

COPY / /diagnoseme/
