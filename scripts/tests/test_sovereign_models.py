from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "litellm" / "modules"


def load(name: str, path: Path, modules: dict[str, ModuleType] | None = None) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {**(modules or {}), name: module}), patch.object(
        sys, "path", [str(MODULES), *sys.path]
    ):
        spec.loader.exec_module(module)
    return module


def full_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "DATA_SOVEREIGNTY_REGION": "eu",
        "BEDROCK_MODELS": "anthropic.claude-sonnet-5-5, opus=anthropic.claude-opus-5-5",
        "BEDROCK_AWS_REGION": "eu-central-1",
        "BEDROCK_ACCESS_KEY_ID": "AKIA-test",
        "BEDROCK_SECRET_ACCESS_KEY": "bedrock-secret-value",
        "AZURE_OPENAI_DEPLOYMENTS": "gpt-5.5",
        "AZURE_OPENAI_REGION": "swedencentral",
        "AZURE_OPENAI_API_BASE": "https://example.openai.azure.com",
        "AZURE_OPENAI_API_KEY": "azure-secret-value",
        "VERTEX_MODELS": "gemini-3.5-flash",
        "VERTEX_LOCATION": "eu",
        "VERTEX_PROJECT": "example-project",
        "VERTEX_CREDENTIALS_FILE": "vertex.json",
    }
    environment.update(overrides)
    return environment


class SovereignModelListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load("sovereign_models_test", MODULES / "sovereign_models.py")

    def models(self, environment: dict[str, str]) -> dict[str, dict[str, Any]]:
        _, entries = self.module.sovereign_model_list(environment)
        return {entry["model_name"]: entry["litellm_params"] for entry in entries}

    def test_no_provider_models_by_default(self) -> None:
        for region in ("eu", "us"):
            with self.subTest(region=region):
                self.assertEqual(
                    self.module.sovereign_model_list({"DATA_SOVEREIGNTY_REGION": region}), (region, [])
                )

    def test_region_is_required(self) -> None:
        for value in ("", "global", "EU-west"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "DATA_SOVEREIGNTY_REGION"):
                self.module.sovereign_model_list({"DATA_SOVEREIGNTY_REGION": value})

    def test_wires_each_provider_inside_the_eu(self) -> None:
        models = self.models(full_environment())
        self.assertEqual(list(models), ["claude-sonnet-5-5", "opus", "gpt-5.5", "gemini-3.5-flash"])
        self.assertEqual(models["claude-sonnet-5-5"]["model"], "bedrock/converse/eu.anthropic.claude-sonnet-5-5")
        self.assertEqual(models["opus"]["model"], "bedrock/converse/eu.anthropic.claude-opus-5-5")
        self.assertEqual(models["opus"]["aws_region_name"], "eu-central-1")
        self.assertEqual(models["gpt-5.5"]["model"], "azure/gpt-5.5")
        self.assertEqual(models["gpt-5.5"]["api_version"], self.module.DEFAULT_AZURE_API_VERSION)
        self.assertEqual(models["gemini-3.5-flash"]["vertex_location"], "eu")
        self.assertEqual(models["gemini-3.5-flash"]["vertex_credentials"], "/app/secrets/vertex.json")

    def test_uses_the_us_profile_for_the_us_region(self) -> None:
        models = self.models({
            "DATA_SOVEREIGNTY_REGION": "us",
            "BEDROCK_MODELS": "anthropic.claude-haiku-5-5",
            "BEDROCK_AWS_REGION": "us-east-1",
            "BEDROCK_ACCESS_KEY_ID": "a",
            "BEDROCK_SECRET_ACCESS_KEY": "b",
        })
        self.assertEqual(models["claude-haiku-5-5"]["model"], "bedrock/converse/us.anthropic.claude-haiku-5-5")

    def test_generated_entries_reference_secrets_only_by_name(self) -> None:
        text = json.dumps(self.module.sovereign_model_list(full_environment()))
        for secret in ("AKIA-test", "bedrock-secret-value", "azure-secret-value", "example.openai.azure.com"):
            self.assertNotIn(secret, text)
        self.assertIn("os.environ/BEDROCK_SECRET_ACCESS_KEY", text)

    def test_rejects_locations_outside_the_region(self) -> None:
        outside = (
            {"BEDROCK_AWS_REGION": "eu-west-2"},
            {"BEDROCK_AWS_REGION": "eu-central-2"},
            {"BEDROCK_AWS_REGION": "us-east-1"},
            {"AZURE_OPENAI_REGION": "uksouth"},
            {"AZURE_OPENAI_REGION": "switzerlandnorth"},
            {"VERTEX_LOCATION": "global"},
            {"VERTEX_LOCATION": "europe-west2"},
            {"VERTEX_LOCATION": "us"},
        )
        for overrides in outside:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, "outside"):
                self.module.sovereign_model_list(full_environment(**overrides))

    def test_rejects_bedrock_ids_that_choose_their_own_routing(self) -> None:
        for model_id in ("global.anthropic.claude-sonnet-5-5", "us.anthropic.claude-sonnet-5-5"):
            with self.subTest(model_id=model_id), self.assertRaisesRegex(ValueError, "routing prefix"):
                self.module.sovereign_model_list(full_environment(BEDROCK_MODELS=model_id))

    def test_fails_closed_when_a_configured_provider_lacks_a_value(self) -> None:
        for name in ("BEDROCK_AWS_REGION", "BEDROCK_SECRET_ACCESS_KEY", "AZURE_OPENAI_API_KEY",
                     "AZURE_OPENAI_REGION", "VERTEX_PROJECT", "VERTEX_CREDENTIALS_FILE"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                self.module.sovereign_model_list(full_environment(**{name: ""}))

    def test_credentials_without_models_enable_nothing(self) -> None:
        environment = full_environment(BEDROCK_MODELS="", AZURE_OPENAI_DEPLOYMENTS="", VERTEX_MODELS="")
        self.assertEqual(self.module.sovereign_model_list(environment), ("eu", []))

    def test_rejects_invalid_or_conflicting_names(self) -> None:
        cases = (
            ({"VERTEX_CREDENTIALS_FILE": "../vertex.json"}, "not a path"),
            ({"VERTEX_CREDENTIALS_FILE": "/etc/vertex.json"}, "not a path"),
            ({"AZURE_OPENAI_DEPLOYMENTS": "claude-sonnet-5-5"}, "configured twice"),
            ({"AZURE_OPENAI_DEPLOYMENTS": "coding-router=gpt-5.5"}, "reserved"),
            ({"VERTEX_MODELS": "bad name"}, "invalid entry"),
            ({"AZURE_OPENAI_API_BASE": "http://example.openai.azure.com"}, "https"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, message):
                self.module.sovereign_model_list(full_environment(**overrides))

    def router_config(self, **overrides: str) -> dict[str, Any]:
        environment = full_environment(CODING_ROUTER_DEFAULT_MODEL="claude-sonnet-5-5", **overrides)
        return self.models(environment)["coding-router"]["complexity_router_config"]

    def test_coding_router_uses_configured_tier_models(self) -> None:
        models = self.models(full_environment(
            CODING_ROUTER_DEFAULT_MODEL="claude-sonnet-5-5", CODING_ROUTER_REASONING_MODEL="opus",
        ))
        router = models["coding-router"]
        self.assertEqual(router["complexity_router_default_model"], "claude-sonnet-5-5")
        self.assertEqual(router["complexity_router_config"]["tiers"], {
            "SIMPLE": "claude-sonnet-5-5", "MEDIUM": "claude-sonnet-5-5",
            "COMPLEX": "claude-sonnet-5-5", "REASONING": "opus",
        })

    def test_coding_router_defaults(self) -> None:
        config = self.router_config()
        self.assertEqual(config["classifier_type"], "oss_classifier")
        self.assertEqual(config["opensource_classifier_config"], {"deployment_name": "d3-mini", "timeout_ms": 3000})
        self.assertEqual(config["classifier_fallback"], "heuristic")
        self.assertFalse(config["adaptive"] or config["session_affinity"] or config["stall_escalation_enabled"])
        self.assertTrue(config["enable_context_window_escalation"] and config["modality_routing"])
        self.assertNotIn("keyword_tier_rules", config)

    def test_coding_router_tier_pools(self) -> None:
        config = self.router_config(
            CODING_ROUTER_COMPLEX_MODEL="claude-sonnet-5-5, gpt-5.5, gemini-3.5-flash, gpt-5.5",
            CODING_ROUTER_SIMPLE_MODEL="opus",
            CODING_ROUTER_ADAPTIVE="true",
        )
        self.assertEqual(config["tiers"]["COMPLEX"], ["claude-sonnet-5-5", "gpt-5.5", "gemini-3.5-flash"])
        self.assertEqual(config["tiers"]["SIMPLE"], "opus")
        self.assertTrue(config["adaptive"])
        with self.assertRaisesRegex(ValueError, "CODING_ROUTER_COMPLEX_MODEL: 'gpt-4o' is not a configured"):
            self.router_config(CODING_ROUTER_COMPLEX_MODEL="gpt-5.5,gpt-4o")

    def test_coding_router_heuristic_classifier_makes_no_model_call(self) -> None:
        config = self.router_config(CODING_ROUTER_CLASSIFIER="heuristic")
        self.assertEqual(config["classifier_type"], "heuristic")
        self.assertNotIn("opensource_classifier_config", config)
        self.assertEqual(self.router_config(CODING_ROUTER_CLASSIFIER_TIMEOUT_MS="1500")
                         ["opensource_classifier_config"]["timeout_ms"], 1500)

    def test_coding_router_keyword_rules(self) -> None:
        config = self.router_config(
            CODING_ROUTER_REASONING_KEYWORDS="architecture, design a system", CODING_ROUTER_SIMPLE_KEYWORDS="typo",
        )
        self.assertEqual(config["keyword_tier_rules"], [
            {"keywords": ["typo"], "tier": "SIMPLE"},
            {"keywords": ["architecture", "design a system"], "tier": "REASONING"},
        ])

    def test_coding_router_session_affinity_and_stall_escalation(self) -> None:
        self.assertTrue(self.router_config(CODING_ROUTER_SESSION_AFFINITY="true")["session_affinity"])
        self.assertTrue(self.router_config(CODING_ROUTER_STALL_ESCALATION="yes")["stall_escalation_enabled"])
        with self.assertRaisesRegex(ValueError, "cannot both be true"):
            self.router_config(CODING_ROUTER_SESSION_AFFINITY="true", CODING_ROUTER_STALL_ESCALATION="true")

    def test_coding_router_rejects_invalid_settings(self) -> None:
        cases = (
            ({"CODING_ROUTER_CLASSIFIER": "gpt-5.5"}, "d3-mini or heuristic"),
            ({"CODING_ROUTER_CLASSIFIER_TIMEOUT_MS": "50"}, "from 100 to 30000"),
            ({"CODING_ROUTER_CLASSIFIER_TIMEOUT_MS": "-1"}, "from 100 to 30000"),
            ({"CODING_ROUTER_CLASSIFIER": "heuristic", "CODING_ROUTER_CLASSIFIER_TIMEOUT_MS": "2000"}, "applies only"),
            ({"CODING_ROUTER_ADAPTIVE": "maybe"}, "true or false"),
            ({"CODING_ROUTER_REASONING_KEYWORDS": "x" * 101}, "invalid keyword"),
            ({"CODING_ROUTER_SIMPLE_KEYWORDS": ",".join(f"k{i}" for i in range(51))}, "at most 50"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, message):
                self.router_config(**overrides)

    def test_coding_router_rejects_unknown_or_orphan_tiers(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a configured provider model"):
            self.module.sovereign_model_list(full_environment(CODING_ROUTER_DEFAULT_MODEL="gpt-4o"))
        for name in ("CODING_ROUTER_SIMPLE_MODEL", "CODING_ROUTER_REASONING_KEYWORDS", "CODING_ROUTER_ADAPTIVE"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "need CODING_ROUTER_DEFAULT_MODEL"):
                self.module.sovereign_model_list(full_environment(**{name: "true"}))
        self.assertNotIn("coding-router", self.models(full_environment(CODING_ROUTER_ADAPTIVE="false")))


class LauncherWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        yaml_stub = ModuleType("yaml")
        yaml_stub.YAMLError = ValueError
        yaml_stub.safe_load = json.load
        yaml_stub.safe_dump = lambda value, stream, sort_keys=False: json.dump(value, stream)
        cls.launcher = load("launcher_wiring_test", MODULES / "prepare_runtime_config.py", {"yaml": yaml_stub})

    def prepare(self, directory: str, environment: dict[str, str]) -> dict[str, Any]:
        source, destination = Path(directory) / "config.yaml", Path(directory) / "runtime.yaml"
        source.write_text(json.dumps({
            "model_list": [{"model_name": "d3-mini", "litellm_params": {"model": "hosted_vllm/vllm-sr/d3-mini"}}],
            "litellm_settings": {},
        }), encoding="utf-8")
        with (
            patch.object(self.launcher, "langfuse_is_available", return_value=False),
            patch.object(self.launcher, "lens_is_available", return_value=False),
            patch("sys.stdout"),
        ):
            self.launcher.prepare_config(source, destination, environment)
        return json.loads(destination.read_text(encoding="utf-8"))

    def test_appends_provider_models_before_static_models(self) -> None:
        original = self.launcher.sovereign_model_list
        with tempfile.TemporaryDirectory() as directory:
            secrets = Path(directory) / "secrets"
            secrets.mkdir()
            (secrets / "vertex.json").write_text("{}", encoding="utf-8")
            with patch.object(
                self.launcher, "sovereign_model_list",
                new=lambda environ: original(environ, PurePosixPath(str(secrets))),
            ):
                config = self.prepare(directory, full_environment())
        names = [entry["model_name"] for entry in config["model_list"]]
        self.assertEqual(names, ["claude-sonnet-5-5", "opus", "gpt-5.5", "gemini-3.5-flash", "d3-mini"])

    def test_aliases_choose_public_model_names(self) -> None:
        environment = full_environment(
            BEDROCK_MODELS="", VERTEX_MODELS="", AZURE_OPENAI_DEPLOYMENTS="d3=gpt-5.5",
        )
        with tempfile.TemporaryDirectory() as directory:
            config = self.prepare(directory, environment)
        self.assertEqual([entry["model_name"] for entry in config["model_list"]], ["d3", "d3-mini"])

    def test_no_provider_models_keeps_only_static_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.prepare(directory, {"DATA_SOVEREIGNTY_REGION": "eu"})
        self.assertEqual([entry["model_name"] for entry in config["model_list"]], ["d3-mini"])

    def test_fails_closed_when_vertex_credentials_file_is_missing(self) -> None:
        environment = full_environment(BEDROCK_MODELS="", AZURE_OPENAI_DEPLOYMENTS="")
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "credentials file"):
            self.prepare(directory, environment)

    def test_fails_closed_without_a_region(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "DATA_SOVEREIGNTY_REGION"):
            self.prepare(directory, {})


if __name__ == "__main__":
    unittest.main()
