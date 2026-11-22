#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compare coding-router classifiers on your own labelled prompts."""

from __future__ import annotations

import argparse
import json
import math
import os
import stat
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from maintenance_common import (
    LOG,
    ROOT,
    compose_capture,
    env_or_dotenv,
    fail,
    require_docker,
    run_main,
    write_private_file,
)

USAGE = """Usage: scripts/compare-coding-router-classifiers.py --prompts FILE [OPTIONS]

Classify labelled prompts with LiteLLM's heuristic, its heuristic_v2
predictor, and each D3 model you name, using the pinned prod LiteLLM image
and the same code, tier descriptions, timeout, and heuristic fallback as the
gateways. Reports how often each classifier picks the labelled tier, how often
it routes too low (quality risk) or too high (cost), and, for D3, how often it
answered, fell back, and how fast. Prompt text is never printed or written.

Options:
  --prompts FILE        Private JSONL file (mode 0600) of labelled prompts:
                        {"id": "...", "prompt": "...", "system_prompt": "...",
                         "expected_tier": "SIMPLE|MEDIUM|COMPLEX|REASONING",
                         "acceptable_tiers": ["..."]}
                        id, system_prompt, and acceptable_tiers are optional.
  --d3 NAME=MODEL@URL   A D3 model to include, for example
                        d3-mini=vllm-sr/d3-mini@http://d3-mini:8000. Repeat
                        for several models. URL must be reachable on
                        --network. The key is D3_DECISION_API_KEY.
  --network NAME        Docker network for the classifier container. Default:
                        the stack's d3-local network with --d3, otherwise none.
  --timeout-ms N        D3 timeout, 100-30000. Default:
                        CODING_ROUTER_CLASSIFIER_TIMEOUT_MS, else 3000.
  --image IMAGE         LiteLLM image. Default: litellm-prod's pinned image.
  --report FILE         Also write per-prompt results (ids and tiers only) and
                        the summary as JSON, with mode 0600.
  --runner-log          Show the classifier container's own log. It can
                        contain LiteLLM warnings; it never contains prompts
                        unless LiteLLM itself logs them.
  -h, --help            Show this help text.
"""

TIERS = ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING")
SEVERITY = {tier: index for index, tier in enumerate(TIERS)}
LOCAL_CLASSIFIERS = ("heuristic", "heuristic_v2")
D3_ANSWER_CAUSE = "jev_classifier"
MAX_CASES = 5000
MAX_PROMPT_CHARS = 1_000_000
RUNNER = ROOT / "scripts" / "coding_router_eval_runner.py"
CONTAINER_RUNNER = "/eval/runner.py"
CONTAINER_PYTHON = "/app/.venv/bin/python"
NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    system_prompt: str | None
    expected: str
    acceptable: frozenset[str]


@dataclass(frozen=True)
class D3Endpoint:
    name: str
    model: str
    api_base: str


def _plain_name(value: str) -> bool:
    return 0 < len(value) <= 64 and set(value) <= NAME_CHARS


def read_cases(path: Path) -> list[Case]:
    """Read and validate the labelled prompts; refuse a file others can read."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"prompt file not found: {path}")
    if not stat.S_ISREG(info.st_mode):
        fail(f"prompt file must be a regular file, not a symbolic link: {path}")
    if info.st_mode & 0o077:
        fail(f"prompt file holds prompts and must not be group- or world-readable; run chmod 600 {path}")
    cases: list[Case] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        where = f"{path.name}:{number}"
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            fail(f"{where}: not a JSON object")
        if not isinstance(record, dict):
            fail(f"{where}: not a JSON object")
        unknown = set(record) - {"id", "prompt", "system_prompt", "expected_tier", "acceptable_tiers"}
        if unknown:
            fail(f"{where}: unknown fields: {', '.join(sorted(unknown))}")
        case_id = str(record.get("id", number))
        if not _plain_name(case_id):
            fail(f"{where}: id must be 1-64 letters, digits, '.', '_', or '-'")
        if case_id in seen:
            fail(f"{where}: duplicate id {case_id}")
        seen.add(case_id)
        prompt, system_prompt = record.get("prompt"), record.get("system_prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
            fail(f"{where}: prompt must be non-empty text of at most {MAX_PROMPT_CHARS} characters")
        if system_prompt is not None and (not isinstance(system_prompt, str) or len(system_prompt) > MAX_PROMPT_CHARS):
            fail(f"{where}: system_prompt must be text of at most {MAX_PROMPT_CHARS} characters")
        expected = record.get("expected_tier")
        if expected not in TIERS:
            fail(f"{where}: expected_tier must be one of {', '.join(TIERS)}")
        acceptable = record.get("acceptable_tiers", [])
        if not isinstance(acceptable, list) or any(tier not in TIERS for tier in acceptable):
            fail(f"{where}: acceptable_tiers must be a list of {', '.join(TIERS)}")
        cases.append(Case(case_id, prompt, system_prompt or None, expected, frozenset(acceptable)))
    if not cases:
        fail(f"prompt file has no prompts: {path}")
    if len(cases) > MAX_CASES:
        fail(f"prompt file has more than {MAX_CASES} prompts; split it")
    return cases


def parse_d3(value: str) -> D3Endpoint:
    """Parse NAME=MODEL@URL; the URL is a base URL without credentials or path."""
    name, separator, rest = value.partition("=")
    # Model names never contain "@"; a URL may (user info), and is then refused.
    model, at, url = rest.partition("@")
    if not separator or not at or not _plain_name(name) or name in LOCAL_CLASSIFIERS:
        fail(f"--d3 must be NAME=MODEL@URL with a plain NAME other than {', '.join(LOCAL_CLASSIFIERS)}: {value}")
    if not model or len(model) > 128 or not set(model) <= NAME_CHARS | {"/"}:
        fail(f"--d3 MODEL must be a model name such as vllm-sr/d3-nano: {value}")
    parsed = urlsplit(url)
    try:
        port_is_valid = parsed.port is None or parsed.port > 0
    except ValueError:
        port_is_valid = False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or not port_is_valid
        or parsed.username
        or parsed.password
        or parsed.path.rstrip("/")
        or parsed.query
        or parsed.fragment
    ):
        fail(f"--d3 URL must be an http(s) base URL without credentials or a path: {value}")
    return D3Endpoint(name, model, url.rstrip("/"))


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def summarise(cases: Sequence[Case], results: Sequence[Mapping[str, Any]], d3_names: Sequence[str]) -> list[dict[str, Any]]:
    """Score every classifier's tiers against the labels."""
    by_id = {case.id: case for case in cases}
    summaries: list[dict[str, Any]] = []
    for name in (*LOCAL_CLASSIFIERS, *d3_names):
        rows = [row for row in results if row.get("classifier") == name and row.get("id") in by_id]
        exact = acceptable = under = over = errors = 0
        confusion: dict[str, Counter[str]] = {tier: Counter() for tier in TIERS}
        for row in rows:
            case, tier = by_id[str(row["id"])], row.get("tier")
            if tier not in SEVERITY:
                errors += 1
                confusion[case.expected]["none"] += 1
                continue
            confusion[case.expected][tier] += 1
            exact += tier == case.expected
            acceptable += tier == case.expected or tier in case.acceptable
            if tier != case.expected and tier not in case.acceptable:
                under += SEVERITY[tier] < SEVERITY[case.expected]
                over += SEVERITY[tier] > SEVERITY[case.expected]
        summary: dict[str, Any] = {
            "classifier": name,
            "prompts": len(rows),
            "exact": exact,
            "acceptable": acceptable,
            "under_routed": under,
            "over_routed": over,
            "errors": errors,
            "confusion": {tier: dict(counts) for tier, counts in confusion.items()},
        }
        if name in d3_names:
            answered = [row for row in rows if row.get("cause") == D3_ANSWER_CAUSE]
            latencies = [float(row["latency_ms"]) for row in answered]
            answered_exact = sum(
                1 for row in answered if row.get("tier") == by_id[str(row["id"])].expected
            )
            summary.update(
                {
                    "d3_answered": len(answered),
                    "d3_answered_exact": answered_exact,
                    "fallbacks": dict(
                        Counter(
                            str(row.get("fallback_reason") or "other")
                            for row in rows
                            if row.get("cause") != D3_ANSWER_CAUSE and row.get("tier") in SEVERITY
                        )
                    ),
                    "latency_ms_p50": percentile(latencies, 0.50),
                    "latency_ms_p95": percentile(latencies, 0.95),
                    "latency_ms_max": max(latencies) if latencies else None,
                }
            )
        summaries.append(summary)
    return summaries


def _share(count: int, total: int) -> str:
    return f"{100 * count / total:5.1f}%" if total else "    -"


def summary_lines(summaries: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        f"{'Classifier':<16} {'Exact':>6} {'Accept':>6} {'Under':>6} {'Over':>6} {'Errors':>6}",
    ]
    for item in summaries:
        total = int(item["prompts"])
        lines.append(
            f"{item['classifier']:<16} {_share(item['exact'], total):>6} {_share(item['acceptable'], total):>6} "
            f"{_share(item['under_routed'], total):>6} {_share(item['over_routed'], total):>6} {item['errors']:>6}"
        )
    for item in summaries:
        if "d3_answered" not in item:
            continue
        answered, total = int(item["d3_answered"]), int(item["prompts"])
        fallbacks = ", ".join(f"{reason} {count}" for reason, count in sorted(item["fallbacks"].items())) or "none"
        p50, p95 = item["latency_ms_p50"], item["latency_ms_p95"]
        lines.append(
            f"{item['classifier']}: D3 answered {answered}/{total} ({_share(answered, total).strip()}), "
            f"exact on those {_share(item['d3_answered_exact'], answered).strip()}; "
            f"fell back to the heuristic: {fallbacks}; latency p50 "
            + (f"{p50:.0f} ms, p95 {p95:.0f} ms" if p50 is not None else "n/a")
        )
    for item in summaries:
        lines.append(f"{item['classifier']} confusion (rows: labelled tier, columns: chosen tier):")
        columns = (*TIERS, "none")
        lines.append("  " + f"{'':<10}" + "".join(f"{column:>10}" for column in columns))
        for tier in TIERS:
            counts = item["confusion"][tier]
            lines.append("  " + f"{tier:<10}" + "".join(f"{counts.get(column, 0):>10}" for column in columns))
    return lines


def stack_defaults() -> tuple[str, str]:
    """Return (litellm-prod's image, the d3-local network name) from the rendered config."""
    config = json.loads(compose_capture(["--profile", "prod", "--profile", "d3", "config", "--format", "json"]))
    image = config.get("services", {}).get("litellm-prod", {}).get("image")
    network = config.get("networks", {}).get("d3-local", {}).get("name")
    if not isinstance(image, str) or not isinstance(network, str):
        fail("could not read litellm-prod's image or the d3-local network from the Compose configuration")
    return image, network


def run_classifiers(
    cases: Sequence[Case],
    endpoints: Sequence[D3Endpoint],
    timeout_ms: int,
    image: str,
    network: str,
    show_log: bool,
) -> dict[str, Any]:
    request = {
        "cases": [
            {"id": case.id, "prompt": case.prompt, "system_prompt": case.system_prompt} for case in cases
        ],
        "d3": [{"name": item.name, "model": item.model, "api_base": item.api_base} for item in endpoints],
        "timeout_ms": timeout_ms,
    }
    environment = dict(os.environ)
    if endpoints:
        environment["D3_DECISION_API_KEY"] = env_or_dotenv("D3_DECISION_API_KEY", "")
        if not environment["D3_DECISION_API_KEY"]:
            fail("D3_DECISION_API_KEY is not set in the environment or .env")
    command = [
        "docker", "run", "--rm", "-i",
        "--network", network,
        "--read-only", "--tmpfs", "/tmp:mode=1777",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        # The value comes from this process's environment, not the command line.
        "--env", "D3_DECISION_API_KEY",
        "--env", "HOME=/tmp",
        "--env", "LITELLM_LOCAL_MODEL_COST_MAP=True",
        "--volume", f"{RUNNER}:{CONTAINER_RUNNER}:ro",
        "--entrypoint", CONTAINER_PYTHON,
        image, "-I", CONTAINER_RUNNER,
    ]
    calls = len(cases) * (len(LOCAL_CLASSIFIERS) + len(endpoints))
    budget = 300 + len(cases) * len(endpoints) * (timeout_ms / 1000 + 5) + calls
    try:
        result = subprocess.run(
            command,
            input=json.dumps(request),
            capture_output=True,
            text=True,
            env=environment,
            shell=False,
            check=False,
            timeout=budget,
        )
    except FileNotFoundError:
        fail("docker is required")
    except subprocess.TimeoutExpired:
        fail(f"the classifier container did not finish within {budget:.0f} seconds")
    if show_log and result.stderr.strip():
        LOG.info(result.stderr.rstrip())
    if result.returncode:
        fail(
            f"the classifier container failed (exit code {result.returncode}); "
            "rerun with --runner-log to see its output"
        )
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("the classifier container returned no JSON; rerun with --runner-log")
    if not isinstance(output, dict) or not isinstance(output.get("results"), list):
        fail("the classifier container returned an unexpected result")
    return output


def parse_arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False, usage=argparse.SUPPRESS)
    parser.add_argument("--prompts", type=Path)
    parser.add_argument("--d3", action="append", default=[])
    parser.add_argument("--network")
    parser.add_argument("--timeout-ms")
    parser.add_argument("--image")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--runner-log", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    options, unknown = parser.parse_known_args(arguments)
    if options.help:
        print(USAGE, end="")
        raise SystemExit(0)
    if unknown or options.prompts is None:
        fail("usage: scripts/compare-coding-router-classifiers.py --prompts FILE [OPTIONS]; see --help", 2)
    return options


def main(arguments: Sequence[str] | None = None) -> None:
    options = parse_arguments(arguments)
    cases = read_cases(options.prompts)
    endpoints = [parse_d3(value) for value in options.d3]
    if len({item.name for item in endpoints}) != len(endpoints):
        fail("each --d3 NAME must be unique", 2)
    raw_timeout = options.timeout_ms or env_or_dotenv("CODING_ROUTER_CLASSIFIER_TIMEOUT_MS", "3000")
    if not raw_timeout.isdigit() or not 100 <= int(raw_timeout) <= 30000:
        fail("--timeout-ms must be a whole number from 100 to 30000", 2)
    require_docker(daemon=True)
    image, d3_network = options.image, None
    if not image or (endpoints and not options.network):
        default_image, d3_network = stack_defaults()
        image = image or default_image
    network = options.network or (d3_network if endpoints else "none")
    LOG.info(
        f"Classifying {len(cases)} prompts with {', '.join([*LOCAL_CLASSIFIERS, *(item.name for item in endpoints)])} "
        f"(D3 timeout {raw_timeout} ms, network {network})"
    )
    output = run_classifiers(cases, endpoints, int(raw_timeout), image, network, options.runner_log)
    summaries = summarise(cases, output["results"], [item.name for item in endpoints])
    LOG.info(f"LiteLLM {output.get('litellm_version', 'unknown')} ({image})")
    for line in summary_lines(summaries):
        LOG.info(line)
    if options.report:
        expected = {case.id: case.expected for case in cases}
        report = {
            "litellm_version": output.get("litellm_version"),
            "image": image,
            "timeout_ms": int(raw_timeout),
            "d3": [{"name": item.name, "model": item.model, "api_base": item.api_base} for item in endpoints],
            "summary": summaries,
            "results": [
                {**row, "expected_tier": expected[row["id"]]}
                for row in output["results"]
                if row.get("id") in expected
            ],
        }
        write_private_file(options.report, json.dumps(report, indent=2) + "\n")
        LOG.info(f"PASS: wrote {options.report}")


if __name__ == "__main__":
    run_main(main)
