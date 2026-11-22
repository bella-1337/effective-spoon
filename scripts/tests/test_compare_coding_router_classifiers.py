from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from maintenance_common import ScriptError  # noqa: E402

EXAMPLE = SCRIPTS / "tests" / "fixtures" / "coding-router-eval.example.jsonl"
KEY = "k" * 64


def load_script() -> ModuleType:
    path = SCRIPTS / "compare-coding-router-classifiers.py"
    spec = importlib.util.spec_from_file_location("compare_coding_router_classifiers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CompareClassifiersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_script()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def prompts(self, lines: list[Any], mode: int = 0o600) -> Path:
        path = self.directory / "prompts.jsonl"
        path.write_text(
            "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines),
            encoding="utf-8",
        )
        path.chmod(mode)
        return path

    def test_reads_the_example_file(self) -> None:
        path = self.prompts(EXAMPLE.read_text(encoding="utf-8").splitlines())

        cases = self.module.read_cases(path)

        self.assertEqual([case.id for case in cases][:2], ["greeting", "rename-variable"])
        self.assertEqual(cases[1].acceptable, frozenset({"MEDIUM"}))
        self.assertEqual(cases[3].system_prompt, "You are a coding agent working in a Python repository.")

    def test_refuses_a_readable_or_linked_prompt_file(self) -> None:
        readable = self.prompts([{"prompt": "hi", "expected_tier": "SIMPLE"}], mode=0o644)
        with self.assertRaisesRegex(ScriptError, "chmod 600"):
            self.module.read_cases(readable)
        link = self.directory / "link.jsonl"
        readable.chmod(0o600)
        link.symlink_to(readable)
        with self.assertRaisesRegex(ScriptError, "symbolic link"):
            self.module.read_cases(link)

    def test_refuses_invalid_records(self) -> None:
        cases = (
            (["not json"], "not a JSON object"),
            ([[1, 2]], "not a JSON object"),
            ([{"prompt": "hi", "expected_tier": "EASY"}], "expected_tier"),
            ([{"prompt": " ", "expected_tier": "SIMPLE"}], "prompt must be"),
            ([{"prompt": "hi", "expected_tier": "SIMPLE", "label": "x"}], "unknown fields"),
            ([{"prompt": "hi", "expected_tier": "SIMPLE", "acceptable_tiers": "MEDIUM"}], "acceptable_tiers"),
            ([{"id": "a b", "prompt": "hi", "expected_tier": "SIMPLE"}], "id must be"),
            (
                [{"id": "a", "prompt": "hi", "expected_tier": "SIMPLE"}] * 2,
                "duplicate id",
            ),
            ([""], "no prompts"),
        )
        for lines, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ScriptError, message):
                self.module.read_cases(self.prompts(lines))

    def test_parses_d3_endpoints(self) -> None:
        endpoint = self.module.parse_d3("d3-nano=vllm-sr/d3-nano@http://d3-nano:8000/")

        self.assertEqual(
            (endpoint.name, endpoint.model, endpoint.api_base),
            ("d3-nano", "vllm-sr/d3-nano", "http://d3-nano:8000"),
        )
        for value in (
            "d3-nano",
            "heuristic=vllm-sr/d3-nano@http://d3-nano:8000",
            "d3=vllm-sr/d3-nano@http://user:secret@d3-nano:8000",
            "d3=vllm-sr/d3-nano@http://d3-nano:8000/v1/systemone",
            "d3=vllm-sr/d3-nano@ftp://d3-nano",
            "d3=vllm-sr/d3-nano@http://d3-nano:99999",
            "d3=@http://d3-nano:8000",
        ):
            with self.subTest(value=value), self.assertRaises(ScriptError):
                self.module.parse_d3(value)

    def test_scores_tiers_fallbacks_and_latency(self) -> None:
        cases = self.module.read_cases(
            self.prompts(
                [
                    {"id": "a", "prompt": "x", "expected_tier": "SIMPLE"},
                    {"id": "b", "prompt": "x", "expected_tier": "COMPLEX", "acceptable_tiers": ["REASONING"]},
                    {"id": "c", "prompt": "x", "expected_tier": "REASONING"},
                    {"id": "d", "prompt": "x", "expected_tier": "MEDIUM"},
                ]
            )
        )
        d3 = "jev_classifier"
        results = [
            {"id": "a", "classifier": "heuristic", "tier": "SIMPLE", "cause": "heuristic_scorer"},
            {"id": "b", "classifier": "heuristic", "tier": "MEDIUM", "cause": "heuristic_scorer"},
            {"id": "c", "classifier": "heuristic", "tier": "SIMPLE", "cause": "heuristic_scorer"},
            {"id": "d", "classifier": "heuristic", "tier": "COMPLEX", "cause": "heuristic_scorer"},
            {"id": "a", "classifier": "d3", "tier": "SIMPLE", "cause": d3, "latency_ms": 10},
            {"id": "b", "classifier": "d3", "tier": "REASONING", "cause": d3, "latency_ms": 30},
            {"id": "c", "classifier": "d3", "tier": "SIMPLE", "cause": "heuristic_scorer",
             "fallback_reason": "timeout", "latency_ms": 3000},
            {"id": "d", "classifier": "d3", "tier": None, "cause": None, "error": "RuntimeError",
             "latency_ms": 1},
        ]

        heuristic, _, d3_summary = self.module.summarise(cases, results, ["d3"])

        self.assertEqual(
            (heuristic["exact"], heuristic["acceptable"], heuristic["under_routed"], heuristic["over_routed"]),
            (1, 1, 2, 1),
        )
        self.assertEqual(heuristic["confusion"]["COMPLEX"], {"MEDIUM": 1})
        self.assertEqual(
            (d3_summary["exact"], d3_summary["acceptable"], d3_summary["under_routed"], d3_summary["errors"]),
            (1, 2, 1, 1),
        )
        self.assertEqual((d3_summary["d3_answered"], d3_summary["d3_answered_exact"]), (2, 1))
        self.assertEqual(d3_summary["fallbacks"], {"timeout": 1})
        self.assertEqual((d3_summary["latency_ms_p50"], d3_summary["latency_ms_p95"]), (10.0, 30.0))
        self.assertEqual(d3_summary["confusion"]["MEDIUM"], {"none": 1})
        lines = self.module.summary_lines([heuristic, d3_summary])
        self.assertTrue(any(line.startswith("d3: D3 answered 2/4") for line in lines), lines)

    def test_runs_the_classifiers_in_a_locked_down_container(self) -> None:
        cases = self.module.read_cases(
            self.prompts([{"id": "a", "prompt": "secret prompt text", "expected_tier": "SIMPLE"}])
        )
        endpoint = self.module.parse_d3("d3=vllm-sr/d3-nano@http://d3-nano:8000")
        output = {"litellm_version": "1", "results": []}
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(output), stderr="")
        with (
            patch.object(self.module, "env_or_dotenv", return_value=KEY),
            patch.object(self.module.subprocess, "run", return_value=completed) as run,
        ):
            result = self.module.run_classifiers(cases, [endpoint], 3000, "image@sha256:x", "net", False)

        self.assertEqual(result, output)
        command = run.call_args.args[0]
        for expected in (
            ["--network", "net"],
            ["--read-only"],
            ["--cap-drop", "ALL"],
            ["--security-opt", "no-new-privileges"],
            ["--user", "65534:65534"],
            ["--env", "D3_DECISION_API_KEY"],
        ):
            self.assertTrue(
                any(command[index : index + len(expected)] == expected for index in range(len(command))),
                (expected, command),
            )
        self.assertNotIn(KEY, " ".join(command))
        self.assertEqual(run.call_args.kwargs["env"]["D3_DECISION_API_KEY"], KEY)
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertIn("secret prompt text", run.call_args.kwargs["input"])

    def test_runner_failure_does_not_echo_its_output(self) -> None:
        cases = self.module.read_cases(self.prompts([{"prompt": "secret prompt text", "expected_tier": "SIMPLE"}]))
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="secret prompt text")
        with patch.object(self.module.subprocess, "run", return_value=failed):
            with self.assertRaises(ScriptError) as raised:
                self.module.run_classifiers(cases, [], 3000, "image", "none", False)
        self.assertNotIn("secret prompt text", str(raised.exception))
        self.assertIn("--runner-log", str(raised.exception))

    def test_report_holds_ids_and_tiers_but_no_prompt_text(self) -> None:
        prompts = self.prompts([{"id": "a", "prompt": "secret prompt text", "expected_tier": "SIMPLE"}])
        report = self.directory / "report.json"
        output = {
            "litellm_version": "1.108.9",
            "results": [
                {"id": "a", "classifier": name, "tier": "SIMPLE", "cause": "heuristic_scorer",
                 "fallback_reason": None, "error": None, "latency_ms": 1.0}
                for name in ("heuristic", "heuristic_v2")
            ],
        }
        with (
            patch.object(self.module, "require_docker"),
            patch.object(self.module, "run_classifiers", return_value=output),
            patch.dict(os.environ, {"CODING_ROUTER_CLASSIFIER_TIMEOUT_MS": "3000"}),
            patch.object(self.module.LOG, "info"),
        ):
            self.module.main(
                ["--prompts", str(prompts), "--image", "image", "--network", "none", "--report", str(report)]
            )

        text = report.read_text(encoding="utf-8")
        self.assertNotIn("secret prompt text", text)
        data = json.loads(text)
        self.assertEqual(data["results"][0]["expected_tier"], "SIMPLE")
        self.assertEqual(data["summary"][0]["exact"], 1)
        self.assertEqual(report.stat().st_mode & 0o777, 0o600)

    def test_rejects_a_timeout_outside_the_gateway_range(self) -> None:
        prompts = self.prompts([{"prompt": "hi", "expected_tier": "SIMPLE"}])
        for value in ("99", "30001", "3s"):
            with self.subTest(value=value), self.assertRaisesRegex(ScriptError, "--timeout-ms"):
                self.module.main(["--prompts", str(prompts), "--timeout-ms", value])


if __name__ == "__main__":
    unittest.main()
