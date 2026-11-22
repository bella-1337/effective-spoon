from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


def load_script() -> ModuleType:
    path = SCRIPTS / "game-day-preflight.py"
    spec = importlib.util.spec_from_file_location("game_day_preflight", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GameDayPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_script()

    def valid_config(self) -> dict[str, object]:
        return {
            "services": {
                "litellm-prod": {
                    "depends_on": {
                        "postgres": {"condition": "service_healthy"},
                        "presidio-analyzer": {"condition": "service_healthy"},
                        "langfuse-web": {
                            "condition": "service_healthy",
                            "required": False,
                        },
                        "lens": {"condition": "service_healthy", "required": False},
                    },
                    "command": [
                        "exec python /app/modules/prepare_runtime_config.py "
                        "/app/config.yaml /app/runtime-config.yaml -- /app/docker/prod_entrypoint.sh"
                    ],
                    "environment": {
                        "LITELLM_LENS_URL": "http://lens:4318",
                        "UI_LOGO_PATH": "/app/branding/logo.png",
                        "UI_LOGO_PATH_DARK": "/app/branding/logo-dark.png",
                        "LITELLM_FAVICON_URL": "/app/branding/favicon.png",
                    },
                    "volumes": [{"target": "/app/branding", "read_only": True}],
                    "cap_drop": ["NET_RAW"],
                },
                "alloy-docker-proxy": {"environment": {"NETWORKS": "1"}},
                "alloy": {},
                "opensearch": {},
                "opensearch-dashboards": {},
                "opensearch-bridge": {},
                "data-prepper": {},
                "grafana-db-init": {"profiles": ["grafana"]},
                "grafana": {"profiles": ["grafana"]},
                "loki": {
                    "profiles": ["grafana"],
                    "healthcheck": {"test": ["CMD", "/usr/bin/loki", "-version"]},
                },
                "alloy-loki": {"profiles": ["grafana"]},
                "langfuse-preflight": {"profiles": ["langfuse"]},
                "langfuse-postgres": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-clickhouse": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-clickhouse-observability-setup": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-minio": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-redis": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-worker": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                },
                "langfuse-web": {
                    "profiles": ["langfuse"],
                    "depends_on": {"langfuse-preflight": {}},
                    "environment": {"AUTH_DISABLE_SIGNUP": "true"},
                },
                "lens-preflight": {"profiles": ["lens"]},
                "lens-clickhouse": {
                    "profiles": ["lens"],
                    "depends_on": {"lens-preflight": {}},
                    "networks": {"lens-storage": {}},
                },
                "lens": {
                    "profiles": ["lens"],
                    "depends_on": {"lens-preflight": {}, "lens-clickhouse": {}},
                    "networks": {"edge": {}, "internal": {}, "lens-storage": {}},
                    "read_only": True,
                    "cap_drop": ["ALL"],
                    "environment": {"LENS_PUBLIC_URL": "https://lens.example.test"},
                },
                "image-security-docker-proxy": {"profiles": ["security"]},
                "image-security-monitor": {"profiles": ["security"]},
                "d3-mini-preflight": {
                    "profiles": ["d3"],
                    "network_mode": "none",
                    "user": "0:0",
                    "cap_drop": ["ALL"],
                    "cap_add": ["CHOWN"],
                },
                "d3-mini-download": {
                    "profiles": ["d3"],
                    "image": "vllm/vllm-openai:v0.33.0@sha256:" + "a" * 64,
                    "user": "10001:10001",
                    "depends_on": {"d3-mini-preflight": {}},
                    "networks": {"d3-download": {}},
                    "read_only": True,
                    "cap_drop": ["ALL"],
                    "environment": {"D3_MODEL_REVISION": "b" * 40},
                },
                "d3-mini": {
                    "profiles": ["d3"],
                    "image": "vllm/vllm-openai:v0.33.0@sha256:" + "a" * 64,
                    "user": "10001:10001",
                    "command": ["vllm-sr/d3-mini", "--revision", "b" * 40],
                    "depends_on": {"d3-mini-download": {}},
                    "networks": {"d3-local": {}},
                    "read_only": True,
                    "cap_drop": ["ALL"],
                    "environment": {"HF_HUB_OFFLINE": "1", "VLLM_API_KEY": "k" * 64},
                    "volumes": [{"target": "/models", "read_only": True}],
                },
                "xberg": {
                    "environment": {"NO_PROXY": "127.0.0.1,localhost"},
                    "dns": ["172.29.0.53"],
                    "networks": {"xberg-sandbox": {}},
                    "command": [
                        "mcp",
                        "--config",
                        "/etc/xberg/xberg.toml",
                    ],
                    "healthcheck": {
                        "test": [
                            "CMD-SHELL",
                            "getent hosts docs.xberg.io >/dev/null && curl --fail",
                        ]
                    },
                },
                "xberg-dns": {
                    "networks": {
                        "egress": {},
                        "xberg-sandbox": {"ipv4_address": "172.29.0.53"},
                    }
                },
                "searxng": {"networks": {"internal": {}}},
            }
        }

    def test_valid_config_passes(self) -> None:
        config = self.valid_config()

        self.assertEqual(self.module.validate_compose_contracts(config), [])

    def test_custom_xberg_dns_address_passes_when_services_match(self) -> None:
        config = self.valid_config()
        config["services"]["xberg"]["dns"] = ["172.29.0.54"]
        config["services"]["xberg-dns"]["networks"]["xberg-sandbox"][
            "ipv4_address"
        ] = "172.29.0.54"

        self.assertEqual(self.module.validate_compose_contracts(config), [])

    def test_d3_profile_contracts_fail_closed(self) -> None:
        cases = {
            "must stay in the d3 profile: d3-mini": lambda s: s["d3-mini"].update(profiles=["prod"]),
            "d3-mini must depend on d3-mini-download": lambda s: s["d3-mini"].update(depends_on={}),
            "litellm-prod must not require d3-mini": lambda s: s["litellm-prod"]["depends_on"].update(
                {"d3-mini": {"condition": "service_healthy"}}
            ),
            "d3-mini-preflight must run without a network": lambda s: s["d3-mini-preflight"].pop(
                "network_mode"
            ),
            "d3-mini must join only the d3-local network": lambda s: s["d3-mini"]["networks"].update(
                {"internal": {}}
            ),
            "d3-mini-download must join only the d3-download network": lambda s: s[
                "d3-mini-download"
            ].update(networks={"internal": {}}),
            "d3-mini must not be published or routed": lambda s: s["d3-mini"].update(
                ports=[{"target": 8000, "published": "8000"}]
            ),
            "d3-mini must run as a non-root user": lambda s: s["d3-mini"].pop("user"),
            "d3-mini-download must keep a read-only root filesystem": lambda s: s[
                "d3-mini-download"
            ].update(read_only=False),
            "d3-mini must drop all Linux capabilities": lambda s: s["d3-mini"].update(
                cap_add=["SYS_ADMIN"]
            ),
            "d3-mini must run offline": lambda s: s["d3-mini"]["environment"].pop("HF_HUB_OFFLINE"),
            "through VLLM_API_KEY": lambda s: s["d3-mini"]["environment"].pop("VLLM_API_KEY"),
            "mount its model volume read-only": lambda s: s["d3-mini"].update(
                volumes=[{"target": "/models", "read_only": False}]
            ),
            "serve the revision d3-mini-download fetches": lambda s: s["d3-mini"].update(
                command=["vllm-sr/d3-mini", "--revision", "c" * 40]
            ),
            "must use the same image": lambda s: s["d3-mini-download"].update(image="other:1"),
            "may join d3-local: searxng": lambda s: s["searxng"]["networks"].update({"d3-local": {}}),
            "Optional D3 service is missing: d3-mini-preflight": lambda s: s.pop("d3-mini-preflight"),
        }
        for expected, mutate in cases.items():
            with self.subTest(expected=expected):
                config = self.valid_config()
                mutate(config["services"])
                failures = self.module.validate_compose_contracts(config)
                self.assertTrue(any(expected in failure for failure in failures), failures)

    def test_d3_gateways_may_join_d3_local(self) -> None:
        config = self.valid_config()
        config["services"]["litellm-prod"]["networks"] = {"internal": {}, "d3-local": {}}
        config["services"]["litellm-dev"] = {"networks": {"d3-local": {}}}

        failures = self.module.validate_compose_contracts(config)

        self.assertFalse([failure for failure in failures if "d3" in failure.lower()], failures)

    def test_auxiliary_production_dependency_fails(self) -> None:
        config = self.valid_config()
        config["services"]["litellm-prod"]["depends_on"] = {
            "postgres": {"condition": "service_healthy"},
            "langfuse-web": {"condition": "service_healthy"},
        }

        failures = self.module.validate_compose_contracts(config)

        self.assertTrue(
            any("auxiliary services" in failure for failure in failures),
            failures,
        )

    def test_docker_metadata_and_health_contracts_fail_closed(self) -> None:
        config = self.valid_config()
        config["services"]["alloy-docker-proxy"]["environment"] = {}
        config["services"]["xberg"]["environment"] = {"NO_PROXY": ""}
        config["services"]["xberg"]["healthcheck"] = {
            "test": ["CMD", "curl", "--silent"]
        }

        failures = self.module.validate_compose_contracts(config)

        self.assertTrue(any("network metadata" in failure for failure in failures))
        self.assertTrue(any("loopback health" in failure for failure in failures))
        self.assertTrue(any("HTTP error" in failure for failure in failures))
        self.assertTrue(any("DNS resolution" in failure for failure in failures))

    def test_opensearch_is_default_and_optional_uis_are_profiled(self) -> None:
        config = self.valid_config()
        config["services"]["opensearch"]["profiles"] = ["observability"]
        del config["services"]["opensearch-bridge"]
        config["services"]["grafana"] = {}
        config["services"]["langfuse-web"] = {}

        failures = self.module.validate_compose_contracts(config)

        self.assertTrue(any("must not require a profile: opensearch" in f for f in failures), failures)
        self.assertTrue(any("missing: opensearch-bridge" in f for f in failures), failures)
        self.assertTrue(any("grafana profile: grafana" in f for f in failures), failures)
        self.assertTrue(any("langfuse profile: langfuse-web" in f for f in failures), failures)

    def test_searxng_must_not_be_publicly_routed(self) -> None:
        exposures = (
            ("networks", {"edge": {}, "internal": {}}, "internal network only"),
            ("labels", {"traefik.enable": "true"}, "Traefik route"),
            ("labels", ["traefik.enable=true"], "Traefik route"),
            ("ports", [{"target": 8080, "published": "8080"}], "host ports"),
        )
        for key, value, message in exposures:
            with self.subTest(key=key, value=value):
                config = self.valid_config()
                config["services"]["searxng"][key] = value

                failures = self.module.validate_compose_contracts(config)

                self.assertTrue(any(message in f for f in failures), failures)

    def test_langfuse_public_signup_must_stay_disabled(self) -> None:
        for environment in ({}, {"AUTH_DISABLE_SIGNUP": "false"}):
            config = self.valid_config()
            config["services"]["langfuse-web"]["environment"] = environment

            failures = self.module.validate_compose_contracts(config)

            self.assertIn(
                "langfuse-web must disable public self-registration", failures
            )

    def test_lens_services_must_stay_optional_and_profiled(self) -> None:
        config = self.valid_config()
        config["services"]["lens"]["profiles"] = []
        del config["services"]["lens-preflight"]
        config["services"]["lens-clickhouse"]["depends_on"] = {}
        config["services"]["litellm-prod"]["depends_on"]["lens"] = {
            "condition": "service_healthy"
        }

        failures = self.module.validate_compose_contracts(config)

        self.assertIn("Optional Lens service must stay in the lens profile: lens", failures)
        self.assertIn("Optional Lens service is missing: lens-preflight", failures)
        self.assertIn("lens-clickhouse must depend on lens-preflight", failures)
        self.assertTrue(any("auxiliary services: lens" in f for f in failures), failures)
        self.assertIn(
            "litellm-prod must treat healthy Lens as an optional dependency", failures
        )

    def test_gateway_must_launch_through_lens_gate_on_private_network(self) -> None:
        config = self.valid_config()
        production = config["services"]["litellm-prod"]
        production["command"] = ["exec /app/docker/prod_entrypoint.sh"]
        production["environment"]["LITELLM_LENS_URL"] = "https://lens.example.test"

        failures = self.module.validate_compose_contracts(config)

        self.assertIn("litellm-prod must reach Lens on the private Compose network", failures)
        self.assertIn(
            "litellm-prod must start through prepare_runtime_config.py so Lens stays optional",
            failures,
        )

    def test_lens_store_and_service_must_stay_private_and_hardened(self) -> None:
        exposures = (
            ("lens-clickhouse", "networks", {"internal": {}, "lens-storage": {}}, "lens-storage network only"),
            ("lens-clickhouse", "ports", [{"target": 8123, "published": "8123"}], "must not be published"),
            ("lens-clickhouse", "labels", ["traefik.enable=true"], "must not be published"),
            ("lens", "ports", [{"target": 4318, "published": "4318"}], "must not publish host ports"),
            ("lens", "read_only", False, "read-only root filesystem"),
            ("lens", "cap_drop", ["NET_RAW"], "drop all Linux capabilities"),
            ("lens", "environment", {"LENS_PUBLIC_URL": "http://lens.example.test"}, "must use HTTPS"),
            ("searxng", "networks", {"internal": {}, "lens-storage": {}}, "Only Lens and its ClickHouse"),
        )
        for service, key, value, message in exposures:
            with self.subTest(service=service, key=key):
                config = self.valid_config()
                config["services"][service][key] = value

                failures = self.module.validate_compose_contracts(config)

                self.assertTrue(any(message in f for f in failures), failures)

    def test_loki_healthcheck_must_not_need_a_shell_or_http_client(self) -> None:
        for test in (
            ["CMD", "/busybox/wget", "-q", "-O", "-", "http://localhost:3100/ready"],
            ["CMD-SHELL", "/usr/bin/loki -version"],
            None,
        ):
            with self.subTest(test=test):
                config = self.valid_config()
                config["services"]["loki"]["healthcheck"] = {"test": test}

                failures = self.module.validate_compose_contracts(config)

                self.assertTrue(any("Loki healthcheck" in f for f in failures), failures)

    def test_explicit_symlinked_environment_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core = root / ".env"
            optional = root / ".env.optional"
            core_target = root / "core.env"
            optional_target = root / "optional.env"
            core_target.write_text("CORE=value\n", encoding="utf-8")
            optional_target.write_text("OPTIONAL=value\n", encoding="utf-8")
            core.symlink_to(core_target)
            optional.write_text("OPTIONAL=value\n", encoding="utf-8")

            with patch.object(
                sys,
                "argv",
                [
                    "game-day-preflight.py",
                    "--env-file",
                    str(core),
                    "--optional-env-file",
                    str(optional),
                ],
            ), patch.object(self.module, "load_compose_config") as load_config:
                self.assertEqual(self.module.main(), 2)
                load_config.assert_not_called()

            core.unlink()
            core.write_text("CORE=value\n", encoding="utf-8")
            optional.unlink()
            optional.symlink_to(optional_target)

            with patch.object(
                sys,
                "argv",
                [
                    "game-day-preflight.py",
                    "--env-file",
                    str(core),
                    "--optional-env-file",
                    str(optional),
                ],
            ), patch.object(self.module, "load_compose_config") as load_config:
                self.assertEqual(self.module.main(), 2)
                load_config.assert_not_called()

    def test_default_symlinked_optional_environment_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("CORE=value\n", encoding="utf-8")
            optional_target = root / "optional.env"
            optional_target.write_text("OPTIONAL=value\n", encoding="utf-8")
            (root / ".env.optional").symlink_to(optional_target)

            with patch.object(self.module, "ROOT", root), patch.object(
                sys, "argv", ["game-day-preflight.py"]
            ), patch.object(self.module, "load_compose_config") as load_config:
                self.assertEqual(self.module.main(), 2)
                load_config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
