#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Fetches the stable Maui firmware from the public gs://maui-firmware bucket.

Bucket layout (one directory per firmware family, one directory per version):

  <gcs_dir>/<version>/<payload>                     the firmware image
  <gcs_dir>/<version>/<channel>.<device><suffix>    empty marker, e.g.
                                                    stable.maui_v11.pdcfw

A component resolves to the version directory holding the stable marker of
the Maui device (--device, editions.json "maui_device"); device independent
components (the maui-utils Docker image, docker/<version>/stable.dockertag)
have a single stable.<suffix> marker. If an old marker was left behind, the
highest version wins (versions compare numerically, so 0.0.1-100 is newer
than 0.0.1-69). The version directory is expected to hold a single image
with the component extension; if it holds several, one is picked
deterministically (see pick_payload) and a warning is printed. There is no
version option: builds and stations always follow the stable markers.

<dest-root>/<component> is replaced as a whole: the image is downloaded into
a temporary directory next to it, verified (size, md5 of the bucket listing
ETag or of the x-goog-hash header; composite objects such as maui-utils.tar
have no md5, so their x-goog-hash crc32c is checked instead) and then
swapped in, so files of older
releases are removed and an interrupted or failed download leaves the
previous firmware in place. Next to the image the directory gets a
STABLE.json, which the DiagnoseMe server uses to pick the exact image:

  {"version": "...", "file": "...", "md5": "...", "source": "gs://..."}

(plus "crc32c" when the bucket has no md5 of the object). The md5 is always
the md5 of the downloaded file, so stations can check the image later.
When the existing STABLE.json already names the same version and md5 (or
crc32c) and the image is intact, nothing is downloaded (unless --force).

--local-file installs a local image as <component> instead (a developer's
unreleased maui-utils build, see tools/fetch_maui_utils.sh), with the same
STABLE.json and the same atomic replacement.

Used by start-diagnoseme (online stations; installed by the deb as
/usr/local/bin/fetch-maui-firmware), tools/fetch_maui_utils.sh,
test_server/build_iso.sh and development_environment/build-offline-bundle.sh.
It only uses the Python 3 standard library (google_crc32c or crcmod speed up
crc32c checks when installed) because it also runs on stations and in build
containers.

Exit status: 0 when every component is up to date (a missing marker of an
optional component is only a warning), 1 when a component failed, 2 for
usage errors. Callers decide whether a failure is fatal.

Examples:
  tools/maui_fw_fetch.py --device maui_v11 --dest-root dist/firmware mcu pdc
  tools/maui_fw_fetch.py --dry-run --device maui_v11 --dest-root /tmp/fw mcu
  tools/maui_fw_fetch.py --dest-root dist maui-utils
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
import shutil
import socket
import struct
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
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_DOWNLOAD_TIMEOUT_S = 60.0
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
    # Extension of the firmware image in the version directory.
    file_ext: str
    # Image names preferred (in this order) when the version directory holds
    # several images; {version} is replaced by the version directory name.
    preferred_names: Tuple[str, ...] = ()
    # Exact payload size in bytes (0: any).
    size: int = 0
    # A missing stable marker is an error (else only a warning).
    required: bool = True
    # The stable marker names the Maui device (stable.<device><suffix>); else
    # there is one marker for every device (stable<suffix>).
    per_device: bool = True


# Station component (/var/lib/diagnoseme/firmware/<component>, editions.json
# "firmware") -> bucket layout. Keep in sync with the maui release scripts.
COMPONENTS: Dict[str, Component] = {
    # Maui v1.1 MCU App (TI MSPM0, TI-TXT).
    "mcu": Component("mcu-fw/mspm0", ".mcufw", ".txt", ("zephyr.txt",)),
    # PDC (TPS66994) firmware of every Maui revision.
    "pdc": Component("pdc-fw", ".pdcfw", ".bin", ("{version}.bin", "pdc.bin")),
    # maui-utils Docker image (docker save) with the Maui host tools of every
    # Maui edition (bundled into the deb, see tools/fetch_maui_utils.sh).
    "maui-utils": Component(
        "docker", ".dockertag", ".tar", ("maui-utils.tar",), per_device=False
    ),
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


def _crc32c_from_goog_hash(header: str) -> str:
    """Returns the crc32c of an x-goog-hash header as 8 hex digits, or ''."""
    for part in header.split(","):
        algo, _, value = part.strip().partition("=")
        if algo == "crc32c" and value:
            try:
                raw = base64.b64decode(value)
            except (binascii.Error, ValueError):
                return ""
            return raw.hex() if len(raw) == 4 else ""
    return ""


def _goog_hash_values(headers) -> List[str]:
    """Returns the x-goog-hash header values of a response."""
    if hasattr(headers, "get_all"):
        return headers.get_all("x-goog-hash") or []
    return [headers.get("x-goog-hash", "") or ""]


def _header_md5(headers) -> str:
    """Returns the md5 of the x-goog-hash header(s) of a response, or ''."""
    for value in _goog_hash_values(headers):
        md5 = _md5_from_goog_hash(value)
        if md5:
            return md5
    return ""


def _header_crc32c(headers) -> str:
    """Returns the crc32c (hex) of the x-goog-hash header(s), or ''."""
    for value in _goog_hash_values(headers):
        crc = _crc32c_from_goog_hash(value)
        if crc:
            return crc
    return ""


def _crc32c_tables() -> List[List[int]]:
    """Returns the slicing-by-8 tables of CRC-32C (Castagnoli, reflected)."""
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
        table.append(crc)
    tables = [table]
    for _ in range(7):
        prev = tables[-1]
        tables.append([(prev[i] >> 8) ^ table[prev[i] & 0xFF] for i in range(256)])
    return tables


_CRC32C_TABLES = _crc32c_tables()


def _crc32c_update_py(crc: int, data: bytes) -> int:
    """Pure Python CRC-32C of |data| continuing |crc| (slow, ~8 MB/s)."""
    # pylint: disable-next=unbalanced-tuple-unpacking
    t0, t1, t2, t3, t4, t5, t6, t7 = _CRC32C_TABLES
    crc ^= 0xFFFFFFFF
    view = memoryview(data)
    whole = len(view) - len(view) % 8
    for low, high in struct.iter_unpack("<II", view[:whole]):
        low ^= crc
        crc = (
            t7[low & 0xFF]
            ^ t6[(low >> 8) & 0xFF]
            ^ t5[(low >> 16) & 0xFF]
            ^ t4[low >> 24]
            ^ t3[high & 0xFF]
            ^ t2[(high >> 8) & 0xFF]
            ^ t1[(high >> 16) & 0xFF]
            ^ t0[high >> 24]
        )
    for byte in view[whole:]:
        crc = t0[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def _crc32c_updater() -> Callable[[int, bytes], int]:
    """Returns a crc32c(crc, data) function, a C implementation if installed."""
    try:
        import google_crc32c  # pylint: disable=import-outside-toplevel

        if google_crc32c.implementation == "c":
            return google_crc32c.extend
    except (ImportError, AttributeError):
        pass
    try:
        import crcmod.predefined  # pylint: disable=import-outside-toplevel

        if getattr(crcmod, "_usingExtension", False):
            fn = crcmod.predefined.mkPredefinedCrcFun("crc-32c")
            return lambda crc, data: fn(data, crc)
    except ImportError:
        pass
    return _crc32c_update_py


def file_crc32c(path: str) -> str:
    """Returns the CRC-32C of the file at |path| as 8 hex digits."""
    update = _crc32c_updater()
    crc = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            crc = update(crc, chunk)
    return f"{crc:08x}"


def version_key(version: str) -> Tuple:
    """Returns a sort key that orders version strings numerically.

    '0.0.1-100-abc' is newer than '0.0.1-69-def' (a text sort gets this
    wrong); numbers sort before text at the same position.
    """
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.findall(r"\d+|[^\d.\-_+]+", version)
    )


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
        timeout: float = DEFAULT_TIMEOUT_S,
        retries: int = 1,
        urlopen: Optional[Callable] = None,
        download_timeout: Optional[float] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.download_timeout = download_timeout or timeout
        self.retries = max(0, retries)
        self._urlopen = urlopen or urllib.request.urlopen

    def object_url(self, key: str) -> str:
        """Returns the download URL of |key|."""
        return f"{self.base_url}/{urllib.parse.quote(key, safe='/')}"

    def open(
        self, url: str, timeout: Optional[float] = None, method: Optional[str] = None
    ):
        """Opens |url|, retrying transient failures."""
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT}, method=method
        )
        for attempt in range(self.retries + 1):
            try:
                return self._urlopen(request, timeout=timeout or self.timeout)
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

    def remote_md5(self, payload: GcsObject) -> str:
        """Returns the hex md5 of |payload|, or '' when it is unknown.

        The listing ETag is the md5 of regular uploads; for composite objects
        the md5 (if any) is read from the x-goog-hash header of a HEAD request.
        """
        return self.remote_hashes(payload)[0]

    def remote_hashes(self, payload: GcsObject) -> Tuple[str, str]:
        """Returns (md5, crc32c) of |payload| as hex ('' when unknown).

        The crc32c is only looked up (HEAD x-goog-hash) when there is no md5,
        e.g. for composite objects such as maui-utils.tar.
        """
        if payload.md5:
            return payload.md5, ""
        with self.open(self.object_url(payload.key), method="HEAD") as response:
            return _header_md5(response.headers), _header_crc32c(response.headers)


def _gs_path(bucket: Bucket, path: str) -> str:
    """Returns a gs:// style name of |path| for messages."""
    name = urllib.parse.urlparse(bucket.base_url).path.strip("/")
    return f"gs://{name}/{path}"


def marker_name(component: Component, device: str) -> str:
    """Returns the stable marker file name of |device|, e.g. stable.maui_v11.pdcfw.

    Device independent components have a single marker, e.g. stable.dockertag.
    """
    if not component.per_device:
        return f"{CHANNEL}{component.marker_suffix}"
    return f"{CHANNEL}.{device}{component.marker_suffix}"


def pick_payload(
    component: Component, version: str, candidates: Sequence[GcsObject]
) -> GcsObject:
    """Picks the image of a version directory deterministically.

    Each version directory is expected to hold a single image. If there are
    several, the first of component.preferred_names that exists wins,
    otherwise the alphabetically first image; a warning is printed.
    """
    if not candidates:
        raise FetchError(f"no *{component.file_ext} image in version {version}")
    by_name = {o.name: o for o in candidates}
    names = sorted(by_name)
    if len(names) == 1:
        return by_name[names[0]]
    chosen = names[0]
    for preferred in component.preferred_names:
        name = preferred.format(version=version)
        if name in by_name:
            chosen = name
            break
    warn(
        f"expected one *{component.file_ext} image in {version}, found "
        f"{len(names)} ({', '.join(names)}); using {chosen}"
    )
    return by_name[chosen]


def resolve(  # pylint: disable=too-many-locals
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
    # highest version wins (numeric version order).
    def marker_version(o: GcsObject) -> str:
        return o.key[len(prefix) :].split("/")[0]

    newest = max(markers, key=lambda o: (version_key(marker_version(o)), o.key))
    if len(markers) > 1:
        warn(
            f"{len(markers)} '{marker}' markers in "
            f"{_gs_path(bucket, prefix)}; using the highest version ({newest.key})"
        )
    version = marker_version(newest)
    version_dir = f"{prefix}{version}/"
    candidates = [
        o
        for o in objects
        if o.key.startswith(version_dir)
        and "/" not in o.key[len(version_dir) :]
        and o.name.endswith(component.file_ext)
    ]
    try:
        payload = pick_payload(component, version, candidates)
    except FetchError as e:
        raise FetchError(f"{e} ({_gs_path(bucket, version_dir)})") from e
    if component.size and payload.size != component.size:
        raise FetchError(
            f"{_gs_path(bucket, payload.key)} has {payload.size} bytes, expected "
            f"{component.size}"
        )
    return version, payload


def _check_file_name(payload: GcsObject) -> str:
    """Returns the local file name of |payload| (raises when it is unsafe)."""
    name = payload.name
    if not SAFE_FILE_NAME_RE.fullmatch(name) or name == MANIFEST_NAME:
        raise FetchError(f"{payload.key}: unsafe file name {name!r}")
    return name


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


def is_up_to_date(component_dir: str, version: str, md5: str, crc32c: str = "") -> bool:
    """Whether |component_dir| already holds |version| with the given hash.

    The remote md5 (else crc32c) must match STABLE.json, and the local image
    the md5 STABLE.json records.
    """
    manifest = read_manifest(component_dir)
    if manifest.get("version") != version:
        return False
    if md5:
        if manifest.get("md5") != md5:
            return False
    elif not crc32c or manifest.get("crc32c") != crc32c:
        return False
    name = manifest.get("file") or ""
    if not isinstance(name, str) or not SAFE_FILE_NAME_RE.fullmatch(name):
        return False
    path = os.path.join(component_dir, name)
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    recorded = manifest.get("md5")
    return bool(recorded) and file_md5(path) == recorded


def verify_component(component_dir: str) -> str:
    """Returns why |component_dir| has no valid stable image, '' if it has one.

    Valid: STABLE.json names a version and a safe file name, and the image
    exists and matches the md5 STABLE.json records.
    """
    manifest = read_manifest(component_dir)
    if not manifest:
        return f"no valid {MANIFEST_NAME} in {component_dir}"
    name = manifest.get("file") or ""
    if not isinstance(name, str) or not SAFE_FILE_NAME_RE.fullmatch(name):
        return f"{MANIFEST_NAME} in {component_dir} names an invalid file {name!r}"
    if not manifest.get("version"):
        return f"{MANIFEST_NAME} in {component_dir} has no version"
    path = os.path.join(component_dir, name)
    if os.path.islink(path) or not os.path.isfile(path):
        return f"{path} named by {MANIFEST_NAME} does not exist"
    recorded = manifest.get("md5")
    if not recorded or file_md5(path) != recorded:
        return f"{path} does not match the md5 of {MANIFEST_NAME}"
    return ""


# pylint: disable-next=too-many-arguments,too-many-positional-arguments,too-many-locals
def download(
    bucket: Bucket,
    payload: GcsObject,
    path: str,
    expected_md5: str,
    expected_crc32c: str = "",
) -> Tuple[str, str]:
    """Downloads |payload| to |path| and verifies it.

    The size is checked against the listing; the md5 against |expected_md5|
    (listing ETag or HEAD x-goog-hash) and the x-goog-hash of the download.
    Objects without any md5 (composite uploads) are checked against their
    crc32c (|expected_crc32c| and the x-goog-hash of the download); an object
    without md5 and crc32c is an error.

    Returns:
      (md5, crc32c) of the download in hex; crc32c is '' when the md5 was
      checked.
    """
    digest = _new_md5()
    size = 0
    with open(path, "wb") as out:
        with bucket.open(
            bucket.object_url(payload.key), timeout=bucket.download_timeout
        ) as response:
            header_md5 = _header_md5(response.headers)
            header_crc32c = _header_crc32c(response.headers)
            for chunk in iter(lambda: response.read(CHUNK_SIZE), b""):
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(path, 0o644)
    if payload.size and size != payload.size:
        raise FetchError(
            f"{payload.key}: got {size} bytes, the listing says {payload.size}"
        )
    md5 = digest.hexdigest()
    expected_md5s = list(dict.fromkeys(filter(None, (expected_md5, header_md5))))
    for expected in expected_md5s:
        if md5 != expected:
            raise FetchError(f"{payload.key}: md5 {md5} does not match {expected}")
    if expected_md5s:
        return md5, ""
    expected_crcs = list(dict.fromkeys(filter(None, (expected_crc32c, header_crc32c))))
    if not expected_crcs:
        raise FetchError(f"{payload.key}: the bucket has neither an md5 nor a crc32c")
    info(f"{payload.key}: checking the crc32c ({size} bytes)")
    crc = file_crc32c(path)
    for expected in expected_crcs:
        if crc != expected:
            raise FetchError(f"{payload.key}: crc32c {crc} does not match {expected}")
    return md5, crc


def remove_leftovers(dest_root: str, name: str) -> None:
    """Removes temporary directories left behind by interrupted runs."""
    for entry in os.listdir(dest_root):
        if entry.startswith((f".{name}.new.", f".{name}.old.")):
            path = os.path.join(dest_root, entry)
            if os.path.islink(path):
                os.unlink(path)
            else:
                shutil.rmtree(path, ignore_errors=True)


def swap_in(new_dir: str, component_dir: str) -> None:
    """Replaces |component_dir| by |new_dir|, restoring it if that fails."""
    parent = os.path.dirname(component_dir)
    old_dir = None
    if os.path.lexists(component_dir):
        old_dir = tempfile.mkdtemp(
            prefix=f".{os.path.basename(component_dir)}.old.", dir=parent
        )
        os.rmdir(old_dir)
        os.rename(component_dir, old_dir)
    try:
        os.rename(new_dir, component_dir)
    except BaseException:
        if old_dir:
            os.rename(old_dir, component_dir)
        raise
    _fsync_dir(parent)
    if old_dir:
        shutil.rmtree(old_dir, ignore_errors=True)


def info(message: str) -> None:
    """Prints a progress message."""
    print(f"maui_fw_fetch: {message}", flush=True)


def warn(message: str) -> None:
    """Prints a warning."""
    print(f"maui_fw_fetch: WARNING: {message}", file=sys.stderr, flush=True)


def error(message: str) -> None:
    """Prints an error."""
    print(f"maui_fw_fetch: ERROR: {message}", file=sys.stderr, flush=True)


def install_component(
    dest_root: str, name: str, fill: Callable[[str], Dict[str, str]]
) -> None:
    """Atomically replaces <dest_root>/<name> by a new directory.

    |fill| writes the image into the (temporary) new directory and returns
    its STABLE.json content; the directory is swapped in afterwards, or
    removed when |fill| fails.
    """
    os.makedirs(dest_root, exist_ok=True)
    remove_leftovers(dest_root, name)
    new_dir = tempfile.mkdtemp(prefix=f".{name}.new.", dir=dest_root)
    try:
        manifest = fill(new_dir)
        manifest_path = os.path.join(new_dir, MANIFEST_NAME)
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(manifest, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(manifest_path, 0o644)
        os.chmod(new_dir, 0o755)
        swap_in(new_dir, os.path.join(dest_root, name))
    except BaseException:
        shutil.rmtree(new_dir, ignore_errors=True)
        raise


def install_local_file(dest_root: str, name: str, path: str, version: str) -> None:
    """Installs the local image |path| as component |name| (developer builds).

    Writes <dest_root>/<name>/<file> and a STABLE.json with |version| and the
    md5 of the copy, replacing the component directory atomically.
    """
    file_name = os.path.basename(path)
    if not SAFE_FILE_NAME_RE.fullmatch(file_name) or file_name == MANIFEST_NAME:
        raise FetchError(f"unsafe file name {file_name!r}")
    if not version or not SAFE_FILE_NAME_RE.fullmatch(version):
        raise FetchError(f"invalid version {version!r}")
    source = os.path.abspath(path)

    def fill(new_dir: str) -> Dict[str, str]:
        target = os.path.join(new_dir, file_name)
        shutil.copyfile(source, target)
        os.chmod(target, 0o644)
        return {
            "version": version,
            "file": file_name,
            "md5": file_md5(target),
            "source": f"local:{source}",
        }

    install_component(dest_root, name, fill)
    info(f"{name}: installed the local {version}: {source} -> {dest_root}/{name}")


class Fetcher:
    """Fetches the stable firmware components of a Maui device."""

    def __init__(
        self,
        bucket: Bucket,
        dest_root: str,
        device: str,
        dry_run: bool = False,
        force: bool = False,
        quiet: bool = False,
        require: Sequence[str] = (),
    ):
        self.bucket = bucket
        # Components whose missing stable marker is an error even when they
        # are optional (release builds: ISO, offline bundle).
        self.require = frozenset(require)
        self.dest_root = dest_root
        self.device = device
        self.dry_run = dry_run
        self.force = force
        self.quiet = quiet

    def _progress(self, message: str) -> None:
        """Prints a progress message unless --quiet."""
        if not self.quiet:
            info(message)

    def fetch(self, name: str) -> None:
        """Fetches component |name| into <dest_root>/<name>/ (raises FetchError)."""
        component = COMPONENTS[name]
        resolved = resolve(self.bucket, component, self.device)
        if resolved is None:
            message = (
                f"no '{marker_name(component, self.device)}' marker in "
                f"{_gs_path(self.bucket, component.gcs_dir + '/')}"
            )
            if component.required or name in self.require:
                raise FetchError(message)
            warn(f"{name}: {message} yet; skipping")
            return
        version, payload = resolved
        file_name = _check_file_name(payload)
        source = _gs_path(self.bucket, payload.key)
        component_dir = os.path.join(self.dest_root, name)
        if os.path.islink(component_dir):
            raise FetchError(f"refusing to replace the symbolic link {component_dir}")
        if self.dry_run:
            info(f"{name}: {version}: {source} -> {component_dir}/{file_name}")
            return
        expected_md5, expected_crc32c = self.bucket.remote_hashes(payload)
        if not self.force and is_up_to_date(
            component_dir, version, expected_md5, expected_crc32c
        ):
            self._progress(
                f"{name}: {component_dir}/{file_name} ({version}) is up to date"
            )
            return

        def fill(new_dir: str) -> Dict[str, str]:
            self._progress(f"{name}: downloading {source}")
            md5, crc32c = download(
                self.bucket,
                payload,
                os.path.join(new_dir, file_name),
                expected_md5,
                expected_crc32c,
            )
            manifest = {"version": version, "file": file_name, "md5": md5}
            if crc32c:
                manifest["crc32c"] = crc32c
            manifest["source"] = source
            return manifest

        install_component(self.dest_root, name, fill)
        info(f"{name}: downloaded {version}: {source} -> {component_dir}/{file_name}")

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
        default=DEFAULT_TIMEOUT_S,
        help="timeout in seconds of bucket listing requests (default: %(default)s)",
    )
    parser.add_argument(
        "--download-timeout",
        type=float,
        default=DEFAULT_DOWNLOAD_TIMEOUT_S,
        help="timeout in seconds of firmware downloads (default: %(default)s)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="retries of failed requests (default: %(default)s)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="download even if STABLE.json shows the same version and md5",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only print what would be downloaded",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="COMPONENT",
        help="a missing stable marker of COMPONENT is an error, also for "
        "optional components (release builds; repeatable)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="download nothing; fail unless every COMPONENT in DEST_ROOT has a "
        "STABLE.json whose image exists and matches its md5",
    )
    parser.add_argument(
        "--local-file",
        default="",
        metavar="FILE",
        help="install FILE as the (single) COMPONENT instead of the stable "
        "release (developer builds); needs --local-version",
    )
    parser.add_argument("--local-version", default="", help=argparse.SUPPRESS)
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="only report downloaded components and failures",
    )
    return parser


# pylint: disable-next=too-many-return-statements
def main(argv: Optional[Sequence[str]] = None, bucket: Optional[Bucket] = None) -> int:
    """Command line entry point; returns the exit status."""
    args = _build_parser().parse_args(argv)
    unknown = [c for c in args.components + args.require if c not in COMPONENTS]
    if unknown:
        error(
            f"unknown firmware component(s): {', '.join(unknown)} "
            f"(known: {', '.join(COMPONENTS)})"
        )
        return EXIT_USAGE
    if not args.components:
        if not args.quiet:
            info("no firmware components requested")
        return EXIT_OK
    if args.verify:
        problems = [
            f"{c}: {p}"
            for c in args.components
            for p in [verify_component(os.path.join(args.dest_root, c))]
            if p
        ]
        for problem in problems:
            error(problem)
        return EXIT_FAILED if problems else EXIT_OK
    if args.local_file:
        if len(args.components) != 1 or not args.local_version:
            error("--local-file needs a single COMPONENT and --local-version")
            return EXIT_USAGE
        try:
            install_local_file(
                args.dest_root, args.components[0], args.local_file, args.local_version
            )
        except (FetchError, OSError) as e:
            error(f"{args.components[0]}: {e}")
            return EXIT_FAILED
        return EXIT_OK
    per_device = any(COMPONENTS[c].per_device for c in args.components)
    if per_device and not DEVICE_RE.fullmatch(args.device):
        error(
            f"--device must name the Maui device, e.g. maui_v11 (got {args.device!r})"
        )
        return EXIT_USAGE

    fetcher = Fetcher(
        bucket
        or Bucket(
            args.base_url,
            args.timeout,
            args.retries,
            download_timeout=args.download_timeout,
        ),
        args.dest_root,
        args.device,
        dry_run=args.dry_run,
        force=args.force,
        quiet=args.quiet,
        require=args.require,
    )
    failed = fetcher.fetch_all(args.components)
    if failed:
        error(f"failed to fetch: {', '.join(failed)}")
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
