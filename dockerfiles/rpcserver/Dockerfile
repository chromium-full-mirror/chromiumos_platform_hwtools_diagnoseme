# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
####################################################################
FROM alpine/git AS test_keys
WORKDIR /
RUN git clone --filter=blob:none --no-checkout --depth 1 --sparse https://chromium.googlesource.com/chromiumos/chromite
WORKDIR /chromite
RUN git sparse-checkout init --cone
RUN git sparse-checkout add ssh_keys/testing_rsa
RUN git checkout

#####################################################################
FROM python:3.11-bookworm AS dolostools

RUN apt-get update --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    git \
    apt-transport-https

WORKDIR /
RUN git clone https://chromium.googlesource.com/chromiumos/platform/dolos
RUN pip3 install -r /dolos/tools/requirements.txt --require-hashes --break-system-packages

WORKDIR /dolos/tools/doloscmd
RUN python3 setup.py bdist_wheel

WORKDIR /dolos/tools/dolosbattery
RUN python3 setup.py bdist_wheel

######################################################################
FROM python:3.11-bookworm AS serverbuild

RUN apt-get update --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    python3-build

WORKDIR /build/server

COPY src/server/*.py .
COPY src/generated/*.py .
COPY src/generated/*.pyi .

WORKDIR /build
COPY src/server/pyproject.toml .
ENV PYTHONPATH=/usr/lib/python3/dist-packages
RUN python3 -m build

#####################################################################
FROM python:3.11-bookworm

RUN apt-get update && apt-get install -yq \
    fping \
    python3-paramiko \
    python3-serial \
    python3-requests \
    ssh \
    python3-ftdi \
    python3-cffi \
    python3-bcrypt \
    python3-docker \
    git \
    sudo \
    vim

COPY dockerfiles/rpcserver/requirements.txt .
RUN pip3 install -r requirements.txt --require-hashes --break-system-packages

RUN mkdir /var/log/rpcserver
RUN mkdir /usr/local/tmp

WORKDIR /usr/local/server

RUN mkdir -p .ssh
COPY dockerfiles/rpcserver/ssh_config .ssh/config
COPY --from=test_keys /chromite/ssh_keys/testing_rsa .ssh/
RUN chmod -R 600 .ssh/*

WORKDIR /install_tmp
COPY --from=dolostools /dolos/tools/doloscmd/dist .
COPY --from=dolostools /dolos/tools/dolosbattery/dist .
COPY --from=dolostools /dolos/tools/requirements.txt .
RUN pip3 install *.whl --no-deps --no-index --break-system-packages
RUN pip3 install -r requirements.txt --require-hashes --break-system-packages

COPY --from=serverbuild /build/dist .
RUN pip3 install *.whl --no-deps --no-index --break-system-packages

RUN rm -rf /install_tmp

RUN chmod og+w /var/log

ENV PYTHONPATH=/usr/lib/python3/dist-packages/

EXPOSE 6002

CMD ["rpcserver", "-v"]
