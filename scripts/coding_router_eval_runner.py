"""Classify labelled prompts with LiteLLM's own coding-router code.

Runs inside the pinned LiteLLM image, started by
scripts/compare-coding-router-classifiers.py; it is not meant to be run
directly. It builds the same ComplexityRouter the gateways build, so every
classifier sees the input LiteLLM would send and falls back the way LiteLLM
would.

stdin: one JSON object
  {"cases": [{"id": str, "prompt": str, "system_prompt": str | null}],
   "d3": [{"name": str, "model": str, "api_base": str}],
   "timeout_ms": int}
stdout: one JSON object
  {"litellm_version": str,
   "results": [{"id": str, "classifier": str, "tier": str | null,
                "cause": str | null, "fallback_reason": str | null,
                "error": str | null, "latency_ms": float}]}

The D3 key comes from the D3_DECISION_API_KEY environment variable. Prompt
text never appears in the output; errors are reported by exception type only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from importlib.metadata import version
from typing import Any
from unittest.mock import MagicMock

from litellm.llms.custom_httpx.http_handler import get_async_httpx_client
from litellm.router_strategy.complexity_router.complexity_router import ComplexityRouter
from litellm.router_strategy.complexity_router.jev_classifier import HttpJevClassifierClient
from litellm.types.llms.custom_http import httpxSpecialProvider

# The tiers the gateways configure (litellm/modules/sovereign_models.py). The
# model names are placeholders: classification never calls them.
TIERS = {tier: f"eval-{tier.lower()}" for tier in ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING")}


def build_router(classifier_type: str, d3: dict[str, str] | None, timeout_ms: int) -> ComplexityRouter:
    config: dict[str, Any] = {"classifier_type": classifier_type, "tiers": TIERS}
    client = None
    if d3 is not None:
        config.update(
            {
                # The endpoint and key live in the injected client below.
                "opensource_classifier_config": {
                    "model": d3["model"],
                    "timeout_ms": timeout_ms,
                    # Try every prompt: an open circuit would hide later answers.
                    "circuit_breaker_enabled": False,
                },
                "classifier_fallback": "heuristic",
            }
        )
        client = HttpJevClassifierClient(
            api_key=os.environ.get("D3_DECISION_API_KEY") or None,
            api_base=d3["api_base"],
            http_client=get_async_httpx_client(httpxSpecialProvider.PassThroughEndpoint),
        )
    return ComplexityRouter(
        model_name="coding-router-eval",
        litellm_router_instance=MagicMock(),
        complexity_router_config=config,
        jev_client=client,
    )


def tier_name(tier: object) -> str:
    return str(getattr(tier, "value", tier))


async def classify_all(request: dict[str, Any]) -> list[dict[str, Any]]:
    timeout_ms = int(request["timeout_ms"])
    classifiers: list[tuple[str, ComplexityRouter]] = [
        ("heuristic", build_router("heuristic", None, timeout_ms)),
        ("heuristic_v2", build_router("heuristic_v2", None, timeout_ms)),
        *((d3["name"], build_router("oss_classifier", d3, timeout_ms)) for d3 in request["d3"]),
    ]
    results: list[dict[str, Any]] = []
    for case in request["cases"]:
        for name, router in classifiers:
            started = time.perf_counter()
            tier = cause = reason = error = None
            try:
                outcome = await router.aclassify(case["prompt"], case.get("system_prompt"))
                tier, cause = tier_name(outcome.tier), str(outcome.cause)
                # Set when D3 failed and LiteLLM fell back to the heuristic:
                # timeout, classifier_error (for example a 400 over the input
                # cap), or invalid_response.
                reason = outcome.classifier_failure_reason
                error = outcome.classifier_error_type
            except Exception as exception:  # noqa: BLE001 - report and continue with the next case
                error = type(exception).__name__
            results.append(
                {
                    "id": case["id"],
                    "classifier": name,
                    "tier": tier,
                    "cause": cause,
                    "fallback_reason": reason,
                    "error": error,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
    return results


def main() -> int:
    request = json.load(sys.stdin)
    results = asyncio.run(classify_all(request))
    json.dump({"litellm_version": version("litellm"), "results": results}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
