#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["truststore==0.10.5"]
# ///
"""Verify pgvector integration and reject invalid vector-store writes."""

from __future__ import annotations

import json
import ssl
import sys

import truststore

from maintenance_common import (
    compose,
    compose_capture,
    container_http_request,
    env_or_dotenv,
    fail,
    http_request,
    run_main,
)

STORE_ID = "sme-knowledge"
TLS_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
USAGE = """Usage: scripts/verify-pgvector.py

Verifies the managed pgvector store and confirms invalid vector writes are
rejected without creating records.
"""


def scalar(sql: str) -> str:
    return compose_capture([
        "exec", "-T", "vector-postgres", "psql", "--no-psqlrc", "--quiet",
        "--tuples-only", "--no-align", "--username", "vector", "--dbname", "vector_store",
        "--command", sql,
    ]).strip()


def assert_rejected(description: str, sql: str) -> None:
    result = compose([
        "exec", "-T", "vector-postgres", "psql", "--no-psqlrc", "--quiet", "--set", "ON_ERROR_STOP=1",
        "--username", "vector", "--dbname", "vector_store", "--command", sql,
    ], check=False)
    if result.returncode == 0:
        print(f"FAIL: {description} was accepted", file=sys.stderr)
        raise SystemExit(1)
    print(f"PASS: {description} was rejected")


def json_response(url: str, method: str, headers: dict[str, str], payload: dict[str, object] | None) -> tuple[int, object]:
    status, body = http_request(url, method=method, headers=headers,
                                data=json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None,
                                context=TLS_CONTEXT)
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, None


def main() -> None:
    if sys.argv[1:] in (["-h"], ["--help"]):
        print(USAGE, end="")
        return
    if len(sys.argv) > 1:
        fail("verify-pgvector accepts no command-line arguments", 2)
    prod_host = env_or_dotenv("LITELLM_PROD_HOST", "llm.example.test")
    dev_host = env_or_dotenv("LITELLM_DEV_HOST", "llm-dev.example.test")
    extension = scalar("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
    initial = scalar(f"SELECT COUNT(*) FROM embeddings WHERE vector_store_id = '{STORE_ID}';")
    if not extension:
        fail("pgvector extension version is empty")
    print(f"PASS: pgvector {extension} is installed and the knowledge store contains {initial} vector row(s)")
    status, _ = json_response(f"https://{prod_host}/v1/vector_stores", "POST", {"Content-Type": "application/json"},
                              {"name": "must-not-exist"})
    if status != 401:
        fail(f"unauthenticated vector-store request returned HTTP {status}")
    print("PASS: unauthenticated vector-store request returned HTTP 401")
    connector, _ = container_http_request(
        "litellm-pgvector",
        "http://localhost:8000/v1/vector_stores",
    )
    if connector not in {401, 403}:
        fail(f"unauthenticated pgvector connector request returned HTTP {connector}")
    print(f"PASS: unauthenticated pgvector connector request returned HTTP {connector}")
    master_key = compose_capture(["exec", "-T", "litellm-dev", "printenv", "LITELLM_MASTER_KEY"]).strip()
    if not master_key:
        fail("LITELLM_MASTER_KEY is empty")
    headers = {"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"}
    status, registry = json_response(f"https://{dev_host}/vector_store/list", "GET", headers, None)
    if status < 200 or status >= 300 or not isinstance(registry, dict) or not any(
        item.get("vector_store_id") == STORE_ID and item.get("custom_llm_provider") == "pg_vector"
        for item in registry.get("data", []) if isinstance(item, dict)
    ):
        fail("LiteLLM did not list the managed sme-knowledge pgvector store")
    print("PASS: LiteLLM lists the managed sme-knowledge pgvector store")
    status, search = json_response(f"https://{dev_host}/v1/vector_stores/{STORE_ID}/search", "POST", headers,
                                   {"query": "empty store verification", "max_num_results": 1})
    if status < 200 or status >= 300 or not isinstance(search, dict) or search.get("object") != "vector_store.search_results.page" or not isinstance(search.get("data"), list):
        fail("LiteLLM did not search the managed store through the official connector")
    print("PASS: LiteLLM searched the managed store through the official connector")
    status, _ = http_request(
        f"https://{dev_host}/v1/vector_stores",
        method="POST",
        headers=headers,
        data=b"{",
        context=TLS_CONTEXT,
    )
    if status not in {400, 422}:
        fail(f"authenticated invalid JSON request returned HTTP {status}")
    print(f"PASS: authenticated invalid JSON request returned HTTP {status}")
    assert_rejected("embedding for an unknown store",
                    "INSERT INTO embeddings (id, vector_store_id, content, embedding) VALUES (gen_random_uuid()::text, 'unknown-store', 'invalid', array_fill(0::real, ARRAY[1024])::vector);")
    assert_rejected("embedding with the wrong dimensions",
                    f"INSERT INTO embeddings (id, vector_store_id, content, embedding) VALUES (gen_random_uuid()::text, '{STORE_ID}', 'invalid', '[0,0,0]'::vector);")
    assert_rejected("empty content",
                    f"INSERT INTO embeddings (id, vector_store_id, content, embedding) VALUES (gen_random_uuid()::text, '{STORE_ID}', '', array_fill(0::real, ARRAY[1024])::vector);")
    if scalar(f"SELECT COUNT(*) FROM embeddings WHERE vector_store_id = '{STORE_ID}';") != initial:
        fail("rejected inputs created vector rows")
    print("PASS: rejected inputs created no vector rows")


if __name__ == "__main__":
    run_main(main)
