# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

FROM envoyproxy/envoy:debug-v1.30.0

ARG UID=246

RUN addgroup --gid $UID diagnoseme && adduser --ingroup diagnoseme --uid $UID --disabled-password --gecos "" diagnoseme

COPY dockerfiles/envoy/envoy.yaml /etc/envoy/envoy.yaml

USER diagnoseme

WORKDIR /var/log/envoy

CMD /usr/local/bin/envoy -c /etc/envoy/envoy.yaml
