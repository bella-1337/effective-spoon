from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parents[1]


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
                    }
                },
                "alloy-docker-proxy": {"environment": {"NETWORKS": "1"}},
                "xberg": {
                    "environment": {"NO_PROXY": "127.0.0.1,localhost"},
                    "healthcheck": {"test": ["CMD", "curl", "--fail"]},
                },
            }
        }

    def test_current_contracts_pass(self) -> None:
        config = self.valid_config()

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


if __name__ == "__main__":
    unittest.main()
