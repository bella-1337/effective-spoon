from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]


def load_module() -> ModuleType:
    litellm = ModuleType("litellm")
    litellm.aembedding = AsyncMock()
    integrations = ModuleType("litellm.integrations")
    custom_logger = ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = type("CustomLogger", (), {})
    psycopg = ModuleType("psycopg")
    psycopg.AsyncConnection = object
    stubs = {
        "litellm": litellm,
        "litellm.integrations": integrations,
        "litellm.integrations.custom_logger": custom_logger,
        "psycopg": psycopg,
    }
    path = ROOT / "litellm" / "modules" / "xberg_auto_index.py"
    spec = importlib.util.spec_from_file_location("xberg_auto_index_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class XbergAutoIndexSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_principal_uses_server_supplied_api_key_hash(self) -> None:
        api_key_hash = "server-generated-key-hash"

        principal = self.module._trusted_principal_id(
            {
                "metadata": {
                    "user_api_key_hash": api_key_hash,
                    "trace_user_id": "untrusted-caller-value",
                }
            }
        )

        expected = hashlib.sha256(api_key_hash.encode("utf-8")).hexdigest()
        self.assertEqual(principal, f"litellm-api-key-{expected}")

    def test_principal_rejects_missing_hash(self) -> None:
        self.assertIsNone(
            self.module._trusted_principal_id(
                {"metadata": {"trace_user_id": "litellm-user-attacker-controlled"}}
            )
        )

    def test_redaction_is_applied_to_bounded_chunks(self) -> None:
        chunks = ["a" * self.module.CHUNK_SIZE, "b" * 100]

        with patch.object(
            self.module,
            "_redact_content",
            AsyncMock(side_effect=lambda content: f"redacted:{len(content)}"),
        ) as redact:
            result = asyncio.run(self.module._redact_chunks(chunks))

        self.assertEqual(result, ["redacted:4000", "redacted:100"])
        self.assertEqual(redact.await_count, 2)


if __name__ == "__main__":
    unittest.main()
