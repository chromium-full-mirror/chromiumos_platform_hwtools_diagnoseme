# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for tools/maui_fw_fetch.py (shared Maui firmware fetcher)."""

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.parse
from xml.sax.saxutils import escape


# pylint: disable=missing-function-docstring
# pylint: disable=too-many-instance-attributes


REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)


def _load_tool(name):
    """Imports tools/<name>.py, which is not part of a package."""
    path = os.path.join(REPO_ROOT, "tools", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fw_fetch = _load_tool("maui_fw_fetch")

BASE_URL = "https://storage.test/maui-firmware"
NS = "http://doc.s3.amazonaws.com/2006-03-01"


def _md5(data):
    """Returns the hex md5 of |data|."""
    return hashlib.md5(data).hexdigest()


def _crc32c_header(data):
    """Returns the x-goog-hash crc32c value of |data| (base64, big endian)."""
    crc = fw_fetch._crc32c_update_py(0, data)  # pylint: disable=protected-access
    return base64.b64encode(crc.to_bytes(4, "big")).decode()


class _Response(io.BytesIO):
    """Minimal urlopen() response."""

    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}


class FakeGcs:
    """Serves a GCS XML listing and object downloads from a dict."""

    def __init__(self, objects, page_size=1000, md5_in_listing=True):
        # key -> content (bytes)
        self.objects = dict(objects)
        self.page_size = page_size
        self.md5_in_listing = md5_in_listing
        self.requests = []
        # (method, url, timeout) of every request.
        self.calls = []
        self.corrupt = set()
        # key -> x-goog-hash header of the object (default: its real crc32c
        # only, like a composite object).
        self.goog_hash = {}

    def listing_xml(self, prefix, marker):
        """Returns one listing page of the objects below |prefix|."""
        keys = sorted(k for k in self.objects if k.startswith(prefix) and k > marker)
        page = keys[: self.page_size]
        truncated = len(keys) > len(page)
        parts = [f'<?xml version="1.0"?><ListBucketResult xmlns="{NS}">']
        parts.append(f"<Name>maui-firmware</Name><Prefix>{escape(prefix)}</Prefix>")
        if truncated:
            parts.append("<IsTruncated>true</IsTruncated>")
        for key in page:
            data = self.objects[key]
            etag = _md5(data) if self.md5_in_listing else "CJ7m0+Pjx/0CEAE="
            parts.append(
                f"<Contents><Key>{escape(key)}</Key>"
                f"<LastModified>2026-08-21T09:20:21.000Z</LastModified>"
                f'<ETag>"{etag}"</ETag><Size>{len(data)}</Size></Contents>'
            )
        parts.append("</ListBucketResult>")
        return "".join(parts).encode()

    def urlopen(self, request, timeout=None):
        """Fake urllib.request.urlopen()."""
        url = request.full_url
        self.requests.append(url)
        self.calls.append((request.get_method(), url, timeout))
        parsed = urllib.parse.urlparse(url)
        if parsed.query:
            query = urllib.parse.parse_qs(parsed.query)
            return _Response(
                self.listing_xml(query["prefix"][0], query.get("marker", [""])[0])
            )
        key = urllib.parse.unquote(parsed.path[len("/maui-firmware/") :])
        if key not in self.objects:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        data = self.objects[key]
        headers = {
            "x-goog-hash": self.goog_hash.get(key, f"crc32c={_crc32c_header(data)}")
        }
        if key in self.corrupt:
            data = b"X" * len(data)
        return _Response(data, headers)

    def object_requests(self):
        """Returns the download (non-listing) requests."""
        return [u for u in self.requests if "?" not in u]

    def download_requests(self):
        """Returns the GET requests of objects (no listings, no HEAD)."""
        return [u for m, u, _ in self.calls if m == "GET" and "?" not in u]


BUCKET_CONTENT = {
    "mcu-fw/": b"",
    "mcu-fw/mspm0/0.59-f408714/zephyr.txt": b"mcu 0.59",
    "mcu-fw/mspm0/0.60-042f858/": b"",
    "mcu-fw/mspm0/0.60-042f858/stable.maui_v11.mcufw": b"",
    "mcu-fw/mspm0/0.60-042f858/stable.maui_proto.mcufw": b"",
    "mcu-fw/mspm0/0.60-042f858/stable.mcufw": b"",
    "mcu-fw/mspm0/0.60-042f858/zephyr.txt": b"mcu 0.60",
    "pdc-fw/v20_0325/v20_0325.bin": b"pdc v20 25W",
    "pdc-fw/v20_0325/stable.maui_proto.pdcfw": b"",
    "pdc-fw/v20_0325/stable.pdcfw": b"",
    "pdc-fw/v20_0325_100W/v20_0325_100W.bin": b"pdc v20 100W",
    "pdc-fw/v20_0325_100W/stable.maui_v11.pdcfw": b"",
}


class FetchTestCase(unittest.TestCase):
    """Common setup: a fake bucket and a temporary destination root."""

    def setUp(self):
        self.gcs = FakeGcs(BUCKET_CONTENT)
        self.bucket = fw_fetch.Bucket(BASE_URL, retries=0, urlopen=self.gcs.urlopen)
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.dest = tmp.name

    def run_main(self, *argv, device="maui_v11"):
        """Runs main(); returns (exit status, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = fw_fetch.main(
                ["--dest-root", self.dest, "--device", device, *argv],
                bucket=self.bucket,
            )
        return status, out.getvalue(), err.getvalue()

    def read(self, *path):
        """Returns the content of a file below the destination root."""
        with open(os.path.join(self.dest, *path), "rb") as f:
            return f.read()

    def manifest(self, component):
        """Returns the STABLE.json of |component| below the destination."""
        return json.loads(self.read(component, "STABLE.json"))


class TestListing(FetchTestCase):
    """Tests for the bucket listing."""

    def test_parse_listing_strips_namespace_and_reads_md5(self):
        objects, truncated, marker = fw_fetch.parse_listing(
            self.gcs.listing_xml("pdc-fw/v20_0325/", "")
        )
        self.assertFalse(truncated)
        self.assertEqual(marker, "")
        by_key = {o.key: o for o in objects}
        payload = by_key["pdc-fw/v20_0325/v20_0325.bin"]
        self.assertEqual(payload.size, len(b"pdc v20 25W"))
        self.assertEqual(payload.md5, _md5(b"pdc v20 25W"))
        self.assertEqual(payload.name, "v20_0325.bin")

    def test_composite_etag_is_not_an_md5(self):
        self.gcs.md5_in_listing = False
        objects, _, _ = fw_fetch.parse_listing(self.gcs.listing_xml("mcu-fw/", ""))
        self.assertTrue(objects)
        self.assertTrue(all(o.md5 == "" for o in objects))

    def test_list_follows_pagination(self):
        self.gcs.page_size = 2
        keys = [o.key for o in self.bucket.list("mcu-fw/")]
        self.assertEqual(
            keys, sorted(k for k in BUCKET_CONTENT if k.startswith("mcu-fw/"))
        )
        self.assertEqual(len(self.gcs.requests), 4)

    def test_invalid_listing_raises(self):
        with self.assertRaises(fw_fetch.FetchError):
            fw_fetch.parse_listing(b"<html>")


class TestResolve(FetchTestCase):
    """Tests for the stable marker resolution."""

    def test_device_marker_selects_the_version(self):
        mcu = fw_fetch.COMPONENTS["mcu"]
        version, payload = fw_fetch.resolve(self.bucket, mcu, "maui_v11")
        self.assertEqual(version, "0.60-042f858")
        self.assertEqual(payload.key, "mcu-fw/mspm0/0.60-042f858/zephyr.txt")
        pdc = fw_fetch.COMPONENTS["pdc"]
        self.assertEqual(
            fw_fetch.resolve(self.bucket, pdc, "maui_v11")[1].name,
            "v20_0325_100W.bin",
        )
        self.assertEqual(
            fw_fetch.resolve(self.bucket, pdc, "maui_proto")[1].name, "v20_0325.bin"
        )

    def test_legacy_markers_are_ignored(self):
        # stable.pdcfw (no device) does not count for any device.
        del self.gcs.objects["pdc-fw/v20_0325_100W/stable.maui_v11.pdcfw"]
        self.assertIsNone(
            fw_fetch.resolve(self.bucket, fw_fetch.COMPONENTS["pdc"], "maui_v11")
        )

    def test_highest_version_of_duplicate_markers_wins(self):
        self.gcs.objects["mcu-fw/mspm0/0.59-f408714/stable.maui_v11.mcufw"] = b""
        original = self.gcs.listing_xml

        def listing_xml(prefix, marker):
            xml = original(prefix, marker).decode()
            # The 0.59 marker is the most recent one; the version decides.
            return xml.replace(
                "<Key>mcu-fw/mspm0/0.59-f408714/stable.maui_v11.mcufw</Key>"
                "<LastModified>2026-08-21T09:20:21.000Z",
                "<Key>mcu-fw/mspm0/0.59-f408714/stable.maui_v11.mcufw</Key>"
                "<LastModified>2026-09-30T00:00:00.000Z",
            ).encode()

        self.gcs.listing_xml = listing_xml
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            version, _ = fw_fetch.resolve(
                self.bucket, fw_fetch.COMPONENTS["mcu"], "maui_v11"
            )
        self.assertEqual(version, "0.60-042f858")
        self.assertIn("2 'stable.maui_v11.mcufw' markers", err.getvalue())

    def test_versions_compare_numerically(self):
        # A text sort puts 0.0.1-69 after 0.0.1-100.
        self.gcs.objects.update(
            {
                "mcu-fw/mspm0/0.100-aaaaaaa/zephyr.txt": b"mcu 0.100",
                "mcu-fw/mspm0/0.100-aaaaaaa/stable.maui_v11.mcufw": b"",
                "mcu-fw/mspm0/0.69-bbbbbbb/zephyr.txt": b"mcu 0.69",
                "mcu-fw/mspm0/0.69-bbbbbbb/stable.maui_v11.mcufw": b"",
            }
        )
        with contextlib.redirect_stderr(io.StringIO()):
            version, _ = fw_fetch.resolve(
                self.bucket, fw_fetch.COMPONENTS["mcu"], "maui_v11"
            )
        self.assertEqual(version, "0.100-aaaaaaa")

    def test_version_key(self):
        versions = ["0.0.1-100-a", "0.0.1-69-b", "0.0.2-1-c", "0.0.1-9-d"]
        self.assertEqual(
            sorted(versions, key=fw_fetch.version_key),
            ["0.0.1-9-d", "0.0.1-69-b", "0.0.1-100-a", "0.0.2-1-c"],
        )

    def test_missing_payload_is_an_error(self):
        del self.gcs.objects["mcu-fw/mspm0/0.60-042f858/zephyr.txt"]
        with self.assertRaisesRegex(fw_fetch.FetchError, r"no \*\.txt image"):
            fw_fetch.resolve(self.bucket, fw_fetch.COMPONENTS["mcu"], "maui_v11")

    def test_payload_size_is_checked(self):
        sized = fw_fetch.Component("mcu-fw/mspm0", ".mcufw", ".txt", size=4)
        with self.assertRaisesRegex(fw_fetch.FetchError, "expected 4"):
            fw_fetch.resolve(self.bucket, sized, "maui_v11")

    def test_nested_markers_are_ignored(self):
        del self.gcs.objects["mcu-fw/mspm0/0.60-042f858/stable.maui_v11.mcufw"]
        self.gcs.objects["mcu-fw/mspm0/0.60-042f858/x/stable.maui_v11.mcufw"] = b""
        self.assertIsNone(
            fw_fetch.resolve(self.bucket, fw_fetch.COMPONENTS["mcu"], "maui_v11")
        )


class TestMain(FetchTestCase):
    """End-to-end tests of the command line."""

    def test_fetches_the_stable_payloads_and_writes_manifests(self):
        status, out, _ = self.run_main("mcu", "pdc")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertEqual(self.read("mcu", "zephyr.txt"), b"mcu 0.60")
        self.assertEqual(
            sorted(os.listdir(os.path.join(self.dest, "pdc"))),
            ["STABLE.json", "v20_0325_100W.bin"],
        )
        self.assertEqual(
            self.manifest("pdc"),
            {
                "version": "v20_0325_100W",
                "file": "v20_0325_100W.bin",
                "md5": _md5(b"pdc v20 100W"),
                "source": "gs://maui-firmware/pdc-fw/v20_0325_100W/v20_0325_100W.bin",
            },
        )
        self.assertEqual(self.manifest("mcu")["version"], "0.60-042f858")
        self.assertIn("downloaded 0.60-042f858", out)
        mode = os.stat(os.path.join(self.dest, "pdc", "STABLE.json")).st_mode
        self.assertEqual(mode & 0o777, 0o644)

    def test_identical_file_is_not_downloaded_again(self):
        self.run_main("mcu")
        self.gcs.requests.clear()
        status, out, _ = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertIn("up to date", out)
        self.assertEqual(self.gcs.object_requests(), [])

    def test_changed_file_with_same_name_is_replaced(self):
        os.makedirs(os.path.join(self.dest, "mcu"))
        with open(os.path.join(self.dest, "mcu", "zephyr.txt"), "wb") as f:
            f.write(b"mcu 0.59")
        status, _, _ = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertEqual(self.read("mcu", "zephyr.txt"), b"mcu 0.60")

    def test_new_stable_version_replaces_the_component_directory(self):
        self.run_main("pdc", device="maui_proto")
        # The directory holds exactly the stable image and STABLE.json: files
        # of older releases (or copied by hand) are removed.
        with open(os.path.join(self.dest, "pdc", "manual.bin"), "wb") as f:
            f.write(b"manual")
        status, out, _ = self.run_main("pdc", device="maui_v11")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertEqual(
            sorted(os.listdir(os.path.join(self.dest, "pdc"))),
            ["STABLE.json", "v20_0325_100W.bin"],
        )
        self.assertIn("downloaded v20_0325_100W", out)
        # No temporary directory is left behind.
        self.assertEqual(sorted(os.listdir(self.dest)), ["pdc"])

    def test_md5_mismatch_keeps_the_old_file_and_manifest(self):
        self.run_main("mcu")
        self.gcs.objects["mcu-fw/mspm0/0.60-042f858/zephyr.txt"] = b"mcu 0.61"
        self.gcs.corrupt.add("mcu-fw/mspm0/0.60-042f858/zephyr.txt")
        status, _, err = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("md5", err)
        self.assertEqual(self.read("mcu", "zephyr.txt"), b"mcu 0.60")
        self.assertEqual(self.manifest("mcu")["md5"], _md5(b"mcu 0.60"))
        # No temporary file is left behind.
        self.assertEqual(
            sorted(os.listdir(os.path.join(self.dest, "mcu"))),
            ["STABLE.json", "zephyr.txt"],
        )

    def test_md5_from_download_header_without_listing_md5(self):
        self.gcs.md5_in_listing = False
        original = self.gcs.urlopen

        def urlopen(request, timeout=None):
            response = original(request, timeout)
            if "?" not in request.full_url:
                digest = hashlib.md5(b"something else").digest()
                response.headers = {
                    "x-goog-hash": "crc32c=AAAAAA==,md5="
                    + base64.b64encode(digest).decode()
                }
            return response

        self.bucket = fw_fetch.Bucket(BASE_URL, retries=0, urlopen=urlopen)
        status, _, err = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("does not match", err)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "mcu", "zephyr.txt")))

    def test_manifest_records_the_computed_md5_without_listing_md5(self):
        self.gcs.md5_in_listing = False
        status, _, err = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK, err)
        self.assertEqual(self.manifest("mcu")["md5"], _md5(b"mcu 0.60"))
        # Checked against the crc32c of the composite object instead.
        crc = fw_fetch._crc32c_update_py(0, b"mcu 0.60")  # pylint: disable=W0212
        self.assertEqual(self.manifest("mcu")["crc32c"], f"{crc:08x}")

    def test_crc32c_mismatch_of_a_composite_object_fails(self):
        self.gcs.md5_in_listing = False
        key = "mcu-fw/mspm0/0.60-042f858/zephyr.txt"
        self.gcs.goog_hash[key] = f"crc32c={_crc32c_header(b'other')}"
        status, _, err = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("crc32c", err)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "mcu")))

    def test_object_without_any_hash_fails(self):
        self.gcs.md5_in_listing = False
        self.gcs.goog_hash["mcu-fw/mspm0/0.60-042f858/zephyr.txt"] = ""
        status, _, err = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("neither an md5 nor a crc32c", err)

    def test_unsafe_payload_name_is_rejected(self):
        self.gcs.objects["pdc-fw/v21 x;rm/v21 x;rm.bin"] = b"x"
        self.gcs.objects["pdc-fw/v21 x;rm/stable.maui_v11.pdcfw"] = b""
        del self.gcs.objects["pdc-fw/v20_0325_100W/stable.maui_v11.pdcfw"]
        status, _, err = self.run_main("pdc")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("unsafe file name", err)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "pdc")))
        self.assertEqual(self.gcs.object_requests(), [])

    def test_symlink_in_the_component_directory_is_not_followed(self):
        outside = os.path.join(self.dest, "outside.txt")
        with open(outside, "wb") as f:
            f.write(b"keep")
        os.makedirs(os.path.join(self.dest, "mcu"))
        os.symlink(outside, os.path.join(self.dest, "mcu", "zephyr.txt"))
        status, _, _ = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertEqual(self.read("outside.txt"), b"keep")
        self.assertFalse(os.path.islink(os.path.join(self.dest, "mcu", "zephyr.txt")))
        self.assertEqual(self.read("mcu", "zephyr.txt"), b"mcu 0.60")

    def test_symlink_component_directory_is_refused(self):
        outside = os.path.join(self.dest, "outside")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.dest, "mcu"))
        status, _, err = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("symbolic link", err)
        self.assertEqual(os.listdir(outside), [])

    def test_missing_marker_of_a_required_component_fails(self):
        status, _, err = self.run_main("mcu", device="maui_v99")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("no 'stable.maui_v99.mcufw' marker", err)

    def test_missing_marker_of_an_optional_component_is_a_warning(self):
        optional = fw_fetch.Component("mcu-fw/other", ".mcufw", ".bin", required=False)
        with mock.patch.dict(fw_fetch.COMPONENTS, {"opt": optional}):
            status, _, err = self.run_main("opt", "pdc")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertIn("opt: no 'stable.maui_v11.mcufw' marker", err)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "opt")))
        self.assertTrue(os.path.exists(os.path.join(self.dest, "pdc", "STABLE.json")))

    def test_dry_run_downloads_nothing(self):
        status, out, _ = self.run_main("--dry-run", "mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertIn("mcu-fw/mspm0/0.60-042f858/zephyr.txt", out)
        self.assertEqual(self.gcs.object_requests(), [])
        self.assertFalse(os.path.exists(os.path.join(self.dest, "mcu")))

    def test_no_components_is_a_no_op(self):
        status, _, _ = self.run_main()
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertEqual(self.gcs.requests, [])

    def test_usage_errors(self):
        self.assertEqual(self.run_main("bogus")[0], fw_fetch.EXIT_USAGE)
        self.assertEqual(self.run_main("mcu", device="")[0], fw_fetch.EXIT_USAGE)
        self.assertEqual(self.run_main("mcu", device="../x")[0], fw_fetch.EXIT_USAGE)
        self.assertEqual(self.gcs.requests, [])

    def test_unreachable_bucket_skips_remaining_components(self):
        def urlopen(request, timeout=None):
            del request, timeout
            raise urllib.error.URLError("Name or service not known")

        self.bucket = fw_fetch.Bucket(BASE_URL, retries=0, urlopen=urlopen)
        status, _, err = self.run_main("mcu", "pdc")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("cannot reach", err)
        self.assertIn("pdc: skipped, the bucket is not reachable", err)

    def test_server_error_is_retried(self):
        calls = []
        original = self.gcs.urlopen

        def urlopen(request, timeout=None):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)
            return original(request, timeout)

        self.bucket = fw_fetch.Bucket(BASE_URL, retries=1, urlopen=urlopen)
        with mock.patch.object(fw_fetch.time, "sleep") as sleep:
            status, _, _ = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        sleep.assert_called_once()


class TestReleaseChecks(FetchTestCase):
    """--require / --verify of release builds (ISO, offline bundle)."""

    def test_require_makes_a_missing_marker_an_error(self):
        del self.gcs.objects["pdc-fw/v20_0325_100W/stable.maui_v11.pdcfw"]
        status, _, err = self.run_main("--require", "pdc", "pdc")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("failed to fetch: pdc", err)
        status, _, _ = self.run_main("--require", "nope", "pdc")
        self.assertEqual(status, fw_fetch.EXIT_USAGE)

    def test_verify(self):
        self.assertEqual(self.run_main("pdc")[0], fw_fetch.EXIT_OK)
        self.assertEqual(self.run_main("--verify", "pdc")[0], fw_fetch.EXIT_OK)
        # No STABLE.json at all.
        status, _, err = self.run_main("--verify", "pdc", "mcu")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("mcu: no valid STABLE.json", err)
        # Damaged image and image missing.
        image = os.path.join(self.dest, "pdc", "v20_0325_100W.bin")
        with open(image, "ab") as f:
            f.write(b"x")
        status, _, err = self.run_main("--verify", "pdc")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("does not match the md5", err)
        os.remove(image)
        status, _, err = self.run_main("--verify", "pdc")
        self.assertIn("does not exist", err)


class TestStationSync(FetchTestCase):
    """Tests of the station sync behaviour (skip, force, swap, cleanup)."""

    def test_permissions(self):
        self.run_main("mcu")
        mode = os.stat(os.path.join(self.dest, "mcu")).st_mode
        self.assertEqual(mode & 0o777, 0o755)
        mode = os.stat(os.path.join(self.dest, "mcu", "zephyr.txt")).st_mode
        self.assertEqual(mode & 0o777, 0o644)

    def test_up_to_date_component_is_not_replaced(self):
        self.run_main("mcu")
        inode = os.stat(os.path.join(self.dest, "mcu")).st_ino
        status, _, _ = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertEqual(os.stat(os.path.join(self.dest, "mcu")).st_ino, inode)

    def test_force_downloads_again(self):
        self.run_main("mcu")
        self.gcs.requests.clear()
        status, out, _ = self.run_main("--force", "mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertIn("downloaded 0.60-042f858", out)
        self.assertEqual(len(self.gcs.object_requests()), 1)

    def test_corrupted_local_image_is_downloaded_again(self):
        self.run_main("mcu")
        with open(os.path.join(self.dest, "mcu", "zephyr.txt"), "wb") as f:
            f.write(b"broken")
        status, out, _ = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertIn("downloaded", out)
        self.assertEqual(self.read("mcu", "zephyr.txt"), b"mcu 0.60")

    def test_changed_manifest_version_downloads_again(self):
        self.run_main("mcu")
        manifest = self.manifest("mcu")
        manifest["version"] = "0.59-f408714"
        with open(
            os.path.join(self.dest, "mcu", "STABLE.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(manifest, f)
        status, out, _ = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertIn("downloaded", out)
        self.assertEqual(self.manifest("mcu")["version"], "0.60-042f858")

    def test_composite_object_md5_from_head_allows_skipping(self):
        self.gcs.md5_in_listing = False
        key = "mcu-fw/mspm0/0.60-042f858/zephyr.txt"
        digest = hashlib.md5(b"mcu 0.60").digest()
        self.gcs.goog_hash[key] = (
            "crc32c=AAAAAA==,md5=" + base64.b64encode(digest).decode()
        )
        self.run_main("mcu")
        self.gcs.calls.clear()
        status, out, _ = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertIn("up to date", out)
        self.assertEqual(self.gcs.download_requests(), [])
        self.assertIn("HEAD", [m for m, _, _ in self.gcs.calls])

    def test_composite_object_crc32c_allows_skipping(self):
        self.gcs.md5_in_listing = False
        self.run_main("mcu")
        self.gcs.calls.clear()
        status, out, _ = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertIn("up to date", out)
        self.assertEqual(self.gcs.download_requests(), [])

    def test_changed_crc32c_downloads_again(self):
        self.gcs.md5_in_listing = False
        self.run_main("mcu")
        key = "mcu-fw/mspm0/0.60-042f858/zephyr.txt"
        self.gcs.objects[key] = b"mcu 0.60 rebuilt"
        self.gcs.calls.clear()
        status, out, _ = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertIn("downloaded", out)
        self.assertEqual(self.read("mcu", "zephyr.txt"), b"mcu 0.60 rebuilt")

    def test_leftover_temporary_directories_are_removed(self):
        for name in (".mcu.new.abc", ".mcu.old.def"):
            os.makedirs(os.path.join(self.dest, name, "sub"))
        os.makedirs(os.path.join(self.dest, ".pdc.new.keep"))
        status, _, _ = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertEqual(sorted(os.listdir(self.dest)), [".pdc.new.keep", "mcu"])

    def test_failed_swap_restores_the_old_directory(self):
        self.run_main("mcu")
        self.gcs.objects["mcu-fw/mspm0/0.60-042f858/zephyr.txt"] = b"mcu 0.61"
        real_rename = os.rename

        def rename(src, dst):
            if os.path.basename(src).startswith(".mcu.new."):
                raise OSError("rename failed")
            return real_rename(src, dst)

        with mock.patch.object(fw_fetch.os, "rename", side_effect=rename):
            status, _, err = self.run_main("mcu")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("rename failed", err)
        self.assertEqual(self.read("mcu", "zephyr.txt"), b"mcu 0.60")
        self.assertEqual(sorted(os.listdir(self.dest)), ["mcu"])

    def test_quiet_only_reports_downloads(self):
        status, out, _ = self.run_main("-q", "mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertIn("downloaded", out)
        self.assertNotIn("downloading", out)
        status, out, _ = self.run_main("-q", "mcu")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertEqual(out, "")

    def test_download_timeout_is_used_for_downloads(self):
        self.bucket = fw_fetch.Bucket(
            BASE_URL,
            timeout=5,
            retries=0,
            urlopen=self.gcs.urlopen,
            download_timeout=15,
        )
        self.run_main("mcu")
        timeouts = {(m, "?" in u): t for m, u, t in self.gcs.calls}
        self.assertEqual(timeouts[("GET", True)], 5)
        self.assertEqual(timeouts[("GET", False)], 15)


class TestPickPayload(unittest.TestCase):
    """Tests for the deterministic choice of the image of a version."""

    def pick(self, component, version, *names):
        """Returns the name pick_payload chooses and the warnings."""
        objects = [fw_fetch.GcsObject(f"x/{version}/{n}") for n in names]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            chosen = fw_fetch.pick_payload(
                fw_fetch.COMPONENTS[component], version, objects
            )
        return chosen.name, err.getvalue()

    def test_single_image_is_used_whatever_its_name(self):
        self.assertEqual(self.pick("mcu", "1.0", "app.txt"), ("app.txt", ""))

    def test_preferred_names_win(self):
        name, warning = self.pick("mcu", "1.0", "a.txt", "zephyr.txt")
        self.assertEqual(name, "zephyr.txt")
        self.assertIn("found 2 (a.txt, zephyr.txt); using zephyr.txt", warning)
        self.assertEqual(
            self.pick("pdc", "v20", "a.bin", "pdc.bin", "v20.bin")[0], "v20.bin"
        )
        self.assertEqual(self.pick("pdc", "v20", "a.bin", "pdc.bin")[0], "pdc.bin")

    def test_alphabetically_first_otherwise(self):
        self.assertEqual(self.pick("pdc", "v20", "b.bin", "a.bin")[0], "a.bin")

    def test_no_image_is_an_error(self):
        with self.assertRaises(fw_fetch.FetchError):
            fw_fetch.pick_payload(fw_fetch.COMPONENTS["mcu"], "1.0", [])

    def test_only_images_of_the_version_directory_count(self):
        gcs = FakeGcs(
            {
                "mcu-fw/mspm0/1.0/stable.maui_v11.mcufw": b"",
                "mcu-fw/mspm0/1.0/app.txt": b"app",
                "mcu-fw/mspm0/1.0/notes.md": b"notes",
                "mcu-fw/mspm0/1.0/old/zephyr.txt": b"old",
            }
        )
        bucket = fw_fetch.Bucket(BASE_URL, retries=0, urlopen=gcs.urlopen)
        version, payload = fw_fetch.resolve(
            bucket, fw_fetch.COMPONENTS["mcu"], "maui_v11"
        )
        self.assertEqual((version, payload.name), ("1.0", "app.txt"))


class TestComponentTable(unittest.TestCase):
    """Consistency of the component table with the other registries."""

    def test_every_edition_firmware_component_is_known(self):
        with open(os.path.join(REPO_ROOT, "editions.json"), encoding="utf-8") as f:
            editions = json.load(f)["editions"]
        for edition_id, meta in editions.items():
            for component in meta.get("firmware", []):
                self.assertIn(component, fw_fetch.COMPONENTS, edition_id)

    def test_payloads_are_unique(self):
        markers = {(c.gcs_dir, c.marker_suffix) for c in fw_fetch.COMPONENTS.values()}
        self.assertEqual(len(markers), len(fw_fetch.COMPONENTS))


MAUI_UTILS_CONTENT = {
    "docker/": b"",
    "docker/0.0.1-36-0a9b996/maui-utils.tar": b"utils 36",
    "docker/0.0.1-69-89b053b/maui-utils.tar": b"utils 69",
    "docker/0.0.1-69-89b053b/stable.dockertag": b"",
    "docker/0.0.1-100-1234567/maui-utils.tar": b"utils 100",
    "docker/0.0.1-100-1234567/alpha.dockertag": b"",
}


class TestMauiUtils(FetchTestCase):
    """Tests of the maui-utils component (docker/<version>/stable.dockertag)."""

    def setUp(self):
        super().setUp()
        self.gcs = FakeGcs(MAUI_UTILS_CONTENT, page_size=2, md5_in_listing=False)
        self.bucket = fw_fetch.Bucket(BASE_URL, retries=0, urlopen=self.gcs.urlopen)

    def run_utils(self, *argv):
        """Runs main() without --device; returns (status, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = fw_fetch.main(
                ["--dest-root", self.dest, *argv], bucket=self.bucket
            )
        return status, out.getvalue(), err.getvalue()

    def test_marker_is_device_independent(self):
        component = fw_fetch.COMPONENTS["maui-utils"]
        self.assertFalse(component.per_device)
        self.assertEqual(
            fw_fetch.marker_name(component, "maui_v11"), "stable.dockertag"
        )

    def test_fetches_the_stable_release_without_device(self):
        status, _, err = self.run_utils("maui-utils")
        self.assertEqual(status, fw_fetch.EXIT_OK, err)
        self.assertEqual(self.read("maui-utils", "maui-utils.tar"), b"utils 69")
        manifest = self.manifest("maui-utils")
        self.assertEqual(manifest["version"], "0.0.1-69-89b053b")
        self.assertEqual(manifest["file"], "maui-utils.tar")
        self.assertEqual(manifest["md5"], _md5(b"utils 69"))
        self.assertIn("crc32c", manifest)
        # The paginated listing was followed (page size 2).
        self.assertGreater(len([u for u in self.gcs.requests if "marker=" in u]), 0)

    def test_highest_stable_version_wins(self):
        self.gcs.objects["docker/0.0.1-100-1234567/stable.dockertag"] = b""
        status, _, _ = self.run_utils("maui-utils")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertEqual(self.manifest("maui-utils")["version"], "0.0.1-100-1234567")

    def test_second_fetch_is_up_to_date(self):
        self.run_utils("maui-utils")
        self.gcs.calls.clear()
        status, out, _ = self.run_utils("maui-utils")
        self.assertEqual(status, fw_fetch.EXIT_OK)
        self.assertIn("up to date", out)
        self.assertEqual(self.gcs.download_requests(), [])

    def test_missing_stable_marker_fails(self):
        del self.gcs.objects["docker/0.0.1-69-89b053b/stable.dockertag"]
        status, _, err = self.run_utils("maui-utils")
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertIn("no 'stable.dockertag' marker", err)

    def test_device_is_still_required_for_firmware(self):
        self.assertEqual(self.run_utils("maui-utils", "mcu")[0], fw_fetch.EXIT_USAGE)

    def test_local_file(self):
        local = os.path.join(self.dest, "build.tar")
        with open(local, "wb") as f:
            f.write(b"local utils")
        status, _, err = self.run_utils(
            "--local-file", local, "--local-version", "0.0.2-1-abc", "maui-utils"
        )
        self.assertEqual(status, fw_fetch.EXIT_OK, err)
        self.assertEqual(self.read("maui-utils", "build.tar"), b"local utils")
        manifest = self.manifest("maui-utils")
        self.assertEqual(manifest["version"], "0.0.2-1-abc")
        self.assertEqual(manifest["md5"], _md5(b"local utils"))
        self.assertTrue(manifest["source"].startswith("local:"))
        self.assertEqual(self.gcs.requests, [])

    def test_local_file_usage_errors(self):
        self.assertEqual(
            self.run_utils("--local-file", "x.tar", "maui-utils")[0],
            fw_fetch.EXIT_USAGE,
        )
        status, _, _ = self.run_utils(
            "--local-file",
            os.path.join(self.dest, "missing.tar"),
            "--local-version",
            "1.0",
            "maui-utils",
        )
        self.assertEqual(status, fw_fetch.EXIT_FAILED)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "maui-utils")))


class TestCrc32c(unittest.TestCase):
    """Tests of the CRC-32C helpers."""

    def test_known_value(self):
        # pylint: disable=protected-access
        self.assertEqual(fw_fetch._crc32c_update_py(0, b"123456789"), 0xE3069283)
        partial = fw_fetch._crc32c_update_py(0, b"12345")
        self.assertEqual(fw_fetch._crc32c_update_py(partial, b"6789"), 0xE3069283)

    def test_goog_hash_header(self):
        # pylint: disable=protected-access
        self.assertEqual(
            fw_fetch._crc32c_from_goog_hash("crc32c=CbEeiA==,md5=x"), "09b11e88"
        )
        self.assertEqual(fw_fetch._crc32c_from_goog_hash("md5=abc"), "")

    def test_file_crc32c(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"123456789")
            f.flush()
            self.assertEqual(fw_fetch.file_crc32c(f.name), "e3069283")


if __name__ == "__main__":
    unittest.main()
