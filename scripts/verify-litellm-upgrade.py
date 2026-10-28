#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["truststore==0.10.5"]
# ///
"""Run post-upgrade, read-mostly regression checks for the LiteLLM stack."""

from __future__ import annotations

import base64
import http.client
import json
import os
import secrets
import ssl
import string
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib import error as urlerror
from urllib import request as urlrequest

import truststore

from maintenance_common import (
    ROOT,
    SCRIPTS_DIR,
    ScriptError,
    compose,
    compose_capture,
    container_http_request,
    container_environment,
    env_or_dotenv,
    fail,
    require_docker,
    run_main,
    run_python_script,
    service_is_running,
)

ALL_CHECKS = (
    "compose-config", "compose-health", "network-exposure", "prod-auth", "dev-auth", "isolation",
    "dev-vllm-embedding", "coding-routing", "pgvector", "presidio", "searxng", "xberg-mcp",
    "langfuse", "observability", "logs",
)
USAGE = """Usage: scripts/verify-litellm-upgrade.py [--only CHECK[,CHECK...]] [--skip CHECK[,CHECK...]] [--list]

Runs a safe, non-destructive post-upgrade regression suite against the
already-running Compose stack. Start the desired profile(s) first:

  docker compose --profile prod up -d
  docker compose --profile dev up -d

Checks (run in this order unless filtered):
  compose-config compose-health network-exposure prod-auth dev-auth isolation
  dev-vllm-embedding coding-routing pgvector presidio searxng xberg-mcp
  langfuse observability logs

Options:
  --only CHECK[,CHECK...]  Run only the listed checks (comma-separated)
  --skip CHECK[,CHECK...]  Run every check except the listed ones
  --list                   Print the available check names and exit
  -h, --help               Print this help and exit
"""


def usage(stream: object = sys.stdout) -> None:
    print(USAGE, file=stream, end="")


class Suite:
    def __init__(self) -> None:
        self.prod_host = env_or_dotenv("LITELLM_PROD_HOST", "llm.example.test")
        self.dev_host = env_or_dotenv("LITELLM_DEV_HOST", "llm-dev.example.test")
        self.grafana_host = env_or_dotenv("GRAFANA_HOST", "grafana.example.test")
        self.searxng_host = env_or_dotenv("SEARXNG_HOST", "search.example.test")
        self.langfuse_host = env_or_dotenv("LANGFUSE_WEB_HOST", "langfuse.example.test")
        self.prod_url, self.dev_url = f"https://{self.prod_host}", f"https://{self.dev_host}"
        self.grafana_url, self.searxng_url = f"https://{self.grafana_host}", f"https://{self.searxng_host}"
        self.langfuse_url = f"https://{self.langfuse_host}"
        self.prod_running, self.dev_running = service_is_running("litellm-prod"), service_is_running("litellm-dev")
        self.nonce = ""
        self.nonce_service = ""
        self._tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._mkcert_context: ssl.SSLContext | None = None
        self._mkcert_checked = False

    @staticmethod
    def pass_(message: str) -> None:
        print(f"PASS: {message}")

    @staticmethod
    def fail_(message: str) -> None:
        print(f"FAIL: {message}", file=sys.stderr)

    @staticmethod
    def skip(message: str) -> int:
        print(f"SKIP: {message}")
        return 2

    @staticmethod
    def info(message: str) -> None:
        print(f"INFO: {message}")

    @staticmethod
    def mkcert_root_ca() -> Path | None:
        configured = os.environ.get("CAROOT")
        if configured:
            root = Path(configured).expanduser()
        elif os.name == "nt":
            local_app_data = os.environ.get("LOCALAPPDATA")
            root = (
                Path(local_app_data)
                if local_app_data
                else Path.home() / "AppData" / "Local"
            ) / "mkcert"
        elif os.environ.get("XDG_DATA_HOME"):
            root = Path(os.environ["XDG_DATA_HOME"]).expanduser() / "mkcert"
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support" / "mkcert"
        else:
            root = Path.home() / ".local" / "share" / "mkcert"
        candidate = root / "rootCA.pem"
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
        return None

    def _open_request(
        self,
        url: str,
        method: str,
        headers: dict[str, str] | None,
        payload: object | None,
        timeout: int,
    ) -> tuple[int, dict[str, str], bytes]:
        data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        request = urlrequest.Request(url, data=data, headers=headers or {}, method=method)
        context = self._mkcert_context or self._tls_context
        try:
            with urlrequest.urlopen(request, timeout=timeout, context=context) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urlerror.HTTPError as response:
            return response.code, dict(response.headers.items()), response.read()
        except (urlerror.URLError, OSError, http.client.HTTPException) as initial_error:
            reason = (
                initial_error.reason
                if isinstance(initial_error, urlerror.URLError)
                else initial_error
            )
            if (
                not isinstance(reason, ssl.SSLCertVerificationError)
                or self._mkcert_checked
            ):
                return 0, {}, b""
            self._mkcert_checked = True
            candidate = self.mkcert_root_ca()
            if candidate is None:
                return 0, {}, b""
            self._mkcert_context = ssl.create_default_context(cafile=str(candidate))
            try:
                with urlrequest.urlopen(
                    request,
                    timeout=timeout,
                    context=self._mkcert_context,
                ) as response:
                    return response.status, dict(response.headers.items()), response.read()
            except urlerror.HTTPError as response:
                return response.code, dict(response.headers.items()), response.read()
            except (urlerror.URLError, OSError, http.client.HTTPException):
                return 0, {}, b""

    def request(self, url: str, method: str = "GET", headers: dict[str, str] | None = None,
                payload: object | None = None, timeout: int = 30) -> tuple[int, object | None]:
        status, _, body = self._open_request(url, method, headers, payload, timeout)
        try:
            return status, json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return status, None

    def request_with_headers(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        payload: object | None = None,
        timeout: int = 30,
    ) -> tuple[int, dict[str, str], object | None]:
        status, response_headers, body = self._open_request(url, method, headers, payload, timeout)
        try:
            return status, {key.lower(): value for key, value in response_headers.items()}, json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return status, {key.lower(): value for key, value in response_headers.items()}, None

    @staticmethod
    def vector_generation_query() -> str:
        return """SELECT COALESCE(
  FLOOR(EXTRACT(EPOCH FROM MAX(created_at)) * 1000000)::bigint,
  0
)
FROM embeddings
WHERE vector_store_id = 'sme-knowledge'
  AND metadata->>'indexed_by' = 'xberg-auto-index'
  AND metadata->>'source' = 'https://docs.xberg.io/guides/docker/';"""

    @staticmethod
    def vector_count_query() -> str:
        return """SELECT COUNT(*) FROM embeddings
WHERE vector_store_id = 'sme-knowledge'
  AND metadata->>'indexed_by' = 'xberg-auto-index'
  AND metadata->>'source' = 'https://docs.xberg.io/guides/docker/';"""

    def vector_scalar(self, query: str) -> str | None:
        result = compose(
            [
                "exec", "-T", "vector-postgres", "psql", "--no-psqlrc", "--quiet",
                "--tuples-only", "--no-align", "--username", "vector", "--dbname", "vector_store",
                "--command", query,
            ],
            capture_output=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def master_key(self) -> str | None:
        service = "litellm-dev" if self.dev_running else "litellm-prod" if self.prod_running else ""
        if not service:
            return None
        result = compose(["exec", "-T", service, "printenv", "LITELLM_MASTER_KEY"], capture_output=True, check=False)
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None

    def auth_headers(self) -> dict[str, str] | None:
        key = self.master_key()
        return {"Authorization": f"Bearer {key}", "x-litellm-api-key": key} if key else None

    def grafana_admin_password(self) -> str | None:
        if not service_is_running("grafana"):
            return None
        result = compose(["exec", "-T", "grafana", "printenv", "GF_SECURITY_ADMIN_PASSWORD"],
                         capture_output=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    @staticmethod
    def basic_headers(password: str) -> dict[str, str]:
        token = base64.b64encode(f"admin:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def grafana_headers(self) -> dict[str, str] | None:
        password = self.grafana_admin_password()
        return self.basic_headers(password) if password else None

    def check_compose_config(self) -> int:
        bad = False
        for profile, label in (("prod", "production"), ("dev", "development")):
            if compose(["--profile", profile, "config", "--quiet"], check=False).returncode == 0:
                self.pass_(f"{label} Compose profile config is valid")
            else:
                self.fail_(f"{label} Compose profile config failed validation")
                bad = True
        return int(bad)

    def compose_ps(self) -> list[dict[str, object]]:
        output = compose_capture(["ps", "--all", "--format", "json"])
        try:
            parsed = json.loads(output)
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            return [json.loads(line) for line in output.splitlines() if line.strip()]

    def check_compose_health(self) -> int:
        try:
            containers = self.compose_ps()
        except (json.JSONDecodeError, ScriptError):
            self.fail_("docker compose ps failed")
            return 1
        if not containers:
            self.fail_("no Compose services are known to this project; start a profile first")
            return 1
        states = {str(item.get("Service")): str(item.get("State")) for item in containers}
        bad = False
        for service in ("traefik", "postgres", "presidio-analyzer", "presidio-anonymizer", "searxng", "xberg",
                        "prometheus", "loki", "alloy", "langfuse-postgres", "langfuse-clickhouse", "langfuse-minio",
                        "langfuse-redis", "langfuse-worker", "langfuse-web", "grafana"):
            if states.get(service) != "running":
                self.fail_(f"shared service '{service}' is not running (state: {states.get(service, 'missing')})")
                bad = True
        for item in containers:
            if item.get("Health") == "unhealthy":
                self.fail_(f"unhealthy service(s): {item.get('Service')}")
                bad = True
        for job in ("postgres-init", "pgvector-migrate"):
            for item in containers:
                if item.get("Service") == job and str(item.get("ExitCode", "")) not in {"", "0"}:
                    self.fail_(f"one-off job '{job}' exited with code {item.get('ExitCode')}")
                    bad = True
        if self.prod_running and states.get("litellm-prod") != "running":
            self.fail_("litellm-prod is not running")
            bad = True
        if self.dev_running and states.get("litellm-dev") != "running":
            self.fail_("litellm-dev is not running")
            bad = True
        if not bad:
            self.pass_("Compose services report expected running/health state")
        return int(bad)

    def check_network_exposure(self) -> int:
        try:
            containers = self.compose_ps()
            leaks = [
                str(item.get("Service"))
                for item in containers
                if item.get("Service") != "traefik"
                and any(publisher.get("URL") for publisher in (item.get("Publishers") or []) if isinstance(publisher, dict))
            ]
        except (json.JSONDecodeError, ScriptError):
            self.fail_("docker compose ps failed")
            return 1
        if leaks:
            self.fail_(f"service(s) unexpectedly publish ports to the host: {' '.join(leaks)}")
            return 1
        self.pass_("only traefik publishes ports to the host")
        return 0

    def profile_auth(self, label: str, url: str) -> int:
        headers = self.auth_headers()
        if not headers:
            self.fail_(f"{label}: could not read LITELLM_MASTER_KEY")
            return 1
        bad = False
        for authorised, expected in ((False, 401), (True, 200)):
            selected = {"Authorization": headers["Authorization"]} if authorised else {}
            status, _ = self.request(f"{url}/v1/models", headers=selected)
            if status == expected:
                self.pass_(f"{label}: {'authenticated' if authorised else 'unauthenticated'} request returned HTTP {expected}")
            else:
                self.fail_(f"{label}: {'authenticated' if authorised else 'unauthenticated'} request returned HTTP {status} (expected {expected})")
                bad = True
        return int(bad)

    def check_prod_auth(self) -> int:
        return self.skip("prod-auth: litellm-prod is not running") if not self.prod_running else self.profile_auth("production", self.prod_url)

    def check_dev_auth(self) -> int:
        return self.skip("dev-auth: litellm-dev is not running") if not self.dev_running else self.profile_auth("development", self.dev_url)

    def check_isolation(self) -> int:
        if not self.prod_running or not self.dev_running:
            return self.skip("isolation: requires both litellm-prod and litellm-dev running")
        headers = self.auth_headers()
        if not headers:
            self.fail_("isolation: could not read LITELLM_MASTER_KEY")
            return 1
        bad = False
        database_query = "SELECT datname FROM pg_database WHERE datname IN ('litellm_prod','litellm_dev')"
        database_result = compose(
            [
                "exec", "-T", "postgres", "psql",
                "--username", container_environment("postgres", "POSTGRES_USER"),
                "--dbname", "postgres", "--tuples-only", "--no-align",
                "--command", database_query,
            ],
            capture_output=True,
            check=False,
        )
        databases = database_result.stdout
        if database_result.returncode:
            self.fail_("isolation: could not query postgres for litellm_prod/litellm_dev")
            bad = True
        elif {"litellm_prod", "litellm_dev"} <= set(databases.split()):
            self.pass_("isolation: litellm_prod and litellm_dev exist as separate databases")
        else:
            self.fail_("isolation: expected both litellm_prod and litellm_dev databases to exist")
            bad = True
        status, prod = self.request(f"{self.prod_url}/v1/models", headers={"Authorization": headers["Authorization"]})
        status2, dev = self.request(f"{self.dev_url}/v1/models", headers={"Authorization": headers["Authorization"]})
        prod_models = {item.get("id") for item in prod.get("data", []) if isinstance(item, dict)} if isinstance(prod, dict) else set()
        dev_models = {item.get("id") for item in dev.get("data", []) if isinstance(item, dict)} if isinstance(dev, dict) else set()
        if not prod_models or not dev_models:
            self.fail_("isolation: could not read model lists from one or both profiles")
            bad = True
        else:
            dev_only = dev_models - prod_models
            if dev_only: self.pass_("isolation: development declares model(s) not present in production")
            else: self.info("isolation: no development-only models are currently configured")
            for model in dev_only:
                result, _ = self.request(f"{self.prod_url}/v1/chat/completions", "POST", {"Authorization": headers["Authorization"], "Content-Type": "application/json"}, {"model": model, "messages": [{"role": "user", "content": "ping"}]})
                if result // 100 != 4:
                    self.fail_(f"isolation: production unexpectedly returned HTTP {result} for dev-only model")
                    bad = True
            if dev_only and not bad:
                self.pass_("isolation: production rejects every development-only model")
            prod_only = prod_models - dev_models
            if prod_only:
                accepted = True
                for model in prod_only:
                    result, _ = self.request(
                        f"{self.dev_url}/v1/chat/completions",
                        "POST",
                        {"Authorization": headers["Authorization"], "Content-Type": "application/json"},
                        {"model": model, "messages": [{"role": "user", "content": "ping"}]},
                    )
                    if result // 100 != 4:
                        self.fail_(f"isolation: development unexpectedly returned HTTP {result} for production-only model")
                        accepted = False
                        bad = True
                if accepted:
                    self.pass_("isolation: development rejects every production-only model")
        _, stores = self.request(f"{self.prod_url}/vector_store/list", headers={"Authorization": headers["Authorization"]})
        if isinstance(stores, dict) and stores.get("data") == []:
            self.pass_("isolation: production lists no pgvector stores")
        else:
            self.fail_("isolation: production unexpectedly lists vector stores")
            bad = True
        return int(bad)

    def check_dev_vllm_embedding(self) -> int:
        if not self.dev_running: return self.skip("dev-vllm-embedding: litellm-dev is not running")
        if not service_is_running("bge-m3-vllm"):
            self.fail_("dev-vllm-embedding: required bge-m3-vllm service is not running")
            return 1
        headers = self.auth_headers()
        if not headers: self.fail_("dev-vllm-embedding: could not read LITELLM_MASTER_KEY"); return 1
        dimensions = 0
        for attempt in range(2):
            _, data = self.request(f"{self.dev_url}/v1/embeddings", "POST", {"Authorization": headers["Authorization"], "Content-Type": "application/json"}, {"model": "bge-m3", "input": "litellm upgrade verification probe"}, 90)
            dimensions = len(data.get("data", [{}])[0].get("embedding", [])) if isinstance(data, dict) else 0
            if dimensions == 1024: break
            if attempt == 0: time.sleep(15)
        if dimensions == 1024:
            self.pass_("dev-vllm-embedding: bge-m3 returned a 1024-dimension embedding")
            return 0
        self.fail_(f"dev-vllm-embedding: bge-m3 returned an unexpected embedding (dimensions: {dimensions or 'none'})")
        return 1

    def check_coding_routing(self) -> int:
        if not self.dev_running:
            return self.skip("coding-routing: litellm-dev is not running")
        headers = self.auth_headers()
        if not headers:
            self.fail_("coding-routing: could not read LITELLM_MASTER_KEY")
            return 1
        decision_status, decision = self.request(
            f"{self.dev_url}/v1/systemone",
            "POST",
            {
                "Authorization": headers["Authorization"],
                "Content-Type": "application/json",
            },
            {
                "model": "d3-mini",
                "state": "Refactor authentication across several modules and preserve compatibility.",
                "questions": {
                    "tier": {
                        "type": "choice",
                        "instructions": "Choose the required coding effort.",
                        "criteria": {
                            "SIMPLE": "Small local and reversible change",
                            "MEDIUM": "Bounded multi-step implementation",
                            "COMPLEX": "Cross-module or compatibility work",
                            "REASONING": "Security, data-loss, or architecture risk",
                        },
                    }
                },
            },
            30,
        )
        tier = (
            decision.get("answers", {}).get("tier", {}).get("choice")
            if isinstance(decision, dict)
            else None
        )
        if decision_status != 200 or tier not in {
            "SIMPLE", "MEDIUM", "COMPLEX", "REASONING"
        }:
            self.fail_(
                "coding-routing: D3 Decisions probe failed "
                f"(HTTP {decision_status}, tier {tier or 'none'})"
            )
            return 1
        self.nonce = "probe-" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(16))
        status, response_headers, response = self.request_with_headers(
            f"{self.dev_url}/v1/chat/completions",
            "POST",
            {"Authorization": headers["Authorization"], "Content-Type": "application/json"},
            {
                "model": "coding-router",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Analyze this software architecture and compare its tradeoffs. "
                            f"Request reference: {self.nonce}"
                        ),
                    }
                ],
                "temperature": 0,
                "max_tokens": 32,
            },
            120,
        )
        routed_model = response.get("model", "") if isinstance(response, dict) else ""
        choices = response.get("choices", []) if isinstance(response, dict) else []
        classifier_cause = response_headers.get("x-litellm-complexity-router-cause", "")
        if (
            status == 200
            and isinstance(routed_model, str)
            and bool(routed_model)
            and choices
            and classifier_cause == "jev_classifier"
        ):
            self.nonce_service = "litellm-dev"
            self.pass_("coding-routing: D3 classified a request and LiteLLM Auto Router completed it")
            return 0
        self.fail_(
            "coding-routing: Auto Router did not use the D3 classifier "
            f"(HTTP {status}, model {routed_model or 'none'}, cause {classifier_cause or 'none'})"
        )
        return 1

    def check_pgvector(self) -> int:
        if not self.dev_running: return self.skip("pgvector: litellm-dev is not running")
        if not service_is_running("litellm-pgvector"): return self.skip("pgvector: litellm-pgvector is not running")
        script = SCRIPTS_DIR / "verify-pgvector.py"
        if not script.exists(): return self.skip("pgvector: scripts/verify-pgvector.py is not present")
        if run_python_script(script, []).returncode == 0: return 0
        self.fail_("pgvector: scripts/verify-pgvector.py reported a failure")
        return 1

    def check_presidio(self) -> int:
        if not service_is_running("presidio-analyzer"): return self.skip("presidio: presidio-analyzer is not running")
        if not service_is_running("presidio-anonymizer"): return self.skip("presidio: presidio-anonymizer is not running")
        text = "My name is Jordan Example and I work at Example Client Limited."
        status, body = container_http_request(
            "presidio-analyzer",
            "http://localhost:3000/analyze",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"text": text, "language": "en"}).encode(),
        )
        try:
            analysis = json.loads(body) if status == 200 else None
        except json.JSONDecodeError: self.fail_("presidio: could not reach the analyzer API"); return 1
        if not isinstance(analysis, list):
            self.fail_("presidio: could not reach the analyzer API")
            return 1
        entities = {item.get("entity_type") for item in analysis if isinstance(item, dict)}
        bad = False
        if "PERSON" in entities: self.pass_("presidio: analyzer detected a PERSON entity")
        else: self.fail_("presidio: analyzer did not detect the PERSON entity in the probe text"); bad = True
        if "ORGANIZATION" not in entities: self.pass_("presidio: analyzer did not detect an ORGANIZATION entity (intentional current configuration)")
        else: self.fail_("presidio: analyzer unexpectedly detected an ORGANIZATION entity"); bad = True
        anonymize_status, anonymize_body = container_http_request(
            "presidio-analyzer",
            "http://presidio-anonymizer:3000/anonymize",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"text": text, "analyzer_results": analysis}).encode(),
        )
        try:
            anonymized_payload = json.loads(anonymize_body) if anonymize_status == 200 else {}
            anonymized = anonymized_payload.get("text", "") if isinstance(anonymized_payload, dict) else ""
        except json.JSONDecodeError:
            self.fail_("presidio: could not reach the anonymizer API")
            return 1
        if anonymized and "Jordan Example" not in anonymized and "Example Client Limited" in anonymized:
            self.pass_("presidio: anonymizer masked the PERSON name and left the organization name intact")
        else:
            self.fail_("presidio: anonymizer output did not match the expected masking behavior")
            bad = True
        headers = self.auth_headers()
        if headers:
            for profile, running, url in (("prod", self.prod_running, self.prod_url), ("dev", self.dev_running, self.dev_url)):
                if not running:
                    continue
                _, resolution = self.request(f"{url}/policies/resolve", "POST",
                                              {"Authorization": headers["Authorization"], "Content-Type": "application/json"},
                                              {"model": "gpt-4o-mini"})
                guards = resolution.get("effective_guardrails", []) if isinstance(resolution, dict) else []
                if "presidio-pii" in guards:
                    self.pass_(f"presidio: {profile} policy resolution includes the presidio-pii guardrail")
                else:
                    self.fail_(f"presidio: {profile} policy resolution does not include the presidio-pii guardrail")
                    bad = True
        return int(bad)

    def check_searxng(self) -> int:
        if not service_is_running("searxng"): return self.skip("searxng: searxng is not running")
        headers = self.auth_headers()
        if not headers: self.fail_("searxng: could not read LITELLM_MASTER_KEY"); return 1
        bad = False
        status, _ = self.request(f"{self.searxng_url}/")
        if status == 200: self.pass_("searxng: public search page returned HTTP 200")
        else: self.fail_(f"searxng: public search page returned HTTP {status} (expected 200)"); bad = True
        for name, running, url in (("prod", self.prod_running, self.prod_url), ("dev", self.dev_running, self.dev_url)):
            if not running: continue
            status, _ = self.request(f"{url}/v1/search/searxng-search", "POST", {"Content-Type": "application/json"}, {"query": "litellm upgrade verification", "max_results": 1})
            if status == 401: self.pass_(f"searxng: {name} unauthenticated search request returned HTTP 401")
            else: self.fail_(f"searxng: {name} unauthenticated search request returned HTTP {status} (expected 401)"); bad = True
            _, response = self.request(
                f"{url}/v1/search/searxng-search",
                "POST",
                {"Authorization": headers["Authorization"], "Content-Type": "application/json"},
                {"query": "litellm upgrade verification", "max_results": 1},
            )
            if isinstance(response, dict) and "results" in response:
                self.pass_(f"searxng: {name} authenticated search returned a results payload")
            else:
                self.fail_(f"searxng: {name} authenticated search did not return a results payload")
                bad = True
        return int(bad)

    def check_xberg_mcp(self) -> int:
        if not service_is_running("xberg"): return self.skip("xberg-mcp: xberg is not running")
        headers = self.auth_headers()
        if not headers:
            self.fail_("xberg-mcp: could not read LITELLM_MASTER_KEY")
            return 1
        if not headers["x-litellm-api-key"]:
            self.fail_("xberg-mcp: LITELLM_MASTER_KEY is empty")
            return 1
        bad = False
        for name, running, url in (("prod", self.prod_running, self.prod_url), ("dev", self.dev_running, self.dev_url)):
            if not running:
                continue
            status, _ = self.request(f"{url}/mcp-rest/tools/list?server_id=xberg")
            if status == 401:
                self.pass_(f"xberg-mcp: {name} unauthenticated tool discovery returned HTTP 401")
            else:
                self.fail_(f"xberg-mcp: {name} unauthenticated tool discovery returned HTTP {status} (expected 401)")
                bad = True
            discovery_status, tools = self.request(
                f"{url}/mcp-rest/tools/list?server_id=xberg",
                headers={"x-litellm-api-key": headers["x-litellm-api-key"]},
            )
            if discovery_status == 0:
                self.fail_(f"xberg-mcp: {name} authenticated tool discovery failed")
                bad = True
                continue
            names = sorted(item.get("name") for item in tools.get("tools", []) if isinstance(item, dict)) if isinstance(tools, dict) else []
            if names == ["detect_mime_type", "extract", "get_version", "list_formats"]:
                self.pass_(f"xberg-mcp: {name} exposes only the approved read-only tools")
            else:
                self.fail_(f"xberg-mcp: {name} tool list does not match the approved allowlist")
                bad = True
            generation_before = self.vector_scalar(self.vector_generation_query()) if name == "dev" else ""
            extract_payload = {
                "server_id": "xberg",
                "name": "extract",
                "arguments": {"input": {"kind": "uri", "uri": "https://docs.xberg.io/guides/docker/"}},
            }
            extract_status, response = self.request(
                f"{url}/mcp-rest/tools/call", "POST",
                {"x-litellm-api-key": headers["x-litellm-api-key"], "Content-Type": "application/json"},
                extract_payload,
                120,
            )
            if extract_status == 0:
                self.fail_(f"xberg-mcp: {name} Docker guide extraction request failed")
                bad = True
                continue
            evidence = json.dumps(response) if response is not None else ""
            if all(item in evidence for item in ("Official Docker images", "ghcr.io/xberg-io/xberg:latest", "MCP server")):
                self.pass_(f"xberg-mcp: {name} extracted the expected official Docker guide content")
            else:
                self.fail_(f"xberg-mcp: {name} response did not contain the expected Docker guide evidence")
                bad = True
            if name == "dev":
                generation = generation_before
                for _ in range(60):
                    time.sleep(1)
                    generation = self.vector_scalar(self.vector_generation_query())
                    if generation != generation_before:
                        break
                indexed_count = self.vector_scalar(self.vector_count_query())
                if indexed_count is not None and indexed_count.isdigit() and int(indexed_count) > 0:
                    self.pass_(f"xberg-mcp: dev extract automatically indexed {indexed_count} chunk(s)")
                else:
                    self.fail_("xberg-mcp: dev extract did not create automatic index chunks")
                    bad = True
                repeat_generation_before = generation
                repeated_status, _ = self.request(
                    f"{url}/mcp-rest/tools/call", "POST",
                    {"x-litellm-api-key": headers["x-litellm-api-key"], "Content-Type": "application/json"},
                    extract_payload,
                    120,
                )
                if repeated_status == 0:
                    self.fail_("xberg-mcp: dev repeated extraction request failed")
                    bad = True
                generation = repeat_generation_before
                for _ in range(60):
                    time.sleep(1)
                    generation = self.vector_scalar(self.vector_generation_query())
                    if generation != repeat_generation_before:
                        break
                repeated_count = self.vector_scalar(self.vector_count_query())
                if (
                    indexed_count is not None
                    and repeated_count == indexed_count
                    and generation != repeat_generation_before
                ):
                    self.pass_("xberg-mcp: repeated dev extraction replaced chunks without duplicates")
                else:
                    self.fail_("xberg-mcp: repeated dev extraction changed the source chunk count")
                    bad = True
                search_url = f"{url}/v1/vector_stores/sme-knowledge/search"
                _, search = self.request(search_url, "POST",
                                          {"x-litellm-api-key": headers["x-litellm-api-key"], "Content-Type": "application/json"},
                                          {"query": "What Docker image runs the Xberg MCP server?", "max_num_results": 3}, 120)
                if "ghcr.io/xberg-io/xberg" in json.dumps(search):
                    self.pass_("xberg-mcp: automatically indexed content is searchable through LiteLLM")
                else:
                    self.fail_("xberg-mcp: automatically indexed Docker guide content was not searchable")
                    bad = True
                semantic_status, semantic_headers, semantic_response = self.request_with_headers(
                    f"{url}/v1/chat/completions",
                    "POST",
                    {"x-litellm-api-key": headers["x-litellm-api-key"], "Content-Type": "application/json"},
                    {
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": "Use the appropriate tool to report the installed Xberg version."}],
                        "tools": [{"type": "mcp", "server_url": "litellm_proxy"}],
                        "tool_choice": "required",
                        "max_tokens": 128,
                    },
                    180,
                )
                filter_summary = semantic_headers.get("x-litellm-semantic-filter", "").replace(" ", "").replace("\r", "")
                filtered_tools = semantic_headers.get("x-litellm-semantic-filter-tools", "").replace(" ", "").replace("\r", "")
                choices = semantic_response.get("choices", []) if isinstance(semantic_response, dict) else []
                if semantic_status == 200 and filter_summary == "4->3" and "xberg-get_version" in filtered_tools and choices:
                    self.pass_("xberg-mcp: dev bge-m3 semantic filter reduced 4 tools to 3 and retained get_version")
                else:
                    self.fail_("xberg-mcp: dev bge-m3 semantic filter did not return the expected 4->3 selection")
                    bad = True
        return int(bad)

    def check_langfuse(self) -> int:
        if not service_is_running("langfuse-web"): return self.skip("langfuse: web service is not running")
        if not service_is_running("langfuse-worker"): self.fail_("langfuse: worker service is not running"); return 1
        bad = False
        status, _ = self.request(f"{self.langfuse_url}/api/public/health?failIfDatabaseUnavailable=true")
        if status == 200: self.pass_("langfuse: public health endpoint and PostgreSQL check returned HTTP 200")
        else: self.fail_(f"langfuse: public health endpoint returned HTTP {status} (expected 200)"); bad = True
        worker = compose(
            [
                "exec",
                "-T",
                "langfuse-worker",
                "node",
                "-e",
                "fetch('http://127.0.0.1:3030/api/health')"
                ".then(response => process.exit(response.ok ? 0 : 1))"
                ".catch(() => process.exit(1))",
            ],
            check=False,
        )
        if worker.returncode == 0: self.pass_("langfuse: worker health endpoint is reachable")
        else: self.fail_("langfuse: worker health endpoint is not reachable"); bad = True
        expected = {
            "LITELLM_OTEL_V2": "true",
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "no_content",
            "LANGFUSE_HOST": "http://langfuse-web:3000",
            "OTEL_PYTHON_FASTAPI_EXCLUDED_URLS": ".*",
        }
        for service in ("litellm-prod", "litellm-dev"):
            if not service_is_running(service):
                continue
            values = {
                key: compose(["exec", "-T", service, "printenv", key], capture_output=True, check=False).stdout.strip()
                for key in expected
            }
            baggage = [
                compose(["exec", "-T", service, "printenv", key], capture_output=True, check=False).stdout.strip()
                for key in ("LITELLM_OTEL_BAGGAGE_PROMOTED_KEYS", "LITELLM_OTEL_BAGGAGE_METADATA_KEYS", "LITELLM_OTEL_BAGGAGE_TEAM_METADATA_KEYS")
            ]
            if values == expected and not any(baggage):
                self.pass_(f"langfuse: {service} uses privacy-hardened OTel v2 with message content and server spans disabled")
            else:
                self.fail_(f"langfuse: {service} OTel environment is not privacy-hardened")
                bad = True
        return int(bad)

    def check_observability(self) -> int:
        if not service_is_running("grafana"): return self.skip("observability: grafana is not running")
        admin_password = self.grafana_admin_password()
        if admin_password is None:
            self.fail_("observability: could not read Grafana admin password")
            return 1
        if not admin_password:
            self.fail_("observability: Grafana admin password is empty")
            return 1
        headers = self.basic_headers(admin_password)
        bad = False
        status, _ = self.request(f"{self.grafana_url}/api/health")
        if status == 200: self.pass_("observability: Grafana health endpoint returned HTTP 200")
        else: self.fail_(f"observability: Grafana health endpoint returned HTTP {status} (expected 200)"); bad = True
        for datasource in ("prom", "loki"):
            _, data = self.request(f"{self.grafana_url}/api/datasources/uid/{datasource}/health", headers=headers)
            if isinstance(data, dict) and data.get("status") in {"OK", "success"}:
                self.pass_(f"observability: Grafana datasource '{datasource}' reports healthy")
            else:
                self.fail_(f"observability: Grafana datasource '{datasource}' did not report healthy")
                bad = True
        dashboard_uids = ["litellm-stack", "litellm-cost", "litellm-admin", "litellm-team-cost"]
        for uid in dashboard_uids:
            _, data = self.request(f"{self.grafana_url}/api/dashboards/uid/{uid}", headers=headers)
            if isinstance(data, dict) and isinstance(data.get("dashboard"), dict) and data["dashboard"].get("uid") == uid:
                self.pass_(f"observability: Grafana dashboard '{uid}' is provisioned")
            else:
                self.fail_(f"observability: Grafana dashboard '{uid}' is not provisioned")
                bad = True
        demo_query = """WITH demo_spend AS (
  SELECT team_id, "user" AS user_id, api_key, SUM(spend) AS spend
  FROM "LiteLLM_SpendLogs"
  WHERE request_id LIKE 'demo-%'
  GROUP BY team_id, "user", api_key
)
SELECT
  (SELECT COUNT(*) FROM "LiteLLM_TeamTable" WHERE team_id LIKE 'demo-%'),
  (SELECT COUNT(*) FROM "LiteLLM_UserTable" WHERE user_id LIKE 'demo-%'),
  (SELECT COUNT(*) FROM "LiteLLM_SpendLogs" WHERE request_id LIKE 'demo-%'),
  (SELECT COUNT(*)
   FROM "LiteLLM_TeamTable" team
   JOIN (SELECT team_id, SUM(spend) AS spend FROM demo_spend GROUP BY team_id) logs
     USING (team_id)
   WHERE team.team_id LIKE 'demo-%'
     AND ROUND(team.spend::numeric, 6) = ROUND(logs.spend::numeric, 6)),
  (SELECT COUNT(*)
   FROM "LiteLLM_UserTable" demo_user
   JOIN (SELECT user_id, SUM(spend) AS spend FROM demo_spend GROUP BY user_id) logs
     USING (user_id)
   WHERE demo_user.user_id LIKE 'demo-%'
     AND ROUND(demo_user.spend::numeric, 6) = ROUND(logs.spend::numeric, 6)),
  (SELECT COUNT(*)
   FROM "LiteLLM_VerificationToken" demo_key
   JOIN (SELECT api_key, SUM(spend) AS spend FROM demo_spend GROUP BY api_key) logs
     ON logs.api_key = demo_key.token
   WHERE demo_key.token LIKE 'demo-token-%'
     AND ROUND(demo_key.spend::numeric, 6) = ROUND(logs.spend::numeric, 6)
     AND ROUND(demo_key.total_spend::numeric, 6) = ROUND(logs.spend::numeric, 6));"""
        for service, database in (("litellm-prod", "litellm_prod"), ("litellm-dev", "litellm_dev")):
            if not service_is_running(service):
                continue
            try:
                postgres_user = container_environment("postgres", "POSTGRES_USER")
            except ScriptError as error:
                self.fail_(f"observability: could not query {database}: {error}")
                bad = True
                continue
            result = compose(
                [
                    "exec", "-T", "postgres", "psql", "--username", postgres_user,
                    "--dbname", database, "--tuples-only", "--no-align", "--command", demo_query,
                ],
                capture_output=True,
                check=False,
            )
            counts = "".join(result.stdout.split()) if result.returncode == 0 else ""
            if counts == "2|4|12|2|4|4":
                self.pass_(f"observability: {database} demo counts and team, user, and key spend counters match")
            elif database == "litellm_prod" and counts == "0|0|0|0|0|0":
                self.pass_("observability: litellm_prod has no synthetic demo data (production demo profile is opt-in)")
            elif database == "litellm_prod":
                self.fail_(
                    "observability: litellm_prod demo data result was "
                    f"'{counts}' (expected either complete opt-in data 2|4|12|2|4|4 or no demo data 0|0|0|0|0|0)"
                )
                bad = True
            else:
                self.fail_(f"observability: litellm_dev demo data result was '{counts}' (expected 2|4|12|2|4|4)")
                bad = True
        _, targets = self.request(f"{self.grafana_url}/api/datasources/proxy/uid/prom/api/v1/targets", headers=headers)
        active = targets.get("data", {}).get("activeTargets", []) if isinstance(targets, dict) else []
        for job in ("traefik", "postgres", "pgvector", "node", "cadvisor", "blackbox-http"):
            matching = [target for target in active if isinstance(target, dict) and target.get("labels", {}).get("job") == job]
            if not matching:
                self.fail_(f"observability: Prometheus job '{job}' has no active target")
                bad = True
            elif any(target.get("health") != "up" for target in matching):
                self.fail_(f"observability: Prometheus job '{job}' has {sum(target.get('health') != 'up' for target in matching)} unhealthy target(s)")
                bad = True
        for job, running in (("litellm-prod", self.prod_running), ("litellm-dev", self.dev_running)):
            if not running:
                continue
            matching = [target for target in active if isinstance(target, dict) and target.get("labels", {}).get("job") == job]
            if not matching:
                self.fail_(f"observability: Prometheus job '{job}' has no active target")
                bad = True
            elif any(target.get("health") != "up" for target in matching):
                self.fail_(f"observability: Prometheus job '{job}' is unhealthy")
                bad = True
        if not bad:
            self.pass_("observability: expected Prometheus scrape targets are healthy")
        return int(bad)

    def check_logs(self) -> int:
        if not service_is_running("loki"): return self.skip("logs: loki is not running")
        if not service_is_running("grafana"): return self.skip("logs: grafana is not running (used as the Loki query proxy)")
        headers = self.grafana_headers()
        if not headers:
            self.fail_("logs: could not read Grafana admin password")
            return 1
        now = int(time.time())
        label_url = f"{self.grafana_url}/api/datasources/proxy/uid/loki/loki/api/v1/label/compose_service/values?{urlencode({'start': f'{now - 604800}000000000', 'end': f'{now}000000000'})}"
        _, labels = self.request(label_url, headers=headers)
        bad = False
        if not isinstance(labels, dict) or labels.get("status") != "success":
            self.fail_("logs: Loki label query did not succeed")
            bad = True
        else:
            available = set(labels.get("data", []))
            missing = [service for service in compose_capture(["ps", "--services", "--status", "running"]).splitlines() if service not in available]
            if missing:
                self.fail_(f"logs: Loki has no log label for running service(s): {' '.join(missing)}")
                bad = True
            else:
                self.pass_("logs: Loki has log labels for every currently running Compose service")
        if self.nonce and self.nonce_service:
            query = f'{{compose_service="{self.nonce_service}"}} |= `{self.nonce}`'
            url = f"{self.grafana_url}/api/datasources/proxy/uid/loki/loki/api/v1/query_range?{urlencode({'query': query, 'start': f'{now - 600}000000000', 'end': f'{now}000000000', 'limit': '5'})}"
            _, response = self.request(url, headers=headers)
            count = len(response.get("data", {}).get("result", [])) if isinstance(response, dict) else -1
            if count == 0:
                self.pass_(f"logs: the chat probe text was not found in {self.nonce_service} logs (message logging stays off)")
            else:
                self.fail_(f"logs: the chat probe text appeared in {self.nonce_service} logs; prompt content may be leaking into logs")
                bad = True
        else:
            self.info("logs: skipping the log-leak probe because no deterministic chat probe ran")
        return int(bad)


def main() -> None:
    only = skip = ""
    args = sys.argv[1:]
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--only", "--skip"}:
            if index + 1 >= len(args) or not args[index + 1]:
                fail(f"{arg} requires a non-empty argument")
            if arg == "--only": only = args[index + 1]
            else: skip = args[index + 1]
            index += 2
        elif arg.startswith("--only="):
            only, index = arg.partition("=")[2], index + 1
            if not only:
                fail("--only requires a non-empty argument")
        elif arg.startswith("--skip="):
            skip, index = arg.partition("=")[2], index + 1
            if not skip:
                fail("--skip requires a non-empty argument")
        elif arg == "--list":
            print("\n".join(ALL_CHECKS)); return
        elif arg in {"-h", "--help"}:
            usage(); return
        else:
            usage(sys.stderr); raise SystemExit(2)
    if only and skip: fail("--only and --skip are mutually exclusive")
    requested = only.split(",") if only else skip.split(",") if skip else []
    for name in requested:
        if name not in ALL_CHECKS: fail(f"unknown check name: {name} (use --list)")
    selected = [name for name in ALL_CHECKS if (not only or name in only.split(",")) and (not skip or name not in skip.split(","))]
    require_docker()
    compose(["config", "--quiet"])
    suite = Suite()
    passed = failed = skipped = 0
    skipped_names: list[str] = []
    failed_names: list[str] = []
    for name in selected:
        print(f"--- {name} ---")
        try:
            result = getattr(suite, f"check_{name.replace('-', '_')}")()
        except ScriptError as error:
            suite.fail_(f"{name}: check could not complete: {error}")
            result = 1
        if result == 0: passed += 1
        elif result == 2: skipped += 1; skipped_names.append(name)
        else: failed += 1; failed_names.append(name)
        print()
    print("==========================================")
    print(f"Summary: {passed} passed, {failed} failed, {skipped} skipped")
    if skipped_names: print(f"Skipped (documented prerequisite absent): {' '.join(skipped_names)}")
    if failed_names: print(f"Failed: {' '.join(failed_names)}")
    if failed: raise SystemExit(1)


if __name__ == "__main__":
    run_main(main)
