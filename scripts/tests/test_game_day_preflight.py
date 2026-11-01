from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


def load_script() -> ModuleType:
    path = SCRIPTS / "game-day-preflight.py"
    spec = importlib.util.spec_from_file_location("game_day_preflight", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GameDayPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_script()

    def valid_config(self) -> dict[str, object]:
        return {
            "services": {
                "litellm-prod": {
                    "depends_on": {
                        "postgres": {"condition": "service_healthy"},
                        "presidio-analyzer": {"condition": "service_healthy"},
                        "langfuse-web": {
                            "condition": "service_healthy",
                            "required": False,
                        },
                    }
                },
                "alloy-docker-proxy": {"environment": {"NETWORKS": "1"}},
                "alloy": {},
                "opensearch": {},
                "opensearch-dashboards": {},
                "opensearch-bridge": {},
                "data-prepper": {},
                "grafana-db-init": {"profiles": ["grafana"]},
                "grafana": {"profiles": ["grafana"]},
                "loki": {"profiles": ["grafana"]},
                "alloy-loki": {"profiles": ["grafana"]},
                "langfuse-preflight": {"profiles": ["langfuse"]},
                "langfuse-postgres": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-clickhouse": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-clickhouse-observability-setup": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-minio": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-redis": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-worker": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-web": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                    "environment": {"AUTH_DISABLE_SIGNUP": "true"},
                },
                "image-security-docker-proxy": {"profiles": ["security"]},
                "image-security-monitor": {"profiles": ["security"]},
                "xberg": {
                    "environment": {"NO_PROXY": "127.0.0.1,localhost"},
                    "dns": ["172.29.0.53"],
                    "networks": {"xberg-sandbox": {}},
                    "command": [
                        "mcp",
                        "--config",
                        "/etc/xberg/xberg.toml",
                    ],
                    "healthcheck": {
                        "test": [
                            "CMD-SHELL",
                            "getent hosts docs.xberg.io >/dev/null && curl --fail",
                        ]
                    },
                },
                "xberg-dns": {
                    "networks": {
                        "egress": {},
                        "xberg-sandbox": {"ipv4_address": "172.29.0.53"},
                    }
                },
                "searxng": {"networks": {"internal": {}}},
            }
        }

    def test_current_contracts_pass(self) -> None:
        config = self.valid_config()

        self.assertEqual(self.module.validate_contracts(config), [])

    def test_custom_xberg_dns_address_passes_when_services_match(self) -> None:
        config = self.valid_config()
        config["services"]["xberg"]["dns"] = ["172.29.0.54"]
        config["services"]["xberg-dns"]["networks"]["xberg-sandbox"][
            "ipv4_address"
        ] = "172.29.0.54"

        self.assertEqual(self.module.validate_contracts(config), [])

    def test_auxiliary_production_dependency_fails(self) -> None:
        config = self.valid_config()
        config["services"]["litellm-prod"]["depends_on"] = {
            "postgres": {"condition": "service_healthy"},
            "langfuse-web": {"condition": "service_healthy"},
        }

        failures = self.module.validate_contracts(config)

        self.assertTrue(
            any("auxiliary services" in failure for failure in failures),
            failures,
        )

    def test_docker_metadata_and_health_contracts_fail_closed(self) -> None:
        config = self.valid_config()
        config["services"]["alloy-docker-proxy"]["environment"] = {}
        config["services"]["xberg"]["environment"] = {"NO_PROXY": ""}
        config["services"]["xberg"]["healthcheck"] = {
            "test": ["CMD", "curl", "--silent"]
        }

        failures = self.module.validate_contracts(config)

        self.assertTrue(any("network metadata" in failure for failure in failures))
        self.assertTrue(any("loopback health" in failure for failure in failures))
        self.assertTrue(any("HTTP error" in failure for failure in failures))
        self.assertTrue(any("DNS resolution" in failure for failure in failures))

    def test_opensearch_is_default_and_optional_uis_are_profiled(self) -> None:
        config = self.valid_config()
        config["services"]["opensearch"]["profiles"] = ["observability"]
        del config["services"]["opensearch-bridge"]
        config["services"]["grafana"] = {}
        config["services"]["langfuse-web"] = {}

        failures = self.module.validate_contracts(config)

        self.assertTrue(any("must not require a profile: opensearch" in f for f in failures), failures)
        self.assertTrue(any("missing: opensearch-bridge" in f for f in failures), failures)
        self.assertTrue(any("grafana profile: grafana" in f for f in failures), failures)
        self.assertTrue(any("langfuse profile: langfuse-web" in f for f in failures), failures)

    def test_searxng_must_not_be_publicly_routed(self) -> None:
        exposures = (
            ("networks", {"edge": {}, "internal": {}}, "internal network only"),
            ("labels", {"traefik.enable": "true"}, "Traefik route"),
            ("labels", ["traefik.enable=true"], "Traefik route"),
            ("ports", [{"target": 8080, "published": "8080"}], "host ports"),
        )
        for key, value, message in exposures:
            with self.subTest(key=key, value=value):
                config = self.valid_config()
                config["services"]["searxng"][key] = value

                failures = self.module.validate_contracts(config)

                self.assertTrue(any(message in f for f in failures), failures)

    def test_langfuse_public_signup_must_stay_disabled(self) -> None:
        for environment in ({}, {"AUTH_DISABLE_SIGNUP": "false"}):
            config = self.valid_config()
            config["services"]["langfuse-web"]["environment"] = environment

            failures = self.module.validate_contracts(config)

            self.assertIn(
                "langfuse-web must disable public self-registration", failures
            )

    def test_explicit_symlinked_environment_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core = root / ".env"
            optional = root / ".env.optional"
            core_target = root / "core.env"
            optional_target = root / "optional.env"
            core_target.write_text("CORE=value\n", encoding="utf-8")
            optional_target.write_text("OPTIONAL=value\n", encoding="utf-8")
            core.symlink_to(core_target)
            optional.write_text("OPTIONAL=value\n", encoding="utf-8")

            with patch.object(
                sys,
                "argv",
                [
                    "game-day-preflight.py",
                    "--env-file",
                    str(core),
                    "--optional-env-file",
                    str(optional),
                ],
            ), patch.object(self.module, "load_compose_config") as load_config:
                self.assertEqual(self.module.main(), 2)
                load_config.assert_not_called()

            core.unlink()
            core.write_text("CORE=value\n", encoding="utf-8")
            optional.unlink()
            optional.symlink_to(optional_target)

            with patch.object(
                sys,
                "argv",
                [
                    "game-day-preflight.py",
                    "--env-file",
                    str(core),
                    "--optional-env-file",
                    str(optional),
                ],
            ), patch.object(self.module, "load_compose_config") as load_config:
                self.assertEqual(self.module.main(), 2)
                load_config.assert_not_called()

    def test_default_symlinked_optional_environment_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("CORE=value\n", encoding="utf-8")
            optional_target = root / "optional.env"
            optional_target.write_text("OPTIONAL=value\n", encoding="utf-8")
            (root / ".env.optional").symlink_to(optional_target)

            with patch.object(self.module, "ROOT", root), patch.object(
                sys, "argv", ["game-day-preflight.py"]
            ), patch.object(self.module, "load_compose_config") as load_config:
                self.assertEqual(self.module.main(), 2)
                load_config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
