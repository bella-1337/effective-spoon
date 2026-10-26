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

    def test_current_contracts_pass(self) -> None:
        config = {
            "services": {
                "litellm-prod": {
                    "depends_on": {
                        "postgres": {"condition": "service_healthy"},
                        "presidio-analyzer": {"condition": "service_healthy"},
                    }
                }
            }
        }

        self.assertEqual(self.module.validate_contracts(config), [])

    def test_auxiliary_production_dependency_fails(self) -> None:
        config = {
            "services": {
                "litellm-prod": {
                    "depends_on": {
                        "postgres": {"condition": "service_healthy"},
                        "langfuse-web": {"condition": "service_healthy"},
                    }
                }
            }
        }

        failures = self.module.validate_contracts(config)

        self.assertTrue(
            any("auxiliary services" in failure for failure in failures),
            failures,
        )


if __name__ == "__main__":
    unittest.main()
