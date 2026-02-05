# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Helper functions to find required files on the system."""

from pathlib import Path
import subprocess
from typing import Any
from typing import List
from typing import Optional


BIN_DIR = "binfiles"


def get_bindir() -> Path:
    """Get the directory with the manufacturing binary files.

    Returns:
      Path object representing the |BIN_DIR| absolute path.
    """
    return Path(__file__).resolve().parent / BIN_DIR


def find_binfile(binfile: str) -> Optional[Path]:
    """Find the full path for |binfile| if available.

    Args:
      binfile: name of the binfile to find.

    Returns:
      Path to |binfile| if found in |BIN_DIR|, or None if not found.
    """
    path = get_bindir() / binfile
    return path if path.exists() else None


def run_command(cmd: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Run a subprocess command with common default arguments.

    Args:
      cmd: command to run as a list of strings.
      **kwargs: additional arguments passed to subprocess.run.

    Returns:
      CompletedProcess instance.
    """
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    check = kwargs.pop("check", True)
    return subprocess.run(cmd, check=check, **kwargs)
