# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
####################################################################
FROM alpine/git as test_keys
WORKDIR /
RUN git clone --filter=blob:none --no-checkout --depth 1 --sparse https://chromium.googlesource.com/chromiumos/chromite
WORKDIR /chromite
RUN git sparse-checkout init --cone
RUN git sparse-checkout add ssh_keys/testing_rsa
RUN git checkout

#####################################################################
FROM python:3.11-bookworm as dolostools

RUN apt-get update --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    git \
    python3-pip

WORKDIR /workspace
COPY dockerfiles/rpcserver/requirements.txt .
RUN pip3 install -r requirements.txt --require-hashes --break-system-packages

WORKDIR /
RUN git clone https://chromium.googlesource.com/chromiumos/platform/dolos

WORKDIR /dolos/tools/doloscmd
RUN python3 setup.py bdist_wheel

WORKDIR /dolos/tools/dolosbattery
RUN python3 setup.py bdist_wheel

# #####################################################################
FROM us-docker.pkg.dev/chromeos-hw-tools/servod/servod:release

ARG UID=246
ARG GID=412

RUN addgroup --gid $UID diagnoseme && adduser --ingroup diagnoseme --uid $UID \
    --disabled-password --gecos "" diagnoseme

RUN addgroup --gid 402 serial
RUN adduser diagnoseme serial
RUN adduser diagnoseme dialout

RUN apt-get update && apt-get install -yq \
    fping \
    python3-paramiko \
    python3-serial \
    python3-requests \
    ssh \
    python3-ftdi \
    python3-cffi \
    python3-bcrypt \
    git \
    vim

WORKDIR /workspace
COPY dockerfiles/rpcserver/requirements.txt .
RUN pip3 install -r requirements.txt --require-hashes --break-system-packages

RUN mkdir /var/log/rpcserver
RUN chown diagnoseme /var/log/rpcserver
RUN mkdir /usr/local/tmp
RUN chown diagnoseme /usr/local/tmp

WORKDIR /usr/local/server

COPY --chown=diagnoseme src/server/* .
COPY --chown=diagnoseme src/generated/*.py .
COPY --chown=diagnoseme src/generated/*.pyi .

RUN mkdir -p /home/diagnoseme/.ssh
RUN chown diagnoseme /home/diagnoseme/.ssh
COPY --chown=diagnoseme  dockerfiles/rpcserver/ssh_config /home/diagnoseme/.ssh/config
COPY --chown=diagnoseme --from=test_keys /chromite/ssh_keys/testing_rsa /home/diagnoseme/.ssh/
RUN chmod -R 600 /home/diagnoseme/.ssh/*

COPY --from=dolostools /dolos/tools/doloscmd/dist .
COPY --from=dolostools /dolos/tools/dolosbattery/dist .
RUN pip3 install *.whl --break-system-packages

WORKDIR /builder/hdctools

RUN chmod og+w /var/log

RUN echo "diagnoseme  ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER diagnoseme

EXPOSE 6002

ENV PYTHONPATH=/usr/local/server:/usr/lib/python3/dist-packages/

RUN find . -print
RUN chmod u+x /usr/local/server/rpcserver.py

CMD ["/usr/local/server/rpcserver.py", "-v"]
