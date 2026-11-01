from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

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
                },
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


if __name__ == "__main__":
    unittest.main()
