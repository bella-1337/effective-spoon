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


    def test_env_values_expand_stack_domain_like_compose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "STACK_DOMAIN=corp.internal\n"
                "LITELLM_PROD_HOST=llm.${STACK_DOMAIN}\n"
                "LITELLM_DEV_HOST=llm-dev.$STACK_DOMAIN\n"
                "FALLBACK=${UNSET_VALUE:-x.${STACK_DOMAIN}}\n"
                "LITERAL='llm.${STACK_DOMAIN}'\n"
                "ESCAPED=price$$\n",
                encoding="utf-8",
            )
            (root / ".env.optional.example").write_text(
                "GRAFANA_HOST=grafana.${STACK_DOMAIN}\n", encoding="utf-8"
            )
            (root / ".env.optional").write_text(
                "GRAFANA_HOST=dash.${STACK_DOMAIN}\n", encoding="utf-8"
            )

            with patch.object(common, "ROOT", root), patch.dict(
                common.os.environ, {}, clear=True
            ):
                self.assertEqual(common.public_host("LITELLM_PROD_HOST"), "llm.corp.internal")
                self.assertEqual(common.public_host("LITELLM_DEV_HOST"), "llm-dev.corp.internal")
                self.assertEqual(common.public_host("GRAFANA_HOST"), "dash.corp.internal")
                self.assertEqual(common.env_or_dotenv("FALLBACK", ""), "x.corp.internal")
                self.assertEqual(common.env_or_dotenv("LITERAL", ""), "llm.${STACK_DOMAIN}")
                self.assertEqual(common.env_or_dotenv("ESCAPED", ""), "price$")
                with patch.dict(common.os.environ, {"STACK_DOMAIN": "shell.example"}):
                    self.assertEqual(
                        common.public_host("LITELLM_PROD_HOST"), "llm.shell.example"
                    )

    def test_public_host_rejects_unresolved_or_malformed_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "STACK_DOMAIN=\n"
                "LITELLM_PROD_HOST=llm.${STACK_DOMAIN}\n"
                "LITELLM_DEV_HOST=https://llm-dev.corp.internal/\n"
                "LOOP_HOST=${LOOP_HOST}\n",
                encoding="utf-8",
            )

            with patch.object(common, "ROOT", root), patch.dict(
                common.os.environ, {}, clear=True
            ):
                for name in ("LITELLM_PROD_HOST", "LITELLM_DEV_HOST", "LOOP_HOST", "UNSET_HOST"):
                    with self.subTest(name=name), self.assertRaisesRegex(
                        common.ScriptError, f"{name}=.* is not a DNS hostname"
                    ):
                        common.public_host(name)

    def test_stack_env_exports_are_shell_quoted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "STACK_DOMAIN=corp.internal\n"
                + "".join(
                    f"{name}={name.lower().replace('_', '-')}.${{STACK_DOMAIN}}\n"
                    for name in common.PUBLIC_HOST_VARIABLES
                ),
                encoding="utf-8",
            )

            with patch.object(common, "ROOT", root), patch.dict(
                common.os.environ, {}, clear=True
            ):
                exports = common.stack_env_exports().splitlines()

        self.assertEqual(exports[0], "export STACK_DOMAIN=corp.internal")
        self.assertIn("export LITELLM_PROD_HOST=litellm-prod-host.corp.internal", exports)
        self.assertEqual(len(exports), 1 + len(common.PUBLIC_HOST_VARIABLES))

if __name__ == "__main__":
    unittest.main()
