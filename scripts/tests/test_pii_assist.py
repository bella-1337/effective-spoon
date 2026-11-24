"""Tests for the gateway's local-model PII assist (litellm/modules/pii_assist.py)
and the pii-assist model wiring in prepare_runtime_config.py."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "litellm" / "modules"


class FakeCustomGuardrail:
    def __init__(self, **kwargs: Any) -> None:
        self.guardrail_name = kwargs.get("guardrail_name", "pii-assist")
        self.run = True

    def should_run_guardrail(self, data: dict, event_type: Any) -> bool:
        return self.run


class FakePresidio:
    """Stands in for LiteLLM's _OPTIONAL_PresidioPIIMasking."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    async def check_pii(self, text: str, output_parse_pii: bool, presidio_config: Any, request_data: dict) -> str:
        self.seen.append(presidio_config)
        return text

    def _get_presidio_analyze_request_payload(self, text: str, presidio_config: Any, request_data: dict) -> dict:
        return {"text": text, "language": getattr(presidio_config, "language", None) or "en"}


class PerRequestConfig:
    def __init__(self, language: str | None = None, entities: Any = None) -> None:
        self.language = language


def load_assist(presidio_class: type = FakePresidio) -> tuple[ModuleType, Any]:
    proxy_server = ModuleType("litellm.proxy.proxy_server")
    proxy_server.llm_router = None
    presidio_module = ModuleType("litellm.proxy.guardrails.guardrail_hooks.presidio")
    presidio_module._OPTIONAL_PresidioPIIMasking = presidio_class
    logging_module = ModuleType("litellm._logging")
    logging_module.verbose_proxy_logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    custom = ModuleType("litellm.integrations.custom_guardrail")
    custom.CustomGuardrail = FakeCustomGuardrail
    types_module = ModuleType("litellm.types.guardrails")
    types_module.GuardrailEventHooks = SimpleNamespace(pre_call="pre_call")
    types_module.PresidioPerRequestConfig = PerRequestConfig
    proxy = ModuleType("litellm.proxy")
    proxy.proxy_server = proxy_server
    guardrail_hooks = ModuleType("litellm.proxy.guardrails.guardrail_hooks")
    guardrail_hooks.presidio = presidio_module
    stubs = {
        "litellm": ModuleType("litellm"),
        "litellm._logging": logging_module,
        "litellm.integrations": ModuleType("litellm.integrations"),
        "litellm.integrations.custom_guardrail": custom,
        "litellm.types": ModuleType("litellm.types"),
        "litellm.types.guardrails": types_module,
        "litellm.proxy": proxy,
        "litellm.proxy.proxy_server": proxy_server,
        "litellm.proxy.guardrails": ModuleType("litellm.proxy.guardrails"),
        "litellm.proxy.guardrails.guardrail_hooks": guardrail_hooks,
        "litellm.proxy.guardrails.guardrail_hooks.presidio": presidio_module,
    }
    spec = importlib.util.spec_from_file_location("pii_assist_test", MODULES / "pii_assist.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    patcher = patch.dict(sys.modules, stubs)
    patcher.start()
    spec.loader.exec_module(module)
    return module, (patcher, proxy_server)


class FakeRouter:
    def __init__(self, answers: dict[str, dict[str, Any]] | None = None, fail: BaseException | None = None) -> None:
        self.answers = answers or {}
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def get_model_names(self) -> list[str]:
        return ["pii-assist", "echo"]

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail is not None:
            raise self.fail
        text = kwargs["messages"][-1]["content"]
        answer = next((value for key, value in self.answers.items() if key in text), {"language": "en", "items": []})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))])


GERMAN = "Bitte rufen Sie Brunhilde Kesselmeyer an, Steuer-ID 12 345 678 901, bei der Müller GmbH."
GERMAN_ANSWER = {
    "language": "de",
    "items": [
        {"text": "Brunhilde Kesselmeyer", "type": "PERSON"},
        {"text": "12 345 678 901", "type": "ID_NUMBER"},
        {"text": "Müller GmbH", "type": "ORGANIZATION"},
        {"text": "invented by the model", "type": "PERSON"},
        {"text": "x", "type": "PERSON"},
        {"text": "Bitte", "type": "NOT_A_TYPE"},
    ],
}


class AssistHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module, (self.patcher, self.proxy_server) = load_assist()
        self.addCleanup(self.patcher.stop)
        self.guardrail = self.module.PiiAssistGuardrail(guardrail_name="pii-assist")

    def run_hook(self, data: dict) -> dict:
        return asyncio.run(self.guardrail.async_pre_call_hook(None, None, data, "completion"))

    def test_sets_language_and_masks_found_items_except_organisations(self) -> None:
        router = FakeRouter({"Kesselmeyer": GERMAN_ANSWER})
        self.proxy_server.llm_router = router
        data = self.run_hook({"model": "echo", "messages": [{"role": "user", "content": GERMAN}]})
        self.assertEqual(data["metadata"]["guardrail_config"], {"language": "de"})
        self.assertEqual(
            data["messages"][0]["content"],
            "Bitte rufen Sie <PERSON> an, Steuer-ID <ID_NUMBER>, bei der Müller GmbH.",
        )
        call = router.calls[0]
        self.assertEqual(call["model"], "pii-assist")
        # LiteLLM forwards reasoning_effort to vLLM unchanged; vLLM may reject "none".
        self.assertNotIn("reasoning_effort", call)
        self.assertEqual(call["extra_body"], {"chat_template_kwargs": {"enable_thinking": False}})
        self.assertEqual(call["response_format"]["type"], "json_schema")

    def test_text_parts_and_roles(self) -> None:
        self.proxy_server.llm_router = FakeRouter({"Kesselmeyer": GERMAN_ANSWER})
        data = self.run_hook(
            {
                "model": "echo",
                "messages": [
                    {"role": "system", "content": "Brunhilde Kesselmeyer is the user."},
                    {"role": "assistant", "content": "Hello Brunhilde Kesselmeyer"},
                    {"role": "user", "content": [{"type": "text", "text": GERMAN}, {"type": "image_url", "image_url": {}}]},
                ],
            }
        )
        self.assertEqual(data["messages"][0]["content"], "<PERSON> is the user.")
        # Earlier model output is not re-masked by this hook (Presidio still checks it).
        self.assertEqual(data["messages"][1]["content"], "Hello Brunhilde Kesselmeyer")
        self.assertTrue(data["messages"][2]["content"][0]["text"].startswith("Bitte rufen Sie <PERSON>"))

    def test_explicit_language_wins(self) -> None:
        self.proxy_server.llm_router = FakeRouter({"Kesselmeyer": GERMAN_ANSWER})
        data = self.run_hook(
            {"model": "echo", "messages": [{"role": "user", "content": GERMAN}], "metadata": {"guardrail_config": {"language": "fr"}}}
        )
        self.assertEqual(data["metadata"]["guardrail_config"]["language"], "fr")

    def test_cache_avoids_repeat_calls(self) -> None:
        router = FakeRouter({"Kesselmeyer": GERMAN_ANSWER})
        self.proxy_server.llm_router = router
        for _ in range(3):
            self.run_hook({"model": "echo", "messages": [{"role": "user", "content": GERMAN}]})
        self.assertEqual(len(router.calls), 1)

    def test_call_budget(self) -> None:
        router = FakeRouter()
        self.proxy_server.llm_router = router
        self.guardrail.chunk_chars = 500
        self.guardrail.max_calls = 2
        self.run_hook({"model": "echo", "messages": [{"role": "user", "content": "word " * 1000}]})
        self.assertEqual(len(router.calls), 2)

    def test_fails_open_when_the_model_fails(self) -> None:
        for failure in (TimeoutError(), ConnectionError(), ValueError("bad json")):
            with self.subTest(failure=type(failure).__name__):
                self.proxy_server.llm_router = FakeRouter(fail=failure)
                self.guardrail._cache.clear()
                data = self.run_hook({"model": "echo", "messages": [{"role": "user", "content": GERMAN}]})
                self.assertEqual(data["messages"][0]["content"], GERMAN)
                self.assertNotIn("guardrail_config", data.get("metadata", {}))

    def test_skips_without_model_for_the_assist_model_or_when_disabled(self) -> None:
        self.proxy_server.llm_router = None
        data = self.run_hook({"model": "echo", "messages": [{"role": "user", "content": GERMAN}]})
        self.assertEqual(data["messages"][0]["content"], GERMAN)
        router = FakeRouter({"Kesselmeyer": GERMAN_ANSWER})
        self.proxy_server.llm_router = router
        self.run_hook({"model": "pii-assist", "messages": [{"role": "user", "content": GERMAN}]})
        self.guardrail.run = False
        self.run_hook({"model": "echo", "messages": [{"role": "user", "content": GERMAN}]})
        self.assertEqual(router.calls, [])

    def test_parse_answer_accepts_only_verbatim_items(self) -> None:
        language, items = self.module.parse_answer(json.dumps(GERMAN_ANSWER), GERMAN)
        self.assertEqual(language, "de")
        self.assertEqual(
            items,
            [("Brunhilde Kesselmeyer", "PERSON"), ("12 345 678 901", "ID_NUMBER"), ("Müller GmbH", "ORGANIZATION")],
        )
        self.assertEqual(self.module.parse_answer("not json", GERMAN), (None, []))
        self.assertEqual(self.module.parse_answer(json.dumps({"language": "es", "items": "x"}), GERMAN), (None, []))

    def test_masking_never_replaces_inside_a_longer_word(self) -> None:
        compiled = self.module.masking_pattern([("Ann", "PERSON"), ("Ann Lee", "PERSON")], frozenset())
        self.assertEqual(self.module.mask("Ann Lee met Anna and Ann.", compiled), "<PERSON> met Anna and <PERSON>.")
        self.assertIsNone(self.module.masking_pattern([("Acme", "ORGANIZATION")], self.module.GATEWAY_IGNORED_TYPES))
        placeholder = self.module.masking_pattern([("1 Main St", "ADDRESS"), ("1 May 1990", "DATE_OF_BIRTH")], frozenset())
        self.assertEqual(self.module.mask("1 Main St, born 1 May 1990", placeholder), "<LOCATION>, born <DATE_TIME>")

    def test_address_remnants_are_folded_into_placeholders(self) -> None:
        compiled = self.module.masking_pattern(
            [("3, place de la Comédie", "ADDRESS"), ("Montpellier", "LOCATION"), ("Lindenstraße", "ADDRESS"), ("München", "LOCATION")],
            frozenset(),
        )
        self.assertEqual(
            self.module.mask("au 3, place de la Comédie\n34000 Montpellier. Le 5 mai, 75000 personnes.", compiled),
            "au <LOCATION>\n<LOCATION>. Le 5 mai, 75000 personnes.",
        )
        self.assertEqual(
            self.module.mask("in der Lindenstraße 12, 80331 München hinterlegt", compiled),
            "in der <LOCATION>, <LOCATION> hinterlegt",
        )
        # A number that is not part of the address stays.
        self.assertEqual(self.module.mask("in München 12 Tage, ab 12345 Euro", compiled), "in <LOCATION> 12 Tage, ab 12345 Euro")

    def test_chunks_cover_the_text(self) -> None:
        text = "alpha beta " * 300
        chunks = self.module.chunk_text(text, 500)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))


class PresidioShimTests(unittest.TestCase):
    def test_uses_the_request_language_and_allows_placeholders(self) -> None:
        class Presidio(FakePresidio):
            pass

        module, (patcher, _proxy) = load_assist(Presidio)
        self.addCleanup(patcher.stop)
        self.assertTrue(module.install_presidio_language_shim())
        self.assertTrue(module.install_presidio_language_shim())  # idempotent
        presidio = Presidio()
        request = {"metadata": {"guardrail_config": {"language": "de"}}}
        asyncio.run(presidio.check_pii(text="x", output_parse_pii=False, presidio_config=None, request_data=request))
        self.assertEqual(presidio.seen[-1].language, "de")
        explicit = PerRequestConfig("fr")
        asyncio.run(presidio.check_pii(text="x", output_parse_pii=False, presidio_config=explicit, request_data=request))
        self.assertIs(presidio.seen[-1], explicit)
        for unsupported in ({"metadata": {"guardrail_config": {"language": "xx"}}}, {}, {"metadata": None}):
            asyncio.run(presidio.check_pii(text="x", output_parse_pii=False, presidio_config=None, request_data=unsupported))
            self.assertIsNone(presidio.seen[-1])
        payload = presidio._get_presidio_analyze_request_payload("x", None, {})
        self.assertEqual(payload["allow_list_match"], "regex")
        import re

        pattern = re.compile(payload["allow_list"][0], re.IGNORECASE)
        for allowed in ("LOCATION", "<LOCATION>", "ID_NUMBER", "DATE_TIME>.", "<<PERSON>>"):
            self.assertTrue(pattern.match(allowed), allowed)
        for kept in ("Location", "person", "SMITH", "Brunhilde Kesselmeyer", "LOCATION Berlin"):
            self.assertFalse(pattern.match(kept), kept)

    def test_not_installed_when_litellm_changes(self) -> None:
        class Changed:
            async def check_pii(self, text: str, config: Any) -> str:
                return text

        module, (patcher, _proxy) = load_assist(Changed)
        self.addCleanup(patcher.stop)
        self.assertFalse(module.install_presidio_language_shim())
        self.assertFalse(getattr(Changed.check_pii, "_stack_language_shim", False))


class PromptFileTests(unittest.TestCase):
    def test_prompt_file_matches_the_analyzer_languages(self) -> None:
        prompt = json.loads((MODULES / "pii_assist_prompt.json").read_text(encoding="utf-8"))
        self.assertEqual(prompt["languages"], ["en", "de", "fr"])
        engine = (ROOT / "presidio" / "analyzer-engine.yaml").read_text(encoding="utf-8")
        for language in prompt["languages"]:
            self.assertIn(f"  - {language}\n", engine)
        self.assertIn("ORGANIZATION", prompt["item_types"])


class RuntimeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_prepare_runtime_config import load_module

        cls.module = load_module()

    def test_not_added_without_api_base(self) -> None:
        config: dict[str, Any] = {"model_list": []}
        self.assertIsNone(self.module.with_pii_assist(config, {}))
        self.assertEqual(config["model_list"], [])

    KEY = "k" * 64

    def test_adds_local_vllm_model(self) -> None:
        config: dict[str, Any] = {"model_list": [{"model_name": "d3-mini"}]}
        environ = {"PII_ASSIST_API_BASE": "http://host.docker.internal:8000/", "PII_ASSIST_API_KEY": self.KEY}
        model = self.module.with_pii_assist(config, environ)
        self.assertEqual(model, "gemma4")
        entry = config["model_list"][-1]
        self.assertEqual(entry["model_name"], "pii-assist")
        self.assertEqual(entry["litellm_params"]["model"], "hosted_vllm/gemma4")
        self.assertEqual(entry["litellm_params"]["api_base"], "http://host.docker.internal:8000/v1")
        # The key stays in the environment, not in the written runtime config.
        self.assertEqual(entry["litellm_params"]["api_key"], "os.environ/PII_ASSIST_API_KEY")
        self.assertNotIn(self.KEY, repr(config))

    def test_served_model_name_or_repository_id(self) -> None:
        base = {"PII_ASSIST_API_BASE": "http://vllm:8000", "PII_ASSIST_API_KEY": self.KEY}
        for name in ("gemma4", "pii-model_2", "google/some-model-it"):
            with self.subTest(name=name):
                config: dict[str, Any] = {"model_list": []}
                self.assertEqual(self.module.with_pii_assist(config, {**base, "PII_ASSIST_VLLM_MODEL": name}), name)
                self.assertEqual(config["model_list"][0]["litellm_params"]["model"], f"hosted_vllm/{name}")

    def test_accepts_only_local_addresses(self) -> None:
        for base, expected in (
            ("http://host.docker.internal:8000", "http://host.docker.internal:8000/v1"),
            ("http://vllm:8000/v1", "http://vllm:8000/v1"),
            ("http://vllm:8000/v1/", "http://vllm:8000/v1"),
            ("https://10.0.0.5", "https://10.0.0.5/v1"),
            ("http://192.168.1.20:8000/", "http://192.168.1.20:8000/v1"),
        ):
            with self.subTest(base=base):
                self.assertEqual(self.module.validate_local_api_base(base), expected)
        for base in (
            "https://api.openai.com",
            "http://vllm.example.com:8000",
            "http://8.8.8.8:8000",
            "http://169.254.169.254",
            "http://host.docker.internal:8000/v2",
            "http://host.docker.internal:8000/v1/chat/completions",
            "http://user:pass@vllm:8000",
            "ftp://vllm",
        ):
            with self.subTest(base=base), self.assertRaises(ValueError):
                self.module.validate_local_api_base(base)

    def test_requires_an_api_key(self) -> None:
        base = {"PII_ASSIST_API_BASE": "http://vllm:8000"}
        for key in (None, "", "short", "k" * 31, "replace_with_" + "k" * 32, "k" * 40 + " x", "k" * 513):
            environ = base if key is None else {**base, "PII_ASSIST_API_KEY": key}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.module.with_pii_assist({"model_list": []}, environ)

    def test_rejects_bad_model_names_and_name_clashes(self) -> None:
        base = {"PII_ASSIST_API_BASE": "http://vllm:8000", "PII_ASSIST_API_KEY": self.KEY}
        for name in ("gemma; rm -rf", "a/b/c", "../gemma", "gemma4:latest"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.module.with_pii_assist({"model_list": []}, {**base, "PII_ASSIST_VLLM_MODEL": name})
        with self.assertRaises(ValueError):
            self.module.with_pii_assist({"model_list": [{"model_name": "pii-assist"}]}, base)


class PolicyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import maintenance_contracts

        self.contracts = maintenance_contracts
        self.source = (MODULES / "policies" / "eu-data-protection.yaml").read_text(encoding="utf-8")

    def test_repository_policy_passes(self) -> None:
        self.assertEqual(self.contracts.data_protection_policy_failures(self.source), [])

    def test_rejects_other_attachments_order_or_scope(self) -> None:
        for changed in (
            self.source + "  - policy: eu-data-protection\n    teams: [research]\n",
            self.source.replace("        - pii-assist\n        - presidio-pii", "        - presidio-pii\n        - pii-assist"),
            self.source.replace("    default: true\n", ""),
            self.source.replace('models: ["pii-assist"]', 'models: ["gpt-4o"]'),
            self.source.replace("      add: []", "      add: [presidio-pii]"),
        ):
            with self.subTest(changed=changed[-80:]):
                self.assertEqual(len(self.contracts.data_protection_policy_failures(changed)), 1)


if __name__ == "__main__":
    unittest.main()
