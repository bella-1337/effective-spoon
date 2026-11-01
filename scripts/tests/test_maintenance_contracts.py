from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import maintenance_common as common  # noqa: E402
import maintenance_contracts as contracts  # noqa: E402


class MaintenanceContractTests(unittest.TestCase):
    def test_compose_render_rejects_unsafe_default_optional_environment(self) -> None:
        for kind in ("symlink", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                optional = root / ".env.optional"
                if kind == "symlink":
                    target = root / "target.env"
                    target.write_text("OPTIONAL=value\n", encoding="utf-8")
                    optional.symlink_to(target)
                else:
                    optional.mkdir()

                with patch.object(contracts, "ROOT", root), patch.object(
                    contracts, "compose_capture"
                ) as compose_capture:
                    with self.assertRaisesRegex(
                        common.ScriptError, "environment file is unsafe.*install -m 600"
                    ):
                        contracts.load_compose_config(root / ".env")

                compose_capture.assert_not_called()

    def test_compose_render_selects_default_optional_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for present, expected in ((False, ".env.optional.example"), (True, ".env.optional")):
                if present:
                    (root / ".env.optional").write_text("OPTIONAL=value\n", encoding="utf-8")
                with self.subTest(present=present), patch.object(
                    contracts, "ROOT", root
                ), patch.object(
                    contracts, "compose_capture", return_value='{"services": {}}'
                ) as compose_capture:
                    contracts.load_compose_config(root / ".env")

                arguments = compose_capture.call_args.args[0]
                self.assertEqual(arguments[3], root / expected)

    def test_compose_render_enables_every_profile_once(self) -> None:
        with patch.object(
            contracts,
            "compose_capture",
            return_value='{"services": {}}',
        ) as compose_capture:
            config = contracts.load_compose_config(
                Path("/tmp/test.env"),
                Path("/tmp/test.optional.env"),
            )

        self.assertEqual(config, {"services": {}})
        arguments = compose_capture.call_args.args[0]
        self.assertEqual(
            [
                arguments[index + 1]
                for index, argument in enumerate(arguments)
                if argument == "--profile"
            ],
            list(contracts.COMPOSE_PROFILES),
        )
        self.assertEqual(
            arguments[:4],
            [
                "--env-file",
                Path("/tmp/test.env"),
                "--env-file",
                Path("/tmp/test.optional.env"),
            ],
        )
        self.assertEqual(arguments[-3:], ["config", "--format", "json"])

    def test_backup_metric_is_valid_prometheus_text(self) -> None:
        metric = contracts.backup_freshness_metrics(123)

        self.assertIn(
            f"# TYPE {contracts.BACKUP_FRESHNESS_METRIC} gauge",
            metric,
        )
        self.assertTrue(metric.endswith(f"{contracts.BACKUP_FRESHNESS_METRIC} 123\n"))
        with self.assertRaises(ValueError):
            contracts.backup_freshness_metrics(-1)

    def test_restore_contracts_generate_safe_names_and_session_cleanup(self) -> None:
        self.assertEqual(
            contracts.restore_database_names("litellm_prod", 42),
            ("litellm_prod_restore_42", "litellm_prod_pre_restore_42"),
        )
        self.assertIn(
            "pg_terminate_backend",
            contracts.terminate_database_connections_sql("litellm_prod"),
        )
        self.assertIn("opensearch-bridge", contracts.RESTORE_DATABASE_CLIENTS)
        self.assertIn("pgvector-exporter", contracts.RESTORE_DATABASE_CLIENTS)
        with self.assertRaises(ValueError):
            contracts.restore_database_names("litellm-prod", 42)
        with self.assertRaises(ValueError):
            contracts.terminate_database_connections_sql("litellm_prod'; DROP")

    def test_rollback_override_requires_known_services_and_immutable_images(self) -> None:
        image = "registry.example/litellm@sha256:" + ("a" * 64)
        override = contracts.render_rollback_override(
            ["litellm-prod"], {"litellm-prod": image}
        )

        self.assertTrue(override.startswith(contracts.ACTIVE_ROLLBACK_MARKER + "\n"))
        self.assertIn(f"    image: {image}\n", override)
        with self.assertRaises(ValueError):
            contracts.render_rollback_override(
                ["unknown"], {"unknown": image}
            )
        with self.assertRaises(ValueError):
            contracts.render_rollback_override(
                ["litellm-prod"], {"litellm-prod": "litellm:latest"}
            )

    def test_alert_uids_are_parsed_from_provisioning_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            alert_file = Path(directory) / "rules.yml"
            alert_file.write_text(
                "groups:\n  - rules:\n      - uid: first-rule\n      - uid: second_rule\n",
                encoding="utf-8",
            )

            self.assertEqual(
                contracts.alert_rule_uids(Path(directory)),
                {"first-rule", "second_rule"},
            )

    def test_current_repository_contracts_pass(self) -> None:
        self.assertEqual(contracts.validate_repository_contracts(), [])

    def test_traefik_dynamic_config_must_not_route_to_searxng(self) -> None:
        exposed = (
            "http:\n  services:\n    search:\n      loadBalancer:\n"
            "        servers:\n          - url: http://SearXNG:8080\n"
        )
        self.assertEqual(
            contracts.traefik_dynamic_config_failures(exposed),
            ["Traefik dynamic configuration must not route to internal-only SearXNG"],
        )
        self.assertEqual(
            contracts.traefik_dynamic_config_failures(
                "# searxng stays internal-only\ntls:\n  certificates: []\n"
            ),
            [],
        )

    def test_repository_contracts_detect_disconnected_workflows(self) -> None:
        with patch.object(contracts.runpy, "run_path", return_value={}):
            failures = contracts.validate_repository_contracts()

        self.assertTrue(
            any("disconnected from shared contracts" in failure for failure in failures),
            failures,
        )


if __name__ == "__main__":
    unittest.main()
