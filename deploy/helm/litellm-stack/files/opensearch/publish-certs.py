#!/usr/bin/env python3
"""Publish the OpenSearch TLS material into a Kubernetes Secret.

The upstream stack ran generate-certs.sh as a one-shot container and shared four
named volumes with the services that needed each piece. A cluster cannot rely on
a ReadWriteOnce volume being mountable by pods on different nodes, so this
wrapper keeps the upstream script unchanged and adds Secret distribution:

  1. Hydrate /certs from the existing Secret, if there is one. That preserves
     the private CA, which is what lets generate-certs.sh reissue leaves without
     invalidating the cluster.
  2. Run generate-certs.sh. It creates the CA once and renews a leaf only when
     it is missing, unverifiable, or inside the renewal window.
  3. Write the resulting material back as one Secret. Consumers mount the keys
     they need, matching the per-volume split the Compose stack used.

Runs as root inside the Job: the upstream script chowns its output and sets
0400 modes.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CERTS_ROOT = Path("/certs")
GENERATE_SCRIPT = Path("/app/generate-certs.sh")

# Secret key -> destination path under /certs. The directory layout is the one
# generate-certs.sh expects: ca-private, ca, node, admin.
SECRET_LAYOUT = {
    "ca-key.pem": "ca-private/ca-key.pem",
    "ca.pem": "ca/ca.pem",
    "node.pem": "node/node.pem",
    "node-key.pem": "node/node-key.pem",
    "admin.pem": "admin/admin.pem",
    "admin-key.pem": "admin/admin-key.pem",
}

SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def api_request(method: str, url: str, body: dict[str, object] | None = None) -> tuple[int, dict]:
    token = (SERVICE_ACCOUNT_DIR / "token").read_text(encoding="utf-8").strip()
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    context = ssl.create_default_context(cafile=str(SERVICE_ACCOUNT_DIR / "ca.crt"))
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return 404, {}
        fail(f"{method} {url} returned {error.code}: {error.read().decode(errors='replace')}")
    except urllib.error.URLError as error:
        fail(f"{method} {url} failed: {error.reason}")
    raise AssertionError("unreachable")


def secret_url(namespace: str, name: str) -> str:
    return f"https://kubernetes.default.svc/api/v1/namespaces/{namespace}/secrets"


def hydrate(secret: dict) -> None:
    """Restore any existing material so the CA survives an upgrade."""
    data = secret.get("data") or {}
    restored = 0
    for key, relative in SECRET_LAYOUT.items():
        if key not in data:
            continue
        target = CERTS_ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(data[key]))
        restored += 1
    if restored:
        print(f"Reused {restored} existing certificate file(s) from the Secret")
    else:
        print("No existing certificate material; a new CA will be created")


def collect() -> dict[str, str]:
    data: dict[str, str] = {}
    for key, relative in SECRET_LAYOUT.items():
        path = CERTS_ROOT / relative
        if not path.is_file():
            fail(f"{path} was not produced by generate-certs.sh")
        data[key] = base64.b64encode(path.read_bytes()).decode()
    return data


def main() -> int:
    name = os.environ.get("CERT_SECRET_NAME") or "opensearch-certs"
    namespace = os.environ.get("POD_NAMESPACE") or "default"
    url = secret_url(namespace, name)

    status, existing = api_request("GET", f"{url}/{name}")
    if status == 200:
        hydrate(existing)
    else:
        print("Certificate Secret does not exist yet")

    print("Running generate-certs.sh")
    completed = subprocess.run(["/bin/sh", str(GENERATE_SCRIPT)], check=False)
    if completed.returncode != 0:
        fail(f"generate-certs.sh exited {completed.returncode}")

    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "litellm-stack",
                "app.kubernetes.io/component": "opensearch-certs",
            },
        },
        "type": "Opaque",
        "data": collect(),
    }

    if status == 200:
        api_request("PUT", f"{url}/{name}", body)
        print(f"Updated Secret {namespace}/{name}")
    else:
        api_request("POST", url, body)
        print(f"Created Secret {namespace}/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
