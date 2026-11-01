from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

SPEC = importlib.util.spec_from_file_location(
    "run_image_security_scheduler",
    SCRIPTS / "run-image-security-scheduler.py",
)
assert SPEC is not None and SPEC.loader is not None
scheduler = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scheduler
SPEC.loader.exec_module(scheduler)


class RunImageSecuritySchedulerTests(unittest.TestCase):
    def tearDown(self) -> None:
        scheduler.STOP_EVENT.clear()
        scheduler.ACTIVE_PROCESS = None

    def test_next_run_uses_same_day_before_schedule(self) -> None:
        now = datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)
        self.assertEqual(
            scheduler.next_run(now, 2, 45),
            datetime(2026, 10, 25, 2, 45, tzinfo=timezone.utc),
        )

    def test_next_run_rolls_to_following_day(self) -> None:
        now = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)
        self.assertEqual(
            scheduler.next_run(now, 2, 45),
            datetime(2026, 10, 26, 2, 45, tzinfo=timezone.utc),
        )

    def test_schedule_environment_is_validated(self) -> None:
        with patch.dict(
            os.environ,
            {"IMAGE_SECURITY_SCHEDULE_HOUR": "24"},
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                scheduler.bounded_environment_integer(
                    "IMAGE_SECURITY_SCHEDULE_HOUR", 2, 23
                )

    def test_local_timezone_uses_configured_iana_timezone(self) -> None:
        with patch.dict(os.environ, {"TZ": "Europe/Berlin"}, clear=False):
            self.assertEqual(scheduler.local_timezone().key, "Europe/Berlin")

    def test_local_timezone_rejects_unknown_timezone(self) -> None:
        with patch.dict(os.environ, {"TZ": "Europe/Unknown"}, clear=False):
            with self.assertRaises(RuntimeError):
                scheduler.local_timezone()

    def test_run_monitor_uses_current_python_without_shell(self) -> None:
        class CompletedProcess:
            returncode = 0

            def poll(self):
                return 0

        scanner = Path("/app/scripts/monitor-image-vulnerabilities.py")
        with patch.object(
            scheduler.subprocess,
            "Popen",
            return_value=CompletedProcess(),
        ) as popen:
            self.assertEqual(scheduler.run_monitor(scanner, ["--all"]), 0)
        popen.assert_called_once_with(
            [sys.executable, str(scanner), "--all"],
            shell=False,
            text=True,
        )

    def test_scheduled_jobs_generate_sboms_after_the_scan(self) -> None:
        scanner = Path("/app/scripts/monitor-image-vulnerabilities.py")
        generator = scanner.with_name("generate-image-sbom.py")
        with patch.object(scheduler, "run_monitor", side_effect=[1, 0]) as run_monitor:
            scheduler.run_scheduled_jobs(scanner, ["--all"], generator)
        self.assertEqual(
            [call.args for call in run_monitor.call_args_list],
            [(scanner, ["--all"]), (generator, [])],
        )

    def test_scheduled_jobs_skip_disabled_sbom_generation(self) -> None:
        scanner = Path("/app/scripts/monitor-image-vulnerabilities.py")
        with patch.object(scheduler, "run_monitor", return_value=0) as run_monitor:
            scheduler.run_scheduled_jobs(scanner, ["--all"], None)
        run_monitor.assert_called_once_with(scanner, ["--all"])


if __name__ == "__main__":
    unittest.main()
