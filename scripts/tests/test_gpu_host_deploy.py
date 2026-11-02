from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("gpu_host_deploy", ROOT / "gpu-host" / "deploy.py")
assert SPEC and SPEC.loader
deploy = importlib.util.module_from_spec(SPEC)
sys.modules["gpu_host_deploy"] = deploy
SPEC.loader.exec_module(deploy)


class GpuHostDeployTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        for name in ("tls.crt", "tls.key", "ca.crt"):
            (self.root / name).write_text(f"{name}\n", encoding="utf-8")
        self.values = {
            "D3_SERVER_NAME": "d3-mini.example.test",
            "D3_API_KEY": "a" * 32,
            "D3_TLS_CERT_FILE": str(self.root / "tls.crt"),
            "D3_TLS_KEY_FILE": str(self.root / "tls.key"),
            "D3_CA_CERT_FILE": str(self.root / "ca.crt"),
            "BGE_M3_API_KEY": "b" * 32,
        }

    def tearDown(self) -> None:
        self.directory.cleanup()

    def write_env(self, content: str, mode: int = 0o600) -> Path:
        path = self.root / "env"
        path.write_text(content, encoding="utf-8")
        os.chmod(path, mode)
        return path

    def test_env_file_must_be_private(self) -> None:
        path = self.write_env("D3_API_KEY=x\n", 0o640)
        with self.assertRaisesRegex(deploy.DeployError, "chmod 600"):
            deploy.parse_env_file(path)

    def test_env_file_is_parsed_without_shell_evaluation(self) -> None:
        path = self.write_env('# comment\n\nD3_SERVER_NAME="$(id)"\nGPU_DEVICE_ID=1\n')
        self.assertEqual(
            deploy.parse_env_file(path),
            {"D3_SERVER_NAME": "$(id)", "GPU_DEVICE_ID": "1"},
        )

    def test_valid_settings_fill_defaults(self) -> None:
        settings = deploy.validate(self.values)
        self.assertEqual(settings["BGE_M3_PUBLIC_PORT"], "8080")
        self.assertEqual(settings["D3_PUBLIC_PORT"], "8443")

    def test_validation_rejects_unsafe_settings(self) -> None:
        cases = {
            "D3_API_KEY": ("replace_with_random_32_byte_d3_api_key", "URL-safe"),
            "BGE_M3_API_KEY": ("a" * 32, "different secrets"),
            "D3_SERVER_NAME": ("d3 mini", "DNS hostname"),
            "BGE_M3_PUBLIC_PORT": ("8443", "must differ"),
            "BGE_M3_BIND_ADDRESS": ("example.test", "IP address"),
            "BGE_M3_MAX_BATCH_TOKENS": ("4096", "at least 8192"),
            "GPU_DEVICE_ID": ("all", "nvidia-smi index"),
        }
        for key, (value, message) in cases.items():
            with self.subTest(key=key), self.assertRaisesRegex(deploy.DeployError, message):
                deploy.validate({**self.values, key: value})

    def test_d3_config_quotes_the_key_and_pins_the_revision(self) -> None:
        rendered = deploy.render_d3_config("key-with.dots~and-dashes-0123456789")
        self.assertIn(f"- {json.dumps('key-with.dots~and-dashes-0123456789')}", rendered)
        self.assertIn(f"revision: {deploy.D3_MODEL_REVISION}", rendered)
        self.assertIn("overflow: reject", rendered)

    def test_rendered_state_is_private(self) -> None:
        state = self.root / "state"
        deploy.prepare_state(deploy.validate(self.values), state)
        self.assertEqual((state / "bge-m3.env").read_text(encoding="utf-8"), f"API_KEY={'b' * 32}\n")
        for path in [state, *state.rglob("*")]:
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o700 if path.is_dir() else 0o600, path)


if __name__ == "__main__":
    unittest.main()
