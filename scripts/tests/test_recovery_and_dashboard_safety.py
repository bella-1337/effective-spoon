from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


def load_script(name: str) -> ModuleType:
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RestoreSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_script("restore-databases.py")
        self.archive_dir = tempfile.TemporaryDirectory()
        self.archive = Path(self.archive_dir.name) / "database.dump"
        self.archive.write_bytes(b"archive")

    def tearDown(self) -> None:
        self.archive_dir.cleanup()

    def test_successful_restore_preserves_previous_database(self) -> None:
        renames: list[tuple[str, str]] = []
        with (
            patch.object(self.module, "service_is_running", return_value=True),
            patch.object(
                self.module,
                "compose_from_file",
                return_value=SimpleNamespace(returncode=0),
            ),
            patch.object(
                self.module,
                "postgres_command",
                side_effect=[True, True, True],
            ),
            patch.object(self.module, "compose_capture", return_value="3"),
            patch.object(
                self.module, "container_environment", return_value="litellm"
            ),
            patch.object(
                self.module,
                "database_exists",
                side_effect=[True, False],
            ),
            patch.object(
                self.module,
                "rename_database",
                side_effect=lambda _service, source, destination: (
                    renames.append((source, destination)) or True
                ),
            ),
        ):
            self.assertTrue(
                self.module.restore_database("postgres", "litellm_prod", self.archive)
            )

        self.assertEqual(renames[0][0], "litellm_prod")
        self.assertIn("_pre_restore_", renames[0][1])
        self.assertIn("_restore_", renames[1][0])
        self.assertEqual(renames[1][1], "litellm_prod")

    def test_failed_post_promotion_check_restores_previous_database(self) -> None:
        renames: list[tuple[str, str]] = []
        with (
            patch.object(self.module, "service_is_running", return_value=True),
            patch.object(
                self.module,
                "compose_from_file",
                return_value=SimpleNamespace(returncode=0),
            ),
            patch.object(
                self.module,
                "postgres_command",
                side_effect=[True, True, False],
            ),
            patch.object(self.module, "compose_capture", return_value="3"),
            patch.object(
                self.module, "container_environment", return_value="litellm"
            ),
            patch.object(
                self.module,
                "database_exists",
                side_effect=[True, False],
            ),
            patch.object(
                self.module,
                "rename_database",
                side_effect=lambda _service, source, destination: (
                    renames.append((source, destination)) or True
                ),
            ),
        ):
            self.assertFalse(
                self.module.restore_database("postgres", "litellm_prod", self.archive)
            )

        self.assertEqual(renames[-1][1], "litellm_prod")
        self.assertIn("_pre_restore_", renames[-1][0])

    def test_rename_terminates_stale_sessions_before_alter(self) -> None:
        calls: list[list[str]] = []

        def capture(arguments: list[str], check: bool) -> SimpleNamespace:
            self.assertFalse(check)
            calls.append(arguments)
            return SimpleNamespace(returncode=0)

        with (
            patch.object(self.module, "container_environment", return_value="litellm"),
            patch.object(self.module, "compose", side_effect=capture),
        ):
            self.assertTrue(
                self.module.rename_database(
                    "postgres", "litellm_prod", "litellm_prod_pre_restore_123"
                )
            )

        self.assertIn("pg_terminate_backend", calls[0][-1])
        self.assertIn("ALTER DATABASE", calls[1][-1])

    def test_restore_requires_known_database_clients_stopped(self) -> None:
        checked: list[str] = []

        with patch.object(
            self.module,
            "service_is_running",
            side_effect=lambda service: checked.append(service) or False,
        ):
            self.module.require_application_services_stopped()

        self.assertEqual(
            checked,
            [
                "litellm-prod",
                "litellm-dev",
                "litellm-pgvector",
                "grafana",
                "pgvector-exporter",
            ],
        )


class DashboardSupplyChainTests(unittest.TestCase):
    def test_dashboard_sources_are_immutable_and_checksummed(self) -> None:
        module = load_script("fetch-dashboards.py")
        for url, _relative, checksum in module.DASHBOARDS:
            self.assertNotIn("/latest/", url)
            self.assertNotIn("/main/", url)
            self.assertRegex(checksum, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
