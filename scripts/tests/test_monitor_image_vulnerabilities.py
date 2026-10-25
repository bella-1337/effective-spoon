from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "monitor_image_vulnerabilities",
    SCRIPTS / "monitor-image-vulnerabilities.py",
)
assert SPEC is not None and SPEC.loader is not None
monitor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = monitor
SPEC.loader.exec_module(monitor)


class MonitorImageVulnerabilitiesTests(unittest.TestCase):
    def test_default_scope_scans_all_running_services(self) -> None:
        arguments = argparse.Namespace(all=False, services=None)
        self.assertIsNone(monitor.selected_services(arguments))

    def test_explicit_scope_retains_requested_services(self) -> None:
        arguments = argparse.Namespace(
            all=False,
            services="litellm-dev,vllm-semantic-router",
        )
        self.assertEqual(
            monitor.selected_services(arguments),
            ("litellm-dev", "vllm-semantic-router"),
        )

    def test_exception_removes_finding_from_policy_but_not_report(self) -> None:
        payload = {
            "Results": [
                {
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2026-12345",
                            "PkgName": "example",
                            "InstalledVersion": "1.0",
                            "FixedVersion": "1.1",
                            "Severity": "CRITICAL",
                        }
                    ]
                }
            ]
        }
        rule = monitor.ExceptionRule(
            service="litellm-prod",
            vulnerability="CVE-2026-12345",
            package="example",
            installed_version="1.0",
            expires=date(2026, 12, 31),
            reason="test",
            reviewed_by="operator",
        )
        summary = monitor.summarize_scan(
            "litellm-prod",
            payload,
            {"CVE-2026-12345"},
            [rule],
            date(2026, 10, 24),
        )
        counts, kev, unexcepted_kev, unexcepted_critical, violations, findings = (
            summary
        )
        self.assertEqual(counts["CRITICAL|true"], 1)
        self.assertEqual(kev, 1)
        self.assertEqual(unexcepted_kev, 0)
        self.assertEqual(unexcepted_critical, 0)
        self.assertEqual(violations, 0)
        self.assertEqual(findings, 1)

    def test_expired_exception_does_not_suppress_policy(self) -> None:
        vulnerability = {
            "VulnerabilityID": "CVE-2026-12345",
            "PkgName": "example",
            "InstalledVersion": "1.0",
            "FixedVersion": "1.1",
            "Severity": "CRITICAL",
        }
        rule = monitor.ExceptionRule(
            service="*",
            vulnerability="CVE-2026-12345",
            package="*",
            installed_version="*",
            expires=date(2026, 10, 23),
            reason="expired",
            reviewed_by="operator",
        )
        summary = monitor.summarize_scan(
            "litellm-prod",
            {"Results": [{"Vulnerabilities": [vulnerability]}]},
            {"CVE-2026-12345"},
            [rule],
            date(2026, 10, 24),
        )
        self.assertEqual(summary[2:5], (1, 1, 1))

    def test_reference_cves_are_not_treated_as_aliases(self) -> None:
        vulnerability = {
            "VulnerabilityID": "CVE-2026-111943",
            "References": ["https://example.test/CVE-2026-78667"],
        }
        self.assertEqual(
            monitor.vulnerability_aliases(vulnerability),
            {"CVE-2026-111943"},
        )

    def test_platform_reference_selects_running_architecture(self) -> None:
        image = monitor.RunningImage(
            service="litellm-prod",
            configured_reference=(
                "ghcr.io/berriai/litellm:v1.106.3"
                "@sha256:" + "a" * 64
            ),
            local_image_id="sha256:" + "b" * 64,
            platform="linux/arm64",
        )
        manifest = (
            '{"manifests":['
            '{"digest":"sha256:' + "c" * 64 + '",'
            '"platform":{"os":"linux","architecture":"amd64"}},'
            '{"digest":"sha256:' + "d" * 64 + '",'
            '"platform":{"os":"linux","architecture":"arm64"}}]}'
        )
        completed = subprocess.CompletedProcess([], 0, manifest, "")
        with patch.object(monitor, "run", return_value=completed):
            reference = monitor.platform_reference(image, 10)
        self.assertEqual(
            reference,
            "ghcr.io/berriai/litellm@sha256:" + "d" * 64,
        )

    def test_metrics_emit_failure_sentinel_and_no_sample_timestamps(self) -> None:
        state = monitor.empty_state()
        state["last_run"] = {"timestamp": 100, "success": False}
        state["services"] = {
            "litellm-prod": {
                "status": "scan-failed",
                "scan_success": False,
                "last_success_timestamp": 0,
                "counts": {},
            },
            "litellm-dev": {
                "status": "not-applicable",
                "reason": "not-running",
            },
        }
        output = monitor.render_metrics(state)
        self.assertIn(
            'image_security_scan_success{service="litellm-prod"} 0',
            output,
        )
        self.assertIn(
            'image_security_images_unknown{reason="not-running",service="litellm-dev"} 1',
            output,
        )
        for line in output.splitlines():
            if line and not line.startswith("#"):
                self.assertLessEqual(len(line.rsplit(" ", 2)), 2)

    def test_atomic_write_enforces_metrics_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image-security.prom"
            monitor.atomic_write(path, "metric 1\n", 0o644)
            self.assertEqual(path.read_text(encoding="utf-8"), "metric 1\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertFalse(
                any(item.name.endswith(".tmp") for item in path.parent.iterdir())
            )

    def test_metrics_root_is_world_traversable_not_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics"
            with patch.dict(
                os.environ,
                {"IMAGE_SECURITY_METRICS_DIR": str(path)},
            ):
                resolved = monitor.metrics_root()
            self.assertEqual(resolved.stat().st_mode & 0o777, 0o755)

    def test_kernel_lock_is_released_after_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory)
            with self.assertRaises(RuntimeError):
                with monitor.exclusive_lock(state_root):
                    with self.assertRaises(monitor.ScriptError):
                        with monitor.exclusive_lock(state_root):
                            pass
                    raise RuntimeError("test")
            with monitor.exclusive_lock(state_root):
                self.assertTrue((state_root / "scan.lock").is_file())

    def test_enforce_fails_closed_on_unknown_coverage(self) -> None:
        self.assertFalse(
            monitor.scan_succeeded(
                operational_success=True,
                enforce=True,
                policy_failed=False,
                coverage_failed=True,
            )
        )
        self.assertTrue(
            monitor.scan_succeeded(
                operational_success=True,
                enforce=False,
                policy_failed=False,
                coverage_failed=True,
            )
        )

    def test_global_failure_invalidates_retained_service_success(self) -> None:
        state = monitor.empty_state()
        state["services"] = {
            "litellm-prod": {
                "status": "scanned-ok",
                "scan_success": True,
                "last_success_timestamp": 100,
                "counts": {"CRITICAL|true": 1},
            },
            "litellm-dev": {
                "status": "not-applicable",
                "reason": "not-running",
            },
        }
        monitor.invalidate_retained_services(state, 200, "feed failed")
        services = state["services"]
        self.assertFalse(services["litellm-prod"]["scan_success"])
        self.assertEqual(
            services["litellm-prod"]["last_success_timestamp"], 100
        )
        self.assertEqual(
            services["litellm-dev"]["status"], "not-applicable"
        )

    def test_partial_scope_preserves_unrequested_service_state(self) -> None:
        previous = {
            "litellm-prod": {
                "status": "scanned-ok",
                "policy_violations": 1,
            },
            "litellm-dev": {
                "status": "scanned-ok",
                "policy_violations": 2,
            },
        }
        partial = monitor.initial_service_state(previous, full_scope=False)
        partial["litellm-dev"] = {
            "status": "scanned-ok",
            "policy_violations": 0,
        }
        self.assertEqual(partial["litellm-prod"]["policy_violations"], 1)
        self.assertEqual(partial["litellm-dev"]["policy_violations"], 0)
        self.assertEqual(
            monitor.initial_service_state(previous, full_scope=True),
            {},
        )

    def test_trivy_runs_as_host_user_for_native_bind_permissions(self) -> None:
        with patch.dict(
            os.environ, {"IMAGE_SECURITY_TRIVY_EXECUTABLE": ""}, clear=False
        ):
            command = monitor.trivy_command(Path("/cache"), ["version"])
        self.assertIn("--user", command)
        self.assertIn(f"{os.getuid()}:{os.getgid()}", command)

    def test_local_trivy_avoids_nested_docker_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "trivy"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            with patch.dict(
                os.environ,
                {"IMAGE_SECURITY_TRIVY_EXECUTABLE": str(executable)},
                clear=False,
            ):
                command = monitor.trivy_command(
                    Path("/cache"), ["version", "--format", "json"]
                )
        self.assertEqual(
            command,
            [
                str(executable.resolve()),
                "--cache-dir",
                "/cache",
                "version",
                "--format",
                "json",
            ],
        )


if __name__ == "__main__":
    unittest.main()
