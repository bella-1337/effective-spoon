from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import maintenance_common as common  # noqa: E402
import maintenance_contracts as contracts  # noqa: E402


class MaintenanceContractTests(unittest.TestCase):
    def test_compose_render_rejects_unsafe_default_optional_environment(self) -> None:
        for kind in ("symlink", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                optional = root / ".env.optional"
                if kind == "symlink":
                    target = root / "target.env"
                    target.write_text("OPTIONAL=value\n", encoding="utf-8")
                    optional.symlink_to(target)
                else:
                    optional.mkdir()

                with patch.object(contracts, "ROOT", root), patch.object(
                    contracts, "compose_capture"
                ) as compose_capture:
                    with self.assertRaisesRegex(
                        common.ScriptError, "environment file is unsafe.*install -m 600"
                    ):
                        contracts.load_compose_config(root / ".env")

                compose_capture.assert_not_called()

    def test_compose_render_selects_default_optional_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for present, expected in ((False, ".env.optional.example"), (True, ".env.optional")):
                if present:
                    (root / ".env.optional").write_text("OPTIONAL=value\n", encoding="utf-8")
                with self.subTest(present=present), patch.object(
                    contracts, "ROOT", root
                ), patch.object(
                    contracts, "compose_capture", return_value='{"services": {}}'
                ) as compose_capture:
                    contracts.load_compose_config(root / ".env")

                arguments = compose_capture.call_args.args[0]
                self.assertEqual(arguments[3], root / expected)

    def test_compose_render_enables_every_profile_once(self) -> None:
        with patch.object(
            contracts,
            "compose_capture",
            return_value='{"services": {}}',
        ) as compose_capture:
            config = contracts.load_compose_config(
                Path("/tmp/test.env"),
                Path("/tmp/test.optional.env"),
            )

        self.assertEqual(config, {"services": {}})
        arguments = compose_capture.call_args.args[0]
        self.assertEqual(
            [
                arguments[index + 1]
                for index, argument in enumerate(arguments)
                if argument == "--profile"
            ],
            list(contracts.COMPOSE_PROFILES),
        )
        self.assertEqual(
            arguments[:4],
            [
                "--env-file",
                Path("/tmp/test.env"),
                "--env-file",
                Path("/tmp/test.optional.env"),
            ],
        )
        self.assertEqual(arguments[-3:], ["config", "--format", "json"])

    def test_backup_metric_is_valid_prometheus_text(self) -> None:
        metric = contracts.backup_freshness_metrics(123)

        self.assertIn(
            f"# TYPE {contracts.BACKUP_FRESHNESS_METRIC} gauge",
            metric,
        )
        self.assertTrue(metric.endswith(f"{contracts.BACKUP_FRESHNESS_METRIC} 123\n"))
        with self.assertRaises(ValueError):
            contracts.backup_freshness_metrics(-1)

    def test_restore_contracts_generate_safe_names_and_session_cleanup(self) -> None:
        self.assertEqual(
            contracts.restore_database_names("litellm_prod", 42),
            ("litellm_prod_restore_42", "litellm_prod_pre_restore_42"),
        )
        self.assertIn(
            "pg_terminate_backend",
            contracts.terminate_database_connections_sql("litellm_prod"),
        )
        self.assertIn("opensearch-bridge", contracts.RESTORE_DATABASE_CLIENTS)
        self.assertIn("pgvector-exporter", contracts.RESTORE_DATABASE_CLIENTS)
        with self.assertRaises(ValueError):
            contracts.restore_database_names("litellm-prod", 42)
        with self.assertRaises(ValueError):
            contracts.terminate_database_connections_sql("litellm_prod'; DROP")

    def test_rollback_override_requires_known_services_and_immutable_images(self) -> None:
        image = "registry.example/litellm@sha256:" + ("a" * 64)
        override = contracts.render_rollback_override(
            ["litellm-prod"], {"litellm-prod": image}
        )

        self.assertTrue(override.startswith(contracts.ACTIVE_ROLLBACK_MARKER + "\n"))
        self.assertIn(f"    image: {image}\n", override)
        with self.assertRaises(ValueError):
            contracts.render_rollback_override(
                ["unknown"], {"unknown": image}
            )
        with self.assertRaises(ValueError):
            contracts.render_rollback_override(
                ["litellm-prod"], {"litellm-prod": "litellm:latest"}
            )

    def test_alert_uids_are_parsed_from_provisioning_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            alert_file = Path(directory) / "rules.yml"
            alert_file.write_text(
                "groups:\n  - rules:\n      - uid: first-rule\n      - uid: second_rule\n",
                encoding="utf-8",
            )

            self.assertEqual(
                contracts.alert_rule_uids(Path(directory)),
                {"first-rule", "second_rule"},
            )

    def test_current_repository_contracts_pass(self) -> None:
        self.assertEqual(contracts.validate_repository_contracts(), [])

    def test_traefik_dynamic_config_must_not_route_to_searxng(self) -> None:
        exposed = (
            "http:\n  services:\n    search:\n      loadBalancer:\n"
            "        servers:\n          - url: http://SearXNG:8080\n"
        )
        self.assertEqual(
            contracts.traefik_dynamic_config_failures(exposed),
            ["Traefik dynamic configuration must not route to internal-only SearXNG"],
        )
        self.assertEqual(
            contracts.traefik_dynamic_config_failures(
                "# searxng stays internal-only\ntls:\n  certificates: []\n"
            ),
            [],
        )

    def test_repository_contracts_detect_disconnected_workflows(self) -> None:
        with patch.object(contracts.runpy, "run_path", return_value={}):
            failures = contracts.validate_repository_contracts()

        self.assertTrue(
            any("disconnected from shared contracts" in failure for failure in failures),
            failures,
        )


    def test_gpu_host_stack_feeds_the_licence_register(self) -> None:
        images, failures = contracts.auxiliary_images(contracts.ROOT)
        self.assertEqual(failures, [])
        for image in (
            "ghcr.io/vllm-project/semantic-router/vllm-sr-cuda:latest",
            "docker.io/nginxinc/nginx-unprivileged:1.29.5-alpine",
            "ghcr.io/huggingface/text-embeddings-inference:cuda-1.9.4",
        ):
            self.assertIn(image, images)
        revisions = {
            revision
            for relative in contracts.GPU_HOST_REVISION_SOURCES
            for revision in contracts.GPU_HOST_REVISION_RE.findall(
                (contracts.ROOT / relative).read_text(encoding="utf-8")
            )
        }
        self.assertEqual(
            revisions,
            {
                "5617a9f61b028005a4858fdac845db406aefb181",
                "a475367de0d8133a3c1a2a0dc12303d89cfa2ab4",
            },
        )

    def test_shared_compose_fragments_must_match_across_include(self) -> None:
        fragments = "".join(
            f"{key}: &{key.removeprefix('x-')}\n  value: 1\n\n"
            for key in contracts.SHARED_COMPOSE_FRAGMENTS
        )
        self.assertEqual(
            contracts.shared_compose_fragment_failures(
                "name: stack\n" + fragments, "# optional\n" + fragments
            ),
            [],
        )

        drifted = fragments.replace(
            "x-resources-small: &resources-small\n  value: 1",
            "x-resources-small: &resources-small\n  value: 2",
        )
        missing = fragments.replace("x-default-logging:", "x-other-logging:")
        self.assertEqual(
            contracts.shared_compose_fragment_failures(fragments, drifted),
            [
                "Shared Compose fragment x-resources-small differs between "
                "docker-compose.yml and compose.optional.yml"
            ],
        )
        self.assertEqual(
            contracts.shared_compose_fragment_failures(fragments, missing),
            ["Shared Compose fragment x-default-logging is missing from compose.optional.yml"],
        )

    def test_repository_shared_compose_fragments_are_in_sync(self) -> None:
        root = contracts.ROOT
        self.assertEqual(
            contracts.shared_compose_fragment_failures(
                (root / "docker-compose.yml").read_text(encoding="utf-8"),
                (root / "compose.optional.yml").read_text(encoding="utf-8"),
            ),
            [],
        )

    def test_public_hostnames_must_not_be_hard_coded(self) -> None:
        env_examples = {
            ".env.example": {
                "STACK_DOMAIN": "example.test",
                "LITELLM_PROD_HOST": "llm.${STACK_DOMAIN}",
                "LITELLM_DEV_HOST": "llm-dev.example.test",
                "OPENSEARCH_DASHBOARDS_HOST": "opensearch.${STACK_DOMAIN}",
            },
            ".env.optional.example": {
                "GRAFANA_HOST": "grafana.${STACK_DOMAIN}",
                "LANGFUSE_WEB_HOST": "langfuse.${STACK_DOMAIN}",
            },
        }
        failures = contracts.public_hostname_source_failures(
            {
                "docker-compose.yml": "name: stack\n"
                "x: Host(`${LITELLM_PROD_HOST:-llm.example.test}`)\n",
                "compose.optional.yml": "y: ${GRAFANA_HOST:?Set GRAFANA_HOST in .env.optional}\n",
            },
            env_examples,
        )

        self.assertEqual(
            failures,
            [
                "docker-compose.yml hard-codes a public hostname fallback: "
                "${LITELLM_PROD_HOST:-llm.example.test}",
                "docker-compose.yml does not require STACK_DOMAIN",
                "LITELLM_DEV_HOST example must derive from ${STACK_DOMAIN}",
                "LANGFUSE_S3_HOST must be defined in exactly one environment example",
            ],
        )

    def test_rendered_traefik_hosts_must_be_dns_names(self) -> None:
        services = {
            "litellm-prod": {
                "labels": {"traefik.http.routers.litellm-prod.rule": "Host(`llm.`)"}
            },
            "grafana": {
                "labels": ["traefik.http.routers.grafana.rule=Host(`grafana.corp.internal`)"]
            },
        }

        self.assertEqual(
            contracts.public_hostname_failures(services),
            [
                "litellm-prod Traefik rule has an invalid public hostname 'llm.'; "
                "set STACK_DOMAIN or the matching *_HOST variable"
            ],
        )

    def test_branding_uses_only_native_litellm_inputs(self) -> None:
        def service(**environment: str) -> dict[str, object]:
            return {
                "environment": environment,
                "volumes": [{"target": "/app/branding", "read_only": True}],
            }

        valid = {
            "UI_LOGO_PATH": "/app/branding/logo.png",
            "UI_LOGO_PATH_DARK": "https://cdn.corp.example/logo-dark.svg",
            "LITELLM_FAVICON_URL": "/app/branding/favicon.png",
        }
        self.assertEqual(
            contracts.branding_failures({"litellm-prod": service(**valid)}), []
        )
        failures = contracts.branding_failures(
            {
                "litellm-dev": {
                    "environment": {**valid, "UI_LOGO_PATH": "/etc/passwd", "LITELLM_FAVICON_URL": "http://x.test/f.png"},
                    "volumes": [{"target": "/app/branding", "read_only": False}],
                }
            }
        )
        self.assertEqual(
            failures,
            [
                "litellm-dev must mount the brand asset directory read-only at /app/branding",
                "litellm-dev UI_LOGO_PATH must be an https:// URL or a file under /app/branding/",
                "litellm-dev LITELLM_FAVICON_URL must be an https:// URL or a file under /app/branding/",
            ],
        )

    def test_brand_example_files_must_be_raster_images_litellm_serves(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "brand"
            assets.mkdir()
            (assets / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nrest")
            (assets / "logo.svg").write_text("<svg><script>x</script></svg>", encoding="utf-8")
            (assets / "favicon.svg").write_text("<svg/>", encoding="utf-8")
            failures = contracts.brand_asset_failures(
                root,
                {
                    "BRAND_ASSETS_DIR": "./brand",
                    "UI_LOGO_PATH": "/app/branding/logo.png",
                    "UI_LOGO_PATH_DARK": "/app/branding/missing.png",
                    "LITELLM_FAVICON_URL": "/app/branding/favicon.svg",
                },
            )
            outside = contracts.brand_asset_failures(root, {"BRAND_ASSETS_DIR": "../elsewhere"})

        self.assertEqual(
            failures,
            [
                "UI_LOGO_PATH_DARK example file is missing: ./brand/missing.png",
                "LITELLM_FAVICON_URL example file is not a PNG, JPEG, GIF, WEBP, or ICO image "
                "that LiteLLM can serve: ./brand/favicon.svg",
                "Brand SVG contains scriptable content: brand/logo.svg",
            ],
        )
        self.assertEqual(
            outside, ["BRAND_ASSETS_DIR in .env.example must be a repository-relative directory"]
        )

    def write_register_fixture(self, root: Path, rows: list[str], revisions: str) -> None:
        (root / "docs").mkdir()
        (root / "docs" / "third-party-licences.md").write_text(
            "# Register\n\n## Runtime images\n\n| Image | Licence |\n| --- | --- |\n"
            + "".join(f"| `{row}` | MIT |\n" for row in rows)
            + "\n## Models\n\n"
            + revisions,
            encoding="utf-8",
        )
        for relative, base in (
            ("pgvector/Dockerfile", "python:3.11@sha256:" + "a" * 64),
            ("opensearch/tools/Dockerfile", "base:1"),
            ("security/image-monitor.Dockerfile", "base:1 AS builder"),
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"FROM {base}\n", encoding="utf-8")

    def test_licence_register_must_match_images_and_model_pins(self) -> None:
        revision = "5" * 40
        services = {
            "app": {"image": "example/app:1.0@sha256:" + "b" * 64},
            "model": {"image": "vllm/vllm:1", "command": ["--revision", revision]},
            "tools": {
                "image": "local-tools:0.1.0",
                "build": {
                    "labels": {"org.opencontainers.image.revision": "c" * 40},
                    "additional_contexts": {"upstream": "https://example.test/x.git#" + "c" * 40},
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_register_fixture(
                root,
                ["example/app:1.0", "vllm/vllm:1", "local-tools:0.1.0", "python:3.11", "base:1"],
                f"`{revision}`\n",
            )
            self.assertEqual(contracts.licence_register_failures(services, root), [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_register_fixture(root, ["example/app:0.9", "vllm/vllm:1", "python:3.11", "base:1"], "")
            services["tools"]["build"]["labels"]["org.opencontainers.image.revision"] = "d" * 40
            failures = contracts.licence_register_failures(services, root)
        self.assertEqual(
            failures,
            [
                "Licence register has no row for image example/app:1.0",
                "Licence register has no row for image local-tools:0.1.0",
                "Licence register lists an image the stack no longer uses: example/app:0.9",
                f"Licence register does not record pinned model revision {revision}",
                "Build revision label of tools does not match its pinned upstream context",
            ],
        )

    def test_licence_register_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                contracts.licence_register_failures({}, Path(directory)),
                ["Licence register is missing or unsafe: docs/third-party-licences.md"],
            )


    def test_own_images_require_consistent_oci_labels(self) -> None:
        labels = {
            f"org.opencontainers.image.{key}": value
            for key, value in {
                "vendor": "example.test",
                "title": "tools",
                "description": "Tools",
                "version": "0.1.0",
                "revision": "abc123",
                "licenses": "LicenseRef-Proprietary",
                "base.name": "base:1",
                "created": "",
                "source": "",
                "url": "",
            }.items()
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools.Dockerfile").write_text(
                "FROM helper:2 AS helper\nFROM base:1@sha256:" + "a" * 64 + "\n",
                encoding="utf-8",
            )
            config = {
                "x-stack-domain": "example.test",
                "services": {
                    "tools": {
                        "image": "registry.example.test/tools:0.1.0",
                        "build": {"dockerfile": "tools.Dockerfile", "labels": dict(labels)},
                    },
                    "upstream": {"image": "postgres:16"},
                },
            }
            self.assertEqual(contracts.own_image_label_failures(config, root), [])

            build = config["services"]["tools"]["build"]
            build["labels"]["org.opencontainers.image.vendor"] = "other.test"
            build["labels"]["org.opencontainers.image.version"] = "0.2.0"
            build["labels"]["org.opencontainers.image.base.name"] = "helper:2"
            self.assertEqual(
                contracts.own_image_label_failures(config, root),
                [
                    "Vendor label of tools must equal STACK_DOMAIN",
                    "Version label of tools must equal its image tag",
                    "Base-name label of tools must equal the final FROM image of its Dockerfile",
                ],
            )

            del build["labels"]["org.opencontainers.image.revision"]
            del build["labels"]["org.opencontainers.image.source"]
            build["labels"]["org.opencontainers.image.title"] = " "
            self.assertEqual(
                contracts.own_image_label_failures(config, root),
                ["Locally built image of tools lacks OCI labels: title, revision, source"],
            )


class EdgeAndAbuseContractTests(unittest.TestCase):
    """Regression guards for the DDoS, MITM, and prompt-injection hardening."""

    REPO = SCRIPTS.parent

    def _copy_policy_files(self, root: Path) -> None:
        for relative in (
            "security/xberg-config.toml",
            "security/xberg-egress-squid.conf",
            "security/xberg-egress-allowlist.txt",
            "security/xberg-dns-zones.conf",
            "security/xberg-dns-deny-root.zone",
            "litellm/config.prod.yaml",
            "litellm/config.dev.yaml",
            "litellm/modules/guardrails/presidio.yaml",
            "litellm/modules/guardrails/prompt-injection.yaml",
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((self.REPO / relative).read_text(encoding="utf-8"), encoding="utf-8")

    def _replace(self, path: Path, old: str, new: str) -> None:
        source = path.read_text(encoding="utf-8")
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def test_current_policy_files_pass(self) -> None:
        self.assertEqual(contracts.xberg_sandbox_policy_failures(self.REPO), [])
        self.assertEqual(contracts.litellm_guardrail_failures(self.REPO), [])
        dynamic = (self.REPO / "traefik" / "dynamic.yml").read_text(encoding="utf-8")
        self.assertEqual(contracts.traefik_edge_hardening_failures(dynamic), [])

    def test_traefik_edge_hardening_rejects_weak_tls_and_missing_limits(self) -> None:
        weak = (
            "tls:\n  options:\n    default:\n      minVersion: VersionTLS10\n"
            "      cipherSuites:\n        - TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA\n"
        )
        failures = contracts.traefik_edge_hardening_failures(weak)
        for expected in (
            "require TLS 1.2",
            "AEAD-only",
            "HSTS",
            "edge-rate-limit",
        ):
            self.assertTrue(any(expected in failure for failure in failures), (expected, failures))

    def test_compose_rejects_removed_edge_controls(self) -> None:
        hardened = {
            "traefik": {
                "command": [
                    "--entrypoints.websecure.http.middlewares="
                    "edge-security-headers@file,edge-rate-limit@file,edge-in-flight@file"
                ],
                "cap_drop": ["NET_RAW"],
            },
            "litellm-prod": {"cap_drop": ["NET_RAW"]},
            "presidio-analyzer": {"environment": {"WORKERS": "2"}},
            "presidio-anonymizer": {"environment": {"WORKERS": "2"}},
        }
        self.assertEqual(contracts.edge_and_abuse_compose_failures(hardened), [])

        regressed = {
            "traefik": {
                "command": [
                    "--entrypoints.websecure.http.middlewares=edge-security-headers@file",
                    "--entrypoints.websecure.forwardedheaders.insecure=true",
                ]
            },
            "litellm-prod": {},
            "presidio-analyzer": {"environment": {"WORKERS": "1"}},
        }
        failures = contracts.edge_and_abuse_compose_failures(regressed)
        for expected in (
            "edge-rate-limit",
            "forwarded headers",
            "traefik must drop NET_RAW",
            "litellm-prod must drop NET_RAW",
            "presidio-analyzer needs at least two workers",
        ):
            self.assertTrue(any(expected in failure for failure in failures), (expected, failures))

    def test_xberg_policy_rejects_file_access_plain_http_and_open_dns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_policy_files(root)
            self.assertEqual(contracts.xberg_sandbox_policy_failures(root), [])

            security = root / "security"
            self._replace(security / "xberg-config.toml", "allow_file_uris = false", "allow_file_uris = true")
            self._replace(
                security / "xberg-egress-squid.conf",
                "http_access deny !CONNECT\n",
                "acl Safe_ports port 80\n",
            )
            self._replace(
                security / "xberg-egress-squid.conf",
                "http_access deny !allowed_domains\n",
                "",
            )
            self._replace(security / "xberg-dns-zones.conf", "type primary;", "type hint;")
            (security / "xberg-egress-allowlist.txt").write_text(
                ".docs.xberg.io\n.example.org\n", encoding="utf-8"
            )

            failures = contracts.xberg_sandbox_policy_failures(root)
        for expected in (
            "local path and file://",
            "HTTPS CONNECT",
            "plaintext port 80",
            "before resolving",
            "authoritative empty root",
            "no DNS forward zone: example.org",
        ):
            self.assertTrue(any(expected in failure for failure in failures), (expected, failures))

    def test_litellm_guardrails_reject_missing_injection_screen_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_policy_files(root)
            self.assertEqual(contracts.litellm_guardrail_failures(root), [])

            modules = root / "litellm" / "modules" / "guardrails"
            (modules / "prompt-injection.yaml").unlink()
            self._replace(
                modules / "presidio.yaml",
                "presidio_analyze_chunk_size_bytes: 100000",
                "presidio_analyze_chunk_size_bytes: 500000",
            )
            self._replace(
                root / "litellm" / "config.prod.yaml",
                "    max_parallel_requests: 32\n",
                "",
            )

            failures = contracts.litellm_guardrail_failures(root)
        for expected in (
            "default-on prompt-injection guardrail",
            "200 KB or less",
            "config.prod.yaml must set default per-key request limits",
        ):
            self.assertTrue(any(expected in failure for failure in failures), (expected, failures))


if __name__ == "__main__":
    unittest.main()
