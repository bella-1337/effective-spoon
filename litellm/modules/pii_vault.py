"""Reversible PII masking: numbered tokens to the provider, originals restored in the response.

With PII_REVERSIBLE_MASKING=true, the `pii-assist` and `presidio-pii`
guardrails replace each detected value in a request with a numbered token such
as `<PERSON_1>` instead of `<PERSON>`. The same value gets the same token
throughout the request, so the model can still tell two people apart. When the
answer comes back, `presidio-pii` first masks personal data the model produced
itself, as before; then the `pii-restore` guardrail, which runs after it, puts
the request's own values back in place of their tokens. The client sees its own
data; the provider never does.

Each request keeps a vault in its metadata (`metadata.pii_vault`) with, per
token, the original value encrypted with AES-256-GCM and, per value, an
HMAC-SHA-256 lookup key. Nothing in it is readable without the key. The key is
random, created when the gateway process starts, and never stored or logged:
request and response are handled by the same process, and a vault copied into
logs or traces cannot be decrypted after the request, or at all once the
process stops.

Tokenising wraps two methods of LiteLLM's Presidio guardrail (checked with
1.108.9 and 1.111.0-dev.4). If an upgrade changes them, the wrapper is not
installed, a warning is logged, and requests are masked irreversibly as before.
Restoring covers chat completions, streamed or not: message text and tool-call
arguments.
"""

from __future__ import annotations

import base64
import contextvars
import copy
import hashlib
import hmac
import inspect
import os
import re
import secrets
import sys
from types import ModuleType
from typing import Any, AsyncGenerator

from litellm._logging import verbose_proxy_logger
from litellm.integrations.custom_guardrail import CustomGuardrail

try:  # in the LiteLLM image; absent on hosts that only run the unit tests
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - reversible masking then stays off
    AESGCM = None  # type: ignore[assignment,misc]

ENABLED_ENV = "PII_REVERSIBLE_MASKING"
VAULT_KEY = "pii_vault"
MAX_TOKENS_PER_REQUEST = 10_000
ENTITY_CHARS = 40
# <PERSON_1>, <LOCATION_12>: an upper-case entity name and a sequence number.
TOKEN_RE = re.compile(r"<([A-Z][A-Z0-9_]{0,%d})_([1-9][0-9]{0,4})>" % (ENTITY_CHARS - 1))
# The longest possible token, and an unfinished one at the end of a stream chunk.
MAX_TOKEN_CHARS = ENTITY_CHARS + 8
PARTIAL_TOKEN_RE = re.compile(r"<[A-Z0-9_]{0,%d}$" % (ENTITY_CHARS + 6))


def _shared_state() -> ModuleType:
    """Keys and the request-side marker, one set per process.

    LiteLLM loads this file as a guardrail module and pii_assist.py loads it
    too; both copies must use the same key, so it lives in sys.modules.
    """
    name = "litellm_stack_pii_vault_state"
    state = sys.modules.get(name)
    if state is None:
        state = ModuleType(name)
        state.encryption_key = secrets.token_bytes(32)  # AES-256
        state.lookup_key = secrets.token_bytes(32)
        # "request" while LiteLLM's Presidio guardrail masks a request.
        state.side = contextvars.ContextVar("pii_vault_side", default=None)
        sys.modules[name] = state
    return state


_STATE = _shared_state()


def enabled() -> bool:
    return AESGCM is not None and os.environ.get(ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


class Vault:
    """The tokens of one request, kept in its metadata as encrypted entries."""

    def __init__(self, store: dict[str, Any]) -> None:
        self.store = store
        store.setdefault("tokens", {})  # token -> nonce + ciphertext
        store.setdefault("lookup", {})  # HMAC(type, value) -> token
        store.setdefault("counters", {})  # type -> last sequence number

    @classmethod
    def for_request(cls, request_data: Any, create: bool) -> Vault | None:
        if not isinstance(request_data, dict):
            return None
        metadata = request_data.get("metadata")
        if not isinstance(metadata, dict):
            if not create:
                return None
            metadata = request_data["metadata"] = {}
        store = metadata.get(VAULT_KEY)
        if not isinstance(store, dict):
            if not create:
                return None
            store = metadata[VAULT_KEY] = {}
        return cls(store)

    def token_for(self, value: str, entity_type: str) -> str:
        """The token for `value`, the same one each time it occurs in this request."""
        entity = re.sub(r"[^A-Z0-9_]", "_", entity_type.upper())[:ENTITY_CHARS] or "PII"
        if not entity[0].isalpha():
            entity = f"PII_{entity}"[:ENTITY_CHARS]
        lookup = _b64(hmac.new(_STATE.lookup_key, f"{entity}\0{value}".encode("utf-8"), hashlib.sha256).digest())
        token = self.store["lookup"].get(lookup)
        if token is not None:
            return token
        if len(self.store["tokens"]) >= MAX_TOKENS_PER_REQUEST:
            return f"<{entity}>"  # beyond the budget: masked, not restorable
        number = int(self.store["counters"].get(entity, 0)) + 1
        self.store["counters"][entity] = number
        token = f"<{entity}_{number}>"
        nonce = secrets.token_bytes(12)
        # The token is authenticated data: an entry moved to another token fails.
        ciphertext = AESGCM(_STATE.encryption_key).encrypt(nonce, value.encode("utf-8"), token.encode("ascii"))
        self.store["tokens"][token] = _b64(nonce + ciphertext)
        self.store["lookup"][lookup] = token
        return token

    def original(self, token: str) -> str | None:
        entry = self.store["tokens"].get(token)
        if not isinstance(entry, str):
            return None
        try:
            raw = base64.urlsafe_b64decode(entry.encode("ascii"))
            return AESGCM(_STATE.encryption_key).decrypt(raw[:12], raw[12:], token.encode("ascii")).decode("utf-8")
        except Exception:  # another process's key, or a tampered entry
            return None

    def restore(self, text: str) -> tuple[str, int]:
        """Replace this request's tokens in `text` with their values; unknown tokens stay."""
        if not self.store["tokens"] or "<" not in text:
            return text, 0
        restored = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal restored
            value = self.original(match.group(0))
            if value is None:
                return match.group(0)
            restored += 1
            return value

        return TOKEN_RE.sub(replace, text), restored


def request_vault(request_data: Any) -> Vault | None:
    """The vault to tokenise into while a request is masked, or None."""
    if not enabled():
        return None
    return Vault.for_request(request_data, create=True)


# -- tokenising: LiteLLM's Presidio guardrail -----------------------------------


def install_reversible_masking_shim() -> bool:
    """Make LiteLLM's Presidio guardrail put numbered tokens into requests."""
    requested = os.environ.get(ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    if requested and AESGCM is None:
        verbose_proxy_logger.warning("pii-vault: the cryptography package is missing; masking stays irreversible")
        return False
    if not enabled():
        return False
    try:
        from litellm.proxy.guardrails.guardrail_hooks import presidio as presidio_module

        cls = presidio_module._OPTIONAL_PresidioPIIMasking
        apply_original = cls.apply_guardrail
        pre_call_original = cls.async_pre_call_hook
        anonymize_original = cls.anonymize_text
    except (ImportError, AttributeError):
        verbose_proxy_logger.warning("pii-vault: LiteLLM's Presidio guardrail not found; masking stays irreversible")
        return False
    if getattr(anonymize_original, "_stack_pii_vault", False):
        return True
    if not {"inputs", "request_data", "input_type"} <= set(inspect.signature(apply_original).parameters) or not {
        "text", "analyze_results", "masked_entity_count", "request_data"
    } <= set(inspect.signature(anonymize_original).parameters):
        verbose_proxy_logger.warning("pii-vault: LiteLLM's Presidio guardrail changed; masking stays irreversible")
        return False
    side = _STATE.side

    async def anonymize_text(self: Any, *args: Any, **kwargs: Any) -> Any:
        bound = inspect.signature(anonymize_original).bind(self, *args, **kwargs)
        text = bound.arguments.get("text")
        results = bound.arguments.get("analyze_results")
        # Only a request is tokenised; output masking keeps <TYPE> placeholders.
        vault = request_vault(bound.arguments.get("request_data")) if side.get() == "request" else None
        if vault is None or not isinstance(text, str) or not isinstance(results, list) or not results:
            return await anonymize_original(self, *args, **kwargs)
        counts = bound.arguments.get("masked_entity_count")
        # The analyzer may report overlapping spans of different types; keep the
        # longest at each place, then replace from the end so offsets stay valid.
        spans: list[tuple[int, int, str]] = []
        for item in sorted(results, key=lambda r: (r.get("start") or 0, -((r.get("end") or 0) - (r.get("start") or 0)))):
            start, end, entity = item.get("start"), item.get("end"), item.get("entity_type")
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
                continue
            if spans and start < spans[-1][1]:
                continue
            spans.append((start, end, str(entity or "PII")))
        tokens = [vault.token_for(text[start:end], entity) for start, end, entity in spans]
        masked = text
        for (start, end, entity), token in zip(reversed(spans), reversed(tokens)):
            masked = masked[:start] + token + masked[end:]
            if isinstance(counts, dict):
                counts[entity] = counts.get(entity, 0) + 1
        return masked

    async def apply_guardrail(self: Any, *args: Any, **kwargs: Any) -> Any:
        bound = inspect.signature(apply_original).bind(self, *args, **kwargs)
        marker = side.set("request" if bound.arguments.get("input_type") == "request" else None)
        try:
            return await apply_original(self, *args, **kwargs)
        finally:
            side.reset(marker)

    async def async_pre_call_hook(self: Any, *args: Any, **kwargs: Any) -> Any:
        marker = side.set("request")
        try:
            return await pre_call_original(self, *args, **kwargs)
        finally:
            side.reset(marker)

    for wrapper in (anonymize_text, apply_guardrail, async_pre_call_hook):
        wrapper._stack_pii_vault = True  # type: ignore[attr-defined]
    cls.anonymize_text = anonymize_text
    cls.apply_guardrail = apply_guardrail
    cls.async_pre_call_hook = async_pre_call_hook
    verbose_proxy_logger.info("pii-vault: reversible masking on; responses get the request's own values back")
    return True


# -- restoring: the pii-restore guardrail ---------------------------------------


def _restore_message(vault: Vault, message: Any) -> int:
    """Restore tokens in a chat message or stream delta: text and tool-call arguments."""
    restored = 0
    content = getattr(message, "content", None)
    if isinstance(content, str):
        message.content, count = vault.restore(content)
        restored += count
    for call in getattr(message, "tool_calls", None) or []:
        function = getattr(call, "function", None)
        arguments = getattr(function, "arguments", None)
        if isinstance(arguments, str):
            function.arguments, count = vault.restore(arguments)
            restored += count
    return restored


class StreamRestorer:
    """Restores tokens across stream chunks, holding back an unfinished `<TYPE_n`."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.pending: dict[int, str] = {}
        self.restored = 0

    def feed(self, index: int, text: str, final: bool) -> str:
        text = self.pending.pop(index, "") + text
        if not final:
            partial = PARTIAL_TOKEN_RE.search(text[-MAX_TOKEN_CHARS:])
            if partial is not None:
                cut = len(text) - (len(text[-MAX_TOKEN_CHARS:]) - partial.start())
                text, self.pending[index] = text[:cut], text[cut:]
        restored, count = self.vault.restore(text)
        self.restored += count
        return restored


class PiiRestoreGuardrail(CustomGuardrail):
    """post_call, after presidio-pii: the request's own values back into the response."""

    def __init__(self, **kwargs: Any) -> None:
        install_reversible_masking_shim()
        super().__init__(**kwargs)

    async def async_post_call_success_hook(self, data: dict, user_api_key_dict: Any, response: Any) -> Any:
        vault = Vault.for_request(data, create=False)
        if vault is None:
            return response
        restored = 0
        for choice in getattr(response, "choices", None) or []:
            message = getattr(choice, "message", None)
            if message is not None:
                restored += _restore_message(vault, message)
            text = getattr(choice, "text", None)
            if isinstance(text, str):
                choice.text, count = vault.restore(text)
                restored += count
        if restored:
            verbose_proxy_logger.info("pii-restore: restored %d values in the response", restored)
        return response

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict: Any, response: Any, request_data: dict
    ) -> AsyncGenerator[Any, None]:
        vault = Vault.for_request(request_data, create=False)
        if vault is None:
            async for chunk in response:
                yield chunk
            return
        restorer = StreamRestorer(vault)
        last: Any = None
        async for chunk in response:
            choices = getattr(chunk, "choices", None)
            if not choices:
                yield chunk
                continue
            last = chunk
            for choice in choices:
                delta = getattr(choice, "delta", None)
                content = getattr(delta, "content", None)
                final = getattr(choice, "finish_reason", None) is not None
                index = getattr(choice, "index", 0) or 0
                if isinstance(content, str) or (final and index in restorer.pending):
                    delta.content = restorer.feed(index, content or "", final)
                for call in getattr(delta, "tool_calls", None) or []:
                    function = getattr(call, "function", None)
                    arguments = getattr(function, "arguments", None)
                    if isinstance(arguments, str):
                        # Arguments stream in fragments; restore what is whole.
                        function.arguments, count = vault.restore(arguments)
                        restorer.restored += count
            yield chunk
        if restorer.pending and last is not None:
            # The stream ended without a finish chunk: send what was held back.
            tail = copy.deepcopy(last)
            tail_choices = getattr(tail, "choices", None) or []
            for choice in tail_choices:
                index = getattr(choice, "index", 0) or 0
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    delta.content = restorer.feed(index, "", True) if index in restorer.pending else ""
                    delta.tool_calls = None
            yield tail
        if restorer.restored:
            verbose_proxy_logger.info("pii-restore: restored %d values in the streamed response", restorer.restored)
