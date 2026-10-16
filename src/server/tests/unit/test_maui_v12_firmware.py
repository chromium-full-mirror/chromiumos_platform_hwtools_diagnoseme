# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for the Maui v1.2 MCU firmware choice: App vs full images and
the stable file of STABLE.json."""

# Generated protobuf members are not visible to pylint.
# pylint: disable=no-member

import json
import os
import tempfile
import unittest
from unittest import mock

from server import maui_v12_device as device
from server import maui_v12_ite_flash as ite_flash
from server import maui_v12_mcu_service as mcu_service
from server import maui_v12_profile as profile
from server.generated import maui_manufacturing_pb2


MIB = profile.MCU_FULL_IMAGE_SIZE
APP = profile.FW_COMPONENT_MCU_APP
FULL = profile.FW_COMPONENT_MCU_FULL


class _FirmwareTestCase(unittest.IsolatedAsyncioTestCase):
    """A temporary firmware root with mcu_app/ and mcu_full/."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.mtime = 1000
        for component in (APP, FULL):
            os.makedirs(os.path.join(self.root, component))
        patcher = mock.patch.object(mcu_service, "FIRMWARE_ROOTS", (self.root,))
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, component, name, size=1000):
        """Creates a firmware file; later files are newer."""
        path = os.path.join(self.root, component, name)
        with open(path, "wb") as f:
            f.write(b"\xa5" * size)
        self.mtime += 10
        os.utime(path, (self.mtime, self.mtime))
        return path

    def stable(self, component, name, version="0.69-5324f43"):
        """Writes the STABLE.json manifest of the firmware sync."""
        with open(
            os.path.join(self.root, component, mcu_service.STABLE_MANIFEST),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump({"version": version, "file": name, "md5": "x", "source": "gs"}, f)


class TestImageKinds(_FirmwareTestCase):
    """app_image_error(): only App images go to maui-flash --target app."""

    def test_app_image_error(self):
        """Full images (by name or 1 MiB size) and bootloaders are refused."""
        self.assertEqual(mcu_service.app_image_error(self.add(APP, "maui_app.bin")), "")
        full = mcu_service.app_image_error(self.add(APP, "maui_full_flash_1mb.bin"))
        self.assertIn("is a full MCU image", full)
        self.assertIn("itecomdbgr", full)
        self.assertIn(
            "bootloader image",
            mcu_service.app_image_error(self.add(APP, "maui_rw_bootloader_signed.bin")),
        )
        by_size = mcu_service.app_image_error(self.add(APP, "renamed.bin", MIB))
        self.assertIn("1048576 bytes", by_size)
        self.assertIn("Cannot read", mcu_service.app_image_error("/nonexistent.bin"))

    def test_version_from_name(self):
        """Build ids in file names are reported."""
        self.assertEqual(
            mcu_service.version_from_name("maui_full_flash_1mb_0.69-5324f43.bin"),
            "0.69-5324f43",
        )
        self.assertEqual(mcu_service.version_from_name("maui_app_signed.bin"), "")


class TestStableFile(_FirmwareTestCase):
    """STABLE.json names the default file of a component."""

    def test_stable_file_is_the_default(self):
        """The stable file wins over newer files."""
        stable = self.add(APP, "maui_app_signed.bin")
        self.add(APP, "maui_app_test_build.bin")
        self.stable(APP, "maui_app_signed.bin")
        self.assertEqual(mcu_service.stable_firmware(APP), (stable, "0.69-5324f43"))
        self.assertEqual(mcu_service.select_firmware_file(APP), stable)

    def test_without_usable_manifest_the_newest_file_wins(self):
        """Missing, broken or wrong manifests fall back to the newest file."""
        self.add(APP, "old.bin")
        newest = self.add(APP, "new.bin")
        self.assertEqual(mcu_service.select_firmware_file(APP), newest)
        self.stable(APP, "gone.bin")
        self.assertEqual(mcu_service.stable_firmware(APP), ("", ""))
        self.assertEqual(mcu_service.select_firmware_file(APP), newest)
        with open(
            os.path.join(self.root, APP, mcu_service.STABLE_MANIFEST),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("{not json")
        self.assertEqual(mcu_service.select_firmware_file(APP), newest)

    def test_default_skips_files_that_are_not_accepted(self):
        """A stable full image in mcu_app/ is never the App default."""
        app = self.add(APP, "maui_app_signed.bin")
        self.add(APP, "maui_full_flash_1mb.bin", MIB)
        self.stable(APP, "maui_full_flash_1mb.bin")
        self.assertEqual(
            mcu_service.select_firmware_file(
                APP, accept=lambda p: not mcu_service.app_image_error(p)
            ),
            app,
        )

    def test_full_image_default(self):
        """The stable full image if it has 1 MiB, else the newest one that has."""
        good = self.add(FULL, "maui_full_flash_1mb_0.69-5324f43.bin", MIB)
        self.add(FULL, "short.bin", 10)

        def accept(path):
            return not ite_flash.check_image(path)

        self.assertEqual(mcu_service.select_firmware_file(FULL, accept=accept), good)
        self.stable(FULL, "short.bin")
        self.assertEqual(mcu_service.select_firmware_file(FULL, accept=accept), good)
        self.stable(FULL, os.path.basename(good), "0.69-5324f43")
        self.assertEqual(mcu_service.select_firmware_file(FULL, accept=accept), good)


class TestListFirmware(_FirmwareTestCase):
    """list_firmware(): App images only for mcu_app, stable info for all."""

    def _list(self, component):
        return mcu_service.MauiMcuServicer().list_firmware(
            maui_manufacturing_pb2.MauiListFirmwareRequest(component=component), None
        )

    def test_app_list_has_only_app_images(self):
        """Full images and bootloaders are not offered for the App update."""
        self.add(APP, "maui_app_signed.bin")
        self.add(APP, "maui_full_flash_1mb.bin", MIB)
        self.add(APP, "maui_rw_bootloader_signed.bin")
        self.stable(APP, "maui_app_signed.bin", "0.70-abcdef1")
        resp = self._list(APP)
        self.assertEqual([f.file_name for f in resp.files], ["maui_app_signed.bin"])
        self.assertTrue(resp.files[0].is_stable)
        self.assertEqual(resp.files[0].version, "0.70-abcdef1")
        self.assertIn("Not MCU App images (skipped)", resp.message)
        self.assertIn("maui_full_flash_1mb.bin", resp.message)

    def test_full_list_marks_stable_and_sizes(self):
        """Full images carry the 1 MiB size and the stable flag."""
        self.add(FULL, "maui_full_flash_1mb_0.68-1111111.bin", MIB)
        self.add(FULL, "maui_full_flash_1mb.bin", MIB)
        self.stable(FULL, "maui_full_flash_1mb.bin", "0.69-5324f43")
        files = {f.file_name: f for f in self._list(FULL).files}
        self.assertTrue(files["maui_full_flash_1mb.bin"].is_stable)
        self.assertEqual(files["maui_full_flash_1mb.bin"].version, "0.69-5324f43")
        old = files["maui_full_flash_1mb_0.68-1111111.bin"]
        self.assertFalse(old.is_stable)
        self.assertEqual(old.version, "0.68-1111111")
        self.assertEqual(old.expected_size_bytes, MIB)


class TestAppUpdateRefusesFullImages(_FirmwareTestCase):
    """update_mcu_app never hands a full image to maui-flash --target app."""

    def setUp(self):
        super().setUp()
        console = device.McuConsole(
            dev_node="/dev/ttyACM0",
            usb_path="/sys/usb1/1-2/1-2.1",
            serial="MAUIV12H26010001",
            product=profile.PRODUCT_APP,
            stage=profile.STAGE_APP,
        )
        patches = [
            mock.patch.object(
                device, "find_unit", return_value=device.UnitInfo(mcu=console)
            ),
            mock.patch.object(device, "scan_mcu_consoles", return_value=[console]),
            mock.patch.object(mcu_service, "write_device_log"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _update(self, file_name):
        context = mock.MagicMock()
        context.done.return_value = False
        with mock.patch.object(
            mcu_service.asyncio, "create_subprocess_exec"
        ) as run_tool:
            out = [
                r
                async for r in mcu_service.MauiMcuServicer().update_mcu_app(
                    maui_manufacturing_pb2.MauiMcuUpdateRequest(file_name=file_name),
                    context,
                )
            ]
        run_tool.assert_not_called()
        return out[-1]

    async def test_full_image_by_name(self):
        """A full_flash file is refused with a pointer to itecomdbgr."""
        self.add(APP, "maui_full_flash_1mb.bin", 4096)
        final = await self._update("maui_full_flash_1mb.bin")
        self.assertFalse(final.success)
        self.assertIn("is a full MCU image", final.error_message)
        self.assertIn("itecomdbgr", final.error_message)

    async def test_full_image_by_size(self):
        """Any 1 MiB file is a full image, whatever its name."""
        self.add(APP, "maui_app_signed.bin", MIB)
        final = await self._update("maui_app_signed.bin")
        self.assertIn("1048576 bytes", final.error_message)

    async def test_no_app_image_at_all(self):
        """Without App images the default selection finds nothing."""
        self.add(APP, "maui_full_flash_1mb.bin", MIB)
        final = await self._update("")
        self.assertIn("not found", final.error_message)


if __name__ == "__main__":
    unittest.main()
