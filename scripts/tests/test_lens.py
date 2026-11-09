from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
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


def load_script() -> ModuleType:
    path = SCRIPTS / "lens.py"
    spec = importlib.util.spec_from_file_location("lens_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ready_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "url": "https://lens.example.test",
        "configured": True,
        "connected": True,
        "release": "1.108.0-rc.1",
        "status": {"storage_ready": True, "credentials_ready": True, "public_contract": 1},
    }
    body.update(overrides)
    return body


class LensSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_script()
        self.counter = 0

    def generate(self) -> str:
        self.counter += 1
        return f"{self.counter:064x}"

    def test_fills_empty_and_missing_values_without_rotating_existing_ones(self) -> None:
        content = "# comment\nLITELLM_LENS_SERVICE_TOKEN=\nLENS_GATEWAY_SECRET=" + "k" * 40 + "\nOTHER=1"
        updated, generated, values = self.module.fill_secrets(
            content, ["LITELLM_LENS_SERVICE_TOKEN", "LENS_GATEWAY_SECRET", "LENS_ADMIN_TOKEN"], self.generate
        )

        self.assertEqual(generated, ["LITELLM_LENS_SERVICE_TOKEN", "LENS_ADMIN_TOKEN"])
        self.assertEqual(values["LENS_GATEWAY_SECRET"], "k" * 40)
        self.assertEqual(
            updated.splitlines(),
            [
                "# comment",
                f"LITELLM_LENS_SERVICE_TOKEN={1:064x}",
                "LENS_GATEWAY_SECRET=" + "k" * 40,
                "OTHER=1",
                f"LENS_ADMIN_TOKEN={2:064x}",
            ],
        )
        self.assertTrue(updated.endswith("\n"))

    def test_duplicate_assignments_fail(self) -> None:
        with self.assertRaisesRegex(ScriptError, "more than once"):
            self.module.fill_secrets("LENS_ADMIN_TOKEN=\nLENS_ADMIN_TOKEN=x\n", ["LENS_ADMIN_TOKEN"], self.generate)

    def test_secret_values_must_be_long_enough_and_distinct(self) -> None:
        base = {
            "LITELLM_LENS_SERVICE_TOKEN": "s" * 64,
            "LENS_GATEWAY_SECRET": "g" * 64,
            "LENS_ADMIN_TOKEN": "a" * 64,
            "LENS_CLICKHOUSE_PASSWORD": "c" * 64,
        }
        self.module.validate_secret_values(base)
        self.module.validate_secret_values({**base, "LENS_ADMIN_TOKEN": "'" + "q" * 40 + "'"})
        for overrides, message in (
            ({"LENS_GATEWAY_SECRET": "short"}, "between 32 and 512"),
            ({"LENS_CLICKHOUSE_PASSWORD": "x" * 513}, "between 32 and 512"),
            ({"LENS_GATEWAY_SECRET": "s" * 64}, "must differ"),
            ({"LENS_ADMIN_TOKEN": '"' + "s" * 64 + '"'}, "must differ"),
        ):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ScriptError, message):
                self.module.validate_secret_values({**base, **overrides})

    def test_generate_secrets_writes_private_files_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core, optional = root / ".env", root / ".env.optional"
            core.write_text("STACK_DOMAIN=example.test\nLITELLM_LENS_SERVICE_TOKEN=\nLENS_GATEWAY_SECRET=\n")
            optional.write_text("LENS_HOST=lens.${STACK_DOMAIN}\nLENS_ADMIN_TOKEN=\n")
            core.chmod(0o644)
            with patch.object(self.module.LOG, "info"):
                self.module.generate_secrets(core, optional)

            core_values = dict(line.split("=", 1) for line in core.read_text().splitlines())
            optional_values = dict(line.split("=", 1) for line in optional.read_text().splitlines())
            for name in ("LITELLM_LENS_SERVICE_TOKEN", "LENS_GATEWAY_SECRET"):
                self.assertRegex(core_values[name], r"^[0-9a-f]{64}$")
            for name in ("LENS_ADMIN_TOKEN", "LENS_CLICKHOUSE_PASSWORD"):
                self.assertRegex(optional_values[name], r"^[0-9a-f]{64}$")
            self.assertEqual(optional_values["LENS_HOST"], "lens.${STACK_DOMAIN}")
            self.assertEqual(stat.S_IMODE(core.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(optional.stat().st_mode), 0o600)

            before = (core.read_text(), optional.read_text())
            with patch.object(self.module.LOG, "info"), patch.object(
                self.module, "write_private_file"
            ) as write:
                self.module.generate_secrets(core, optional)
            write.assert_not_called()
            self.assertEqual((core.read_text(), optional.read_text()), before)

    def test_generate_secrets_requires_regular_env_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core, optional = root / ".env", root / ".env.optional"
            core.write_text("LITELLM_LENS_SERVICE_TOKEN=\n")
            with self.assertRaisesRegex(ScriptError, r"copy \.env\.optional\.example"):
                self.module.generate_secrets(core, optional)
            target = root / "target.env"
            target.write_text("")
            optional.symlink_to(target)
            with self.assertRaisesRegex(ScriptError, "unsafe"):
                self.module.generate_secrets(core, optional)

    def test_invalid_existing_values_leave_files_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core, optional = root / ".env", root / ".env.optional"
            core.write_text("LITELLM_LENS_SERVICE_TOKEN=too-short\nLENS_GATEWAY_SECRET=\n")
            optional.write_text("LENS_ADMIN_TOKEN=\n")
            with patch.object(self.module.LOG, "info"), self.assertRaisesRegex(
                ScriptError, "LITELLM_LENS_SERVICE_TOKEN must contain"
            ):
                self.module.generate_secrets(core, optional)
            self.assertEqual(core.read_text(), "LITELLM_LENS_SERVICE_TOKEN=too-short\nLENS_GATEWAY_SECRET=\n")
            self.assertEqual(optional.read_text(), "LENS_ADMIN_TOKEN=\n")


class LensStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_script()

    def states(self, *, lens: bool = True, **extra: str) -> dict[str, dict[str, str]]:
        states = {"litellm-prod": {"state": "running", "health": "healthy"}}
        if lens:
            states["lens"] = {"state": "running", "health": "healthy"}
            states["lens-clickhouse"] = {"state": "running", "health": "healthy"}
        for service, health in extra.items():
            states[service.replace("_", "-")] = {"state": "running", "health": health}
        return states

    def run_status(
        self,
        states: dict[str, dict[str, str]],
        lens_responses: dict[str, tuple[int, Any]],
        gateway: tuple[int, Any],
    ) -> tuple[bool, list[str]]:
        messages: list[str] = []

        def lens_request(method: str, path: str, **_: Any) -> tuple[int, Any]:
            return lens_responses[path]

        with (
            patch.object(self.module, "lens_request", side_effect=lens_request),
            patch.object(self.module, "gateway_connection", return_value=gateway),
            patch.object(self.module.LOG, "info", side_effect=messages.append),
            patch.object(self.module.LOG, "error", side_effect=messages.append),
        ):
            return self.module.check_status(states), messages

    def test_connected_profile_passes(self) -> None:
        ok, messages = self.run_status(
            self.states(),
            {"/health/ready": (200, None), "/lens/tracing/keys": (401, None)},
            (200, ready_body()),
        )

        self.assertTrue(ok, messages)
        self.assertIn("PASS: litellm-prod is connected to Lens (release 1.108.0-rc.1)", messages)

    def test_unready_or_open_lens_fails(self) -> None:
        ok, messages = self.run_status(
            self.states(lens_clickhouse="starting"),
            {"/health/ready": (503, None), "/lens/tracing/keys": (200, [])},
            (200, ready_body()),
        )

        self.assertFalse(ok)
        self.assertIn("FAIL: lens-clickhouse is running (health: starting)", messages)
        self.assertIn("FAIL: Lens /health/ready returned HTTP 503 (expected 200)", messages)
        self.assertTrue(any("unauthenticated Lens API" in m for m in messages), messages)

    def test_gateway_that_started_before_lens_is_reported(self) -> None:
        body = ready_body(configured=False, connected=False, status={})
        ok, messages = self.run_status(
            self.states(),
            {"/health/ready": (200, None), "/lens/tracing/keys": (401, None)},
            (200, body),
        )

        self.assertFalse(ok)
        failure = next(m for m in messages if m.startswith("FAIL: litellm-prod"))
        self.assertIn("LITELLM_LENS_URL is not set", failure)
        self.assertIn("public contract is None", failure)
        self.assertTrue(any("recreate it" in m for m in messages))

    def test_gateways_must_not_keep_lens_configured_without_profile(self) -> None:
        ok, messages = self.run_status(self.states(lens=False), {}, (200, ready_body(configured=False)))
        self.assertTrue(ok, messages)
        self.assertIn("PASS: litellm-prod runs without Lens while the lens profile is stopped", messages)

        ok, messages = self.run_status(self.states(lens=False), {}, (200, ready_body()))
        self.assertFalse(ok)
        self.assertTrue(any("still has a Lens connection" in m for m in messages), messages)

    def test_connection_problems_reject_non_objects(self) -> None:
        self.assertEqual(
            self.module.connection_problems(None),
            ["the gateway returned no Lens service description"],
        )
        self.assertEqual(self.module.connection_problems(ready_body()), [])


class LensSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_script()

    def test_otlp_trace_uses_requested_identifiers(self) -> None:
        trace = self.module.otlp_trace("a" * 32, "b" * 16, 1_000)
        span = trace["resourceSpans"][0]["scopeSpans"][0]["spans"][0]

        self.assertEqual((span["traceId"], span["spanId"]), ("a" * 32, "b" * 16))
        self.assertEqual(span["endTimeUnixNano"], str(1_000 + 1_000_000))
        self.assertIn(
            {"key": "gen_ai.agent.name", "value": {"stringValue": self.module.SMOKE_AGENT}},
            span["attributes"],
        )

    def smoke_with(self, responses: dict[tuple[str, str], tuple[int, Any]]) -> list[tuple[str, str, str]]:
        calls: list[tuple[str, str, str]] = []

        def lens_request(method: str, path: str, *, auth: str = "admin", **_: Any) -> tuple[int, Any]:
            calls.append((method, path, auth))
            if method == "GET" and path.startswith("/v1/traces/"):
                status, body = responses[("GET", "/v1/traces/")]
                return status, body if body != "echo" else {"trace_id": path.rsplit("/", 1)[1]}
            return responses[(method, path)]

        with (
            patch.object(self.module, "compose_states", return_value={"lens": {"state": "running"}}),
            patch.object(self.module, "check_status", return_value=True),
            patch.object(self.module, "lens_request", side_effect=lens_request),
            patch.object(self.module.time, "sleep"),
            patch.object(self.module.LOG, "info"),
            patch.object(self.module.LOG, "error"),
        ):
            try:
                self.module.smoke()
            except ScriptError as error:
                calls.append(("ERROR", str(error), ""))
        return calls

    def test_smoke_ingests_reads_back_and_revokes_key(self) -> None:
        calls = self.smoke_with(
            {
                ("POST", "/lens/tracing/keys"): (200, {"key": "k" * 40, "record": {"id": "key-1"}}),
                ("POST", "/v1/traces"): (200, {}),
                ("GET", "/v1/traces/"): (200, "echo"),
                ("DELETE", "/lens/tracing/keys/key-1"): (200, True),
            }
        )

        self.assertEqual(calls[0], ("POST", "/lens/tracing/keys", "admin"))
        self.assertEqual(calls[1], ("POST", "/v1/traces", "key"))
        self.assertEqual(calls[2][0:1] + calls[2][2:], ("GET", "admin"))
        self.assertEqual(calls[-1], ("DELETE", "/lens/tracing/keys/key-1", "admin"))

    def test_smoke_revokes_key_when_ingestion_fails(self) -> None:
        calls = self.smoke_with(
            {
                ("POST", "/lens/tracing/keys"): (200, {"key": "k" * 40, "record": {"id": "key-2"}}),
                ("POST", "/v1/traces"): (401, None),
                ("DELETE", "/lens/tracing/keys/key-2"): (200, True),
            }
        )

        self.assertIn(("DELETE", "/lens/tracing/keys/key-2", "admin"), calls)
        self.assertEqual(calls[-1][0], "ERROR")
        self.assertIn("rejected the smoke trace (HTTP 401)", calls[-1][1])
        self.assertEqual(sum(call[:2] == ("POST", "/v1/traces") for call in calls), 30)

    def test_smoke_requires_running_lens(self) -> None:
        with patch.object(self.module, "compose_states", return_value={}):
            with self.assertRaisesRegex(ScriptError, "not running"):
                self.module.smoke()


@unittest.skipUnless(shutil.which("sh"), "POSIX sh is required")
class LensRequestScriptTests(unittest.TestCase):
    """Run the in-container request script against a recording curl stub."""

    def setUp(self) -> None:
        self.module = load_script()

    def run_script(self, method: str, path: str, auth: str, stdin: str) -> tuple[list[str], str, str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            curl = root / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$@" > "{root}/argv"\n'
                'while [ "$#" -gt 0 ]; do\n'
                '  case "$1" in --header) cp "${2#@}" '
                f'"{root}/headers"; shift ;; esac\n'
                "  shift\n"
                "done\n"
                f'cat > "{root}/stdin"\n'
                "printf '{}\\n200'\n"
            )
            curl.chmod(0o755)
            environment = {
                "PATH": f"{root}:{os.environ['PATH']}",
                "LENS_ADMIN_TOKEN": "admin-secret-value",
                "TMPDIR": str(root),
            }
            result = subprocess.run(
                ["sh", "-c", self.module.LENS_REQUEST_SCRIPT, "lens-request", method, path, auth],
                input=stdin,
                capture_output=True,
                text=True,
                env=environment,
                check=True,
            )
            self.assertEqual(result.stdout, "{}\n200")
            argv = (root / "argv").read_text().splitlines()
            headers = (root / "headers").read_text()
            body = (root / "stdin").read_text()
            self.assertEqual(sorted(p.name for p in root.iterdir() if p.name.startswith("tmp")), [])
        return argv, headers, body

    def test_admin_token_reaches_curl_only_through_header_file(self) -> None:
        argv, headers, _ = self.run_script("GET", "/v1/traces/abc", "admin", "")

        self.assertIn("Authorization: Bearer admin-secret-value", headers)
        self.assertFalse(any("admin-secret-value" in argument for argument in argv), argv)
        self.assertNotIn("--data-binary", argv)
        self.assertEqual(argv[-1], "http://127.0.0.1:4318/v1/traces/abc")

    def test_tracing_key_is_read_from_stdin_before_the_body(self) -> None:
        payload = json.dumps({"resourceSpans": []})
        argv, headers, body = self.run_script("POST", "/v1/traces", "key", "tracing-key-value\n" + payload)

        self.assertIn("Authorization: Bearer tracing-key-value", headers)
        self.assertFalse(any("tracing-key-value" in argument for argument in argv), argv)
        self.assertIn("--data-binary", argv)
        self.assertEqual(body, payload)

    def test_unknown_authentication_mode_is_rejected(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self.run_script("GET", "/", "other", "")
        self.assertEqual(raised.exception.returncode, 64)


class LensCommandLineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_script()

    def test_secret_options_and_errors(self) -> None:
        core, optional = self.module.parse_secret_options(
            ["--env-file", "a.env", "--optional-env-file", "b.env"]
        )
        self.assertEqual((core, optional), (Path("a.env"), Path("b.env")))
        for arguments in (["--other"], ["--env-file"], ["--env-file", ""]):
            with self.subTest(arguments=arguments), patch("sys.stderr"), patch.object(
                self.module.LOG, "error"
            ), self.assertRaises(SystemExit) as raised:
                self.module.parse_secret_options(arguments)
            self.assertEqual(raised.exception.code, 2)

    def test_maintenance_dispatcher_lists_lens_script(self) -> None:
        source = (SCRIPTS / "maintenance.py").read_text(encoding="utf-8")
        self.assertIn('"lens.py"', source)
        self.assertIn('invoke("lens.py", args)', source)
        self.assertTrue(os.access(SCRIPTS / "lens.py", os.X_OK))


if __name__ == "__main__":
    unittest.main()
