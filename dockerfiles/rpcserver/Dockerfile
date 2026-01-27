# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
####################################################################
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
    python3-docker \
    dfu-util

COPY dockerfiles/rpcserver/requirements.txt .
RUN pip3 install -r requirements.txt --require-hashes --break-system-packages

RUN mkdir /var/log/rpcserver
RUN mkdir /usr/local/tmp

WORKDIR /usr/local/server

WORKDIR /install_tmp
COPY --from=dolostools /dolos/tools/doloscmd/dist .
COPY --from=dolostools /dolos/tools/dolosbattery/dist .
COPY --from=dolostools /dolos/tools/requirements.txt .
RUN pip3 install *.whl --no-deps --no-index --break-system-packages
RUN pip3 install -r requirements.txt --require-hashes --break-system-packages

COPY --from=serverbuild /build/dist .
RUN pip3 install *.whl --no-deps --no-index --break-system-packages

RUN rm -rf /install_tmp

RUN python3 -m unittest discover -s server

RUN chmod og+w /var/log

ENV PYTHONPATH=/usr/lib/python3/dist-packages/

EXPOSE 6002

CMD ["rpcserver", "-v"]
