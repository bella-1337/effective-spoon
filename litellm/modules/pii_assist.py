"""Local-model language and personal-data assist for the presidio-pii guardrail.

Runs as the `pii-assist` guardrail in pre_call, before `presidio-pii`. For each
request it asks the local `pii-assist` model (an on-host model such as Ollama's
qwen3.5:4b, never a cloud provider) for the language of the prompt and the
personal data in it, then:

1. sets the request's Presidio language (`metadata.guardrail_config.language`)
   so the analyzer uses its German or French pipeline when needed;
2. replaces the personal data the model found with `<TYPE>` placeholders, so
   items Presidio's recognizers miss are masked as well.

Presidio still runs afterwards and remains the baseline: when the model is not
configured, slow, or unavailable, the request continues with Presidio alone in
the default language. Organisation names are not masked. Nothing from the
prompt is logged.

Settings (gateway environment):
  PII_ASSIST_MAX_CHUNK_CHARS  characters sent to the model per call (default 4000)
  PII_ASSIST_MAX_CALLS        model calls per request for uncached text (default 3)
  PII_ASSIST_TIMEOUT_SECONDS  timeout of one model call (default 20)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from litellm._logging import verbose_proxy_logger
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

ASSIST_MODEL = "pii-assist"
DEFAULT_LANGUAGE = "en"
# The prompt, languages, and item types (pii_assist_prompt.json).
PROMPT = json.loads((Path(__file__).with_name("pii_assist_prompt.json")).read_text(encoding="utf-8"))
SYSTEM_PROMPT: str = PROMPT["system_prompt"]
LANGUAGES: tuple[str, ...] = tuple(PROMPT["languages"])
ITEM_TYPES: tuple[str, ...] = tuple(PROMPT["item_types"])
# Placeholders use Presidio's entity names where one exists.
PLACEHOLDER_TYPES: dict[str, str] = PROMPT["placeholder_types"]
# ORGANIZATION and OTHER are reported but not masked at the gateway:
# organisation names stay unmasked for model calls, and OTHER is too vague to
# mask without review.
GATEWAY_IGNORED_TYPES = frozenset({"ORGANIZATION", "OTHER"})
MASKED_ROLES = frozenset({"system", "developer", "user", "tool", "function"})
# Every entity name that can appear as a "<NAME>" placeholder: Presidio's
# recognizers in presidio/recognizers.yaml and this guardrail's item types.
PLACEHOLDER_NAMES = (
    "PERSON", "LOCATION", "DATE_TIME", "NRP", "ORGANIZATION", "EMAIL_ADDRESS", "PHONE_NUMBER",
    "CREDIT_CARD", "CRYPTO", "IBAN_CODE", "IP_ADDRESS", "MAC_ADDRESS", "MEDICAL_LICENSE", "URL",
    "UK_NHS", "UK_NINO", "UK_PASSPORT", "UK_POSTCODE", "US_BANK_NUMBER", "US_DRIVER_LICENSE",
    "US_ITIN", "US_PASSPORT", "US_SSN", "ID_NUMBER", "CUSTOM_TERM",
)
# Case-sensitive even under the analyzer's IGNORECASE default, and only a whole
# detection that is nothing but one placeholder (with brackets or punctuation).
PLACEHOLDER_ALLOW_PATTERN = r"^[\W_]*(?-i:(?:%s))[\W_]*$" % "|".join(PLACEHOLDER_NAMES)
MAX_ITEMS_PER_CALL = 200
MAX_ITEM_CHARS = 200
CACHE_ENTRIES = 2048


def response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "language": {"type": "string", "enum": [*LANGUAGES, "other"]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "type": {"type": "string", "enum": list(ITEM_TYPES)},
                    },
                    "required": ["text", "type"],
                },
            },
        },
        "required": ["language", "items"],
    }


def _int_setting(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return min(max(value, low), high)


def chunk_text(text: str, size: int) -> list[str]:
    """Split text into pieces of at most `size` characters, at whitespace when possible."""
    chunks: list[str] = []
    while len(text) > size:
        cut = text.rfind(" ", size // 2, size)
        cut = size if cut <= 0 else cut
        chunks.append(text[:cut])
        text = text[cut:]
    if text.strip():
        chunks.append(text)
    return chunks


def parse_answer(content: Any, source: str) -> tuple[str | None, list[tuple[str, str]]]:
    """Validate the model's JSON: a known language, and items that occur verbatim in `source`."""
    try:
        answer = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        return None, []
    language = answer.get("language")
    language = language if language in LANGUAGES else None
    items: list[tuple[str, str]] = []
    raw_items = answer.get("items")
    for item in raw_items[:MAX_ITEMS_PER_CALL] if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue
        text, kind = item.get("text"), item.get("type")
        if not isinstance(text, str) or kind not in ITEM_TYPES:
            continue
        text = text.strip()
        # Only text the prompt really contains: the model cannot add or alter content.
        if not 2 <= len(text) <= MAX_ITEM_CHARS or text not in source or not re.search(r"\w", text):
            continue
        items.append((text, kind))
    return language, items


def masking_pattern(items: list[tuple[str, str]], ignored: frozenset[str]) -> tuple[re.Pattern[str], dict[str, str]] | None:
    """One regex for every term to mask, longest first, never inside a longer word."""
    placeholders: dict[str, str] = {}
    for text, kind in items:
        if kind not in ignored:
            placeholders.setdefault(text, f"<{PLACEHOLDER_TYPES.get(kind, kind)}>")
    if not placeholders:
        return None
    alternatives = []
    for text in sorted(placeholders, key=len, reverse=True):
        before = r"(?<!\w)" if re.match(r"\w", text) else ""
        after = r"(?!\w)" if re.search(r"\w$", text) else ""
        alternatives.append(f"{before}{re.escape(text)}{after}")
    return re.compile("|".join(alternatives)), placeholders


# Address parts the model can leave between its placeholders. Masking its
# items first hides the address from Presidio's street-address recognizers, so
# "3, place X\n34000 Montpellier" would become "<LOCATION>\n34000 <LOCATION>".
# A five-digit number directly before a masked city is a postcode, and a short
# number directly after a masked street, at the end of the line or before a
# comma, is a house number.
POSTCODE_BEFORE_CITY = re.compile(r"\b\d{5}[ \t]+<LOCATION>")
HOUSE_NUMBER_AFTER_STREET = re.compile(r"<LOCATION>[ \t]\d{1,3}[a-zA-Z]?(?=[ \t]*(?:,|\n|$))")


def mask(text: str, compiled: tuple[re.Pattern[str], dict[str, str]] | None) -> str:
    if compiled is None:
        return text
    pattern, placeholders = compiled
    masked = pattern.sub(lambda match: placeholders[match.group(0)], text)
    if "<LOCATION>" in masked:
        masked = POSTCODE_BEFORE_CITY.sub("<LOCATION>", masked)
        masked = HOUSE_NUMBER_AFTER_STREET.sub("<LOCATION>", masked)
    return masked


def text_parts(messages: list[Any]) -> list[tuple[dict[str, Any], str | int, str]]:
    """(container, key, text) for every maskable text, newest message first."""
    parts: list[tuple[dict[str, Any], str | int, str]] = []
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") not in MASKED_ROLES:
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append((message, "content", content))
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str):
                    parts.append((part, "text", part["text"]))
    return parts


def request_language(request_data: Any) -> str | None:
    """The supported language set in metadata.guardrail_config, if any."""
    metadata = request_data.get("metadata") if isinstance(request_data, dict) else None
    config = metadata.get("guardrail_config") if isinstance(metadata, dict) else None
    language = config.get("language") if isinstance(config, dict) else None
    return language if language in LANGUAGES else None


def install_presidio_language_shim() -> bool:
    """Make LiteLLM's Presidio guardrail honour the request's language.

    LiteLLM (checked up to 1.111.0-dev.4) runs chat-request guardrails through
    `apply_guardrail`, which calls `check_pii(..., presidio_config=None)` and so
    analyses every request in the guardrail's fixed `presidio_language`. This
    wraps `check_pii` to use the language in `metadata.guardrail_config` when
    none is passed, limited to the languages the analyzer serves. If a LiteLLM
    upgrade changes the method, the shim is not installed and Presidio keeps its
    configured language; scripts/verify-litellm-upgrade.py checks German.
    """
    import inspect

    try:
        from litellm.proxy.guardrails.guardrail_hooks import presidio as presidio_module

        cls = presidio_module._OPTIONAL_PresidioPIIMasking
        original = cls.check_pii
    except (ImportError, AttributeError):
        verbose_proxy_logger.warning("pii-assist: LiteLLM's Presidio guardrail not found; language stays fixed")
        return False
    if getattr(original, "_stack_language_shim", False):
        return True
    if not {"presidio_config", "request_data"} <= set(inspect.signature(original).parameters):
        verbose_proxy_logger.warning("pii-assist: LiteLLM's check_pii changed; Presidio language stays fixed")
        return False

    async def check_pii(self: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("presidio_config") is None:
            language = request_language(kwargs.get("request_data"))
            if language is not None:
                from litellm.types.guardrails import PresidioPerRequestConfig

                kwargs["presidio_config"] = PresidioPerRequestConfig(language=language)
        return await original(self, *args, **kwargs)

    check_pii._stack_language_shim = True  # type: ignore[attr-defined]
    cls.check_pii = check_pii

    # The German and French models read placeholder words such as LOCATION in
    # "<LOCATION>" as entities and mask them again ("<<LOCATION>>"). That hits
    # this guardrail's placeholders and Presidio's own ones in conversation
    # history, so detections that are only a placeholder are allowed through.
    build_payload = getattr(cls, "_get_presidio_analyze_request_payload", None)
    if build_payload is not None and not getattr(build_payload, "_stack_language_shim", False):

        def payload_with_placeholder_allow_list(self: Any, *args: Any, **kwargs: Any) -> Any:
            payload = build_payload(self, *args, **kwargs)
            if isinstance(payload, dict) and "allow_list" not in payload:
                payload["allow_list"] = [PLACEHOLDER_ALLOW_PATTERN]
                payload["allow_list_match"] = "regex"
            return payload

        payload_with_placeholder_allow_list._stack_language_shim = True  # type: ignore[attr-defined]
        cls._get_presidio_analyze_request_payload = payload_with_placeholder_allow_list
    return True


class PiiAssistGuardrail(CustomGuardrail):
    def __init__(self, **kwargs: Any) -> None:
        install_presidio_language_shim()
        self.assist_model = ASSIST_MODEL
        self.chunk_chars = _int_setting("PII_ASSIST_MAX_CHUNK_CHARS", 4000, 500, 16000)
        self.max_calls = _int_setting("PII_ASSIST_MAX_CALLS", 3, 0, 10)
        self.timeout = _int_setting("PII_ASSIST_TIMEOUT_SECONDS", 20, 1, 120)
        self._cache: OrderedDict[str, tuple[str | None, list[tuple[str, str]]]] = OrderedDict()
        self._warned_missing_model = False
        super().__init__(**kwargs)

    # -- model access -------------------------------------------------------

    def _router(self) -> Any:
        from litellm.proxy import proxy_server

        router = proxy_server.llm_router
        if router is None or self.assist_model not in (router.get_model_names() or []):
            if not self._warned_missing_model:
                verbose_proxy_logger.info("pii-assist: no %s model configured; Presidio runs alone", self.assist_model)
                self._warned_missing_model = True
            return None
        return router

    async def _ask(self, router: Any, chunk: str) -> tuple[str | None, list[tuple[str, str]]]:
        key = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        # A direct router call: it does not pass through the proxy's guardrails.
        response = await asyncio.wait_for(
            router.acompletion(
                model=self.assist_model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": chunk}],
                response_format={"type": "json_schema", "json_schema": {"name": "personal_data", "schema": response_schema()}},
                reasoning_effort="none",
                temperature=0,
            ),
            timeout=self.timeout,
        )
        result = parse_answer(response.choices[0].message.content, chunk)
        self._cache[key] = result
        if len(self._cache) > CACHE_ENTRIES:
            self._cache.popitem(last=False)
        return result

    # -- hook ---------------------------------------------------------------

    async def async_pre_call_hook(self, user_api_key_dict: Any, cache: Any, data: dict, call_type: Any) -> dict:
        if self.should_run_guardrail(data=data, event_type=GuardrailEventHooks.pre_call) is not True:
            return data
        messages = data.get("messages")
        if data.get("model") == self.assist_model or not isinstance(messages, list):
            return data
        router = self._router()
        if router is None or self.max_calls == 0:
            return data
        parts = text_parts(messages)
        if not parts:
            return data

        started = time.monotonic()
        chunks: list[str] = []
        for _container, _key, text in parts:
            for chunk in chunk_text(text, self.chunk_chars):
                if chunk not in chunks:
                    chunks.append(chunk)
        uncached = [c for c in chunks if hashlib.sha256(c.encode("utf-8")).hexdigest() not in self._cache]
        selected = [c for c in chunks if c not in uncached] + uncached[: self.max_calls]
        try:
            answers = await asyncio.gather(*(self._ask(router, chunk) for chunk in selected), return_exceptions=True)
        except Exception as error:  # never block a request on the assist model
            verbose_proxy_logger.warning("pii-assist: skipped (%s)", type(error).__name__)
            return data

        language: str | None = None
        items: list[tuple[str, str]] = []
        failures = 0
        for chunk, answer in zip(selected, answers):
            if isinstance(answer, BaseException):
                failures += 1
                continue
            chunk_language, chunk_items = answer
            # The newest user text decides the language: chunks are newest first.
            if language is None and chunk_language is not None and chunk == chunks[0]:
                language = chunk_language
            items.extend(chunk_items)

        metadata = data.setdefault("metadata", {})
        if isinstance(metadata, dict):
            config = metadata.get("guardrail_config")
            config = config if isinstance(config, dict) else {}
            # A language the caller set explicitly wins.
            if language and not config.get("language"):
                metadata["guardrail_config"] = {**config, "language": language}

        compiled = masking_pattern(items, GATEWAY_IGNORED_TYPES)
        masked = 0
        if compiled is not None:
            for container, key, text in parts:
                new_text = mask(text, compiled)
                if new_text != text:
                    container[key] = new_text
                    masked += 1
        verbose_proxy_logger.info(
            "pii-assist: language=%s items=%d masked_parts=%d chunks=%d skipped_chunks=%d failed=%d %dms",
            language or DEFAULT_LANGUAGE,
            len(items),
            masked,
            len(selected),
            len(uncached) - min(len(uncached), self.max_calls),
            failures,
            (time.monotonic() - started) * 1000,
        )
        return data
