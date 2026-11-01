from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "litellm" / "modules" / "prepare_runtime_config.py"


def load_module() -> Any:
    yaml_stub = ModuleType("yaml")
    yaml_stub.YAMLError = ValueError
    yaml_stub.safe_load = json.load
    yaml_stub.safe_dump = lambda value, stream, sort_keys=False: json.dump(
        value, stream
    )
    spec = importlib.util.spec_from_file_location("prepare_runtime_config_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"yaml": yaml_stub}):
        spec.loader.exec_module(module)
    return module


class PrepareRuntimeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def write_config(self, directory: str) -> tuple[Path, Path]:
        source = Path(directory) / "config.yaml"
        destination = Path(directory) / "runtime.yaml"
        source.write_text(
            json.dumps(
                {
                    "include": ["modules/search.yaml"],
                    "litellm_settings": {
                        "callbacks": [
                            "modules.langfuse_user_attribution.proxy_handler_instance",
                            "langfuse_otel",
                        ],
                        "success_callback": ["prometheus"],
                    }
                }
            ),
            encoding="utf-8",
        )
        return source, destination

    def test_removes_only_langfuse_callbacks_when_service_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, destination = self.write_config(directory)
            with patch.object(self.module, "langfuse_is_available", return_value=False):
                enabled = self.module.prepare_config(source, destination)

            self.assertFalse(enabled)
            config = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(config["include"], ["modules/search.yaml"])
            self.assertNotIn("callbacks", config["litellm_settings"])
            self.assertEqual(
                config["litellm_settings"]["success_callback"], ["prometheus"]
            )

    def test_keeps_callbacks_when_service_and_credentials_are_available(self) -> None:
        environment = {
            "LANGFUSE_PUBLIC_KEY": "pk-test",
            "LANGFUSE_SECRET_KEY": "sk-test",
            "LANGFUSE_USER_ID_SALT": "test-salt",
        }
        with tempfile.TemporaryDirectory() as directory:
            source, destination = self.write_config(directory)
            with (
                patch.object(self.module, "langfuse_is_available", return_value=True),
                patch.dict(os.environ, environment, clear=False),
            ):
                enabled = self.module.prepare_config(source, destination)

            self.assertTrue(enabled)
            config = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(
                config["litellm_settings"]["callbacks"],
                [
                    "modules.langfuse_user_attribution.proxy_handler_instance",
                    "langfuse_otel",
                ],
            )

    def test_fails_closed_when_langfuse_is_available_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, destination = self.write_config(directory)
            with (
                patch.object(self.module, "langfuse_is_available", return_value=True),
                patch.dict(
                    os.environ,
                    {
                        "LANGFUSE_PUBLIC_KEY": "",
                        "LANGFUSE_SECRET_KEY": "",
                        "LANGFUSE_USER_ID_SALT": "",
                    },
                    clear=False,
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError, "LANGFUSE_PUBLIC_KEY"
                ):
                    self.module.prepare_config(source, destination)

            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
