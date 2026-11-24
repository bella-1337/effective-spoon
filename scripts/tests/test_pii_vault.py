"""Tests for reversible PII masking (litellm/modules/pii_vault.py).

The gateway image has the `cryptography` package; the host running these tests
may not. Without it, a stand-in authenticated cipher replaces AES-GCM so the
token, vault, and hook logic is still tested; RealCipherTests runs only where
`cryptography` is installed (the gateway image).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "litellm" / "modules"

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _REAL_AESGCM  # noqa: F401

    HAVE_CRYPTOGRAPHY = True
except ImportError:
    HAVE_CRYPTOGRAPHY = False


class StandInAESGCM:
    """Authenticated stand-in for AESGCM: XOR keystream plus an HMAC tag."""

    def __init__(self, key: bytes) -> None:
        self.key = key

    @staticmethod
    def generate_key(bit_length: int) -> bytes:
        return os.urandom(bit_length // 8)

    def _stream(self, nonce: bytes, size: int) -> bytes:
        out = b""
        counter = 0
        while len(out) < size:
            out += hashlib.sha256(self.key + nonce + counter.to_bytes(4, "big")).digest()
            counter += 1
        return out[:size]

    def encrypt(self, nonce: bytes, data: bytes, aad: bytes) -> bytes:
        body = bytes(a ^ b for a, b in zip(data, self._stream(nonce, len(data))))
        return body + hmac.new(self.key, nonce + aad + body, hashlib.sha256).digest()[:16]

    def decrypt(self, nonce: bytes, data: bytes, aad: bytes) -> bytes:
        body, tag = data[:-16], data[-16:]
        if not hmac.compare_digest(tag, hmac.new(self.key, nonce + aad + body, hashlib.sha256).digest()[:16]):
            raise ValueError("authentication failed")
        return bytes(a ^ b for a, b in zip(body, self._stream(nonce, len(body))))


class FakeCustomGuardrail:
    def __init__(self, **kwargs: Any) -> None:
        self.guardrail_name = kwargs.get("guardrail_name")


class FakePresidio:
    """Stands in for LiteLLM's _OPTIONAL_PresidioPIIMasking."""

    def __init__(self) -> None:
        self.output_masks: dict[str, str] = {}

    async def anonymize_text(
        self, text: str, analyze_results: Any, output_parse_pii: bool, masked_entity_count: dict, request_data: Any = None
    ) -> str:
        # Presidio's default: <TYPE> placeholders.
        for item in sorted(analyze_results, key=lambda r: r["start"], reverse=True):
            text = text[: item["start"]] + f"<{item['entity_type']}>" + text[item["end"] :]
        return text

    async def apply_guardrail(self, inputs: dict, request_data: dict, input_type: str, logging_obj: Any = None) -> dict:
        texts = []
        for text in inputs["texts"]:
            results = [
                {"start": text.index(value), "end": text.index(value) + len(value), "entity_type": entity}
                for value, entity in self.output_masks.items()
                if value in text
            ]
            texts.append(await self.anonymize_text(text, results, False, {}, request_data))
        inputs["texts"] = texts
        return inputs

    async def async_pre_call_hook(self, user_api_key_dict: Any, cache: Any, data: dict, call_type: Any) -> dict:
        return data


def load_vault(presidio_class: type = FakePresidio, real_cipher: bool = False) -> tuple[ModuleType, Any]:
    presidio_module = ModuleType("litellm.proxy.guardrails.guardrail_hooks.presidio")
    presidio_module._OPTIONAL_PresidioPIIMasking = presidio_class
    logging_module = ModuleType("litellm._logging")
    logging_module.verbose_proxy_logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    guardrail_hooks = ModuleType("litellm.proxy.guardrails.guardrail_hooks")
    guardrail_hooks.presidio = presidio_module
    custom = ModuleType("litellm.integrations.custom_guardrail")
    custom.CustomGuardrail = FakeCustomGuardrail
    stubs: dict[str, ModuleType] = {
        "litellm": ModuleType("litellm"),
        "litellm._logging": logging_module,
        "litellm.integrations": ModuleType("litellm.integrations"),
        "litellm.integrations.custom_guardrail": custom,
        "litellm.proxy": ModuleType("litellm.proxy"),
        "litellm.proxy.guardrails": ModuleType("litellm.proxy.guardrails"),
        "litellm.proxy.guardrails.guardrail_hooks": guardrail_hooks,
        "litellm.proxy.guardrails.guardrail_hooks.presidio": presidio_module,
    }
    # A separate process: its own keys and request-side marker.
    sys.modules.pop("litellm_stack_pii_vault_state", None)
    if not real_cipher:
        aead = ModuleType("cryptography.hazmat.primitives.ciphers.aead")
        aead.AESGCM = StandInAESGCM
        for name in ("cryptography", "cryptography.hazmat", "cryptography.hazmat.primitives",
                     "cryptography.hazmat.primitives.ciphers"):
            stubs[name] = ModuleType(name)
        stubs["cryptography.hazmat.primitives.ciphers.aead"] = aead
    spec = importlib.util.spec_from_file_location("pii_vault_test", MODULES / "pii_vault.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    patcher = patch.dict(sys.modules, stubs)
    patcher.start()
    spec.loader.exec_module(module)
    return module, patcher


TEXT = "Ann Lee wrote to Bob Stone. Ann Lee lives in Berlin."


def results_for(text: str, values: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for value, entity in values.items():
        start = 0
        while (start := text.find(value, start)) != -1:
            out.append({"start": start, "end": start + len(value), "entity_type": entity, "score": 0.85})
            start += len(value)
    return out


class VaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module, patcher = load_vault()
        self.addCleanup(patcher.stop)
        self.request: dict[str, Any] = {"metadata": {}}
        self.vault = self.module.Vault.for_request(self.request, create=True)

    def test_same_value_same_token_numbered_per_type(self) -> None:
        self.assertEqual(self.vault.token_for("Ann Lee", "PERSON"), "<PERSON_1>")
        self.assertEqual(self.vault.token_for("Bob Stone", "PERSON"), "<PERSON_2>")
        self.assertEqual(self.vault.token_for("Ann Lee", "PERSON"), "<PERSON_1>")
        self.assertEqual(self.vault.token_for("Berlin", "LOCATION"), "<LOCATION_1>")
        # The same text as another type is a different value.
        self.assertEqual(self.vault.token_for("Berlin", "PERSON"), "<PERSON_3>")
        self.assertEqual(self.vault.token_for("x", "weird type!"), "<WEIRD_TYPE__1>")

    def test_vault_holds_no_readable_values(self) -> None:
        for value, entity in (("Ann Lee", "PERSON"), ("ann@example.com", "EMAIL_ADDRESS"), ("Berlin", "LOCATION")):
            self.vault.token_for(value, entity)
        stored = json.dumps(self.request)
        for value in ("Ann Lee", "ann@example.com", "Berlin", "Ann", "example"):
            self.assertNotIn(value, stored)
        self.assertEqual(set(self.request["metadata"]["pii_vault"]["tokens"]), {"<PERSON_1>", "<EMAIL_ADDRESS_1>", "<LOCATION_1>"})

    def test_restore_known_tokens_only(self) -> None:
        self.vault.token_for("Ann Lee", "PERSON")
        self.vault.token_for("Berlin", "LOCATION")
        text, count = self.vault.restore("<PERSON_1> is in <LOCATION_1>; <PERSON_7> and <PERSON> stay.")
        self.assertEqual(text, "Ann Lee is in Berlin; <PERSON_7> and <PERSON> stay.")
        self.assertEqual(count, 2)

    def test_tampered_or_moved_entries_are_not_restored(self) -> None:
        self.vault.token_for("Ann Lee", "PERSON")
        self.vault.token_for("Bob Stone", "PERSON")
        tokens = self.request["metadata"]["pii_vault"]["tokens"]
        tokens["<PERSON_1>"], tokens["<PERSON_2>"] = tokens["<PERSON_2>"], tokens["<PERSON_1>"]
        self.assertEqual(self.vault.restore("<PERSON_1> <PERSON_2>")[0], "<PERSON_1> <PERSON_2>")
        tokens["<PERSON_1>"] = "not base64 !"
        self.assertIsNone(self.vault.original("<PERSON_1>"))

    def test_another_process_cannot_decrypt(self) -> None:
        self.vault.token_for("Ann Lee", "PERSON")
        other, patcher = load_vault()
        self.addCleanup(patcher.stop)
        copy = json.loads(json.dumps(self.request))
        self.assertIsNone(other.Vault.for_request(copy, create=False).original("<PERSON_1>"))

    def test_token_budget(self) -> None:
        with patch.object(self.module, "MAX_TOKENS_PER_REQUEST", 2):
            self.assertEqual(self.vault.token_for("a1", "PERSON"), "<PERSON_1>")
            self.assertEqual(self.vault.token_for("a2", "PERSON"), "<PERSON_2>")
            self.assertEqual(self.vault.token_for("a3", "PERSON"), "<PERSON>")

    def test_no_vault_without_metadata_when_not_creating(self) -> None:
        self.assertIsNone(self.module.Vault.for_request({}, create=False))
        self.assertIsNone(self.module.Vault.for_request(None, create=True))


class ShimTests(unittest.TestCase):
    def load(self, presidio_class: type = FakePresidio, enabled: str = "true") -> Any:
        env = patch.dict(os.environ, {"PII_REVERSIBLE_MASKING": enabled})
        env.start()
        self.addCleanup(env.stop)
        self.module, patcher = load_vault(presidio_class)
        self.addCleanup(patcher.stop)
        return self.module.install_reversible_masking_shim()

    def test_not_installed_when_disabled(self) -> None:
        class Presidio(FakePresidio):
            pass

        self.assertFalse(self.load(Presidio, enabled="false"))
        self.assertFalse(getattr(Presidio.anonymize_text, "_stack_pii_vault", False))

    def test_not_installed_when_litellm_changed(self) -> None:
        class Changed(FakePresidio):
            async def anonymize_text(self, text: str) -> str:  # type: ignore[override]
                return text

        self.assertFalse(self.load(Changed))
        self.assertFalse(getattr(Changed.anonymize_text, "_stack_pii_vault", False))

    def test_request_side_is_tokenised_through_apply_guardrail(self) -> None:
        class Presidio(FakePresidio):
            async def apply_guardrail(self, inputs: dict, request_data: dict, input_type: str, logging_obj: Any = None) -> dict:
                texts = []
                for text in inputs["texts"]:
                    results = results_for(text, {"Ann Lee": "PERSON", "Lee": "LOCATION", "Bob Stone": "PERSON", "Berlin": "LOCATION"})
                    texts.append(await self.anonymize_text(text, results, False, {}, request_data))
                inputs["texts"] = texts
                return inputs

        self.assertTrue(self.load(Presidio))
        self.assertTrue(self.load(Presidio))  # idempotent
        request: dict[str, Any] = {"metadata": {}}
        out = asyncio.run(Presidio().apply_guardrail({"texts": [TEXT, "Ann Lee, Berlin"]}, request, "request"))
        # "Lee" inside "Ann Lee" overlaps; the longer span wins. Values keep
        # their token across texts of the same request.
        self.assertEqual(
            out["texts"],
            ["<PERSON_1> wrote to <PERSON_2>. <PERSON_1> lives in <LOCATION_1>.", "<PERSON_1>, <LOCATION_1>"],
        )

    def test_pre_call_hook_marks_the_request_side(self) -> None:
        class Presidio(FakePresidio):
            async def async_pre_call_hook(self, user_api_key_dict: Any, cache: Any, data: dict, call_type: Any) -> dict:
                text = data["messages"][0]["content"]
                data["messages"][0]["content"] = await self.anonymize_text(
                    text, results_for(text, {"Ann Lee": "PERSON"}), False, {}, data
                )
                return data

        self.assertTrue(self.load(Presidio))
        data = {"messages": [{"role": "user", "content": "Hi Ann Lee"}], "metadata": {}}
        asyncio.run(Presidio().async_pre_call_hook(None, None, data, "completion"))
        self.assertEqual(data["messages"][0]["content"], "Hi <PERSON_1>")

    def test_response_and_unmarked_masking_keep_plain_placeholders(self) -> None:
        class Presidio(FakePresidio):
            pass

        self.assertTrue(self.load(Presidio))
        presidio = Presidio()
        presidio.output_masks = {"Carol King": "PERSON"}
        request: dict[str, Any] = {"metadata": {}}
        out = asyncio.run(presidio.apply_guardrail({"texts": ["Carol King"]}, request, "response"))
        self.assertEqual(out["texts"], ["<PERSON>"])
        # Output masking outside apply_guardrail (LiteLLM's output callback).
        masked = asyncio.run(presidio.anonymize_text("Carol King", results_for("Carol King", {"Carol King": "PERSON"}), False, {}, request))
        self.assertEqual(masked, "<PERSON>")
        self.assertNotIn("pii_vault", request["metadata"])

    def test_disabled_shim_leaves_presidio_unchanged(self) -> None:
        class Presidio(FakePresidio):
            pass

        self.load(Presidio, enabled="")
        request: dict[str, Any] = {"metadata": {}}
        out = asyncio.run(Presidio().apply_guardrail({"texts": [TEXT]}, request, "request"))
        self.assertEqual(out["texts"], [TEXT])
        self.assertNotIn("pii_vault", request["metadata"])


def chat_response(*contents: str, arguments: str | None = None) -> Any:
    choices = []
    for index, content in enumerate(contents):
        calls = [SimpleNamespace(function=SimpleNamespace(arguments=arguments))] if arguments else None
        choices.append(SimpleNamespace(index=index, message=SimpleNamespace(content=content, tool_calls=calls)))
    return SimpleNamespace(choices=choices)


def stream_chunk(content: str | None, finish: str | None = None, index: int = 0) -> Any:
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(index=index, delta=delta, finish_reason=finish)])


class RestoreGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {"PII_REVERSIBLE_MASKING": "true"})
        env.start()
        self.addCleanup(env.stop)
        # A fresh class: installing the shim must not change the shared fake.
        self.module, patcher = load_vault(type("Presidio", (FakePresidio,), {}))
        self.addCleanup(patcher.stop)
        self.guardrail = self.module.PiiRestoreGuardrail(guardrail_name="pii-restore")
        self.request: dict[str, Any] = {"metadata": {}}
        vault = self.module.Vault.for_request(self.request, create=True)
        vault.token_for("Ann Lee", "PERSON")
        vault.token_for("ann@example.com", "EMAIL_ADDRESS")

    def test_restores_message_text_and_tool_arguments(self) -> None:
        response = chat_response(
            "Dear <PERSON_1>, <PERSON> wrote. <PERSON_9> is unknown.",
            arguments='{"to": "<EMAIL_ADDRESS_1>", "name": "<PERSON_1>"}',
        )
        asyncio.run(self.guardrail.async_post_call_success_hook(self.request, None, response))
        message = response.choices[0].message
        self.assertEqual(message.content, "Dear Ann Lee, <PERSON> wrote. <PERSON_9> is unknown.")
        self.assertEqual(message.tool_calls[0].function.arguments, '{"to": "ann@example.com", "name": "Ann Lee"}')

    def test_nothing_happens_without_a_vault(self) -> None:
        response = chat_response("Dear <PERSON_1>")
        asyncio.run(self.guardrail.async_post_call_success_hook({"metadata": {}}, None, response))
        self.assertEqual(response.choices[0].message.content, "Dear <PERSON_1>")

    def stream(self, chunks: list[Any], request: dict[str, Any] | None = None) -> list[Any]:
        async def source() -> Any:
            for chunk in chunks:
                yield chunk

        async def collect() -> list[Any]:
            return [c async for c in self.guardrail.async_post_call_streaming_iterator_hook(None, source(), request or self.request)]

        return asyncio.run(collect())

    def text(self, chunks: list[Any]) -> str:
        return "".join(c.choices[0].delta.content or "" for c in chunks)

    def test_stream_tokens_split_across_chunks(self) -> None:
        pieces = ["Dear <PER", "SON_", "1>, mail <EMA", "IL_ADDRESS_1> or <PERSON_9", "> now <", "b>bold</b>"]
        out = self.stream([stream_chunk(p) for p in pieces] + [stream_chunk(None, "stop")])
        self.assertEqual(self.text(out), "Dear Ann Lee, mail ann@example.com or <PERSON_9> now <b>bold</b>")
        self.assertEqual(len(out), len(pieces) + 1)

    def test_stream_held_back_text_is_sent_when_the_stream_ends_without_finish(self) -> None:
        out = self.stream([stream_chunk("Hi <PERSON_1"), stream_chunk("> and <PERS")])
        self.assertEqual(self.text(out), "Hi Ann Lee and <PERS")

    def test_stream_without_vault_passes_through(self) -> None:
        chunks = [stream_chunk("Hi <PER"), stream_chunk("SON_1>")]
        out = self.stream(chunks, {"metadata": {}})
        self.assertEqual(self.text(out), "Hi <PERSON_1>")


class AssistVaultTests(unittest.TestCase):
    """pii-assist's own masking uses the same request vault."""

    def setUp(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_pii_assist import load_assist

        env = patch.dict(os.environ, {"PII_REVERSIBLE_MASKING": "true"})
        env.start()
        self.addCleanup(env.stop)
        vault_module, vault_patcher = load_vault()
        self.addCleanup(vault_patcher.stop)
        # pii_assist loads pii_vault under this name; reuse the stand-in cipher.
        names = patch.dict(sys.modules, {"litellm_stack_pii_vault": vault_module})
        names.start()
        self.addCleanup(names.stop)
        self.assist, (patcher, _proxy) = load_assist()
        self.addCleanup(patcher.stop)
        self.vault_module = vault_module

    def test_items_and_folded_address_parts_become_restorable_tokens(self) -> None:
        request: dict[str, Any] = {"metadata": {}}
        vault = self.vault_module.Vault.for_request(request, create=True)
        compiled = self.assist.masking_pattern(
            [("Anna Müller", "PERSON"), ("Lindenstraße", "ADDRESS"), ("München", "LOCATION")], frozenset()
        )
        masked = self.assist.mask("Anna Müller, Lindenstraße 12, 80331 München. Anna Müller zahlt.", compiled, vault)
        # Lindenstraße and München are tokens 1 and 2; folding the postcode and
        # house number into them makes "80331 München" token 3 and
        # "Lindenstraße 12" token 4.
        self.assertEqual(masked, "<PERSON_1>, <LOCATION_4>, <LOCATION_3>. <PERSON_1> zahlt.")
        self.assertEqual(vault.original("<LOCATION_3>"), "80331 München")
        self.assertEqual(vault.original("<LOCATION_4>"), "Lindenstraße 12")
        restored, _ = vault.restore(masked)
        self.assertEqual(restored, "Anna Müller, Lindenstraße 12, 80331 München. Anna Müller zahlt.")

    def test_numbered_tokens_pass_the_placeholder_allow_list(self) -> None:
        import re

        pattern = re.compile(self.assist.PLACEHOLDER_ALLOW_PATTERN, re.IGNORECASE)
        for text in ("<PERSON_1>", "PERSON_12", "<LOCATION>", "<EMAIL_ADDRESS_3>"):
            self.assertRegex(text, pattern)
        for text in ("person_1", "<PERSON_0>", "<PERSON_1> Smith", "PERSON_123456"):
            self.assertNotRegex(text, pattern)


@unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography is installed in the gateway image, not on this host")
class RealCipherTests(unittest.TestCase):
    def test_aes_gcm_round_trip_and_authentication(self) -> None:
        module, patcher = load_vault(real_cipher=True)
        self.addCleanup(patcher.stop)
        request: dict[str, Any] = {"metadata": {}}
        vault = module.Vault.for_request(request, create=True)
        token = vault.token_for("Zoë Ünal, 12 rue de Rivoli", "PERSON")
        self.assertEqual(vault.original(token), "Zoë Ünal, 12 rue de Rivoli")
        self.assertNotIn("Zoë", json.dumps(request))
        entry = request["metadata"]["pii_vault"]["tokens"][token]
        request["metadata"]["pii_vault"]["tokens"]["<PERSON_9>"] = entry
        self.assertIsNone(vault.original("<PERSON_9>"))


if __name__ == "__main__":
    unittest.main()
