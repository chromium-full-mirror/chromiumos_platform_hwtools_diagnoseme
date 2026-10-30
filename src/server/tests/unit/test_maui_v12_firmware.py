# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for the Maui v1.2 MCU firmware choice: App vs full images and
the stable file of STABLE.json."""

# Generated protobuf members are not visible to pylint.
# pylint: disable=no-member

import hashlib
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

    @staticmethod
    def md5(path):
        """md5 of the file |path|."""
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()

    def stable(self, component, name, version="0.69-5324f43", md5=None):
        """Writes the STABLE.json manifest of the firmware sync (with the md5)."""
        path = os.path.join(self.root, component, name)
        if md5 is None and os.path.exists(path):
            with open(path, "rb") as f:
                md5 = hashlib.md5(f.read()).hexdigest()
        with open(
            os.path.join(self.root, component, mcu_service.STABLE_MANIFEST),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump({"version": version, "file": name, "md5": md5, "source": "gs"}, f)


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
    """STABLE.json names the default file of a component (fails closed, D3)."""

    def choose(self, component, file_name="", accept=None):
        """select_firmware_file() with the test's firmware root."""
        return mcu_service.select_firmware_file(component, file_name, accept)

    def test_stable_file_is_the_default(self):
        """The stable file wins over newer files."""
        stable = self.add(APP, "maui_app_signed.bin")
        self.add(APP, "maui_app_test_build.bin")
        self.stable(APP, "maui_app_signed.bin")
        self.assertEqual(mcu_service.stable_firmware(APP), (stable, "0.69-5324f43"))
        self.assertEqual(self.choose(APP), mcu_service.FirmwareChoice(path=stable))
        self.assertEqual(mcu_service.stable_marker_error(APP), "")

    def test_invalid_manifest_fails_with_an_update_hint(self):
        """Missing image, MD5 mismatch or unreadable STABLE.json: no guessing."""
        old = self.add(APP, "old.bin")
        self.add(APP, "new.bin")
        cases = (
            ("gone.bin", None, "does not exist"),
            ("old.bin", "0" * 32, "MD5 mismatch"),
            ("../old.bin", None, "invalid file"),
        )
        for name, md5, expected in cases:
            self.stable(APP, name, md5=md5)
            choice = self.choose(APP)
            self.assertEqual(choice.path, "", name)
            self.assertIn(expected, choice.error)
            self.assertIn("Update the station firmware", choice.error)
            self.assertEqual(mcu_service.stable_firmware(APP), ("", ""))
            self.assertIn(expected, mcu_service.stable_marker_error(APP))
            # An explicitly selected file of that directory fails too.
            self.assertIn(expected, self.choose(APP, "old.bin").error)
        with open(
            os.path.join(self.root, APP, mcu_service.STABLE_MANIFEST),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("{not json")
        self.assertIn("unreadable", self.choose(APP).error)
        self.assertNotEqual(old, "")

    def test_without_manifest_only_a_single_image_is_used(self):
        """No STABLE.json: one image with a warning, several are an error."""
        only = self.add(APP, "old.bin")
        choice = self.choose(APP)
        self.assertEqual(choice.path, only)
        self.assertIn("No STABLE.json", choice.warning)
        self.assertEqual(choice.error, "")
        self.add(APP, "new.bin")
        choice = self.choose(APP)
        self.assertEqual(choice.path, "")
        self.assertIn("2 mcu_app images", choice.error)
        self.assertIn("new.bin, old.bin", choice.error)
        # The operator may still choose one explicitly (md5 in the step log).
        choice = self.choose(APP, "old.bin")
        self.assertEqual((choice.path, choice.error), (only, ""))
        self.assertIn(f"Selected image old.bin (md5 {self.md5(only)}", choice.warning)
        self.assertIn("no STABLE.json", choice.warning)
        self.assertEqual(self.choose(APP, "missing.bin"), mcu_service.FirmwareChoice())
        self.assertIn("Invalid firmware file name", self.choose(APP, "../x.bin").error)

    def test_explicit_pick_is_logged_with_md5_and_version(self):
        """An operator-picked image other than the stable one is non-stable."""
        stable = self.add(APP, "maui_app_signed.bin")
        other = self.add(APP, "maui_app_0.70-abcdef1.bin")
        self.stable(APP, "maui_app_signed.bin")
        choice = self.choose(APP, "maui_app_0.70-abcdef1.bin")
        self.assertEqual(choice.path, other)
        self.assertEqual(choice.note, "")
        self.assertEqual(
            choice.warning,
            f"Selected image maui_app_0.70-abcdef1.bin (md5 {self.md5(other)}, "
            "version 0.70-abcdef1) is a non-stable image: STABLE.json names "
            "maui_app_signed.bin (version 0.69-5324f43).",
        )
        choice = self.choose(APP, "maui_app_signed.bin")
        self.assertEqual((choice.path, choice.warning), (stable, ""))
        self.assertEqual(
            choice.note,
            f"Selected image maui_app_signed.bin (md5 {self.md5(stable)}, version "
            "0.69-5324f43): the stable image (STABLE.json).",
        )

    def test_no_firmware(self):
        """Without firmware nothing is chosen and nothing fails."""
        self.assertEqual(self.choose(APP), mcu_service.FirmwareChoice())

    def test_stable_image_must_be_accepted(self):
        """A stable full image in mcu_app/ is never the App default."""
        self.add(APP, "maui_app_signed.bin")
        self.add(APP, "maui_full_flash_1mb.bin", MIB)
        self.stable(APP, "maui_full_flash_1mb.bin")
        choice = self.choose(APP, accept=lambda p: not mcu_service.app_image_error(p))
        self.assertEqual(choice.path, "")
        self.assertIn("cannot be used here", choice.error)

    def test_unaccepted_files_do_not_count_without_a_manifest(self):
        """Files the step cannot use do not count as candidates."""
        app = self.add(APP, "maui_app_signed.bin")
        self.add(APP, "maui_full_flash_1mb.bin", MIB)
        choice = self.choose(APP, accept=lambda p: not mcu_service.app_image_error(p))
        self.assertEqual(choice.path, app)
        self.assertIn("No STABLE.json", choice.warning)

    def test_full_image_default(self):
        """The stable full image if it has 1 MiB; a short stable one fails."""
        good = self.add(FULL, "maui_full_flash_1mb_0.69-5324f43.bin", MIB)
        self.add(FULL, "short.bin", 10)

        def accept(path):
            return not ite_flash.check_image(path)

        self.assertEqual(self.choose(FULL, accept=accept).path, good)
        self.stable(FULL, "short.bin")
        self.assertIn("cannot be used here", self.choose(FULL, accept=accept).error)
        self.stable(FULL, os.path.basename(good), "0.69-5324f43")
        self.assertEqual(
            self.choose(FULL, accept=accept), mcu_service.FirmwareChoice(path=good)
        )


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

    def test_invalid_manifest_is_reported(self):
        """A broken STABLE.json marks nothing stable and says why."""
        self.add(FULL, "maui_full_flash_1mb.bin", MIB)
        self.stable(FULL, "maui_full_flash_1mb.bin", md5="0" * 32)
        resp = self._list(FULL)
        self.assertFalse(any(f.is_stable for f in resp.files))
        self.assertIn("Invalid stable mcu_full firmware", resp.message)
        self.assertIn("MD5 mismatch", resp.message)


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

    async def test_invalid_stable_manifest_fails_closed(self):
        """M4: a broken STABLE.json fails the update with an update hint."""
        self.add(APP, "maui_app_signed.bin")
        self.stable(APP, "maui_app_signed.bin", md5="0" * 32)
        for file_name in ("", "maui_app_signed.bin"):
            final = await self._update(file_name)
            self.assertFalse(final.success)
            self.assertIn("MD5 mismatch", final.error_message)
            self.assertIn("Update the station firmware", final.error_message)

    async def test_several_images_without_manifest_fail(self):
        """Several App images without STABLE.json are never guessed."""
        self.add(APP, "maui_app_0.69-5324f43.bin")
        self.add(APP, "maui_app_0.70-aaaaaaa.bin")
        final = await self._update("")
        self.assertIn("2 mcu_app images", final.error_message)

    async def test_picked_non_stable_image_is_marked_in_the_step_log(self):
        """The App update log names the picked image, its md5 and 'non-stable'."""
        self.add(APP, "maui_app_signed.bin")
        other = self.add(APP, "maui_app_0.70-abcdef1.bin")
        self.stable(APP, "maui_app_signed.bin")
        context = mock.MagicMock()
        context.done.return_value = False

        async def failed_flash(unused_port, unused_fw, unused_context, run):
            run["returncode"] = 1
            run["output"].append("maui-flash: failed")
            for update in ():
                yield update

        with mock.patch.object(
            mcu_service.MauiMcuServicer, "_run_maui_flash", side_effect=failed_flash
        ):
            out = [
                r
                async for r in mcu_service.MauiMcuServicer().update_mcu_app(
                    maui_manufacturing_pb2.MauiMcuUpdateRequest(
                        file_name="maui_app_0.70-abcdef1.bin"
                    ),
                    context,
                )
            ]
        self.assertIn("non-stable image", out[0].log_line)
        self.assertIn(self.md5(other), out[0].log_line)
        self.assertFalse(out[-1].success)
        logged = mcu_service.write_device_log.call_args[0][2]
        self.assertIn(f"md5 {self.md5(other)}", logged)
        self.assertIn("is a non-stable image", logged)

    async def test_no_app_image_at_all(self):
        """Without App images the default selection finds nothing."""
        self.add(APP, "maui_full_flash_1mb.bin", MIB)
        final = await self._update("")
        self.assertIn("not found", final.error_message)


if __name__ == "__main__":
    unittest.main()
