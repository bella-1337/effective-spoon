from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from maintenance_common import ScriptError  # noqa: E402

SPEC = importlib.util.spec_from_file_location("ci_images", SCRIPTS / "ci" / "images.py")
assert SPEC is not None and SPEC.loader is not None
images = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = images
SPEC.loader.exec_module(images)
monitor = images.monitor

TODAY = date(2026, 11, 3)
TOOLS_BUILD = {"context": "/repo", "dockerfile": "tools/Dockerfile", "labels": {"a": "1"}}


def finding(identifier: str, severity: str, fixed: str = "", package: str = "openssl") -> dict:
    return {
        "VulnerabilityID": identifier,
        "Severity": severity,
        "PkgName": package,
        "InstalledVersion": "1.0",
        "FixedVersion": fixed,
    }


def exception(service: str, vulnerability: str, expires: date = date(2026, 12, 31)):
    return monitor.ExceptionRule(
        service=service,
        vulnerability=vulnerability,
        package="openssl",
        installed_version="1.0",
        expires=expires,
        reason="reviewed",
        reviewed_by="tester",
    )


TOOLS = images.LocalImage(
    reference="tools:1",
    build_service="setup",
    services=("bridge", "setup"),
    labels={},
)


class LocalImageTests(unittest.TestCase):
    def test_shared_image_is_built_once_and_lists_every_consumer(self) -> None:
        config = {
            "services": {
                "setup": {"image": "tools:1", "build": TOOLS_BUILD},
                "bridge": {"image": "tools:1", "build": TOOLS_BUILD},
                "postgres": {"image": "postgres@sha256:" + "a" * 64},
            }
        }
        [image] = images.local_images(config)
        self.assertEqual(image.reference, "tools:1")
        self.assertEqual(image.build_service, "bridge")
        self.assertEqual(image.services, ("bridge", "setup"))
        self.assertEqual(image.labels, {"a": "1"})

    def test_conflicting_builds_for_one_tag_fail(self) -> None:
        config = {
            "services": {
                "setup": {"image": "tools:1", "build": TOOLS_BUILD},
                "bridge": {"image": "tools:1", "build": {**TOOLS_BUILD, "dockerfile": "x"}},
            }
        }
        with self.assertRaisesRegex(ScriptError, "different settings"):
            images.local_images(config)

    def test_missing_local_builds_fail(self) -> None:
        with self.assertRaisesRegex(ScriptError, "no locally built images"):
            images.local_images({"services": {"postgres": {"image": "postgres:16"}}})

    def test_label_mismatches_report_missing_and_drifted_labels(self) -> None:
        self.assertEqual(
            images.label_mismatches({"a": "1", "b": "2"}, {"a": "1", "b": "3"}),
            ["b: expected '2', found '3'"],
        )
        self.assertEqual(
            images.label_mismatches({"c": ""}, {}),
            ["c: expected '', found None"],
        )


class PolicyTests(unittest.TestCase):
    def violations(self, findings: list[dict], kev: set[str], rules: list) -> list:
        payload = {"Results": [{"Vulnerabilities": findings}]}
        return images.policy_violations(TOOLS, payload, kev, rules, TODAY)

    def test_fixable_critical_and_kev_are_rejected(self) -> None:
        found = self.violations(
            [
                finding("CVE-2026-0001", "CRITICAL", fixed="1.1"),
                finding("CVE-2026-0002", "MEDIUM"),
                finding("CVE-2026-0003", "CRITICAL"),
                finding("CVE-2026-0004", "HIGH", fixed="1.1"),
            ],
            {"CVE-2026-0002"},
            [],
        )
        self.assertEqual(
            [(item.vulnerability, item.kev) for item in found],
            [("CVE-2026-0001", False), ("CVE-2026-0002", True)],
        )
        self.assertEqual(found[0].services, ("bridge", "setup"))

    def test_exception_must_cover_every_consumer_service(self) -> None:
        critical = [finding("CVE-2026-0001", "CRITICAL", fixed="1.1")]
        partial = self.violations(critical, set(), [exception("setup", "CVE-2026-0001")])
        self.assertEqual(partial[0].services, ("bridge",))
        self.assertEqual(
            self.violations(critical, set(), [exception("*", "CVE-2026-0001")]),
            [],
        )

    def test_expired_exception_is_not_applied(self) -> None:
        found = self.violations(
            [finding("CVE-2026-0001", "CRITICAL", fixed="1.1")],
            set(),
            [exception("*", "CVE-2026-0001", expires=date(2026, 11, 2))],
        )
        self.assertEqual(len(found), 1)

    def test_invalid_trivy_json_fails_closed(self) -> None:
        with self.assertRaisesRegex(ScriptError, "invalid scan JSON"):
            images.policy_violations(TOOLS, {"Results": None}, set(), [], TODAY)

    def test_report_only_scan_warns_without_failing(self) -> None:
        payload = {"Results": [{"Vulnerabilities": [finding("CVE-2026-0001", "CRITICAL", "1.1")]}]}
        with (
            patch.object(monitor, "load_exceptions", return_value=[]),
            patch.object(monitor, "fetch_kev", return_value=(set(), b"", 0)),
            patch.object(images, "image_labels", return_value={}),
            patch.object(images, "scan_image", return_value=payload),
        ):
            with self.assertLogs(images.LOG, "WARNING"):
                images.scan([TOOLS], report_only=True)
            with self.assertRaisesRegex(ScriptError, "1 unexcepted"):
                images.scan([TOOLS], report_only=False)


class CommandTests(unittest.TestCase):
    def test_trivy_uses_the_monitor_pin_and_daemon_socket(self) -> None:
        with patch.dict(os.environ, {"CI_DOCKER_SOCKET": "/run/docker.sock"}, clear=False):
            command = images.trivy_scan_command("tools:1")
        self.assertIn(monitor.TRIVY_IMAGE, command)
        self.assertIn("/run/docker.sock:/var/run/docker.sock", command)
        self.assertEqual(command[-1], "tools:1")

    def test_repository_metadata_comes_from_the_ci_platform(self) -> None:
        github = {
            "GITHUB_SHA": "a" * 40,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "org/stack",
        }
        with patch.dict(os.environ, github, clear=True):
            images.set_repository_metadata()
            self.assertEqual(os.environ["STACK_REPOSITORY_REVISION"], "a" * 40)
            self.assertEqual(os.environ["STACK_REPOSITORY_URL"], "https://github.com/org/stack")
        gitlab = {"CI_COMMIT_SHA": "b" * 40, "CI_PROJECT_URL": "https://gitlab.test/org/stack"}
        with patch.dict(os.environ, gitlab, clear=True):
            images.set_repository_metadata()
            self.assertEqual(os.environ["STACK_REPOSITORY_REVISION"], "b" * 40)
            self.assertEqual(os.environ["STACK_REPOSITORY_URL"], "https://gitlab.test/org/stack")
        explicit = {"STACK_REPOSITORY_REVISION": "c" * 40, "CI_COMMIT_SHA": "b" * 40}
        with patch.dict(os.environ, explicit, clear=True):
            images.set_repository_metadata()
            self.assertEqual(os.environ["STACK_REPOSITORY_REVISION"], "c" * 40)
            self.assertNotIn("STACK_REPOSITORY_URL", os.environ)

    def test_malformed_repository_url_is_not_recorded(self) -> None:
        # Local emulators can derive GITHUB_REPOSITORY from a filesystem path.
        emulated = {
            "GITHUB_SHA": "a" * 40,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "/home/user/checkouts/stack",
        }
        with patch.dict(os.environ, emulated, clear=True):
            images.set_repository_metadata()
            self.assertNotIn("STACK_REPOSITORY_URL", os.environ)
        with patch.dict(os.environ, {"GITHUB_SHA": "a" * 40, "CI_PROJECT_URL": "http://x"}, clear=True):
            images.set_repository_metadata()
            self.assertNotIn("STACK_REPOSITORY_URL", os.environ)


if __name__ == "__main__":
    unittest.main()
