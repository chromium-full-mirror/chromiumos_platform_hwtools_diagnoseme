#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A simple mock server to receive and log servo manufacturing results."""

import json
import logging

from flask import Flask  # pylint: disable=import-error
from flask import jsonify  # pylint: disable=import-error
from flask import request  # pylint: disable=import-error


app = Flask(__name__)

# Configure logging to stdout
logging.basicConfig(level=logging.INFO)


@app.route("/", methods=["POST"])
def receive_results():
    """Endpoint to receive results via POST."""
    if not request.is_json:
        logging.error("Received non-JSON request: %s", request.data)
        return jsonify({"success": False, "message": "Expected JSON"}), 400

    results = request.get_json()
    logging.info("Received results: %s", json.dumps(results, indent=2))

    return jsonify({"success": True, "message": "Results received"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
