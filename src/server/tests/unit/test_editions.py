# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Consistency tests for the DiagnoseMe edition registries.

editions.json is the single source of truth of the edition list: the server
registry (server/editions.py) reads it and only adds the services table.
These tests make sure the services table, the Angular edition files and
configurations, the Dockerfile.base targets and the build files stay
consistent with it.
"""

import json
import os
import re
import tempfile
import unittest
from unittest import mock

from server import editions
from server import rpcserver


# pylint: disable=missing-function-docstring


REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
UI_EDITIONS_DIR = os.path.join(REPO_ROOT, "src", "ui", "src", "app", "editions")
MAUI_SERVICES = {
    editions.SERVICE_MAUI_STATION1,
    editions.SERVICE_MAUI_PDC,
    editions.SERVICE_MAUI_STATION2,
    editions.SERVICE_MAUI_DEVICE_MGMT,
}
SERVO_SERVICES = {editions.SERVICE_SERVO_V41, editions.SERVICE_SERVO_MICRO}


def _load_jsonc(path: str):
    """Loads JSON that may contain trailing commas (angular.json)."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


def _read(*path: str) -> str:
    """Returns the content of a repository file."""
    with open(os.path.join(REPO_ROOT, *path), "r", encoding="utf-8") as f:
        return f.read()


class TestEditionRegistry(unittest.TestCase):
    """Tests for server/editions.py and its sibling registries."""

    def setUp(self):
        self.build_registry = _load_jsonc(os.path.join(REPO_ROOT, "editions.json"))[
            "editions"
        ]

    def test_server_registry_is_read_from_editions_json(self):
        self.assertEqual(list(editions.EDITIONS), list(self.build_registry))
        self.assertEqual(set(editions.SERVICES_OF), set(self.build_registry))
        for edition_id, edition in editions.EDITIONS.items():
            meta = self.build_registry[edition_id]
            self.assertEqual(edition.title, meta["title"], edition_id)
            self.assertEqual(edition.shipped, meta["shipped"], edition_id)
            self.assertEqual(edition.maui_hw_rev, meta.get("maui_hw_rev", ""))
            self.assertEqual(edition.services, editions.SERVICES_OF[edition_id])

    def test_editions_json_path_from_the_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "editions.json")
            registry = {
                "editions": {
                    e: dict(self.build_registry[e], title=f"T {e}")
                    for e in self.build_registry
                }
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(registry, f)
            with mock.patch.dict(os.environ, {editions.EDITIONS_JSON_ENV: path}):
                self.assertEqual(editions.editions_json_path(), path)
                loaded = editions.load_editions()
        self.assertEqual(loaded["maui-v1.1"].title, "T maui-v1.1")

    def test_missing_or_inconsistent_editions_json_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "Cannot read"):
                editions.load_editions(os.path.join(tmp, "missing.json"))
            path = os.path.join(tmp, "editions.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"editions": {"bogus": {"title": "B"}}}, f)
            with self.assertRaisesRegex(ValueError, "bogus"):
                editions.load_editions(path)

    def test_all_edition_is_never_shipped(self):
        self.assertFalse(editions.EDITIONS["all"].shipped)
        self.assertFalse(self.build_registry["all"]["shipped"])

    def test_every_service_has_a_registrar(self):
        for edition in editions.EDITIONS.values():
            for service in edition.services:
                self.assertTrue(
                    service in rpcserver.SERVICE_REGISTRARS
                    or service in rpcserver.MAUI_SERVICES,
                    (edition, service),
                )

    def test_maui_fields_are_consistent(self):
        for edition_id, meta in self.build_registry.items():
            services = set(editions.SERVICES_OF[edition_id])
            if meta["maui_utils"]:
                self.assertTrue(meta.get("maui_device"), edition_id)
                self.assertTrue(meta.get("maui_hw_rev"), edition_id)
                self.assertTrue(meta["firmware"], edition_id)
                self.assertTrue(
                    meta.get("results_dir", "").startswith("/var/log/diagnoseme/"),
                    edition_id,
                )
                self.assertTrue(services & MAUI_SERVICES, edition_id)
            else:
                for key in ("maui_device", "maui_hw_rev", "results_dir"):
                    self.assertNotIn(key, meta, edition_id)
                self.assertEqual(meta["firmware"], [], edition_id)
                self.assertFalse(services & MAUI_SERVICES, edition_id)
            # servod runs exactly for the editions with Servo pages.
            self.assertEqual(
                editions.SERVICE_SERVOD in services,
                bool(services & SERVO_SERVICES),
                edition_id,
            )

    def test_servo_dolos_owns_the_historical_channels(self):
        servo_dolos = self.build_registry["servo-dolos"]
        self.assertTrue(servo_dolos["shipped"])
        self.assertFalse(servo_dolos["maui_utils"])
        self.assertEqual(servo_dolos["deb_package"], "diagnoseme")
        self.assertEqual(servo_dolos["docker_tag_aliases"], ["release"])
        self.assertEqual(servo_dolos["iso_latest"], "custom-debian-preseed-latest.iso")
        self.assertEqual(servo_dolos["landing"], "/dolos-rework")
        # Every Servo / Dolos service, as before the editions.
        self.assertEqual(
            set(editions.SERVICES_OF["servo-dolos"]),
            set(editions.ALL_SERVICES) - MAUI_SERVICES,
        )
        for edition_id, meta in self.build_registry.items():
            if edition_id == "servo-dolos":
                continue
            self.assertNotEqual(meta["deb_package"], "diagnoseme", edition_id)
            self.assertNotIn("iso_latest", meta, edition_id)
            # :release belongs to servo-dolos, :maui is not promoted any more.
            aliases = meta.get("docker_tag_aliases", [])
            self.assertNotIn("release", aliases, edition_id)
            self.assertNotIn("maui", aliases, edition_id)

    def test_maui_v11_package(self):
        self.assertEqual(
            self.build_registry["maui-v1.1"]["deb_package"], "diagnoseme-maui-v1.1"
        )

    def test_deb_packages_are_unique(self):
        packages = [m["deb_package"] for m in self.build_registry.values()]
        self.assertEqual(len(packages), len(set(packages)))

    def test_ui_edition_files_exist_and_match(self):
        angular = _load_jsonc(os.path.join(REPO_ROOT, "src", "ui", "angular.json"))
        configs = angular["projects"]["diagnoseme"]["architect"]["build"][
            "configurations"
        ]
        for edition_id, meta in self.build_registry.items():
            edition_file = os.path.join(UI_EDITIONS_DIR, f"{edition_id}.edition.ts")
            active_file = os.path.join(
                UI_EDITIONS_DIR, f"active-edition.{edition_id}.ts"
            )
            self.assertTrue(os.path.exists(edition_file), edition_file)
            self.assertTrue(os.path.exists(active_file), active_file)
            with open(edition_file, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn(f"id: '{edition_id}'", content)
            self.assertIn(f"landing: '{meta['landing']}'", content)
            with open(active_file, "r", encoding="utf-8") as f:
                self.assertIn(f"'./{edition_id}.edition'", f.read())
            self.assertIn(edition_id, configs, "angular.json configuration")
            replacement = configs[edition_id]["fileReplacements"][0]
            self.assertEqual(
                os.path.basename(replacement["with"]),
                os.path.basename(active_file),
            )

    def test_servo_dolos_ui_has_every_servo_and_dolos_page(self):
        content = _read("src", "ui", "src", "app", "editions", "servo-dolos.edition.ts")
        for name in ("DOLOS", "SERVO_V4P1", "SERVO_MICRO"):
            self.assertIn(f"...{name}_MENU", content)
            self.assertIn(f"...{name}_ROUTES", content)
        self.assertNotIn("MAUI", content)

    def test_every_edition_has_a_server_base_target(self):
        dockerfile = _read("dockerfiles", "rpcserver", "Dockerfile.base")
        targets = set(re.findall(r"^FROM \S+ AS (base-\S+)$", dockerfile, re.M))
        for edition_id in self.build_registry:
            self.assertIn(f"base-{edition_id}", targets, edition_id)

    def test_builds_have_no_default_edition(self):
        for path in (
            ("development_environment", "deb_builder", "Dockerfile"),
            ("dockerfiles", "rpcserver", "Dockerfile"),
            ("dockerfiles", "ui", "Dockerfile"),
        ):
            content = _read(*path)
            self.assertRegex(content, r"(?m)^ARG EDITION$", path)
            self.assertNotRegex(content, r"(?m)^ARG EDITION=", path)
        release = _read("dockerfiles", "cloudbuild_release.yaml")
        self.assertIn("  _EDITION: ''", release)
        # The substitution of the existing release trigger.
        self.assertIn("  _COMMIT_SHA: ''", release)
        self.assertIn('validate --shipped "${_EDITION}"', release)
        self.assertNotIn("${COMMIT_SHA}", release)
        self.assertIn(
            "EDITION=servo-dolos",
            _read("development_environment", "deb_builder", "cloudbuild.yaml"),
        )
        for path in (
            ("development_environment", "build-offline-bundle.sh"),
            ("test_server", "build_iso.sh"),
        ):
            self.assertRegex(_read(*path), r'(?m)^EDITION=""$', path)

    def test_servo_dolos_stations_keep_following_release(self):
        # The servo-dolos package points its stations at :release (config.env
        # DOCKER_TAG), the tag the Servo / Dolos stations installed before the
        # editions follow, so no pipeline has to create :servo-dolos first.
        self.assertEqual(self.build_registry["servo-dolos"]["station_tag"], "release")
        for edition_id, meta in self.build_registry.items():
            if edition_id != "servo-dolos":
                self.assertNotIn("station_tag", meta, edition_id)
        for parts in (
            ("development_environment", "deb_builder", "cloudbuild.yaml"),
            ("dockerfiles", "cloudbuild_release.yaml"),
        ):
            self.assertNotIn("gcloud artifacts docker tags add", _read(*parts))
        promote = _read("tools", "promote.sh")
        self.assertIn("docker_tags", promote)

    def test_only_servo_dolos_writes_the_latest_iso(self):
        release = _read("dockerfiles", "cloudbuild_release.yaml")
        self.assertIn('get "${_EDITION}" iso_latest', release)
        self.assertNotIn(
            "gs://diagnoseme_iso/custom-debian-preseed-latest.iso", release
        )
        owners = [
            edition_id
            for edition_id, meta in self.build_registry.items()
            if meta.get("iso_latest") == "custom-debian-preseed-latest.iso"
        ]
        self.assertEqual(owners, ["servo-dolos"])

    def test_get_active_edition_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(editions.get_active_edition().edition_id, "all")

    def test_get_active_edition_from_env(self):
        with mock.patch.dict(os.environ, {"DIAGNOSEME_EDITION": "maui-v1.1"}):
            edition = editions.get_active_edition()
        self.assertEqual(edition.edition_id, "maui-v1.1")
        self.assertEqual(edition.maui_hw_rev, "v1.1")
        self.assertNotIn(editions.SERVICE_SERVOD, edition.services)
        self.assertNotIn(editions.SERVICE_DOLOS, edition.services)

    def test_servo_dolos_edition_from_env(self):
        with mock.patch.dict(os.environ, {"DIAGNOSEME_EDITION": "servo-dolos"}):
            edition = editions.get_active_edition()
        self.assertEqual(edition.maui_hw_rev, "")
        self.assertIn(editions.SERVICE_SERVOD, edition.services)
        self.assertIn(editions.SERVICE_DOLOS, edition.services)

    def test_get_active_edition_unknown(self):
        with mock.patch.dict(os.environ, {"DIAGNOSEME_EDITION": "bogus"}):
            with self.assertRaises(ValueError):
                editions.get_active_edition()


def _recording_registrar(calls, key):
    """Returns a registrar that only records |key| in |calls|."""
    return lambda server, shared: calls.append(key)


class TestReleaseArtifacts(unittest.TestCase):
    """editions.json fields used by the offline bundle and ISO builders."""

    def setUp(self):
        self.build_registry = _load_jsonc(os.path.join(REPO_ROOT, "editions.json"))[
            "editions"
        ]

    def test_stable_firmware_required_for_maui_releases(self):
        # The Maui v1.1 ISO and bundle need mcu / pdc with a valid
        # STABLE.json (ODM B's firmware directories hold older images without
        # one; the station refuses several images without a marker). The
        # bundle builder enforces it.
        self.assertEqual(
            self.build_registry["maui-v1.1"]["firmware_stable_required"],
            ["mcu", "pdc"],
        )
        for edition_id, meta in self.build_registry.items():
            required = meta.get("firmware_stable_required", [])
            self.assertLessEqual(set(required), set(meta["firmware"]), edition_id)
        bundle = _read("development_environment", "build-offline-bundle.sh")
        self.assertIn('get "${EDITION}" firmware_stable_required', bundle)
        self.assertIn('--verify "${FW_STABLE_REQUIRED[@]}"', bundle)
        self.assertIn('REQUIRE_ARGS+=(--require "${COMP}")', bundle)

    def test_realtek_tools_match_the_server_base_targets(self):
        # realtek_tools marks exactly the editions whose Dockerfile.base target
        # (directly or through its base stage) uses rtk-tools-builder: their
        # debs ship rtunicpg and the r8152 DKMS driver. The other targets never
        # reach rtk-fetcher, so BuildKit does not need the files for them.
        dockerfile = _read("dockerfiles", "rpcserver", "Dockerfile.base")
        parent = {}
        uses = {}
        stage = None
        for line in dockerfile.splitlines():
            match = re.match(r"^FROM (\S+) AS (\S+)$", line)
            if match:
                stage = match.group(2)
                parent[stage] = match.group(1)
                uses[stage] = set()
                continue
            match = re.match(r"^COPY --from=(\S+) ", line)
            if match and stage:
                uses[stage].add(match.group(1))

        def needs(stage, dependency):
            if stage not in parent:
                return False
            if dependency in uses[stage] or parent[stage] == dependency:
                return True
            return any(
                needs(s, dependency) for s in uses[stage] | {parent[stage]}
            )

        self.assertTrue(needs("rtk-tools-builder", "rtk-fetcher"))
        for edition_id, meta in self.build_registry.items():
            self.assertEqual(
                bool(meta.get("realtek_tools")),
                needs(f"base-{edition_id}", "rtk-tools-builder"),
                edition_id,
            )
            if not meta.get("realtek_tools"):
                self.assertFalse(
                    needs(f"base-{edition_id}", "rtk-fetcher"), edition_id
                )
        realtek = [e for e, m in self.build_registry.items() if m.get("realtek_tools")]
        self.assertEqual(realtek, ["servo-dolos", "servo-v4p1", "all"])

    def test_realtek_tools_are_fetched_by_one_helper(self):
        # Only tools/fetch_rtk_tools.sh downloads them, and only for the
        # realtek editions; the deb builder does not need them otherwise.
        fetcher = _read("tools", "fetch_rtk_tools.sh")
        self.assertIn("realtek_tools", fetcher)
        for parts in (
            ("development_environment", "build-diagnoseme.sh"),
            ("development_environment", "build-offline-bundle.sh"),
            ("development_environment", "deb_builder", "cloudbuild.yaml"),
            ("dockerfiles", "cloudbuild_cq.yaml"),
            ("dockerfiles", "cloudbuild_release.yaml"),
        ):
            content = _read(*parts)
            self.assertIn("tools/fetch_rtk_tools.sh", content, parts)
            self.assertNotIn("gs://rtk-tools", content, parts)
        deb = _read("development_environment", "deb_builder", "Dockerfile")
        self.assertNotIn("COPY dist/rtunicpg.tar.bz2 ", deb)
        self.assertNotIn("COPY dist/r8152-2.21.4.tar.bz2 ", deb)
        self.assertIn('get "${EDITION}" realtek_tools', deb)

    def test_local_build_images_are_named_per_edition(self):
        # Builds of different editions can run in parallel.
        build = _read("development_environment", "build-diagnoseme.sh")
        self.assertIn('BUILD_TAG="build-${EDITION}"', build)
        self.assertIn('--build-arg BASE_IMAGE="server-base:${BUILD_TAG}"', build)
        self.assertNotRegex(build, r"docker build -t \S+:latest")
        bundle = _read("development_environment", "build-offline-bundle.sh")
        self.assertIn('DEB_BUILDER_IMAGE="diagnoseme-deb-builder-${EDITION}"', bundle)
        self.assertIn('docker rmi "${DEB_BUILDER_IMAGE}"', bundle)
        for parts in (
            ("development_environment", "deb_builder", "cloudbuild.yaml"),
            ("dockerfiles", "cloudbuild_release.yaml"),
        ):
            content = _read(*parts)
            self.assertNotIn("temp-container", content, parts)
            self.assertNotIn("-t deb-builder ", content, parts)

    def test_offline_bundle_records_its_images(self):
        # No :latest fallback, the server image must be the edition, and the
        # default tag is the release build of the checked out commit.
        bundle = _read("development_environment", "build-offline-bundle.sh")
        self.assertNotIn(":latest", bundle)
        self.assertIn('DOCKER_TAG="${EDITION}-${COMMIT}"', bundle)
        self.assertIn('if [ "${IMAGE_EDITION}" != "${EDITION}" ]; then', bundle)
        self.assertIn('"commit": os.environ["COMMIT"]', bundle)
        self.assertIn('"repo_digests"', bundle)
        self.assertIn('"deb_version": os.environ["DEB_VERSION"]', bundle)


class TestUiOfflineFonts(unittest.TestCase):
    """The UI keeps main's filled icons and bundles every font (offline)."""

    def test_ui_fonts_are_bundled_for_offline_stations(self):
        # Filled Material Icons (as on main), Roboto and the Material Symbols
        # of the help button come from npm packages that ng build bundles;
        # no font may be loaded from a CDN (offline ISO installs).
        angular = _load_jsonc(os.path.join(REPO_ROOT, "src", "ui", "angular.json"))
        architect = angular["projects"]["diagnoseme"]["architect"]
        for target in ("build", "test"):
            styles = architect[target]["options"]["styles"]
            self.assertIn("material-icons/iconfont/filled.css", styles, target)
            self.assertIn("@fontsource/roboto/400.css", styles, target)
        ui_src = os.path.join(REPO_ROOT, "src", "ui", "src")
        for root, _, files in os.walk(ui_src):
            if os.path.relpath(root, ui_src).startswith("proto"):
                continue
            for name in files:
                if not name.endswith((".html", ".ts", ".scss", ".css")):
                    continue
                with open(os.path.join(root, name), encoding="utf-8") as f:
                    text = f.read()
                for cdn in ("fonts.googleapis.com", "fonts.gstatic.com"):
                    self.assertNotIn(cdn, text, os.path.join(root, name))
        self.assertNotIn(
            "setDefaultFontSetClass", _read("src", "ui", "src", "app", "app.ts")
        )


class TestRegisterServices(unittest.TestCase):
    """Tests for rpcserver.register_services."""

    def test_registers_only_edition_services_without_servod(self):
        calls = []
        fake = {
            key: _recording_registrar(calls, key)
            for key in rpcserver.SERVICE_REGISTRARS
        }
        services = list(editions.EDITIONS["maui-v1.1"].services)
        with mock.patch.dict(rpcserver.SERVICE_REGISTRARS, fake), mock.patch.object(
            rpcserver, "register_maui_services", return_value=True
        ) as mock_maui:
            with mock.patch("server.servod.ServodRpcService") as mock_servod:
                with rpcserver.contextlib.ExitStack() as stack:
                    rpcserver.register_services(mock.MagicMock(), services, stack)
                mock_servod.assert_not_called()
        self.assertEqual(
            calls, [s for s in services if s not in rpcserver.MAUI_SERVICES]
        )
        # All Maui services are registered together (all or nothing).
        mock_maui.assert_called_once()
        self.assertEqual(
            mock_maui.call_args[0][1],
            [s for s in services if s in rpcserver.MAUI_SERVICES],
        )

    def test_maui_failure_keeps_the_other_services(self):
        calls = []
        fake = {
            key: _recording_registrar(calls, key)
            for key in rpcserver.SERVICE_REGISTRARS
        }
        server = mock.MagicMock()
        # (importlib.import_module is global: patch servod before it.)
        with mock.patch.dict(rpcserver.SERVICE_REGISTRARS, fake), mock.patch(
            "server.servod.ServodRpcService"
        ), mock.patch(
            "server.rpcserver.importlib.import_module",
            side_effect=ImportError("no serial"),
        ), self.assertLogs(
            level="ERROR"
        ):
            with rpcserver.contextlib.ExitStack() as stack:
                rpcserver.register_services(
                    server, list(editions.EDITIONS["all"].services), stack
                )
        self.assertIn(editions.SERVICE_DOLOS, calls)
        self.assertIn(editions.SERVICE_SERVO_V41, calls)
        server.add_generic_rpc_handlers.assert_not_called()

    def test_servod_started_for_servo_edition(self):
        fake = {key: mock.MagicMock() for key in rpcserver.SERVICE_REGISTRARS}
        with mock.patch.dict(rpcserver.SERVICE_REGISTRARS, fake):
            with mock.patch("server.servod.ServodRpcService") as mock_servod:
                with rpcserver.contextlib.ExitStack() as stack:
                    rpcserver.register_services(
                        mock.MagicMock(),
                        list(editions.EDITIONS["servo-v4p1"].services),
                        stack,
                    )
                mock_servod.assert_called_once()
        shared = fake[editions.SERVICE_SERVO_V41].call_args[0][1]
        self.assertIn("servod_service", shared)

    def test_rejects_two_implementations_of_a_grpc_service(self):
        fake = {key: mock.MagicMock() for key in rpcserver.SERVICE_REGISTRARS}
        with mock.patch.dict(rpcserver.SERVICE_REGISTRARS, fake), mock.patch.object(
            rpcserver, "register_maui_services"
        ) as mock_maui:
            with self.assertRaisesRegex(ValueError, "MauiPdcFlashing"):
                with rpcserver.contextlib.ExitStack() as stack:
                    rpcserver.register_services(
                        mock.MagicMock(),
                        [editions.SERVICE_MAUI_PDC, editions.SERVICE_MAUI_PDC],
                        stack,
                    )
            mock_maui.assert_not_called()

    def test_rejects_unknown_service(self):
        with self.assertRaisesRegex(ValueError, "bogus"):
            with rpcserver.contextlib.ExitStack() as stack:
                rpcserver.register_services(mock.MagicMock(), ["bogus"], stack)


class TestMauiRegistrars(unittest.TestCase):
    """Tests for the table-driven Maui service registration."""

    def test_every_maui_service_has_a_grpc_service(self):
        for service in rpcserver.MAUI_SERVICES:
            self.assertIn(service, editions.GRPC_SERVICE_OF)

    def test_registrars_add_the_servicer_and_reflection(self):
        for service, (_, servicer_name) in rpcserver.MAUI_SERVICES.items():
            server = mock.MagicMock()
            with mock.patch.object(
                rpcserver.reflection, "enable_server_reflection"
            ) as reflection:
                self.assertTrue(rpcserver.register_maui_services(server, [service]))
            self.assertTrue(server.add_generic_rpc_handlers.called, service)
            handler = server.add_generic_rpc_handlers.call_args[0][0][0]
            self.assertEqual(
                handler.service_name(),
                f"diagnoseme.{editions.GRPC_SERVICE_OF[service]}",
                servicer_name,
            )
            self.assertEqual(
                reflection.call_args[0][0][0], handler.service_name(), service
            )


if __name__ == "__main__":
    unittest.main()
