from __future__ import annotations

import importlib.util
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_gpu_host_ca_bundle", ROOT / "scripts" / "build-gpu-host-ca-bundle.py")
assert SPEC and SPEC.loader
bundle = importlib.util.module_from_spec(SPEC)
sys.modules["build_gpu_host_ca_bundle"] = bundle
SPEC.loader.exec_module(bundle)


@unittest.skipUnless(shutil.which("openssl"), "openssl is required")
class BuildGpuHostCaBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.public = self.certificate("public", ca=True)
        self.ca = self.certificate("gpu-host-ca", ca=True)
        self.leaf = self.certificate("leaf", ca=False)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def certificate(self, name: str, *, ca: bool) -> str:
        key, cert = self.root / f"{name}.key", self.root / f"{name}.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", f"/CN={name}", "-keyout", str(key), "-out", str(cert),
                "-addext", f"basicConstraints=critical,CA:{'TRUE' if ca else 'FALSE'}",
            ],
            check=True,
            capture_output=True,
        )
        return cert.read_text(encoding="utf-8")

    def test_bundle_holds_public_roots_and_the_gpu_host_ca(self) -> None:
        combined = bundle.build_bundle(self.public, self.ca)
        self.assertEqual(bundle.certificates(combined), [self.public.strip(), self.ca.strip()])
        self.assertEqual(len(ssl.create_default_context(cadata=combined).get_ca_certs()), 2)

    def test_root_ca_with_a_private_key_is_rejected(self) -> None:
        key = (self.root / "gpu-host-ca.key").read_text(encoding="utf-8")
        with self.assertRaisesRegex(bundle.BundleError, "never rootCA-key.pem"):
            bundle.build_bundle(self.public, self.ca + key)

    def test_leaf_certificate_is_rejected(self) -> None:
        with self.assertRaisesRegex(bundle.BundleError, "not a CA certificate"):
            bundle.build_bundle(self.public, self.leaf)

    def test_output_is_written_atomically_and_world_readable(self) -> None:
        output = self.root / "certs" / "gpu-host-ca-bundle.pem"
        public = self.root / "public.pem"
        ca = self.root / "gpu-host-ca.pem"
        self.assertEqual(
            bundle.main(["--root-ca", str(ca), "--public-roots", str(public), "--output", str(output)]),
            0,
        )
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o644)
        self.assertEqual(len(bundle.certificates(output.read_text(encoding="utf-8"))), 2)


if __name__ == "__main__":
    unittest.main()
