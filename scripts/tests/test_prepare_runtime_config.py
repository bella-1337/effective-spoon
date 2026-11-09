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
            with (
                patch.object(self.module, "langfuse_is_available", return_value=False),
                patch.object(self.module, "lens_is_available", return_value=False),
            ):
                enabled, _ = self.module.prepare_config(source, destination)

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
                patch.object(self.module, "lens_is_available", return_value=False),
                patch.dict(os.environ, environment, clear=False),
            ):
                enabled, _ = self.module.prepare_config(source, destination)

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
                patch.object(self.module, "lens_is_available", return_value=False),
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


    def lens_environment(self, **overrides: str) -> dict[str, str]:
        environment = {
            "LITELLM_LENS_URL": "http://lens:4318",
            "LITELLM_LENS_PUBLIC_URL": "https://lens.example.test",
            "LITELLM_LENS_SERVICE_TOKEN": "s" * 64,
            "LENS_GATEWAY_SECRET": "g" * 64,
            "UNRELATED": "kept",
        }
        environment.update(overrides)
        return environment

    def prepare_with_lens(
        self, directory: str, available: bool, environment: dict[str, str]
    ) -> tuple[tuple[bool, bool], dict[str, Any]]:
        source, destination = self.write_config(directory)
        with (
            patch.object(self.module, "langfuse_is_available", return_value=False),
            patch.object(self.module, "lens_is_available", return_value=available),
        ):
            result = self.module.prepare_config(source, destination, environment)
        return result, json.loads(destination.read_text(encoding="utf-8"))

    def test_strips_lens_connection_when_service_is_absent(self) -> None:
        environment = self.lens_environment()
        with tempfile.TemporaryDirectory() as directory:
            (_, lens_enabled), config = self.prepare_with_lens(directory, False, environment)

        self.assertFalse(lens_enabled)
        self.assertNotIn("general_settings", config)
        self.assertEqual(environment, {"UNRELATED": "kept"})

    def test_removes_configured_lens_store_when_service_is_absent(self) -> None:
        environment = self.lens_environment()
        with tempfile.TemporaryDirectory() as directory:
            source, destination = self.write_config(directory)
            config = json.loads(source.read_text(encoding="utf-8"))
            config["general_settings"] = {
                "master_key": "os.environ/LITELLM_MASTER_KEY",
                "tracing": {"store": {"type": "lens"}},
            }
            source.write_text(json.dumps(config), encoding="utf-8")
            with (
                patch.object(self.module, "langfuse_is_available", return_value=False),
                patch.object(self.module, "lens_is_available", return_value=False),
            ):
                self.module.prepare_config(source, destination, environment)
            written = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(
            written["general_settings"], {"master_key": "os.environ/LITELLM_MASTER_KEY"}
        )

    def test_enables_lens_store_and_keeps_connection_when_service_runs(self) -> None:
        environment = self.lens_environment()
        with tempfile.TemporaryDirectory() as directory:
            (_, lens_enabled), config = self.prepare_with_lens(directory, True, environment)

        self.assertTrue(lens_enabled)
        self.assertEqual(config["general_settings"]["tracing"], {"store": {"type": "lens"}})
        self.assertEqual(environment, self.lens_environment())

    def test_fails_closed_when_lens_runs_with_invalid_connection(self) -> None:
        invalid = (
            ({"LITELLM_LENS_SERVICE_TOKEN": ""}, "LITELLM_LENS_SERVICE_TOKEN"),
            ({"LENS_GATEWAY_SECRET": "short"}, "LENS_GATEWAY_SECRET must contain"),
            ({"LENS_GATEWAY_SECRET": "x" * 513}, "LENS_GATEWAY_SECRET must contain"),
            ({"LENS_GATEWAY_SECRET": "s" * 64}, "must be different secrets"),
            ({"LITELLM_LENS_PUBLIC_URL": "https://"}, "LENS_HOST"),
            ({"LITELLM_LENS_URL": "lens:4318"}, "LITELLM_LENS_URL"),
        )
        for overrides, message in invalid:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as directory:
                source, destination = self.write_config(directory)
                with (
                    patch.object(self.module, "langfuse_is_available", return_value=False),
                    patch.object(self.module, "lens_is_available", return_value=True),
                ):
                    with self.assertRaisesRegex(ValueError, message):
                        self.module.prepare_config(
                            source, destination, self.lens_environment(**overrides)
                        )
                self.assertFalse(destination.exists())

    def test_launcher_execs_command_without_lens_variables(self) -> None:
        environment = self.lens_environment(LANGFUSE_PUBLIC_KEY="")
        with tempfile.TemporaryDirectory() as directory:
            source, destination = self.write_config(directory)
            with (
                patch.object(self.module, "langfuse_is_available", return_value=False),
                patch.object(self.module, "lens_is_available", return_value=False),
                patch.dict(os.environ, environment, clear=True),
                patch.object(self.module.os, "execvpe") as execvpe,
                patch("sys.stdout"),
            ):
                status = self.module.main(
                    [str(source), str(destination), "--", "/app/entrypoint.sh", "--port=4000"]
                )

        self.assertEqual(status, 0)
        program, argv, launched_environment = execvpe.call_args.args
        self.assertEqual(program, "/app/entrypoint.sh")
        self.assertEqual(argv, ["/app/entrypoint.sh", "--port=4000"])
        self.assertFalse(set(self.module.LENS_ENV) & set(launched_environment))
        self.assertEqual(launched_environment["UNRELATED"], "kept")

    def test_launcher_does_not_start_command_when_preparation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, destination = self.write_config(directory)
            with (
                patch.object(self.module, "langfuse_is_available", return_value=False),
                patch.object(self.module, "lens_is_available", return_value=True),
                patch.dict(os.environ, {}, clear=True),
                patch.object(self.module.os, "execvpe") as execvpe,
                patch("sys.stderr"),
            ):
                status = self.module.main([str(source), str(destination), "--", "/bin/true"])

        self.assertEqual(status, 1)
        execvpe.assert_not_called()

    def test_launcher_rejects_malformed_arguments(self) -> None:
        for arguments in ([], ["only-source"], ["a", "b", "c"], ["a", "b", "--"]):
            with self.subTest(arguments=arguments), patch("sys.stderr"):
                self.assertEqual(self.module.main(arguments), 64)


if __name__ == "__main__":
    unittest.main()
