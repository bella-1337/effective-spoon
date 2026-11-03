from __future__ import annotations

import io
import os
import stat
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


class FileLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        for stream, replacement in (("stdout", self.stdout), ("stderr", self.stderr)):
            patcher = patch.object(sys, stream, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def environment(self, **values: str) -> dict[str, str]:
        return {common.LOG_DIR_VARIABLE: str(self.root / "logs"), common.FILE_LOGGING_VARIABLE: "true", **values}

    def test_console_output_is_unchanged_and_the_file_is_private_and_readable(self) -> None:
        with patch.dict(os.environ, self.environment()):
            with common.file_logging("/repo/scripts/backup-databases.py") as path:
                common.LOG.info("Backing up litellm_prod from postgres...")
                common.LOG.info("")
                common.LOG.warning("WARNING: skipping litellm_dev because service postgres is unavailable")
                common.LOG.error("FAIL: report line one\nreport line two")

        self.assertEqual(
            self.stdout.getvalue(),
            "Backing up litellm_prod from postgres...\n\n",
        )
        self.assertEqual(
            self.stderr.getvalue(),
            "WARNING: skipping litellm_dev because service postgres is unavailable\n"
            "FAIL: report line one\nreport line two\n",
        )
        assert path is not None
        self.assertEqual(path, self.root / "logs" / "backup-databases.log")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        pattern = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4} "
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 4)
        self.assertRegex(lines[0], pattern + r"INFO    \[backup-databases\] Backing up litellm_prod from postgres\.\.\.$")
        self.assertRegex(lines[1], pattern + r"WARNING \[backup-databases\] skipping litellm_dev because")
        self.assertRegex(lines[2], pattern + r"ERROR   \[backup-databases\] FAIL: report line one$")
        self.assertRegex(lines[3], pattern + r"ERROR   \[backup-databases\] report line two$")
        self.assertEqual(len(common.LOG.handlers), 1)

    def test_run_main_logs_script_errors_and_unexpected_tracebacks_to_the_file(self) -> None:
        def main() -> None:
            raise ValueError("unexpected state")

        with patch.dict(os.environ, self.environment()):
            with self.assertRaises(SystemExit) as raised:
                common.run_main(main)
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("ERROR: unexpected ValueError: unexpected state", self.stderr.getvalue())
        content = (self.root / "logs" / "test_maintenance_common.log").read_text(encoding="utf-8")
        self.assertIn("ERROR   [test_maintenance_common] unexpected ValueError: unexpected state", content)
        self.assertIn("ValueError: unexpected state", content.splitlines()[-1])

    def test_logs_created_under_sudo_belong_to_the_invoking_operator(self) -> None:
        with patch.object(common.os, "geteuid", return_value=0):
            with patch.dict(os.environ, {"SUDO_UID": "1001", "SUDO_GID": "1002"}):
                self.assertEqual(common._sudo_owner(), (1001, 1002))
            with patch.dict(os.environ, {"SUDO_UID": "0", "SUDO_GID": "0"}):
                self.assertIsNone(common._sudo_owner())
            with patch.dict(os.environ, {"SUDO_UID": "x", "SUDO_GID": "1002"}):
                self.assertIsNone(common._sudo_owner())
        with patch.object(common.os, "geteuid", return_value=1001):
            with patch.dict(os.environ, {"SUDO_UID": "1001", "SUDO_GID": "1002"}):
                self.assertIsNone(common._sudo_owner())

        with patch.dict(os.environ, self.environment()), \
                patch.object(common, "_sudo_owner", return_value=(1001, 1002)), \
                patch.object(common.os, "chown") as chown, \
                patch.object(common.os, "fchown") as fchown:
            with common.file_logging("backup-databases.py"):
                common.LOG.info("owned by the operator")
        chown.assert_called_once_with(self.root / "logs", 1001, 1002, follow_symlinks=False)
        self.assertEqual(fchown.call_args.args[1:], (1001, 1002))

    def test_file_logging_can_be_disabled(self) -> None:
        with patch.dict(os.environ, self.environment(**{common.FILE_LOGGING_VARIABLE: "false"})):
            with common.file_logging("backup-databases.py") as path:
                common.LOG.info("console only")
        self.assertIsNone(path)
        self.assertFalse((self.root / "logs").exists())
        self.assertEqual(self.stdout.getvalue(), "console only\n")

    def test_unusable_log_locations_fall_back_to_the_console(self) -> None:
        target = self.root / "elsewhere"
        target.mkdir()
        (self.root / "logs").symlink_to(target, target_is_directory=True)
        with patch.dict(os.environ, self.environment()):
            with common.file_logging("backup-databases.py") as path:
                common.LOG.info("still printed")
        self.assertIsNone(path)
        self.assertEqual(list(target.iterdir()), [])
        self.assertIn("WARNING: file logging is disabled for this run", self.stderr.getvalue())
        self.assertEqual(self.stdout.getvalue(), "still printed\n")

    def test_a_symlinked_log_file_is_never_followed(self) -> None:
        logs = self.root / "logs"
        logs.mkdir(mode=0o700)
        victim = self.root / "victim.txt"
        victim.write_text("original\n", encoding="utf-8")
        (logs / "backup-databases.log").symlink_to(victim)
        with patch.dict(os.environ, self.environment()):
            with common.file_logging("backup-databases.py") as path:
                common.LOG.info("not written through the link")
        self.assertIsNone(path)
        self.assertEqual(victim.read_text(encoding="utf-8"), "original\n")


if __name__ == "__main__":
    unittest.main()
