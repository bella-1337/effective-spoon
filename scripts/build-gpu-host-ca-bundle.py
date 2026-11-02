#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Build the CA bundle LiteLLM uses to trust the remote GPU host.

The GPU host's TLS proxy presents a certificate from its dedicated mkcert CA.
LiteLLM verifies every outbound TLS connection, including OpenAI's, against the
single file named by SSL_CERT_FILE, so the bundle must hold the normal public
roots plus that CA. This script combines them into GPU_HOST_CA_BUNDLE.

Usage:
  scripts/build-gpu-host-ca-bundle.py --root-ca PATH [--public-roots PATH] [--output PATH]

  --root-ca       rootCA.pem copied from gpu-host/state/mkcert/ on the GPU host
                  (the public certificate only; never rootCA-key.pem)
  --public-roots  public root bundle; default: the operating system's bundle
  --output        default: traefik/certs/gpu-host-ca-bundle.pem

Recreate litellm-prod and litellm-dev after rebuilding the bundle.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "traefik" / "certs" / "gpu-host-ca-bundle.pem"
PUBLIC_ROOT_CANDIDATES = (
    Path("/etc/ssl/certs/ca-certificates.crt"),  # Debian, Ubuntu, Alpine
    Path("/etc/pki/tls/certs/ca-bundle.crt"),  # RHEL, Fedora
    Path("/etc/ssl/cert.pem"),  # macOS, BSD
)
CERTIFICATE_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----"
)


class BundleError(Exception):
    pass


def read_regular_file(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise BundleError(f"{label} must be a regular file, not a symbolic link: {path}")
    return path.read_text(encoding="utf-8")


def certificates(text: str) -> list[str]:
    return [match.group(0).strip() for match in CERTIFICATE_RE.finditer(text)]


def default_public_roots() -> Path:
    candidates = [*PUBLIC_ROOT_CANDIDATES]
    cafile = ssl.get_default_verify_paths().cafile
    if cafile:
        candidates.append(Path(cafile))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise BundleError("no operating-system root bundle found; pass --public-roots")


def validate_root_ca(text: str) -> str:
    if "PRIVATE KEY" in text:
        raise BundleError("--root-ca contains a private key; copy only rootCA.pem, never rootCA-key.pem")
    found = certificates(text)
    if len(found) != 1:
        raise BundleError(f"--root-ca must contain exactly one certificate, found {len(found)}")
    if shutil.which("openssl"):
        result = subprocess.run(
            ["openssl", "x509", "-noout", "-ext", "basicConstraints"],
            input=found[0], capture_output=True, text=True, check=False,
        )
        if result.returncode or "CA:TRUE" not in result.stdout:
            raise BundleError("--root-ca is not a CA certificate (basicConstraints CA:TRUE is missing)")
    return found[0]


def build_bundle(public_roots: str, root_ca: str) -> str:
    roots = certificates(public_roots)
    if not roots:
        raise BundleError("the public root bundle contains no certificates")
    root = validate_root_ca(root_ca)
    combined = [certificate for certificate in roots if certificate != root]
    combined.append(root)
    bundle = "\n".join(combined) + "\n"
    context = ssl.create_default_context(cadata=bundle)
    if len(context.get_ca_certs()) < 2:
        raise BundleError("the combined bundle could not be loaded as a trust store")
    return bundle


def write_atomically(path: Path, content: str) -> None:
    if path.is_symlink():
        raise BundleError(f"refusing to write through a symbolic link: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".gpu-host-ca-bundle.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(content)
        # Public certificates only; the LiteLLM containers read it as root.
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root-ca", type=Path, required=True)
    parser.add_argument("--public-roots", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    try:
        public_path = arguments.public_roots or default_public_roots()
        bundle = build_bundle(
            read_regular_file(public_path, "--public-roots"),
            read_regular_file(arguments.root_ca, "--root-ca"),
        )
        write_atomically(arguments.output, bundle)
    except BundleError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {arguments.output} ({len(certificates(bundle))} certificates, public roots from {public_path}).")
    print("Set GPU_HOST_CA_BUNDLE to that path in .env, then recreate litellm-prod and litellm-dev.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
