# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
####################################################################
FROM python:3-trixie AS dolostools

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
FROM python:3-trixie AS serverbuild

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

######################################################################
FROM debian:trixie AS fwupd-builder

RUN apt update
RUN apt install -y xz-utils wget build-essential dh-make devscripts
RUN wget https://github.com/fwupd/fwupd/releases/download/2.0.19/fwupd-2.0.19.tar.xz
RUN tar xvf fwupd-2.0.19.tar.xz

WORKDIR fwupd-2.0.19

RUN python3 ./contrib/ci/fwupd_setup_helpers.py install-dependencies -y --os debian -y
ENV DEBEMAIL="your-email@example.com"
ENV DEBFULLNAME="Your Name"
ENV DEB_BUILD_OPTIONS=nostrip=no

RUN dh_make -s -n -f "../fwupd-2.0.19.tar.xz" -y

RUN printf "\n\noverride_dh_auto_configure:\n\tdh_auto_configure -- \
    -Dauto_features=enabled \
    -Dbluez=disabled \
    -Dcbor=disabled \
    -Ddocs=disabled \
    -Dintrospection=disabled \
    -Dlibarchive=disabled \
    -Dlibdrm=disabled \
    -Dlibmnl=disabled \
    -Dpassim=disabled \
    -Dplugin_flashrom=disabled \
    -Dplugin_modem_manager=disabled \
    -Dpolkit=disabled \
    -Dprotobuf=disabled \
    -Dsystemd=disabled \
    -Dtests=false \
    -Dumockdev_tests=disabled \
    -Dvalgrind=disabled \
    -Dvalgrind=disabled \
    -Dbash_completion=false \
    -Dblkid=disabled \
    -Dbuild=standalone \
    -Dfirmware-packager=false \
    -Dfish_completion=false \
    -Dgnutls=disabled \
    -Dhsi=disabled \
    -Dlvfs=disabled \
    -Dman=false \
    -Dqubes=false \
    -Dreadline=disabled \
    -Dplugin_uefi_capsule_splash=false" >> debian/rules

RUN printf "\noverride_dh_shlibdeps:\n\t-dh_shlibdeps --dpkg-shlibdeps-params=--ignore-missing-info\n" >> debian/rules

RUN debuild -b -j$(nproc) -us -uc

#####################################################################
FROM python:3-trixie

RUN apt-get update && apt-get install -yq \
    python3-docker \
    dfu-util \
    usb.ids \
    libcurl3t64-gnutls \
    libxmlb2 \
    libjcat1

RUN pip3 install pip-tools

COPY dockerfiles/rpcserver/requirements.txt .
RUN pip3 install -r requirements.txt --require-hashes  --break-system-packages

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

COPY --link --from=fwupd-builder /fwupd_*_amd64.deb .
RUN apt install ./fwupd_*_amd64.deb

RUN rm -rf /install_tmp

RUN mkdir /usr/local/genesys/
RUN wget https://storage.googleapis.com/fwupd-cache/GenesysLogic_Google_Servo_GL3590_64.18.cab \
    -O /usr/local/genesys/GenesysLogic_GL3590_64.18.cab
WORKDIR /usr/local/servo_v4p1/
RUN wget https://storage.googleapis.com/chromeos-localmirror/distfiles/servo_v4p1_v2.0.29298-4d4a4e980.tar.xz \
    -O /usr/local/servo_v4p1/servo_v4p1_v2.0.29298-4d4a4e980.tar.xz
RUN tar xvf *
RUN rm *.tar.xz

#RUN python3 -m unittest discover -s server

RUN chmod og+w /var/log

ENV PYTHONPATH=/usr/lib/python3/dist-packages/

EXPOSE 6002

CMD ["rpcserver", "-v"]
