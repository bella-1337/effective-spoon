from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


def load_script(filename: str, name: str) -> ModuleType:
    path = SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MaintenanceUpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_script("maintenance.py", "maintenance_under_test")

    def test_profiles_become_compose_options_and_the_rest_go_to_up(self) -> None:
        profiles, options = self.module.split_up_arguments(
            ["--profile", "prod", "-d", "--profile=grafana", "--wait"]
        )
        self.assertEqual(profiles, ["--profile", "prod", "--profile", "grafana"])
        self.assertEqual(options, ["-d", "--wait"])

    def test_rejects_files_doctor_did_not_validate(self) -> None:
        for arguments in (
            ["--env-file", "other.env"],
            ["--env-file=other.env"],
            ["-f", "other.yml"],
            ["--file=other.yml"],
            ["--project-directory", "/tmp"],
            ["--profile"],
            ["--profile", "-d"],
            ["--profile="],
        ):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as raised, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.module.split_up_arguments(arguments)
            self.assertEqual(raised.exception.code, 2)

    def test_up_runs_doctor_before_starting_the_stack(self) -> None:
        calls: list[str] = []
        result = type("Result", (), {"returncode": 0})()
        with patch.object(self.module, "doctor", side_effect=lambda: calls.append("doctor")), \
                patch.object(self.module, "compose", side_effect=lambda args, check: calls.append(args) or result):
            self.module.up(["--profile", "prod", "-d"])
        self.assertEqual(calls, ["doctor", ["--profile", "prod", "up", "-d"]])

    def test_up_does_not_start_anything_when_doctor_fails(self) -> None:
        compose_calls: list[object] = []
        with patch.object(self.module, "doctor", side_effect=SystemExit(1)), \
                patch.object(self.module, "compose", side_effect=compose_calls.append), \
                self.assertRaises(SystemExit):
            self.module.up(["--profile", "prod", "-d"])
        self.assertEqual(compose_calls, [])


class UpgradeCheckRuntimeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_script("check-litellm-upgrade-compatibility.py", "upgrade_check_under_test")

    def test_validator_starts_through_the_runtime_config_launcher(self) -> None:
        captured: list[list[str]] = []

        def fake_run(args, **_kwargs):
            captured.append([str(arg) for arg in args])
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()

        with patch.object(self.module, "run", side_effect=fake_run):
            self.module.docker_run_validation("image@sha256:" + "0" * 64, Path("/c.yaml"), "net", [])
        command = captured[0]
        image_index = command.index("image@sha256:" + "0" * 64)
        self.assertEqual(command[image_index - 1], "python")
        self.assertEqual(
            command[image_index + 1:],
            [
                "/app/modules/prepare_runtime_config.py", "/app/config.yaml", "/tmp/runtime-config.yaml",
                "--", "litellm", "--config", "/tmp/runtime-config.yaml", "--validate_config",
            ],
        )
        self.assertIn(f"{self.module.MODULES}:/tmp/modules:ro", command)
        self.assertIn("langfuse-web:127.0.0.1", command)
        self.assertIn("lens:127.0.0.1", command)
        self.assertIn(f"{self.module.VALIDATION_SECRETS}:/app/secrets:ro", command)
        self.assertIn("DATA_SOVEREIGNTY_REGION=eu", command)

    def test_lens_placeholders_satisfy_the_launcher(self) -> None:
        environment = self.module.optional_integration_environment()
        values = dict(
            item.split("=", 1) for item in environment if "=" in item and not item.endswith("127.0.0.1")
        )
        token, secret = values["LITELLM_LENS_SERVICE_TOKEN"], values["LENS_GATEWAY_SECRET"]
        self.assertNotEqual(token, secret)
        for value in (token, secret):
            self.assertGreaterEqual(len(value), 32)
            self.assertLessEqual(len(value), 512)

    def test_success_requires_both_integrations_enabled(self) -> None:
        models = self.module.VALIDATION_MODELS_MARKER
        full = f"{models}\nLangfuse callbacks are enabled\nLiteLLM Lens tracing is enabled\nLiteLLM: config OK\n"
        self.assertEqual(self.module.prepared_status(full), 0)
        no_models = "Langfuse callbacks are enabled\nLiteLLM Lens tracing is enabled\nLiteLLM: config OK\n"
        self.assertNotEqual(self.module.prepared_status(no_models), 0)
        partial = "Langfuse callbacks are enabled\nLiteLLM Lens tracing is disabled\nLiteLLM: config OK\n"
        self.assertNotEqual(self.module.prepared_status(partial), 0)
        self.assertIn("LiteLLM Lens tracing is enabled", self.module.validation_failure_detail(125, partial))


if __name__ == "__main__":
    unittest.main()
