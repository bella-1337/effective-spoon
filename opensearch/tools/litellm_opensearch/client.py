"""Small, dependency-free HTTP clients for OpenSearch, Dashboards, and Prometheus."""

from __future__ import annotations

import base64
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

LOG = logging.getLogger("litellm_opensearch")
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class HttpError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        # Response bodies from the cluster can echo request content; keep only a
        # bounded prefix for operator diagnostics.
        super().__init__(f"{method} {redact_url(url)} returned HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


def redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    # Drop credentials and query strings, which can contain PromQL or secrets.
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"Environment variable {name} is required")
    return value or ""


def env_int(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise SystemExit(f"Environment variable {name} must be an integer") from error
    if value < minimum or (maximum is not None and value > maximum):
        raise SystemExit(f"Environment variable {name} is outside the allowed range")
    return value


@dataclass(frozen=True)
class Auth:
    username: str | None = None
    password: str | None = None
    client_cert: str | None = None
    client_key: str | None = None

    def header(self) -> dict[str, str]:
        if self.username is None:
            return {}
        token = base64.b64encode(f"{self.username}:{self.password or ''}".encode()).decode()
        return {"Authorization": f"Basic {token}"}


class HttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        ca_file: str | None = None,
        auth: Auth | None = None,
        timeout: float = 30.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth or Auth()
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        self.context: ssl.SSLContext | None = None
        if self.base_url.startswith("https://"):
            self.context = ssl.create_default_context(cafile=ca_file)
            self.context.minimum_version = ssl.TLSVersion.TLSv1_2
            if self.auth.client_cert:
                self.context.load_cert_chain(self.auth.client_cert, self.auth.client_key)

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
        allowed_status: Iterable[int] = (),
    ) -> tuple[int, Any]:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = raw_body
        if data is None and body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
        merged = {"Accept": "application/json", **self.extra_headers, **self.auth.header()}
        if data is not None:
            merged["Content-Type"] = content_type
        merged.update(headers or {})
        request = urllib.request.Request(url, data=data, method=method, headers=merged)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response:
                status = response.status
                payload = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            payload = error.read(MAX_RESPONSE_BYTES + 1)
            if status not in set(allowed_status):
                raise HttpError(method, url, status, payload.decode("utf-8", "replace")) from None
        if len(payload) > MAX_RESPONSE_BYTES:
            raise RuntimeError(f"{method} {redact_url(url)} response exceeded the size limit")
        if not payload:
            return status, None
        try:
            return status, json.loads(payload)
        except json.JSONDecodeError:
            return status, payload.decode("utf-8", "replace")

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)[1]

    def put(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return self.request("PUT", path, body, **kwargs)[1]

    def post(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return self.request("POST", path, body, **kwargs)[1]

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)[1]

    def wait_until(self, path: str, *, ok_status: Iterable[int], timeout: float, label: str) -> None:
        deadline = time.monotonic() + timeout
        accepted = set(ok_status)
        last_error = "no response"
        while time.monotonic() < deadline:
            try:
                status, _ = self.request("GET", path, allowed_status=range(400, 600))
                if status in accepted:
                    return
                last_error = f"HTTP {status}"
            except (OSError, RuntimeError) as error:
                last_error = type(error).__name__
            time.sleep(3)
        raise SystemExit(f"Timed out waiting for {label} ({last_error})")


def bulk(client: HttpClient, actions: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, int]:
    """Send bulk actions and return counts; document-level conflicts are tolerated."""
    if not actions:
        return {"ok": 0, "conflict": 0, "failed": 0}
    lines: list[str] = []
    for action, source in actions:
        lines.append(json.dumps(action, separators=(",", ":")))
        if source is not None:
            lines.append(json.dumps(source, separators=(",", ":"), default=str))
    payload = ("\n".join(lines) + "\n").encode()
    result = client.post("/_bulk", raw_body=payload, content_type="application/x-ndjson")
    counts = {"ok": 0, "conflict": 0, "failed": 0}
    first_error: str | None = None
    for item in result.get("items", []):
        outcome = next(iter(item.values()))
        status = int(outcome.get("status", 500))
        if status < 300:
            counts["ok"] += 1
        elif status == 409:
            counts["conflict"] += 1
        else:
            counts["failed"] += 1
            if first_error is None:
                error = outcome.get("error", {})
                first_error = f"{error.get('type')}: {str(error.get('reason'))[:300]}"
    if counts["failed"]:
        LOG.warning("Bulk request had %d failed item(s); first error %s", counts["failed"], first_error)
    return counts


def opensearch_from_env(*, admin: bool = False) -> HttpClient:
    url = env("OPENSEARCH_URL", "https://opensearch:9200")
    ca_file = env("OPENSEARCH_CA_FILE", "/certs/ca/ca.pem")
    if admin:
        auth = Auth(
            client_cert=env("OPENSEARCH_ADMIN_CERT_FILE", "/certs/admin/admin.pem"),
            client_key=env("OPENSEARCH_ADMIN_KEY_FILE", "/certs/admin/admin-key.pem"),
        )
    else:
        auth = Auth(
            username=env("OPENSEARCH_USERNAME", required=True),
            password=env("OPENSEARCH_PASSWORD", required=True),
        )
    return HttpClient(url, ca_file=ca_file, auth=auth, timeout=60.0)
