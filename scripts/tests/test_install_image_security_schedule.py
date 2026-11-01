from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from maintenance_common import ScriptError

SPEC = importlib.util.spec_from_file_location(
    "install_image_security_schedule",
    SCRIPTS / "install-image-security-schedule.py",
)
assert SPEC is not None and SPEC.loader is not None
schedule = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = schedule
SPEC.loader.exec_module(schedule)


class InstallImageSecurityScheduleTests(unittest.TestCase):
    def test_build_units_use_non_root_account_and_hardening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = schedule.ServiceAccount(
                name="scanner",
                group="scanner",
                uid=1234,
                gid=1234,
                home=root / "home",
            )
            service, timer = schedule.build_units(
                account=account,
                uv=Path("/usr/local/bin/uv"),
                docker=Path("/usr/bin/docker"),
                hour=3,
                minute=30,
                state_root=root / "state",
                report_root=root / "reports",
                metrics_root=root / "metrics",
                policy=root / "exceptions.toml",
                project="test-project",
            )

        self.assertIn("User=scanner", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        working_directory = next(
            line for line in service.splitlines() if line.startswith("WorkingDirectory=")
        )
        self.assertNotIn('"', working_directory)
        self.assertTrue(working_directory.removeprefix("WorkingDirectory=").startswith("/"))
        self.assertIn('ExecStart="/usr/local/bin/uv" "run" "--script"', service)
        exec_lines = [line for line in service.splitlines() if line.startswith("ExecStart=")]
        self.assertEqual(len(exec_lines), 2)
        self.assertTrue(exec_lines[0].endswith('monitor-image-vulnerabilities.py"'))
        self.assertTrue(exec_lines[1].endswith('generate-image-sbom.py"'))
        self.assertNotIn("WantedBy=multi-user.target", service)
        self.assertIn("OnCalendar=*-*-* 03:30:00", timer)
        self.assertIn("Persistent=true", timer)

    def test_install_writes_units_and_enables_timer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service_path = root / schedule.SERVICE_NAME
            timer_path = root / schedule.TIMER_NAME
            state = root / "state"
            reports = root / "reports"
            metrics = root / "metrics"
            policy = root / "exceptions.toml"
            policy.write_text("version = 1\n", encoding="utf-8")
            account = schedule.ServiceAccount(
                name="scanner",
                group="scanner",
                uid=os.getuid(),
                gid=os.getgid(),
                home=root,
            )
            calls: list[list[str]] = []

            def fake_run(arguments, **_kwargs):
                calls.append([str(item) for item in arguments])
                return subprocess.CompletedProcess(arguments, 0, "", "")

            environment = {
                "IMAGE_SECURITY_STATE_DIR": str(state),
                "IMAGE_SECURITY_REPORT_DIR": str(reports),
                "IMAGE_SECURITY_METRICS_DIR": str(metrics),
                "IMAGE_SECURITY_EXCEPTIONS_FILE": str(policy),
                "COMPOSE_PROJECT_NAME": "test-project",
            }
            executable = Path(sys.executable)
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(schedule.sys, "platform", "linux"),
                patch.object(schedule.os, "geteuid", return_value=0),
                patch.object(schedule, "service_account", return_value=account),
                patch.object(
                    schedule,
                    "executable_path",
                    side_effect=[executable, executable, executable],
                ),
                patch.object(schedule, "systemctl_path", return_value=executable),
                patch.object(
                    schedule,
                    "unit_paths",
                    return_value=(service_path, timer_path),
                ),
                patch.object(schedule, "run", side_effect=fake_run),
            ):
                schedule.install(
                    3,
                    30,
                    "scanner",
                    executable,
                    "test-project",
                    state,
                    reports,
                    metrics,
                    policy,
                )

            self.assertEqual(service_path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(timer_path.stat().st_mode & 0o777, 0o644)
            self.assertIn("COMPOSE_PROJECT_NAME=test-project", service_path.read_text())
            self.assertEqual(state.stat().st_mode & 0o777, 0o700)
            self.assertEqual(reports.stat().st_mode & 0o777, 0o700)
            self.assertEqual(metrics.stat().st_mode & 0o777, 0o755)
            self.assertEqual(
                calls[-1][-3:],
                ["enable", "--now", schedule.TIMER_NAME],
            )

    def test_install_rejects_invalid_time(self) -> None:
        with (
            patch.object(schedule.sys, "platform", "linux"),
            patch.object(schedule.os, "geteuid", return_value=0),
        ):
            with self.assertRaises(ScriptError):
                schedule.install(
                    24,
                    0,
                    "scanner",
                    Path("/usr/bin/uv"),
                    None,
                    None,
                    None,
                    None,
                    None,
                )

    def test_install_requires_root(self) -> None:
        with (
            patch.object(schedule.sys, "platform", "linux"),
            patch.object(schedule.os, "geteuid", return_value=1000),
        ):
            with self.assertRaises(ScriptError):
                schedule.install(
                    2,
                    45,
                    "scanner",
                    Path("/usr/bin/uv"),
                    None,
                    None,
                    None,
                    None,
                    None,
                )

    def test_schedule_rejects_non_linux(self) -> None:
        with patch.object(schedule.sys, "platform", "darwin"):
            with self.assertRaises(ScriptError):
                schedule.status()

    def test_systemd_quote_escapes_specifiers(self) -> None:
        self.assertEqual(
            schedule.systemd_quote('/srv/100%/"scan"'),
            '"/srv/100%%/\\"scan\\""',
        )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and shutil.which("systemd-analyze"),
        "systemd-analyze is available only on Linux systemd hosts",
    )
    def test_generated_units_pass_systemd_verify(self) -> None:
        import grp
        import pwd

        account_record = pwd.getpwuid(os.getuid())
        account = schedule.ServiceAccount(
            name=account_record.pw_name,
            group=grp.getgrgid(account_record.pw_gid).gr_name,
            uid=account_record.pw_uid,
            gid=account_record.pw_gid,
            home=Path(account_record.pw_dir),
        )
        uv = Path(shutil.which("uv") or sys.executable)
        docker = Path(shutil.which("docker") or "/usr/bin/docker")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, timer = schedule.build_units(
                account=account,
                uv=uv,
                docker=docker,
                hour=2,
                minute=45,
                state_root=root / "state",
                report_root=root / "reports",
                metrics_root=root / "metrics",
                policy=root / "exceptions.toml",
                project="litellm-stack",
            )
            service_path = root / schedule.SERVICE_NAME
            timer_path = root / schedule.TIMER_NAME
            service_path.write_text(service, encoding="utf-8")
            timer_path.write_text(timer, encoding="utf-8")
            result = subprocess.run(
                [
                    shutil.which("systemd-analyze") or "systemd-analyze",
                    "verify",
                    str(service_path),
                    str(timer_path),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_build_units_can_disable_sbom_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = schedule.ServiceAccount(
                name="scanner", group="scanner", uid=1234, gid=1234, home=root / "home"
            )
            service, _timer = schedule.build_units(
                account=account,
                uv=Path("/usr/local/bin/uv"),
                docker=Path("/usr/bin/docker"),
                hour=3,
                minute=30,
                state_root=root / "state",
                report_root=root / "reports",
                metrics_root=root / "metrics",
                policy=root / "exceptions.toml",
                project="test-project",
                sbom=False,
            )
        self.assertEqual(service.count("ExecStart="), 1)
        self.assertNotIn("generate-image-sbom.py", service)

if __name__ == "__main__":
    unittest.main()
