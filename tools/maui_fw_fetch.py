#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Fetches the stable Maui firmware from the public gs://maui-firmware bucket.

Bucket layout (one directory per firmware family, one directory per version):

  <gcs_dir>/<version>/<payload>                     one or more payloads
  <gcs_dir>/<version>/<channel>.<device><suffix>    empty marker, e.g.
                                                    stable.maui_v11.pdcfw

A component resolves to the version directory holding the stable marker of
the Maui device (--device, editions.json "maui_device"); its payload is
selected by name. There is no version option: builds and stations always
follow the stable markers.

The payload is downloaded to <dest-root>/<component>/<payload> atomically
(temporary file + rename) and verified (size, md5 of the bucket listing or
of the download headers). <dest-root>/<component>/STABLE.json records it:

  {"version": "...", "file": "...", "md5": "...", "source": "gs://..."}

When the stable version changes, the payload named by the previous
STABLE.json is removed; other files are never deleted.

Used by start-diagnoseme (online stations), test_server/build_iso.sh and
development_environment/build-offline-bundle.sh. It only uses the Python 3
standard library because it also runs on stations and in build containers.

Exit status: 0 when every component is up to date (a missing marker of an
optional component is only a warning), 1 when a component failed, 2 for
usage errors.

Examples:
  tools/maui_fw_fetch.py --device maui_v11 --dest-root dist/firmware mcu pdc
  tools/maui_fw_fetch.py --dry-run --device maui_v11 --dest-root /tmp/fw mcu
"""

import argparse
import base64
import binascii
import dataclasses
import hashlib
import http.client
import json
import os
import re
import socket
import sys
import tempfile
import time
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


GCS_BASE_URL = "https://storage.googleapis.com/maui-firmware"
CHANNEL = "stable"
USER_AGENT = "DiagnoseMe-maui-fw-fetch"
CHUNK_SIZE = 64 * 1024
# Safety net against a listing that never ends.
MAX_LISTING_PAGES = 1000
# Payload file names written to the station (no path separators, no hidden
# files, no shell or control characters).
SAFE_FILE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*")
DEVICE_RE = re.compile(r"[a-z0-9_]+")
# Manifest of the stable payload in every component directory.
MANIFEST_NAME = "STABLE.json"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


@dataclasses.dataclass(frozen=True)
class Component:
    """A firmware component of gs://maui-firmware."""

    gcs_dir: str
    marker_suffix: str
    # Payload file name in the version directory; {version} is replaced.
    payload: str
    # Exact payload size in bytes (0: any).
    size: int = 0
    # A missing stable marker is an error (else only a warning).
    required: bool = True

    def payload_name(self, version: str) -> str:
        """Returns the payload file name of |version|."""
        return self.payload.format(version=version)


# Station component (/var/lib/diagnoseme/firmware/<component>, editions.json
# "firmware") -> bucket layout. Keep in sync with the maui release scripts.
COMPONENTS: Dict[str, Component] = {
    # Maui v1.1 MCU App (TI MSPM0, TI-TXT).
    "mcu": Component("mcu-fw/mspm0", ".mcufw", "zephyr.txt"),
    # PDC (TPS66994) firmware of every Maui revision.
    "pdc": Component("pdc-fw", ".pdcfw", "{version}.bin"),
}


class FetchError(Exception):
    """A component could not be fetched."""


class NetworkError(FetchError):
    """The bucket could not be reached (no network, DNS, timeout)."""


@dataclasses.dataclass(frozen=True)
class GcsObject:
    """An object of a bucket listing."""

    key: str
    size: int = 0
    # Hex md5 of the content; empty when unknown (e.g. composite objects).
    md5: str = ""
    last_modified: str = ""

    @property
    def name(self) -> str:
        """Returns the file name of the object."""
        return self.key.rsplit("/", 1)[-1]


def _local_name(tag: str) -> str:
    """Returns |tag| without its XML namespace."""
    return tag.rsplit("}", 1)[-1]


def _md5_from_etag(etag: str) -> str:
    """Returns the md5 an ETag carries, or '' (composite objects have none)."""
    value = etag.strip().strip('"').lower()
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else ""


def _md5_from_goog_hash(header: str) -> str:
    """Returns the hex md5 of an x-goog-hash header ('crc32c=..,md5=..')."""
    for part in header.split(","):
        algo, _, value = part.strip().partition("=")
        if algo == "md5" and value:
            try:
                return base64.b64decode(value).hex()
            except (binascii.Error, ValueError):
                return ""
    return ""


def _new_md5():
    """Returns an md5 hash object (md5 is an integrity check here only)."""
    return hashlib.md5(usedforsecurity=False)


def file_md5(path: str) -> str:
    """Returns the hex md5 of the file at |path|."""
    digest = _new_md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_listing(data: bytes) -> Tuple[List[GcsObject], bool, str]:
    """Parses one page of a GCS XML listing.

    Returns:
      (objects, is_truncated, next_marker)
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise FetchError(f"invalid bucket listing: {e}") from e
    objects = []
    truncated = False
    next_marker = ""
    for child in root:
        tag = _local_name(child.tag)
        if tag == "Contents":
            fields = {_local_name(e.tag): (e.text or "") for e in child}
            try:
                size = int(fields.get("Size") or 0)
            except ValueError:
                size = 0
            objects.append(
                GcsObject(
                    key=fields.get("Key", ""),
                    size=size,
                    md5=_md5_from_etag(fields.get("ETag", "")),
                    last_modified=fields.get("LastModified", ""),
                )
            )
        elif tag == "IsTruncated":
            truncated = (child.text or "").strip().lower() == "true"
        elif tag == "NextMarker":
            next_marker = child.text or ""
    return objects, truncated, next_marker


class Bucket:
    """Anonymous read access to a public GCS bucket over HTTPS."""

    def __init__(
        self,
        base_url: str = GCS_BASE_URL,
        timeout: float = 30.0,
        retries: int = 1,
        urlopen: Optional[Callable] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, retries)
        self._urlopen = urlopen or urllib.request.urlopen

    def object_url(self, key: str) -> str:
        """Returns the download URL of |key|."""
        return f"{self.base_url}/{urllib.parse.quote(key, safe='/')}"

    def open(self, url: str):
        """Opens |url|, retrying transient failures."""
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        for attempt in range(self.retries + 1):
            try:
                return self._urlopen(request, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == self.retries:
                    raise FetchError(f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
                if attempt == self.retries:
                    reason = getattr(e, "reason", e)
                    raise NetworkError(f"cannot reach {url}: {reason}") from e
            time.sleep(1.0)
        raise FetchError(f"cannot open {url}")  # Not reached.

    def list(self, prefix: str) -> List[GcsObject]:
        """Returns every object below |prefix|, following pagination."""
        objects: List[GcsObject] = []
        marker = ""
        for _ in range(MAX_LISTING_PAGES):
            query = {"prefix": prefix}
            if marker:
                query["marker"] = marker
            url = f"{self.base_url}?{urllib.parse.urlencode(query)}"
            with self.open(url) as response:
                page, truncated, next_marker = parse_listing(response.read())
            objects.extend(page)
            if not truncated:
                return objects
            next_marker = next_marker or (page[-1].key if page else "")
            if not next_marker or next_marker == marker:
                raise FetchError(f"bucket listing of {prefix} does not advance")
            marker = next_marker
        raise FetchError(f"bucket listing of {prefix} is too long")


def _gs_path(bucket: Bucket, path: str) -> str:
    """Returns a gs:// style name of |path| for messages."""
    name = urllib.parse.urlparse(bucket.base_url).path.strip("/")
    return f"gs://{name}/{path}"


def marker_name(component: Component, device: str) -> str:
    """Returns the stable marker file name of |device|, e.g. stable.maui_v11.pdcfw."""
    return f"{CHANNEL}.{device}{component.marker_suffix}"


def resolve(
    bucket: Bucket, component: Component, device: str
) -> Optional[Tuple[str, GcsObject]]:
    """Resolves |component| to (version, payload) by the stable marker.

    Returns:
      None when |device| has no stable marker in the component directory.
    """
    objects = bucket.list(f"{component.gcs_dir}/")
    marker = marker_name(component, device)
    prefix = f"{component.gcs_dir}/"
    markers = []
    for o in objects:
        parts = o.key[len(prefix) :].split("/") if o.key.startswith(prefix) else []
        if len(parts) == 2 and parts[0] and parts[1] == marker:
            markers.append(o)
    if not markers:
        return None
    # Promotion moves the single marker; if an old one was left behind, the
    # most recently written marker wins.
    newest = max(markers, key=lambda o: (o.last_modified, o.key))
    if len(markers) > 1:
        warn(
            f"{len(markers)} '{marker}' markers in "
            f"{_gs_path(bucket, prefix)}; using the newest ({newest.key})"
        )
    version = newest.key[len(prefix) :].split("/")[0]
    key = f"{prefix}{version}/{component.payload_name(version)}"
    payload = next((o for o in objects if o.key == key), None)
    if payload is None:
        raise FetchError(f"{_gs_path(bucket, key)} does not exist")
    if component.size and payload.size != component.size:
        raise FetchError(
            f"{_gs_path(bucket, key)} has {payload.size} bytes, expected "
            f"{component.size}"
        )
    return version, payload


def _destination(payload: GcsObject, dest_dir: str) -> str:
    """Returns the path |payload| is written to, which stays in |dest_dir|."""
    name = payload.name
    if not SAFE_FILE_NAME_RE.fullmatch(name):
        raise FetchError(f"{payload.key}: unsafe file name {name!r}")
    dest = os.path.join(dest_dir, name)
    if os.path.dirname(os.path.abspath(dest)) != os.path.abspath(dest_dir):
        raise FetchError(f"{payload.key}: {dest} is outside {dest_dir}")
    if os.path.islink(dest):
        raise FetchError(f"refusing to replace the symbolic link {dest}")
    return dest


def _fsync_dir(path: str) -> None:
    """Flushes the directory entry of a renamed file (best effort)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path: str, data: bytes) -> None:
    """Writes |data| to |path| through a temporary file and a rename."""
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".STABLE.")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
        _fsync_dir(directory)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def download(bucket: Bucket, payload: GcsObject, dest_dir: str) -> Tuple[bool, str]:
    """Downloads |payload| into |dest_dir| unless an identical file is there.

    The file is written to a temporary name, verified (size and md5 when
    known) and renamed, so an interrupted download never leaves a partial
    file under the final name.

    Returns:
      (written, md5): written is False when the file was already up to date.
    """
    dest = _destination(payload, dest_dir)
    if payload.md5 and os.path.isfile(dest) and file_md5(dest) == payload.md5:
        return False, payload.md5
    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dest_dir, prefix=f".{payload.name}.")
    try:
        digest = _new_md5()
        size = 0
        with os.fdopen(fd, "wb") as out:
            with bucket.open(bucket.object_url(payload.key)) as response:
                header_md5 = _md5_from_goog_hash(
                    response.headers.get("x-goog-hash", "") or ""
                )
                for chunk in iter(lambda: response.read(CHUNK_SIZE), b""):
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            out.flush()
            os.fsync(out.fileno())
        if payload.size and size != payload.size:
            raise FetchError(
                f"{payload.key}: got {size} bytes, the listing says {payload.size}"
            )
        expected_md5 = payload.md5 or header_md5
        if expected_md5 and digest.hexdigest() != expected_md5:
            raise FetchError(
                f"{payload.key}: md5 {digest.hexdigest()} does not match "
                f"{expected_md5}"
            )
        if os.path.isfile(dest) and file_md5(dest) == digest.hexdigest():
            os.unlink(tmp_path)
            return False, digest.hexdigest()
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, dest)
        _fsync_dir(dest_dir)
        return True, digest.hexdigest()
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def read_manifest(component_dir: str) -> Dict[str, str]:
    """Returns the STABLE.json of |component_dir| ({} when missing/invalid)."""
    try:
        with open(
            os.path.join(component_dir, MANIFEST_NAME), "r", encoding="utf-8"
        ) as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _remove_previous(component_dir: str, previous: Dict[str, str], current: str):
    """Removes the payload of the previous STABLE.json if it is not |current|."""
    name = previous.get("file", "")
    if not isinstance(name, str) or name == current:
        return
    if not SAFE_FILE_NAME_RE.fullmatch(name) or name == MANIFEST_NAME:
        return
    path = os.path.join(component_dir, name)
    if os.path.isfile(path) and not os.path.islink(path):
        os.unlink(path)
        info(f"removed the previous stable payload {path}")


def info(message: str) -> None:
    """Prints a progress message."""
    print(f"maui_fw_fetch: {message}", flush=True)


def warn(message: str) -> None:
    """Prints a warning."""
    print(f"maui_fw_fetch: WARNING: {message}", file=sys.stderr, flush=True)


def error(message: str) -> None:
    """Prints an error."""
    print(f"maui_fw_fetch: ERROR: {message}", file=sys.stderr, flush=True)


class Fetcher:
    """Fetches the stable firmware components of a Maui device."""

    def __init__(
        self,
        bucket: Bucket,
        dest_root: str,
        device: str,
        dry_run: bool = False,
    ):
        self.bucket = bucket
        self.dest_root = dest_root
        self.device = device
        self.dry_run = dry_run

    def fetch(self, name: str) -> None:
        """Fetches component |name| into <dest_root>/<name>/ (raises FetchError)."""
        component = COMPONENTS[name]
        resolved = resolve(self.bucket, component, self.device)
        if resolved is None:
            message = (
                f"no '{marker_name(component, self.device)}' marker in "
                f"{_gs_path(self.bucket, component.gcs_dir + '/')}"
            )
            if component.required:
                raise FetchError(message)
            warn(f"{name}: {message} yet; skipping")
            return
        version, payload = resolved
        source = _gs_path(self.bucket, payload.key)
        component_dir = os.path.join(self.dest_root, name)
        dest = _destination(payload, component_dir)
        if self.dry_run:
            info(f"{name}: {version}: {source} -> {dest}")
            return
        previous = read_manifest(component_dir)
        written, md5 = download(self.bucket, payload, component_dir)
        manifest = {
            "version": version,
            "file": payload.name,
            "md5": md5,
            "source": source,
        }
        _atomic_write(
            os.path.join(component_dir, MANIFEST_NAME),
            (json.dumps(manifest, indent=2) + "\n").encode(),
        )
        _remove_previous(component_dir, previous, payload.name)
        if written:
            info(f"{name}: downloaded {version}: {source} -> {dest}")
        else:
            info(f"{name}: {dest} ({version}) is up to date")

    def fetch_all(self, names: Sequence[str]) -> List[str]:
        """Fetches every component of |names|; returns the failed ones."""
        failed: List[str] = []
        network_down = False
        for name in dict.fromkeys(names):
            if network_down:
                # Do not wait for the timeout of every remaining component.
                error(f"{name}: skipped, the bucket is not reachable")
                failed.append(name)
                continue
            try:
                self.fetch(name)
            except NetworkError as e:
                error(f"{name}: {e}")
                failed.append(name)
                network_down = True
            except (FetchError, OSError, http.client.HTTPException) as e:
                error(f"{name}: {e}")
                failed.append(name)
        return failed


def _build_parser() -> argparse.ArgumentParser:
    """Returns the command line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n", 1)[0],
        epilog=__doc__.split("\n\n", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "components",
        nargs="*",
        metavar="COMPONENT",
        help=f"firmware components to fetch: {', '.join(COMPONENTS)}",
    )
    parser.add_argument(
        "--device",
        default="",
        help="Maui device of the stable markers (editions.json maui_device), "
        "e.g. maui_v11",
    )
    parser.add_argument(
        "--dest-root",
        required=True,
        help="writes DEST_ROOT/<component>/<file> and STABLE.json",
    )
    parser.add_argument("--base-url", default=GCS_BASE_URL, help=argparse.SUPPRESS)
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="network timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="retries of failed requests (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only print what would be downloaded",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None, bucket: Optional[Bucket] = None) -> int:
    """Command line entry point; returns the exit status."""
    args = _build_parser().parse_args(argv)
    unknown = [c for c in args.components if c not in COMPONENTS]
    if unknown:
        error(
            f"unknown firmware component(s): {', '.join(unknown)} "
            f"(known: {', '.join(COMPONENTS)})"
        )
        return EXIT_USAGE
    if not args.components:
        info("no firmware components requested")
        return EXIT_OK
    if not DEVICE_RE.fullmatch(args.device):
        error(
            f"--device must name the Maui device, e.g. maui_v11 (got {args.device!r})"
        )
        return EXIT_USAGE

    fetcher = Fetcher(
        bucket or Bucket(args.base_url, args.timeout, args.retries),
        args.dest_root,
        args.device,
        dry_run=args.dry_run,
    )
    failed = fetcher.fetch_all(args.components)
    if failed:
        error(f"failed to fetch: {', '.join(failed)}")
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
