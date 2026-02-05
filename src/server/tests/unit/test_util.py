# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for util.py."""

from pathlib import Path
import unittest
from unittest import mock

from server import util


class TestUtil(unittest.TestCase):
    """Tests for utility functions."""

    def test_get_bindir(self):
        """Test get_bindir returns a Path object ending with BIN_DIR."""
        bindir = util.get_bindir()
        self.assertIsInstance(bindir, Path)
        self.assertEqual(bindir.name, util.BIN_DIR)
        self.assertTrue(bindir.is_absolute())

    @mock.patch("pathlib.Path.exists")
    def test_find_binfile_found(self, mock_exists):
        """Test find_binfile returns Path when file exists."""
        mock_exists.return_value = True
        filename = "test_file.bin"
        result = util.find_binfile(filename)

        self.assertIsInstance(result, Path)
        self.assertEqual(result.name, filename)
        self.assertEqual(result.parent.name, util.BIN_DIR)

    @mock.patch("pathlib.Path.exists")
    def test_find_binfile_not_found(self, mock_exists):
        """Test find_binfile returns None when file does not exist."""
        mock_exists.return_value = False
        result = util.find_binfile("nonexistent.bin")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
