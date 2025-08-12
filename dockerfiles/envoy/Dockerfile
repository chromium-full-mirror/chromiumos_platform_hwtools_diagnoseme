# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

FROM envoyproxy/envoy:v1.33-latest

COPY dockerfiles/envoy/envoy.yaml /etc/envoy/envoy.yaml

WORKDIR /var/log/envoy

CMD ["/usr/local/bin/envoy", "-c", "/etc/envoy/envoy.yaml"]
