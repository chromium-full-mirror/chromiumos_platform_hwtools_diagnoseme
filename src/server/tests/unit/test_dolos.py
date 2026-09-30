# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for dolos.py."""

import sys
import unittest
from unittest import mock


class FakeDolosConsoleError(Exception):
    """Fake DolosConsoleError for unit testing."""


# Mock doloscmd before importing dolos
mock_doloscmd = mock.MagicMock()
mock_doloscmd.error.DolosConsoleError = FakeDolosConsoleError
mock_doloscmd.console_lib.DolosConsoleError = FakeDolosConsoleError
sys.modules["doloscmd"] = mock_doloscmd
sys.modules["doloscmd.console_lib"] = mock_doloscmd.console_lib
sys.modules["doloscmd.error"] = mock_doloscmd.error

# pylint: disable=no-member
# pylint: disable=no-name-in-module
# pylint: disable=import-error
# pylint: disable=wrong-import-position
from server import dolos
from server.generated import diagnoseme_dolos_pb2


class TestDolosRpcService(unittest.TestCase):
    """Tests for DolosRpcService."""

    def setUp(self):
        self.servicer = dolos.DolosRpcService()
        self.context = mock.MagicMock()

    @mock.patch("google.cloud.storage.Client.create_anonymous_client")
    def test_get_firmware_versions(self, mock_storage):
        """Test get_firmware_versions."""
        mock_client = mock_storage.return_value
        blob1 = mock.MagicMock()
        blob1.name = "box_firmware/v1.0/default"
        blob2 = mock.MagicMock()
        blob2.name = "box_firmware/v1.1/other"
        mock_client.list_blobs.return_value = [blob1, blob2]

        response = self.servicer.get_firmware_versions(None, self.context)
        self.assertIn("v1.0", response.firmware_version)
        self.assertIn("v1.1", response.firmware_version)
        self.assertEqual(response.default_firmware_version, "v1.0")

    def test_check_dolos_from_host_success(self):
        """Test check_dolos_from_host success."""
        mock_dolos_inst = mock.MagicMock()
        mock_dolos_inst.serial = "DOLOS123"
        mock_dolos_inst.get_status.return_value = {}
        mock_dolos_inst.determine_status.return_value = (
            diagnoseme_dolos_pb2.DOLOS_STATUS.DOLOS_OK
        )

        mock_doloscmd.console_lib.DolosConsole.get_all_dolos_consoles.return_value = [
            mock_dolos_inst
        ]

        response = self.servicer.check_dolos_from_host(None, self.context)
        self.assertEqual(response.status, diagnoseme_dolos_pb2.TEST_STATUS.PASS)
        self.assertEqual(response.dolos_serial_number, "DOLOS123")

    @mock.patch("time.sleep")
    def test_program_cable_success(self, unused_mock_sleep):
        """Test program_cable success."""
        mock_dolos_inst = mock.MagicMock()
        mock_doloscmd.console_lib.DolosConsole.get_all_dolos_consoles.return_value = [
            mock_dolos_inst
        ]
        request = diagnoseme_dolos_pb2.ProgramCableRequest(
            hwid="HWID123", eeprom_data="data"
        )

        response = self.servicer.program_cable(request, self.context)
        self.assertTrue(response.success)
        mock_dolos_inst.program_cable.assert_called_once()
        mock_dolos_inst.repair.assert_called_once()

    @mock.patch("time.sleep")
    def test_program_cable_test_hwid_uses_first_word(self, unused_mock_sleep):
        """Test program_cable normalizes test HWIDs to model name."""
        mock_dolos_inst = mock.MagicMock()
        mock_doloscmd.console_lib.DolosConsole.get_all_dolos_consoles.return_value = [
            mock_dolos_inst
        ]
        request = diagnoseme_dolos_pb2.ProgramCableRequest(hwid="OCELOT TEST 7005")

        response = self.servicer.program_cable(request, self.context)
        self.assertTrue(response.success)
        mock_dolos_inst.program_cable.assert_called_once_with(
            "ocelot", file=None, new_serial=None
        )
        mock_dolos_inst.repair.assert_called_once()

    @mock.patch("time.sleep")
    def test_program_cable_hwid_validation_error_falls_back_to_model(
        self, unused_mock_sleep
    ):
        """Test program_cable falls back to first word on HWIDv3 validation error."""
        mock_dolos_inst = mock.MagicMock()
        mock_dolos_inst.program_cable.side_effect = [
            FakeDolosConsoleError("Hardware ID '7005' is not multiples of 3"),
            None,
        ]
        mock_doloscmd.console_lib.DolosConsole.get_all_dolos_consoles.return_value = [
            mock_dolos_inst
        ]
        request = diagnoseme_dolos_pb2.ProgramCableRequest(hwid="OCELOT-ABCD 7005")

        response = self.servicer.program_cable(request, self.context)
        self.assertTrue(response.success)
        self.assertEqual(mock_dolos_inst.program_cable.call_count, 2)
        mock_dolos_inst.program_cable.assert_any_call(
            "OCELOT-ABCD 7005", file=None, new_serial=None
        )
        mock_dolos_inst.program_cable.assert_called_with(
            "ocelot", file=None, new_serial=None
        )
        mock_dolos_inst.repair.assert_called_once()


if __name__ == "__main__":
    unittest.main()
