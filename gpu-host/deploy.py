#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Deploy D3-mini and BGE-M3 on the remote GPU host.

Reads gpu-host/.env, renders the secrets the containers need under
gpu-host/state/ (private to the deploying user), issues the TLS certificate
for both endpoints from a dedicated mkcert CA in state/mkcert/, starts the
Compose project, and reports success only after both endpoints answer real
requests over verified TLS and refuse unauthenticated ones.

Usage:
  ./deploy.py                 validate, render, start, and probe
  ./deploy.py --render-only   validate and render without starting anything
  uv run deploy.py [...]      the same, without the executable bit
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

HERE = Path(__file__).resolve().parent
COMPOSE_FILE = HERE / "compose.yml"
DEFAULT_ENV_FILE = HERE / ".env"
STATE = HERE / "state"

D3_MODEL = "vllm-sr/d3-mini"
D3_MODEL_REVISION = "a475367de0d8133a3c1a2a0dc12303d89cfa2ab4"
D3_MAX_INPUT_TOKENS = 8192
BGE_M3_MODEL = "bge-m3"
BGE_M3_MAX_INPUT_TOKENS = 8192
BGE_M3_DIMENSIONS = 1024

ENV_LINE_RE = re.compile(r"([A-Z][A-Z0-9_]*)=(.*)")
API_KEY_RE = re.compile(r"[A-Za-z0-9._~-]{32,}")
HOST_LABEL_RE = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
DOCKER_SIZE_RE = re.compile(r"[0-9]+(?:[kKmMgG][bB]?|[bB])?")
GPU_DEVICE_RE = re.compile(r"[0-9]+|GPU-[0-9a-fA-F-]{8,}")

REQUIRED = (
    "D3_SERVER_NAME",
    "D3_API_KEY",
    "BGE_M3_SERVER_NAME",
    "BGE_M3_API_KEY",
)
DEFAULTS = {
    "D3_BIND_ADDRESS": "0.0.0.0",
    "D3_PUBLIC_PORT": "8443",
    "D3_MEMORY_LIMIT": "16g",
    "D3_SHM_SIZE": "4gb",
    "BGE_M3_BIND_ADDRESS": "0.0.0.0",
    "BGE_M3_PUBLIC_PORT": "9443",
    "BGE_M3_MEMORY_LIMIT": "8g",
    "BGE_M3_MAX_BATCH_TOKENS": "16384",
    "BGE_M3_MAX_CLIENT_BATCH_SIZE": "64",
    "GPU_DEVICE_ID": "0",
    "STARTUP_TIMEOUT": "1800",
    "TLS_EXTRA_NAMES": "",
    "TLS_RENEW_BEFORE_DAYS": "30",
}


class DeployError(Exception):
    pass


# Operator output goes to the console and to a private, human-readable
# state/logs/deploy.log for later review. Same format as the main stack's
# scripts/maintenance_common.py; this host has no copy of that module.
LOG = logging.getLogger("gpu_host.deploy")
FILE_LOGGING_VARIABLE = "MAINTENANCE_FILE_LOGGING"
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUPS = 5


class _ConsoleHandler(logging.Handler):
    """Write INFO to stdout and WARNING and above to stderr."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = sys.stderr if record.levelno >= logging.WARNING else sys.stdout
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:  # noqa: BLE001 - logging must never break the deployment
            self.handleError(record)


class _ReadableFileFormatter(logging.Formatter):
    """Prefix every line with local time, level, and script for later review."""

    def __init__(self) -> None:
        super().__init__(datefmt="%Y-%m-%d %H:%M:%S %z")

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        redundant = f"{record.levelname}: "
        if message.startswith(redundant):
            message = message[len(redundant):]
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        header = f"{self.formatTime(record, self.datefmt)} {record.levelname:<7} [deploy]"
        lines = message.strip("\n").splitlines() or [""]
        return "\n".join(f"{header} {line}".rstrip() for line in lines)


class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating file handler that never follows symlinks and keeps files 0600."""

    def _open(self):  # type: ignore[override]
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.baseFilename, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError(f"log file is not a regular file: {self.baseFilename}")
            os.fchmod(descriptor, 0o600)
            return os.fdopen(descriptor, self.mode, encoding=self.encoding, errors=self.errors)
        except BaseException:
            os.close(descriptor)
            raise


if not LOG.handlers:
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    LOG.addHandler(_ConsoleHandler())


@contextmanager
def file_logging(state: Path = STATE) -> Iterator[Path | None]:
    """Also write this run's output to state/logs/deploy.log (best effort)."""
    if os.environ.get(FILE_LOGGING_VARIABLE, "true").strip().lower() in {"0", "false", "no", "off"}:
        yield None
        return
    directory = state / "logs"
    path: Path | None = directory / "deploy.log"
    handler: logging.Handler | None = None
    try:
        for candidate in (state, directory):
            if candidate.is_symlink():
                raise OSError(f"log directory must not be a symbolic link: {candidate}")
            candidate.mkdir(mode=0o700, exist_ok=True)
        handler = _PrivateRotatingFileHandler(
            path, maxBytes=LOG_FILE_MAX_BYTES, backupCount=LOG_FILE_BACKUPS, encoding="utf-8"
        )
    except OSError as error:
        LOG.warning(f"WARNING: file logging is disabled for this run: {error}")
        path = None
    else:
        handler.setFormatter(_ReadableFileFormatter())
        handler.addFilter(lambda record: bool(record.getMessage().strip()))
        LOG.addHandler(handler)
    try:
        yield path
    finally:
        if handler is not None:
            LOG.removeHandler(handler)
            handler.close()


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines without shell evaluation."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise DeployError(f"{path} is missing; copy .env.example with install -m 600") from None
    if not stat.S_ISREG(info.st_mode):
        raise DeployError(f"{path} must be a regular file, not a symbolic link")
    if info.st_mode & 0o077:
        raise DeployError(f"{path} holds secrets and must not be group- or world-accessible; run chmod 600")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ENV_LINE_RE.fullmatch(line)
        if not match:
            raise DeployError(f"{path}:{number}: expected KEY=VALUE")
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def _is_hostname(value: str) -> bool:
    return 0 < len(value) <= 253 and all(HOST_LABEL_RE.fullmatch(label) for label in value.split("."))


def _port(settings: dict[str, str], key: str) -> int:
    value = settings[key]
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise DeployError(f"{key} must be a port between 1 and 65535")
    return int(value)


def _positive_int(settings: dict[str, str], key: str) -> int:
    value = settings[key]
    if not value.isdigit() or int(value) < 1:
        raise DeployError(f"{key} must be a positive integer")
    return int(value)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _split_names(value: str) -> list[str]:
    return [name.strip() for name in value.split(",") if name.strip()]


def certificate_names(settings: dict[str, str]) -> list[str]:
    names = [settings["D3_SERVER_NAME"], settings["BGE_M3_SERVER_NAME"], *_split_names(settings["TLS_EXTRA_NAMES"])]
    return list(dict.fromkeys(names))


def validate(values: dict[str, str]) -> dict[str, str]:
    settings = {**DEFAULTS, **{key: value for key, value in values.items() if value != ""}}
    missing = [key for key in REQUIRED if not settings.get(key)]
    if missing:
        raise DeployError("required settings are missing: " + ", ".join(missing))
    for key in ("D3_API_KEY", "BGE_M3_API_KEY"):
        value = settings[key]
        if not API_KEY_RE.fullmatch(value) or value.startswith("replace_with_"):
            raise DeployError(f"{key} must be a random secret of at least 32 URL-safe characters ([A-Za-z0-9._~-])")
    if settings["D3_API_KEY"] == settings["BGE_M3_API_KEY"]:
        raise DeployError("D3_API_KEY and BGE_M3_API_KEY must be different secrets")
    for key in ("D3_SERVER_NAME", "BGE_M3_SERVER_NAME"):
        if not _is_hostname(settings[key]):
            raise DeployError(f"{key} must be a DNS hostname")
    for name in _split_names(settings["TLS_EXTRA_NAMES"]):
        if not _is_hostname(name) and not _is_ip(name):
            raise DeployError("TLS_EXTRA_NAMES must be a comma-separated list of hostnames or IP addresses")
    for key in ("D3_BIND_ADDRESS", "BGE_M3_BIND_ADDRESS"):
        try:
            ipaddress.ip_address(settings[key])
        except ValueError:
            raise DeployError(f"{key} must be an IP address") from None
    for key in ("D3_PUBLIC_PORT", "BGE_M3_PUBLIC_PORT"):
        _port(settings, key)
    if settings["D3_PUBLIC_PORT"] == settings["BGE_M3_PUBLIC_PORT"]:
        raise DeployError("D3_PUBLIC_PORT and BGE_M3_PUBLIC_PORT must differ")
    for key in ("D3_MEMORY_LIMIT", "D3_SHM_SIZE", "BGE_M3_MEMORY_LIMIT"):
        if not DOCKER_SIZE_RE.fullmatch(settings[key]):
            raise DeployError(f"{key} must be a Docker size such as 8g or 4gb")
    for key in ("BGE_M3_MAX_BATCH_TOKENS", "BGE_M3_MAX_CLIENT_BATCH_SIZE", "STARTUP_TIMEOUT", "TLS_RENEW_BEFORE_DAYS"):
        _positive_int(settings, key)
    if int(settings["BGE_M3_MAX_BATCH_TOKENS"]) < BGE_M3_MAX_INPUT_TOKENS:
        raise DeployError(f"BGE_M3_MAX_BATCH_TOKENS must be at least {BGE_M3_MAX_INPUT_TOKENS} to fit one full-length input")
    if not GPU_DEVICE_RE.fullmatch(settings["GPU_DEVICE_ID"]):
        raise DeployError("GPU_DEVICE_ID must be an nvidia-smi index or GPU UUID")
    return settings


def render_d3_config(api_key: str) -> str:
    # A JSON string is a valid YAML scalar, so the key cannot break the document.
    return f"""version: v0.3
listeners:
  - name: decisions
    address: 0.0.0.0
    port: 8899
    timeout: 30s
    api_keys:
      - {json.dumps(api_key)}
    systemone:
      models:
        - {D3_MODEL}
global:
  router:
    enabled: false
  model_catalog:
    deployments:
      primary:
        provider: model_runtime
        artifact: {D3_MODEL}
        revision: {D3_MODEL_REVISION}
        device: cuda:0
        input:
          max_tokens: {D3_MAX_INPUT_TOKENS}
          overflow: reject
    system:
      decision_model:
        deployment: primary
"""


def write_private(path: Path, content: str | bytes) -> None:
    data = content.encode("utf-8") if isinstance(content, str) else content
    if path.is_symlink():
        raise DeployError(f"refusing to write through a symbolic link: {path}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(data)
    os.chmod(path, 0o600)


def prepare_state(settings: dict[str, str], state: Path = STATE) -> None:
    for directory in (
        state,
        state / "mkcert",
        state / "tls",
        state / "d3",
        state / "d3" / "models",
        state / "d3" / "runtime",
        state / "bge-m3",
        state / "bge-m3" / "data",
    ):
        if directory.is_symlink():
            raise DeployError(f"state directory must not be a symbolic link: {directory}")
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    write_private(state / "d3" / "config.yaml", render_d3_config(settings["D3_API_KEY"]))
    write_private(state / "bge-m3.env", f"API_KEY={settings['BGE_M3_API_KEY']}\n")
    write_private(state / "d3.header", f"Authorization: Bearer {settings['D3_API_KEY']}\n")
    write_private(state / "bge-m3.header", f"Authorization: Bearer {settings['BGE_M3_API_KEY']}\n")
    write_private(state / "compose.env", f"HOST_UID={os.getuid()}\nHOST_GID={os.getgid()}\n")


def certificate_is_current(state: Path, names: list[str], renew_before_days: int) -> bool:
    certificate, root = state / "tls" / "tls.crt", state / "mkcert" / "rootCA.pem"
    recorded = state / "tls" / "names.txt"
    if not (certificate.is_file() and (state / "tls" / "tls.key").is_file() and root.is_file() and recorded.is_file()):
        return False
    if recorded.read_text(encoding="utf-8").split() != names:
        return False
    checks = (
        ["openssl", "x509", "-noout", "-checkend", str(renew_before_days * 86400), "-in", str(certificate)],
        ["openssl", "verify", "-CAfile", str(root), str(certificate)],
    )
    return all(subprocess.run(check, capture_output=True, check=False).returncode == 0 for check in checks)


def ensure_certificate(settings: dict[str, str], state: Path = STATE) -> bool:
    """Issue the TLS certificate from the dedicated mkcert CA; return True when it changed."""
    names = certificate_names(settings)
    if certificate_is_current(state, names, int(settings["TLS_RENEW_BEFORE_DAYS"])):
        return False
    # A dedicated CAROOT keeps this CA separate from any personal mkcert CA and
    # out of the host trust store; mkcert creates it on first use.
    result = subprocess.run(
        ["mkcert", "-cert-file", str(state / "tls" / "tls.crt"), "-key-file", str(state / "tls" / "tls.key"), *names],
        env={**os.environ, "CAROOT": str(state / "mkcert")},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise DeployError("mkcert could not issue the certificate: " + result.stderr.strip())
    for path in (state / "tls" / "tls.crt", state / "tls" / "tls.key"):
        os.chmod(path, 0o600)
    write_private(state / "tls" / "names.txt", "\n".join(names) + "\n")
    if not certificate_is_current(state, names, int(settings["TLS_RENEW_BEFORE_DAYS"])):
        raise DeployError("the issued certificate does not verify against the mkcert root CA")
    return True


def compose(env_file: Path, *arguments: str) -> None:
    command = [
        "docker", "compose",
        "--project-directory", str(HERE),
        "-f", str(COMPOSE_FILE),
        "--env-file", str(env_file),
        "--env-file", str(STATE / "compose.env"),
        *arguments,
    ]
    if subprocess.run(command, check=False).returncode:
        raise DeployError("docker compose " + " ".join(arguments) + " failed")


def curl(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["curl", "--silent", "--show-error", "--max-time", "120", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def probe_address(bind_address: str) -> str:
    address = ipaddress.ip_address(bind_address)
    if address.is_unspecified:
        return "127.0.0.1" if address.version == 4 else "::1"
    return bind_address


def wait_until(description: str, check: Callable[[], bool], deadline: float) -> None:
    while not check():
        if time.monotonic() >= deadline:
            raise DeployError(f"{description} did not succeed before STARTUP_TIMEOUT")
        time.sleep(10)


def _json(result: subprocess.CompletedProcess[str]) -> object:
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def probe_d3(settings: dict[str, str], deadline: float) -> None:
    name, port = settings["D3_SERVER_NAME"], settings["D3_PUBLIC_PORT"]
    base = f"https://{name}:{port}"
    tls = [
        "--cacert", str(STATE / "mkcert" / "rootCA.pem"),
        "--resolve", f"{name}:{port}:{probe_address(settings['D3_BIND_ADDRESS'])}",
    ]
    header = ["-H", f"@{STATE / 'd3.header'}"]

    def model_listed() -> bool:
        data = _json(curl("--fail", *tls, f"{base}/v1/systemone/models", *header))
        if not isinstance(data, dict):
            return False
        items = data.get("data", data.get("models", []))
        return any(isinstance(item, dict) and D3_MODEL in {item.get("id"), item.get("name")} for item in items)

    wait_until("D3-mini readiness", model_listed, deadline)
    unauthenticated = curl("--output", "/dev/null", "--write-out", "%{http_code}", *tls, f"{base}/v1/systemone/models")
    if unauthenticated.stdout.strip() not in {"401", "403"}:
        raise DeployError(f"D3-mini answered an unauthenticated request with HTTP {unauthenticated.stdout.strip()}")
    question = {
        "model": D3_MODEL,
        "state": "Implement a bounded coding change.",
        "questions": {"tier": {"type": "choice", "instructions": "Choose the required coding effort.", "criteria": {
            "SIMPLE": "Small local change", "MEDIUM": "Bounded multi-step change",
            "COMPLEX": "Cross-module change", "REASONING": "High-risk reasoning"}}},
    }
    data = _json(curl("--fail", *tls, f"{base}/v1/systemone", *header,
                      "-H", "Content-Type: application/json", "--data", json.dumps(question)))
    choice = data.get("answers", {}).get("tier", {}).get("choice") if isinstance(data, dict) else None
    if choice not in {"SIMPLE", "MEDIUM", "COMPLEX", "REASONING"}:
        raise DeployError("D3-mini probe returned an invalid tier response")


def probe_bge_m3(settings: dict[str, str], deadline: float) -> None:
    name, port = settings["BGE_M3_SERVER_NAME"], settings["BGE_M3_PUBLIC_PORT"]
    base = f"https://{name}:{port}"
    tls = [
        "--cacert", str(STATE / "mkcert" / "rootCA.pem"),
        "--resolve", f"{name}:{port}:{probe_address(settings['BGE_M3_BIND_ADDRESS'])}",
    ]
    body = json.dumps({"model": BGE_M3_MODEL, "input": "bge-m3 deployment probe"})
    wait_until("BGE-M3 readiness", lambda: curl("--fail", "--output", "/dev/null", *tls, f"{base}/health").returncode == 0, deadline)
    unauthenticated = curl("--output", "/dev/null", "--write-out", "%{http_code}", *tls, f"{base}/v1/embeddings",
                           "-H", "Content-Type: application/json", "--data", body)
    if unauthenticated.stdout.strip() != "401":
        raise DeployError(f"BGE-M3 answered an unauthenticated request with HTTP {unauthenticated.stdout.strip()}; expected 401")
    data = _json(curl("--fail", *tls, f"{base}/v1/embeddings", "-H", f"@{STATE / 'bge-m3.header'}",
                      "-H", "Content-Type: application/json", "--data", body))
    items = data.get("data") if isinstance(data, dict) else None
    vector = items[0].get("embedding") if isinstance(items, list) and items and isinstance(items[0], dict) else None
    if not isinstance(vector, list) or len(vector) != BGE_M3_DIMENSIONS:
        raise DeployError(f"BGE-M3 probe did not return a {BGE_M3_DIMENSIONS}-dimension embedding")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--render-only", action="store_true", help="validate and render state/ without starting containers")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE, help="settings file (default: gpu-host/.env)")
    arguments = parser.parse_args(argv)
    try:
        for tool in ("docker", "curl", "mkcert", "openssl"):
            if shutil.which(tool) is None:
                raise DeployError(f"{tool} is required")
        os.umask(0o077)
        env_file = arguments.env_file.resolve()
        settings = validate(parse_env_file(env_file))
        prepare_state(settings)
        renewed = ensure_certificate(settings)
        compose(env_file, "config", "--quiet")
        if arguments.render_only:
            LOG.info(f"Validated the GPU host deployment and rendered {STATE}")
            return 0
        compose(env_file, "up", "-d", "--remove-orphans")
        if renewed:
            # Bind-mounted files changed under a running proxy; reload them.
            compose(env_file, "restart", "tls")
        deadline = time.monotonic() + int(settings["STARTUP_TIMEOUT"])
        probe_d3(settings, deadline)
        probe_bge_m3(settings, deadline)
    except DeployError as error:
        LOG.error(f"ERROR: {error}")
        return 1
    LOG.info("D3-mini and BGE-M3 are ready over TLS. On the LiteLLM hosts:")
    LOG.info(f"  1. Copy {STATE / 'mkcert' / 'rootCA.pem'} (the public CA certificate only;")
    LOG.info("     never rootCA-key.pem) and run:")
    LOG.info("     ./scripts/build-gpu-host-ca-bundle.py --root-ca /path/to/rootCA.pem")
    LOG.info(f"  2. Set D3_DECISION_API_BASE=https://{settings['D3_SERVER_NAME']}:{settings['D3_PUBLIC_PORT']}")
    LOG.info(f"     and BGE_M3_API_BASE=https://{settings['BGE_M3_SERVER_NAME']}:{settings['BGE_M3_PUBLIC_PORT']}/v1")
    LOG.info("  3. Copy D3_API_KEY to D3_DECISION_API_KEY and BGE_M3_API_KEY unchanged.")
    return 0


if __name__ == "__main__":
    with file_logging():
        exit_code = main()
    sys.exit(exit_code)
