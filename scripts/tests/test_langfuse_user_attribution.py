from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SALT = "test-only-langfuse-salt"


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class UserAPIKeyAuth:
    def __init__(self, user_id: str | None = None, api_key: str | None = None) -> None:
        self.user_id = user_id
        self.api_key = api_key


def load_module() -> ModuleType:
    fastapi = ModuleType("fastapi")
    fastapi.HTTPException = HTTPException
    litellm = ModuleType("litellm")
    integrations = ModuleType("litellm.integrations")
    custom_logger = ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = type("CustomLogger", (), {})
    proxy = ModuleType("litellm.proxy")
    proxy_types = ModuleType("litellm.proxy._types")
    proxy_types.UserAPIKeyAuth = UserAPIKeyAuth
    stubs = {
        "fastapi": fastapi,
        "litellm": litellm,
        "litellm.integrations": integrations,
        "litellm.integrations.custom_logger": custom_logger,
        "litellm.proxy": proxy,
        "litellm.proxy._types": proxy_types,
    }
    path = ROOT / "litellm" / "modules" / "langfuse_user_attribution.py"
    spec = importlib.util.spec_from_file_location("langfuse_user_attribution_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class LangfuseUserAttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.auth = UserAPIKeyAuth(user_id="user-123", api_key="hashed-api-key")

    def call(self, data: dict[str, Any], auth: UserAPIKeyAuth | None = None) -> dict[str, Any]:
        with patch.dict("os.environ", {"LANGFUSE_USER_ID_SALT": SALT}, clear=False):
            return asyncio.run(
                self.module.proxy_handler_instance.async_pre_call_hook(
                    auth or self.auth,
                    None,
                    data,
                    "acompletion",
                )
            )

    def expected_session(
        self,
        raw_session_id: str,
        *,
        kind: str = "caller",
        principal_id: str = "user-123",
    ) -> str:
        digest = hmac.new(
            SALT.encode("utf-8"),
            f"session:{kind}:user:{principal_id}\0{raw_session_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"litellm-session-{digest}"

    def test_scopes_canonical_session_to_authenticated_user(self) -> None:
        result = self.call(
            {
                "metadata": {"session_id": "conversation-42"},
                "user": "caller-controlled",
            }
        )

        expected = self.expected_session("conversation-42")
        self.assertEqual(result["metadata"]["session_id"], expected)
        self.assertEqual(result["litellm_session_id"], expected)
        self.assertEqual(result["user"], result["metadata"]["trace_user_id"])
        self.assertNotIn("conversation-42", str(result))

    def test_same_raw_session_does_not_collide_between_users(self) -> None:
        first = self.call({"metadata": {"session_id": "shared-session"}})
        second = self.call(
            {"metadata": {"session_id": "shared-session"}},
            UserAPIKeyAuth(user_id="user-456", api_key="other-key"),
        )

        self.assertNotEqual(
            first["metadata"]["session_id"],
            second["metadata"]["session_id"],
        )

    def test_normalizes_agent_metadata_alias(self) -> None:
        result = self.call({"metadata": {"conversation_id": "agent-conversation-7"}})

        self.assertEqual(
            result["metadata"]["session_id"],
            self.expected_session("agent-conversation-7"),
        )
        self.assertNotIn("conversation_id", result["metadata"])

    def test_uses_litellm_metadata_for_responses_routes(self) -> None:
        result = self.call(
            {
                "metadata": {},
                "litellm_metadata": {"thread_id": "thread-abc123"},
            }
        )

        self.assertEqual(
            result["litellm_metadata"]["session_id"],
            self.expected_session("thread-abc123"),
        )
        self.assertNotIn("thread_id", result["litellm_metadata"])

    def test_normalizes_generic_agent_session_header(self) -> None:
        result = self.call(
            {
                "metadata": {},
                "proxy_server_request": {
                    "headers": {
                        "x-crewai-session-id": "crewai-session-123",
                        "langfuse_session_id": "untrusted-langfuse-session",
                    }
                },
            }
        )

        self.assertEqual(
            result["metadata"]["session_id"],
            self.expected_session("crewai-session-123"),
        )
        self.assertNotIn(
            "langfuse_session_id",
            result["proxy_server_request"]["headers"],
        )

    def test_uses_trace_id_as_automatic_agent_run_fallback(self) -> None:
        result = self.call(
            {
                "metadata": {},
                "litellm_trace_id": "otel-agent-trace-123",
            }
        )

        self.assertEqual(
            result["metadata"]["session_id"],
            self.expected_session("otel-agent-trace-123", kind="trace"),
        )

    def test_creates_request_scoped_fallback_without_any_identifier(self) -> None:
        with patch.object(self.module.secrets, "token_urlsafe", return_value="request-token"):
            result = self.call({"metadata": {}})

        self.assertEqual(
            result["metadata"]["session_id"],
            self.expected_session("request-token", kind="request"),
        )

    def test_rejects_malformed_session_id(self) -> None:
        with self.assertRaises(HTTPException) as context:
            self.call({"metadata": {"session_id": "bad\nsession"}})

        self.assertEqual(context.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
