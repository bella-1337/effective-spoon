"""The on-host D3-mini profile's preflight refuses bad settings before any start."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPTIONAL_COMPOSE = ROOT / "compose.optional.yml"
VALID_KEY = "k" * 64


def preflight_script() -> str:
    """Return d3-mini-preflight's shell script as the container receives it."""
    lines = OPTIONAL_COMPOSE.read_text(encoding="utf-8").splitlines()
    start = lines.index("  d3-mini-preflight:")
    block_start = next(
        index for index in range(start, len(lines)) if lines[index].strip() == "- |"
    )
    indent = len(lines[block_start]) - len(lines[block_start].lstrip()) + 2
    script: list[str] = []
    for line in lines[block_start + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        script.append(line[indent:])
    # Compose turns $$ into a literal $ before the shell sees it.
    return "\n".join(script).replace("$$", "$") + "\n"


@unittest.skipUnless(shutil.which("sh") and shutil.which("grep"), "needs a POSIX shell")
class D3PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = preflight_script()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.bin = Path(directory.name) / "bin"
        self.bin.mkdir()
        self.calls = Path(directory.name) / "calls"
        self.owner = Path(directory.name) / "owner"
        self.owner.write_text("10001:10001\n", encoding="utf-8")
        # stat reports the recorded owner; chmod and chown only record calls.
        self.write_tool("stat", f'cat "{self.owner}"')
        for tool in ("chmod", "chown"):
            self.write_tool(tool, f'echo "{tool} $*" >> "{self.calls}"')

    def write_tool(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def run_preflight(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "D3_DECISION_API_BASE": "http://d3-mini:8000",
            "D3_DECISION_API_KEY": VALID_KEY,
            "D3_MAX_INPUT_TOKENS": "4096",
            "D3_GPU_MEMORY_UTILIZATION": "0.90",
            "D3_GPU_DEVICE_ID": "0",
            **overrides,
        }
        return subprocess.run(
            ["sh", "-ec", self.script],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_accepts_valid_settings_and_range_limits(self) -> None:
        for overrides in (
            {},
            {"D3_MAX_INPUT_TOKENS": "512"},
            {"D3_MAX_INPUT_TOKENS": "8192"},
            {"D3_GPU_MEMORY_UTILIZATION": "0.5"},
            {"D3_GPU_MEMORY_UTILIZATION": "0.95"},
            {"D3_GPU_DEVICE_ID": "GPU-0a1b2c3d-4e5f"},
        ):
            with self.subTest(overrides=overrides):
                result = self.run_preflight(**overrides)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls.exists(), "an owned volume must not be changed")

    def test_rejects_invalid_settings(self) -> None:
        cases = (
            ({"D3_DECISION_API_BASE": "https://d3-mini.example.test:8443"}, "D3_DECISION_API_BASE"),
            ({"D3_DECISION_API_BASE": ""}, "D3_DECISION_API_BASE"),
            ({"D3_DECISION_API_KEY": ""}, "D3_DECISION_API_KEY"),
            ({"D3_DECISION_API_KEY": "short"}, "D3_DECISION_API_KEY"),
            ({"D3_DECISION_API_KEY": "replace_with_random_32_byte_d3_api_key"}, "D3_DECISION_API_KEY"),
            ({"D3_DECISION_API_KEY": f"{VALID_KEY}\n{VALID_KEY}"}, "D3_DECISION_API_KEY"),
            ({"D3_DECISION_API_KEY": f"{VALID_KEY}$"}, "D3_DECISION_API_KEY"),
            ({"D3_MAX_INPUT_TOKENS": "511"}, "D3_MAX_INPUT_TOKENS"),
            ({"D3_MAX_INPUT_TOKENS": "8193"}, "D3_MAX_INPUT_TOKENS"),
            ({"D3_MAX_INPUT_TOKENS": "04096"}, "D3_MAX_INPUT_TOKENS"),
            ({"D3_MAX_INPUT_TOKENS": "4096 "}, "D3_MAX_INPUT_TOKENS"),
            ({"D3_MAX_INPUT_TOKENS": "4k"}, "D3_MAX_INPUT_TOKENS"),
            ({"D3_GPU_MEMORY_UTILIZATION": "0.49"}, "D3_GPU_MEMORY_UTILIZATION"),
            ({"D3_GPU_MEMORY_UTILIZATION": "0.96"}, "D3_GPU_MEMORY_UTILIZATION"),
            ({"D3_GPU_MEMORY_UTILIZATION": "1"}, "D3_GPU_MEMORY_UTILIZATION"),
            ({"D3_GPU_MEMORY_UTILIZATION": "90%"}, "D3_GPU_MEMORY_UTILIZATION"),
            ({"D3_GPU_DEVICE_ID": "0,1"}, "D3_GPU_DEVICE_ID"),
            ({"D3_GPU_DEVICE_ID": "all"}, "D3_GPU_DEVICE_ID"),
        )
        for overrides, variable in cases:
            with self.subTest(overrides=overrides):
                result = self.run_preflight(**overrides)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn(variable, result.stderr)
                self.assertNotIn(VALID_KEY, result.stderr)
        self.assertFalse(self.calls.exists(), "a rejected setting must not touch the volume")

    def test_hands_a_new_volume_to_the_d3_user(self) -> None:
        self.owner.write_text("0:0\n", encoding="utf-8")

        result = self.run_preflight()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls.read_text(encoding="utf-8").splitlines(),
            ["chmod 0750 /models", "chown 10001:10001 /models"],
        )


if __name__ == "__main__":
    unittest.main()
