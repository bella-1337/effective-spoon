from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import maintenance_common as common  # noqa: E402


class MaintenanceCommonTests(unittest.TestCase):
    def test_compose_command_loads_existing_environment_files_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("CORE=value\n", encoding="utf-8")
            (root / ".env.optional").write_text("OPTIONAL=value\n", encoding="utf-8")

            with patch.object(common, "ROOT", root):
                command = common.compose_command(["--profile", "grafana", "config"])

        self.assertEqual(
            command,
            [
                "docker",
                "compose",
                "--env-file",
                str(root / ".env"),
                "--env-file",
                str(root / ".env.optional"),
                "--profile",
                "grafana",
                "config",
            ],
        )

    def test_compose_command_uses_compose_defaults_when_environment_files_are_missing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            with patch.object(common, "ROOT", root):
                command = common.compose_command(["config"])

        self.assertEqual(command, ["docker", "compose", "config"])

    def test_compose_command_rejects_symlinked_environment_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("VALUE=secret\n", encoding="utf-8")

            for name in (".env", ".env.optional"):
                candidate = root / name
                candidate.symlink_to(target)
                with self.subTest(name=name), patch.object(common, "ROOT", root):
                    with self.assertRaisesRegex(
                        common.ScriptError, "environment file is unsafe.*install -m 600"
                    ):
                        common.compose_command(["config"])
                candidate.unlink()

    def test_compose_command_rejects_optional_environment_without_core(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.optional").write_text("OPTIONAL=value\n", encoding="utf-8")

            with patch.object(common, "ROOT", root):
                with self.assertRaisesRegex(
                    common.ScriptError,
                    r"\.env\.optional cannot be loaded without a regular \.env",
                ):
                    common.compose_command(["config"])

    def test_compose_command_does_not_mix_live_and_explicit_environment_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("CORE=live\n", encoding="utf-8")
            (root / ".env.optional").write_text("OPTIONAL=live\n", encoding="utf-8")

            with patch.object(common, "ROOT", root):
                command = common.compose_command(
                    ["--env-file", "test.env", "--env-file=test.optional.env", "config"]
                )

        self.assertEqual(
            command,
            [
                "docker",
                "compose",
                "--env-file",
                "test.env",
                "--env-file=test.optional.env",
                "config",
            ],
        )


if __name__ == "__main__":
    unittest.main()
