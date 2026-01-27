# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Load in the public ge build json config and extract the board, model
mapping from that file and write it out into a file called mapping.csv.

typically run as part of the associated dockerfile by:

docker build --output "." --target copytohost -f Dockerfile.mapping .

"""

import json


JSON_PATH = "/chromite/config/ge_build_config.json"

with open(JSON_PATH, "r", encoding="utf-8") as in_file:
    with open("mapping.csv", "w", encoding="utf-8") as out_file:
        data = json.load(in_file)
        for build in data.get("reference_board_unified_builds"):
            for model in build.get("models"):
                board = model.get("board_name")
                if "-" not in board:
                    out_file.write(f"{model.get('name')},{board}\n")
