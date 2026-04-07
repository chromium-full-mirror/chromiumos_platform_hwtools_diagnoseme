# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
FROM python:3.11-slim

RUN pip install flask

WORKDIR /app
COPY mock_sink_server.py .

EXPOSE 8080

CMD ["python", "mock_sink_server.py"]
