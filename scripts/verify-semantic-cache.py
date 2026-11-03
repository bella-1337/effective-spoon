#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["truststore==0.10.5"]
# ///
"""Verify authenticated Qdrant semantic-response caching end to end."""

from __future__ import annotations

import json
import ssl
import sys
import time

import truststore

from maintenance_common import (
    LOG,
    compose_capture,
    container_http_request,
    fail,
    http_request,
    public_host,
    run_main,
)

TLS_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
CUSTOMER_ID = "semantic-cache-verification"
USAGE = """Usage: scripts/verify-semantic-cache.py

Verifies Qdrant authentication and confirms that two non-identical,
semantically similar prompts reuse one cached LiteLLM response.
"""


def main() -> None:
    if sys.argv[1:] in (["-h"], ["--help"]):
        print(USAGE, end="")
        return
    if len(sys.argv) > 1:
        fail("verify-semantic-cache accepts no command-line arguments", 2)

    running = set(
        compose_capture(
            ["--profile", "dev", "ps", "--services", "--status", "running"]
        ).splitlines()
    )
    for service in ("qdrant", "litellm-dev"):
        if service not in running:
            fail(f"{service} is not running")

    unauthenticated, _ = container_http_request(
        "litellm-dev", "http://qdrant:6333/collections"
    )
    if unauthenticated not in {401, 403}:
        fail(
            "Qdrant accepted an unauthenticated request"
            if 200 <= unauthenticated < 300
            else f"unauthenticated Qdrant request returned HTTP {unauthenticated}"
        )

    qdrant_key = compose_capture(
        ["--profile", "dev", "exec", "-T", "litellm-dev", "printenv", "QDRANT_API_KEY"]
    ).strip()
    if not qdrant_key:
        fail("QDRANT_API_KEY is empty")
    qdrant_status, _ = container_http_request(
        "litellm-dev",
        "http://qdrant:6333/readyz",
        headers={"api-key": qdrant_key},
    )
    if qdrant_status != 200:
        fail(f"authenticated Qdrant readiness returned HTTP {qdrant_status}")
    LOG.info("PASS: LiteLLM can authenticate to Qdrant")

    collection_url = "http://qdrant:6333/collections/litellm_dev_semantic_cache"
    initial_status, initial_body = container_http_request(
        "litellm-dev", collection_url, headers={"api-key": qdrant_key}
    )
    if initial_status == 404:
        initial_count = 0
    elif initial_status == 200:
        try:
            initial_count = json.loads(initial_body)["result"]["points_count"]
        except (json.JSONDecodeError, KeyError, TypeError):
            fail("Qdrant semantic-cache collection returned an invalid response")
    else:
        fail(f"Qdrant semantic-cache collection returned HTTP {initial_status}")

    master_key = compose_capture(
        ["--profile", "dev", "exec", "-T", "litellm-dev", "printenv", "LITELLM_MASTER_KEY"]
    ).strip()
    if not master_key:
        fail("LITELLM_MASTER_KEY is empty")

    nonce = int(time.time())
    prompts = (
        f"Return exactly semantic-cache-{nonce} and no additional text.",
        f"Return exactly semantic-cache-{nonce} with no additional text.",
    )
    dev_host = public_host("LITELLM_DEV_HOST")
    responses: list[dict[str, object]] = []
    for prompt in prompts:
        payload = json.dumps(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "cache": {"use-cache": True},
            },
            separators=(",", ":"),
        ).encode()
        status, body = http_request(
            f"https://{dev_host}/v1/chat/completions",
            method="POST",
            headers={
                "Authorization": f"Bearer {master_key}",
                "x-litellm-customer-id": CUSTOMER_ID,
                "Content-Type": "application/json",
            },
            data=payload,
            context=TLS_CONTEXT,
        )
        if status < 200 or status >= 300:
            fail(f"semantic-cache completion request returned HTTP {status}")
        try:
            response = json.loads(body)
        except json.JSONDecodeError:
            fail("semantic-cache completion returned invalid JSON")
        if not isinstance(response, dict):
            fail("semantic-cache completion returned a non-object response")
        responses.append(response)
        time.sleep(2)

    contents: list[str] = []
    for response in responses:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            fail("semantic-cache completion omitted choices")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            fail("semantic-cache completion omitted message content")
        contents.append(message["content"])

    first_content, second_content = contents
    if first_content != second_content:
        fail("semantic-cache hit did not replay the cached response")

    # LiteLLM exposes the score as a response header, but maintenance_common's
    # HTTP helper intentionally returns only status and body. Confirm the hit
    # through Qdrant's stored point count and exact replay instead.
    collection_status, collection_body = container_http_request(
        "litellm-dev", collection_url,
        headers={"api-key": qdrant_key},
    )
    if collection_status != 200:
        fail(f"Qdrant semantic-cache collection returned HTTP {collection_status}")
    try:
        collection = json.loads(collection_body)
        points_count = collection["result"]["points_count"]
    except (json.JSONDecodeError, KeyError, TypeError):
        fail("Qdrant semantic-cache collection returned an invalid response")
    if points_count != initial_count + 1:
        fail(
            f"expected one new cached response, point count changed from "
            f"{initial_count} to {points_count}"
        )
    LOG.info("PASS: semantically similar prompts reused one cached response")


if __name__ == "__main__":
    run_main(main)
