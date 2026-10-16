# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Consistency tests for the DiagnoseMe edition registries.

The edition list lives in three places (server registry, editions.json used by
build scripts, and the Angular edition files/configurations). These tests make
sure they never drift apart.
"""

import json
import os
import re
import unittest
from unittest import mock

from server import editions
from server import rpcserver


REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
UI_EDITIONS_DIR = os.path.join(REPO_ROOT, "src", "ui", "src", "app", "editions")


def _load_jsonc(path: str):
    """Loads JSON that may contain trailing commas (angular.json)."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


class TestEditionRegistry(unittest.TestCase):
    """Tests for server/editions.py and its sibling registries."""

    def setUp(self):
        self.build_registry = _load_jsonc(os.path.join(REPO_ROOT, "editions.json"))[
            "editions"
        ]

    def test_server_and_build_registries_match(self):
        self.assertEqual(set(editions.EDITIONS), set(self.build_registry))
        for edition_id, edition in editions.EDITIONS.items():
            meta = self.build_registry[edition_id]
            self.assertEqual(edition.title, meta["title"], edition_id)
            self.assertEqual(edition.shipped, meta["shipped"], edition_id)

    def test_all_edition_is_never_shipped(self):
        self.assertFalse(editions.EDITIONS["all"].shipped)
        self.assertFalse(self.build_registry["all"]["shipped"])

    def test_every_service_has_a_registrar(self):
        for edition in editions.EDITIONS.values():
            for service in edition.services:
                self.assertIn(service, rpcserver.SERVICE_REGISTRARS, edition)

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
            self.assertIn(edition_id, configs, "angular.json configuration")
            replacement = configs[edition_id]["fileReplacements"][0]
            self.assertEqual(
                os.path.basename(replacement["with"]),
                os.path.basename(active_file),
            )

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

    def test_get_active_edition_unknown(self):
        with mock.patch.dict(os.environ, {"DIAGNOSEME_EDITION": "bogus"}):
            with self.assertRaises(ValueError):
                editions.get_active_edition()


class TestRegisterServices(unittest.TestCase):
    """Tests for rpcserver.register_services."""

    def test_registers_only_edition_services_without_servod(self):
        calls = []
        fake = {
            key: (lambda k: lambda server, shared: calls.append(k))(key)
            for key in rpcserver.SERVICE_REGISTRARS
        }
        with mock.patch.dict(rpcserver.SERVICE_REGISTRARS, fake):
            with mock.patch("server.servod.ServodRpcService") as mock_servod:
                with rpcserver.contextlib.ExitStack() as stack:
                    rpcserver.register_services(
                        mock.MagicMock(),
                        list(editions.EDITIONS["maui-v1.1"].services),
                        stack,
                    )
                mock_servod.assert_not_called()
        self.assertEqual(calls, list(editions.EDITIONS["maui-v1.1"].services))

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
        with mock.patch.dict(rpcserver.SERVICE_REGISTRARS, fake):
            with self.assertRaisesRegex(ValueError, "MauiPdcFlashing"):
                with rpcserver.contextlib.ExitStack() as stack:
                    rpcserver.register_services(
                        mock.MagicMock(),
                        [editions.SERVICE_MAUI_PDC, editions.SERVICE_MAUI_PDC],
                        stack,
                    )


class TestMauiRegistrars(unittest.TestCase):
    """Tests for the table-driven Maui service registrars."""

    def test_every_maui_service_has_a_grpc_service(self):
        for service in rpcserver.MAUI_SERVICERS:
            self.assertIn(service, editions.GRPC_SERVICE_OF)

    def test_registrars_add_the_servicer_and_reflection(self):
        for service, (_, servicer_name) in rpcserver.MAUI_SERVICERS.items():
            server = mock.MagicMock()
            with mock.patch.object(
                rpcserver.reflection, "enable_server_reflection"
            ) as reflection:
                rpcserver.SERVICE_REGISTRARS[service](server, {})
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
