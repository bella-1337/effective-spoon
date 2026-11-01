from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from maintenance_common import ScriptError  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "generate_image_sbom",
    SCRIPTS / "generate-image-sbom.py",
)
assert SPEC is not None and SPEC.loader is not None
sbom = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sbom
SPEC.loader.exec_module(sbom)
monitor = sbom.monitor

REGISTRY_DIGEST = "sha256:" + "a" * 64
LOCAL_DIGEST = "sha256:" + "b" * 64
SERIAL = "urn:uuid:6473021d-863a-4cec-ac15-8bf666d8ece9"
CYCLONEDX = json.dumps(
    {"bomFormat": "CycloneDX", "specVersion": "1.7", "serialNumber": SERIAL, "version": 1}
)
LICENCES = json.dumps(
    {
        "Results": [
            {
                "Licenses": [
                    {"Name": "GPL-2.0-only", "Category": "restricted"},
                    {"Name": "MIT", "Category": "notice"},
                    {"Name": "Custom", "Category": "unknown"},
                    {"Name": "MIT", "Category": "notice"},
                ]
            },
            {"Licenses": None},
        ]
    }
)


def registry_entry(*services: str) -> object:
    return sbom.ImageEntry(
        key=sbom.image_key("registry", REGISTRY_DIGEST),
        source="registry",
        digest=REGISTRY_DIGEST,
        configured_reference="prom/prometheus:v3.15.1@" + "sha256:" + "c" * 64,
        scan_reference="prom/prometheus@" + REGISTRY_DIGEST,
        local_image_id="sha256:" + "d" * 64,
        platform="linux/amd64",
        services=list(services),
    )


def local_entry(*services: str) -> object:
    return sbom.ImageEntry(
        key=sbom.image_key("local", LOCAL_DIGEST),
        source="local",
        digest=LOCAL_DIGEST,
        configured_reference="litellm-opensearch-tools:0.1.0",
        scan_reference="",
        local_image_id=LOCAL_DIGEST,
        platform="linux/arm64",
        services=list(services),
    )


class GenerateImageSbomTests(unittest.TestCase):
    def test_image_key_and_reference_parsing(self) -> None:
        self.assertEqual(sbom.image_key("local", LOCAL_DIGEST), "local-sha256-" + "b" * 64)
        with self.assertRaises(ScriptError):
            sbom.image_key("remote", LOCAL_DIGEST)
        with self.assertRaises(ScriptError):
            sbom.image_key("local", "sha256:short")
        self.assertEqual(
            sbom.split_reference("ghcr.io/berriai/litellm:v1.106.6@" + REGISTRY_DIGEST),
            ("ghcr.io/berriai/litellm", "v1.106.6"),
        )
        self.assertEqual(
            sbom.split_reference("localhost:5000/tools@" + REGISTRY_DIGEST),
            ("localhost:5000/tools", ""),
        )

    def test_cyclonedx_validation_returns_bom_link(self) -> None:
        self.assertEqual(
            sbom.validate_cyclonedx(CYCLONEDX, "svc"),
            ("urn:cdx:6473021d-863a-4cec-ac15-8bf666d8ece9/1", "1.7"),
        )
        for invalid in ("not json", "{}", json.dumps({"bomFormat": "CycloneDX", "serialNumber": "x", "version": 1})):
            with self.subTest(invalid=invalid), self.assertRaises(ScriptError):
                sbom.validate_cyclonedx(invalid, "svc")

    def test_licence_summary_counts_and_flags_review_categories(self) -> None:
        summary = sbom.summarize_licences(LICENCES, "svc")
        self.assertEqual(summary["categories"], {"notice": 2, "restricted": 1, "unknown": 1})
        self.assertEqual(summary["review"], {"restricted": ["GPL-2.0-only"], "unknown": ["Custom"]})

    def test_trivy_paths_map_into_the_scanner_container(self) -> None:
        cache = Path("/data/state/trivy-cache")
        archive = cache / "sbom-input" / "local.tar"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IMAGE_SECURITY_TRIVY_EXECUTABLE", None)
            self.assertEqual(sbom.trivy_visible_path(cache, archive), "/cache/sbom-input/local.tar")
            with self.assertRaises(ScriptError):
                sbom.trivy_visible_path(cache, Path("/tmp/outside.tar"))
        with patch.dict(os.environ, {"IMAGE_SECURITY_TRIVY_EXECUTABLE": "/usr/local/bin/trivy"}):
            self.assertEqual(sbom.trivy_visible_path(cache, archive), str(archive))

    def test_registry_evidence_is_generated_once_per_digest(self) -> None:
        calls: list[list[str]] = []

        def fake_trivy(_cache: Path, arguments: list[str], _timeout: int, _label: str) -> str:
            calls.append(arguments)
            return LICENCES if "--license-full" in arguments else CYCLONEDX

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = root / "images"
            images.mkdir()
            options = dict(
                cache=root / "cache",
                input_root=root / "cache" / "sbom-input",
                image_root=images,
                timeout=60,
                licence_scan=True,
                refresh=False,
                version="0.76.1",
            )
            with patch.object(sbom, "run_trivy", side_effect=fake_trivy), patch.object(
                sbom, "run"
            ) as run:
                meta = sbom.generate_evidence(registry_entry("prometheus"), **options)
                again = sbom.generate_evidence(registry_entry("prometheus"), **options)
            run.assert_not_called()
            key = registry_entry().key
            self.assertEqual(len(calls), 2)
            self.assertIn("cyclonedx", calls[0])
            self.assertEqual(calls[0][-3:], ["--image-src", "remote", "prom/prometheus@" + REGISTRY_DIGEST])
            self.assertIn("--license-full", calls[1])
            self.assertEqual(meta["bom_link"], "urn:cdx:6473021d-863a-4cec-ac15-8bf666d8ece9/1")
            self.assertEqual(again, meta)
            for suffix in ("cdx", "licences", "meta"):
                path = images / f"{key}.{suffix}.json"
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_local_images_are_exported_scanned_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            input_root = cache / "sbom-input"
            input_root.mkdir(parents=True)
            images = root / "images"
            images.mkdir()
            seen: list[list[str]] = []

            def fake_run(arguments: list[object], **_kwargs: object) -> None:
                output = Path(str(arguments[arguments.index("--output") + 1]))
                output.write_bytes(b"tar")

            def fake_trivy(_cache: Path, arguments: list[str], _timeout: int, _label: str) -> str:
                seen.append(arguments)
                self.assertTrue((input_root / f"{local_entry().key}.tar").is_file())
                return CYCLONEDX

            with patch.dict(os.environ, {}, clear=False), patch.object(
                sbom, "run", side_effect=fake_run
            ) as run, patch.object(sbom, "run_trivy", side_effect=fake_trivy):
                os.environ.pop("IMAGE_SECURITY_TRIVY_EXECUTABLE", None)
                sbom.generate_evidence(
                    local_entry("opensearch-setup"),
                    cache=cache,
                    input_root=input_root,
                    image_root=images,
                    timeout=60,
                    licence_scan=False,
                    refresh=False,
                    version="0.76.1",
                )
            command = [str(value) for value in run.call_args.args[0]]
            self.assertEqual(command[:3], ["docker", "image", "save"])
            self.assertEqual(command[-1], LOCAL_DIGEST)
            self.assertEqual(seen[0][-2:], ["--input", f"/cache/sbom-input/{local_entry().key}.tar"])
            self.assertEqual(list(input_root.iterdir()), [])
            self.assertFalse((images / f"{local_entry().key}.licences.json").exists())

    def test_stack_bom_links_image_sboms_and_records_failures(self) -> None:
        meta = {
            "bom_link": "urn:cdx:6473021d-863a-4cec-ac15-8bf666d8ece9/1",
            "sbom_file": "x.cdx.json",
            "licences": {"categories": {"notice": 2}, "review": {"unknown": ["Custom"]}},
        }
        ok = sbom.stack_component(registry_entry("prometheus", "prometheus-secrets-init"), meta, {"org.opencontainers.image.revision": "abc"}, "")
        failed = sbom.stack_component(local_entry("opensearch-setup"), None, {}, "export failed")
        with patch.object(sbom, "repository_revision", return_value="cb9576b"):
            stack = sbom.build_stack_bom(project="litellm-stack", scope="all-running", version="0.76.1", components=[ok, failed])
        self.assertEqual(stack["specVersion"], "1.6")
        self.assertEqual(stack["metadata"]["component"]["version"], "cb9576b")
        self.assertEqual(stack["dependencies"][0]["dependsOn"], [registry_entry().key, local_entry().key])
        self.assertEqual(ok["externalReferences"][0]["type"], "bom")
        self.assertEqual(ok["name"], "prom/prometheus")
        self.assertEqual(ok["version"], "v3.15.1")
        properties = {(item["name"], item["value"]) for item in ok["properties"]}
        self.assertIn(("litellm-stack:service", "prometheus-secrets-init"), properties)
        self.assertIn(("litellm-stack:licence-review:unknown", "Custom"), properties)
        self.assertIn(("litellm-stack:label:org.opencontainers.image.revision", "abc"), properties)
        failed_properties = {item["name"]: item["value"] for item in failed["properties"]}
        self.assertEqual(failed_properties["litellm-stack:sbom-status"], "failed")
        self.assertNotIn("externalReferences", failed)

    def test_prune_keeps_newest_run_and_referenced_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "runs"
            images = root / "images"
            runs.mkdir()
            images.mkdir()
            kept_key = registry_entry().key
            dropped_key = local_entry().key
            old_run = runs / "stack-20240101T000000Z.cdx.json"
            newest_run = runs / "stack-20240201T000000Z.cdx.json"
            old_run.write_text(json.dumps({"components": [{"bom-ref": dropped_key}]}))
            newest_run.write_text(json.dumps({"components": [{"bom-ref": kept_key}]}))
            for key in (kept_key, dropped_key):
                for suffix in ("cdx", "meta"):
                    (images / f"{key}.{suffix}.json").write_text("{}")
            unrelated = images / "notes.txt"
            unrelated.write_text("keep")
            old = time.time() - 30 * 86400
            for path in [*runs.iterdir(), *images.iterdir()]:
                os.utime(path, (old, old))

            sbom.prune_evidence(runs, images, 10, time.time())

            self.assertEqual([path.name for path in runs.iterdir()], [newest_run.name])
            self.assertEqual(
                sorted(path.name for path in images.iterdir()),
                sorted([f"{kept_key}.cdx.json", f"{kept_key}.meta.json", "notes.txt"]),
            )

    def test_prune_is_disabled_by_zero_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "runs"
            images = root / "images"
            runs.mkdir()
            images.mkdir()
            stale = runs / "stack-20240101T000000Z.cdx.json"
            stale.write_text("{}")
            os.utime(stale, (0, 0))
            sbom.prune_evidence(runs, images, 0, time.time())
            self.assertTrue(stale.exists())

    def test_run_sbom_covers_registry_and_local_images(self) -> None:
        prometheus = monitor.RunningImage(
            service="prometheus",
            configured_reference="prom/prometheus:v3.15.1@" + "sha256:" + "c" * 64,
            local_image_id="sha256:" + "d" * 64,
            platform="linux/amd64",
        )
        tools = monitor.RunningImage(
            service="opensearch-setup",
            configured_reference="litellm-opensearch-tools:0.1.0",
            local_image_id=LOCAL_DIGEST,
            platform="linux/amd64",
        )
        inventory = {"prometheus": prometheus, "opensearch-setup": tools}

        def fake_trivy(_cache: Path, arguments: list[str], _timeout: int, _label: str) -> str:
            return LICENCES if "--license-full" in arguments else CYCLONEDX

        def fake_run(arguments: list[object], **_kwargs: object) -> None:
            Path(str(arguments[arguments.index("--output") + 1])).write_bytes(b"tar")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {
                "IMAGE_SECURITY_STATE_DIR": str(root / "state"),
                "IMAGE_SECURITY_REPORT_DIR": str(root / "reports"),
                "IMAGE_SECURITY_TRIVY_EXECUTABLE": "/usr/local/bin/trivy",
                "STACK_REPOSITORY_REVISION": "test-revision",
            }
            arguments = argparse.Namespace(all=False, services=None, refresh=False, project="litellm-stack")
            with patch.dict(os.environ, environment), patch.object(sbom, "require_docker"), patch.object(
                sbom, "trivy_version", return_value="0.76.1"
            ), patch.object(monitor, "inventory_running_images", return_value=inventory), patch.object(
                monitor, "platform_reference", return_value="prom/prometheus@" + REGISTRY_DIGEST
            ), patch.object(sbom, "run_trivy", side_effect=fake_trivy), patch.object(
                sbom, "run", side_effect=fake_run
            ), patch.object(sbom, "image_labels", return_value={}):
                self.assertTrue(sbom.run_sbom(arguments))
                arguments.services = "prometheus,missing"
                self.assertFalse(sbom.run_sbom(arguments))

            sbom_root = root / "reports" / "sbom"
            self.assertEqual(sbom_root.stat().st_mode & 0o777, 0o700)
            runs = sorted((sbom_root / "runs").iterdir())
            self.assertEqual(len(runs), 2)
            stack = json.loads(runs[0].read_text())
            self.assertEqual(stack["metadata"]["component"]["version"], "test-revision")
            self.assertEqual(
                sorted(component["bom-ref"] for component in stack["components"]),
                sorted([registry_entry().key, local_entry().key]),
            )
            self.assertEqual(list((root / "state" / "trivy-cache" / "sbom-input").iterdir()), [])


    def test_review_lines_summarize_counts_and_name_forbidden_licences(self) -> None:
        meta = {
            "bom_link": "urn:cdx:6473021d-863a-4cec-ac15-8bf666d8ece9/1",
            "sbom_file": "x.cdx.json",
            "licences": {
                "categories": {"forbidden": 1, "notice": 9, "restricted": 4},
                "review": {"forbidden": ["AGPL-3.0"], "restricted": ["GPL-2.0-only"]},
            },
        }
        clean = {"bom_link": meta["bom_link"], "sbom_file": "y.cdx.json", "licences": {"categories": {"notice": 3}, "review": {}}}
        lines = sbom.review_lines(
            [
                sbom.stack_component(registry_entry("searxng"), meta, {}, ""),
                sbom.stack_component(local_entry("tools"), clean, {}, ""),
            ]
        )
        self.assertEqual(
            lines,
            [
                f"REVIEW: prom/prometheus: forbidden=1, restricted=4 (details in sbom/images/{registry_entry().key}.licences.json)",
                "REVIEW: prom/prometheus forbidden-category licences: AGPL-3.0",
            ],
        )

if __name__ == "__main__":
    unittest.main()
