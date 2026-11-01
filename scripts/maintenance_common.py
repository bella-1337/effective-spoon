"""Shared safety and process helpers for the maintenance uv scripts."""

from __future__ import annotations

import hashlib
import base64
import http.client
import json
import os
import re
import shlex
import signal
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator, Sequence

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
IMAGE_REF_RE = re.compile(r"^[A-Za-z0-9._/:-]+@sha256:[0-9a-f]{64}$")
STATE_NAME_RE = re.compile(r"^litellm-upgrade-\d{8}T\d{6}Z$")
BACKUP_NAME_RE = re.compile(r"^litellm-stack-\d{8}T\d{6}Z$")


class ScriptError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def fail(message: str, exit_code: int = 1) -> "None":
    raise ScriptError(message, exit_code)


def emit_error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


ENV_FILE_REMEDY = (
    "maintenance tooling reads only regular environment files, never symlinks; "
    "write secrets-manager output to a private regular file instead, for example "
    "'install -m 600 /run/secrets/litellm.env .env'"
)


def env_file_is_present(path: Path) -> bool:
    """Return whether a regular env file exists; fail on symlinks and non-files."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        fail(f"environment file is unsafe: {path}; {ENV_FILE_REMEDY}")
    return path.is_file()


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def run(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path = ROOT,
    input_data: bytes | str | None = None,
    capture: bool = False,
    check: bool = True,
    text: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Run a fixed argument vector without a shell."""
    command = [str(arg) for arg in args]
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            input=input_data,
            capture_output=capture,
            check=False,
            shell=False,
            text=text and not isinstance(input_data, (bytes, bytearray)),
            timeout=timeout,
        )
    except FileNotFoundError:
        fail(f"{command[0]} is required")
    except subprocess.TimeoutExpired:
        fail(f"command timed out: {command[0]}")
    if check and result.returncode:
        if capture:
            stderr = result.stderr.decode() if isinstance(result.stderr, bytes) else result.stderr
            fail(stderr.strip() or f"command failed (exit code {result.returncode}): {command[0]}")
        fail(f"command failed (exit code {result.returncode}): {command[0]}")
    return result


def capture(args: Sequence[str | os.PathLike[str]], *, cwd: Path = ROOT, input_data: bytes | str | None = None,
            check: bool = True, timeout: float | None = None) -> str:
    result = run(args, cwd=cwd, input_data=input_data, capture=True, check=check, timeout=timeout)
    assert isinstance(result.stdout, str)
    return result.stdout


def run_python_script(
    script: Path,
    arguments: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path = ROOT,
    capture_stdout: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a repository uv script with its declared environment and cleanup signals."""
    uv = shutil.which("uv")
    if uv is None:
        fail("uv is required")
    command = [uv, "run", "--script", str(script), *(str(argument) for argument in arguments)]
    child = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE if capture_stdout else None,
        stderr=None,
        text=True,
    )
    handled_signals = [
        sig
        for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None))
        if sig is not None
    ]
    previous_handlers = {sig: signal.getsignal(sig) for sig in handled_signals}
    received_signal: int | None = None

    def forward(signum: int, _frame: object) -> None:
        nonlocal received_signal
        received_signal = signum
        if child.poll() is None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    for sig in handled_signals:
        signal.signal(sig, forward)
    try:
        stdout, _ = child.communicate()
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    if received_signal == signal.SIGINT:
        raise KeyboardInterrupt
    if received_signal is not None:
        raise SystemExit(128 + received_signal)
    return subprocess.CompletedProcess(command, child.returncode, stdout, None)


def run_to_file(args: Sequence[str | os.PathLike[str]], destination: Path, *, cwd: Path = ROOT) -> None:
    """Run a fixed command vector and write its binary stdout to a private file."""
    command = [str(arg) for arg in args]
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as target:
            result = subprocess.run(command, cwd=cwd, stdout=target, stderr=None, check=False, shell=False)
    except FileNotFoundError:
        fail(f"{command[0]} is required")
    if result.returncode:
        fail(f"command failed (exit code {result.returncode}): {command[0]}")


def run_from_file(
    args: Sequence[str | os.PathLike[str]],
    source: Path,
    *,
    cwd: Path = ROOT,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run a fixed command vector with a file streamed to standard input."""
    command = [str(arg) for arg in args]
    try:
        with source.open("rb") as input_file:
            result = subprocess.run(
                command,
                cwd=cwd,
                stdin=input_file,
                stdout=subprocess.DEVNULL,
                check=False,
                shell=False,
            )
    except FileNotFoundError:
        fail(f"{command[0]} is required")
    if check and result.returncode:
        fail(f"command failed (exit code {result.returncode}): {command[0]}")
    return result


def require_docker(*, daemon: bool = False, compose_plugin: bool = True) -> None:
    if not command_exists("docker"):
        fail("docker is required")
    if daemon:
        result = run(["docker", "info"], capture=True, check=False)
        if result.returncode:
            fail("docker daemon is not reachable", 2)
    if compose_plugin:
        result = run(["docker", "compose", "version"], capture=True, check=False)
        if result.returncode:
            fail("docker compose is required")


def compose_command(
    args: Sequence[str | os.PathLike[str]],
) -> list[str]:
    command = ["docker", "compose"]
    string_args = [str(arg) for arg in args]
    has_explicit_env = any(
        arg == "--env-file" or arg.startswith("--env-file=") for arg in string_args
    )
    if not has_explicit_env:
        env_file = ROOT / ".env"
        optional_env_file = ROOT / ".env.optional"
        env_present = env_file_is_present(env_file)
        optional_present = env_file_is_present(optional_env_file)
        if optional_present and not env_present:
            fail(".env.optional cannot be loaded without a regular .env file")
        if env_present:
            command.extend(("--env-file", str(env_file)))
        if optional_present:
            command.extend(("--env-file", str(optional_env_file)))
    command.extend(string_args)
    return command


def compose(
    args: Sequence[str | os.PathLike[str]],
    *,
    input_data: bytes | str | None = None,
    capture_output: bool = False,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    return run(compose_command(args), input_data=input_data, capture=capture_output,
               check=check, timeout=timeout)


def compose_capture(args: Sequence[str | os.PathLike[str]], *, input_data: bytes | str | None = None,
                    check: bool = True, timeout: float | None = None) -> str:
    result = compose(args, input_data=input_data, capture_output=True, check=check, timeout=timeout)
    assert isinstance(result.stdout, str)
    return result.stdout


def compose_from_file(
    args: Sequence[str | os.PathLike[str]],
    source: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return run_from_file(compose_command(args), source, check=check)


def service_is_running(service: str) -> bool:
    result = compose(["ps", "--services", "--status", "running"], capture_output=True, check=False)
    assert isinstance(result.stdout, str)
    return service in result.stdout.splitlines()


@lru_cache(maxsize=None)
def container_environment(service: str, name: str) -> str:
    result = compose(
        ["exec", "-T", service, "printenv", name],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        fail(f"could not read {name} from service '{service}'")
    assert isinstance(result.stdout, str)
    value = result.stdout.rstrip("\r\n")
    if not value or "\n" in value or "\r" in value:
        fail(f"service '{service}' has an invalid {name}")
    return value


def container_http_request(
    service: str,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 30,
) -> tuple[int, bytes]:
    """Make an HTTP request with the Python runtime already in a container."""
    if not url.startswith(("http://", "https://")):
        fail("container HTTP URL must use http or https")
    program = """
import base64
import json
import sys
import urllib.error
import urllib.request

request = urllib.request.Request(
    sys.argv[1],
    data=base64.b64decode(sys.argv[4]) if sys.argv[4] else None,
    headers=json.loads(sys.argv[3]),
    method=sys.argv[2],
)
try:
    with urllib.request.urlopen(request, timeout=float(sys.argv[5])) as response:
        status, body = response.status, response.read()
except urllib.error.HTTPError as error:
    status, body = error.code, error.read()
except urllib.error.URLError:
    status, body = 0, b""
print(json.dumps({"status": status, "body": base64.b64encode(body).decode("ascii")}))
""".strip()
    result = compose(
        [
            "exec",
            "-T",
            service,
            "python3",
            "-c",
            program,
            url,
            method,
            json.dumps(headers or {}, separators=(",", ":")),
            base64.b64encode(data).decode("ascii") if data is not None else "",
            str(timeout),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return 0, b""
    assert isinstance(result.stdout, str)
    try:
        payload = json.loads(result.stdout)
        status = payload["status"]
        body = payload["body"]
        if not isinstance(status, int) or not isinstance(body, str):
            raise ValueError
        return status, base64.b64decode(body, validate=True)
    except (KeyError, ValueError, json.JSONDecodeError):
        fail(f"invalid HTTP response from Python in service '{service}'")


def require_regular(path: Path, message: str | None = None) -> None:
    if not path.is_file() or path.is_symlink():
        fail(message or f"file is missing or unsafe: {path}")


def require_directory(path: Path, message: str | None = None) -> Path:
    if not path.is_dir() or path.is_symlink():
        fail(message or f"directory does not exist or is a symbolic link: {path}")
    return path.resolve()


def safe_output_root(raw_path: str, name: str) -> Path:
    path = Path(raw_path).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = path.resolve()
    if path == Path("/"):
        fail(f"{name} must not be the filesystem root")
    os.chmod(path, 0o700)
    return path


@contextmanager
def secure_tempdir(parent: Path, prefix: str) -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    os.chmod(path, 0o700)
    try:
        yield path
    finally:
        if path.exists():
            for child in path.iterdir():
                if child.is_file() and not child.is_symlink():
                    child.unlink()
            try:
                path.rmdir()
            except OSError:
                pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_checksum_manifest(directory: Path, filenames: Sequence[str], name: str = "SHA256SUMS") -> None:
    lines = []
    for filename in filenames:
        path = directory / filename
        require_regular(path)
        lines.append(f"{sha256_file(path)}  {filename}\n")
    write_private_file(directory / name, "".join(lines))


def verify_checksum_manifest(directory: Path, allowed: set[str], *, require_metadata: bool = False) -> set[str]:
    manifest = directory / "SHA256SUMS"
    require_regular(manifest, "SHA256SUMS is missing or unsafe")
    verified: set[str] = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match:
            fail("invalid SHA256SUMS entry")
        expected, filename = match.groups()
        if filename not in allowed:
            fail(f"unsafe filename in SHA256SUMS: {filename}")
        if filename in verified:
            fail(f"duplicate SHA256SUMS entry: {filename}")
        path = directory / filename
        require_regular(path, f"checksummed file is missing or unsafe: {filename}")
        if sha256_file(path) != expected:
            fail(f"checksum mismatch for {filename}")
        verified.add(filename)
    if not verified:
        fail("SHA256SUMS is empty")
    if require_metadata and "metadata.txt" not in verified:
        fail("metadata.txt is not covered by SHA256SUMS")
    return verified


def write_private_file(path: Path, content: str) -> None:
    if path.is_symlink():
        fail(f"refusing to write through symbolic link: {path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        target.write(content)
    os.chmod(path, 0o600)


def parse_key_values(path: Path, allowed: set[str], label: str) -> dict[str, str]:
    require_regular(path)
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            fail(f"invalid {label} entry")
        key, value = line.split("=", 1)
        if not key:
            fail(f"invalid empty {label} key")
        if key in values:
            fail(f"duplicate {label} key: {key}")
        if key not in allowed:
            fail(f"unsupported {label} key: {key}")
        values[key] = value
    return values


PUBLIC_HOST_VARIABLES = (
    "LITELLM_PROD_HOST",
    "LITELLM_DEV_HOST",
    "OPENSEARCH_DASHBOARDS_HOST",
    "GRAFANA_HOST",
    "LANGFUSE_WEB_HOST",
    "LANGFUSE_S3_HOST",
)
HOSTNAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:(?!-)[A-Za-z0-9-]{1,63}(?<!-)\.)+(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
)
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ENV_REFERENCE_RE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?P<op>:?[-?])?|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)


def _dotenv_files() -> tuple[Path, ...]:
    """Return env files in Compose precedence order (highest first).

    Later ``--env-file`` arguments override earlier ones, and the optional
    module's ``include.env_file`` defaults have the lowest precedence.
    """
    return (ROOT / ".env.optional", ROOT / ".env", ROOT / ".env.optional.example")


def _dotenv_raw_value(name: str) -> str | None:
    for dotenv in _dotenv_files():
        if not dotenv.is_file() or dotenv.is_symlink():
            continue
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                value = line.partition("=")[2]
                if value:
                    return value
    return None


def _expand_env_value(value: str, seen: frozenset[str]) -> str:
    """Expand a dotenv value the way Docker Compose does.

    Single-quoted values are literal; otherwise ${VAR}, $VAR, nested
    ${VAR:-default}/${VAR-default}, ${VAR:?error}/${VAR?error}, and $$ apply.
    """
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return _interpolate(value, seen)


def _interpolate(value: str, seen: frozenset[str]) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if value.startswith("$$", index):
            output.append("$")
            index += 2
            continue
        match = _ENV_REFERENCE_RE.match(value, index)
        if not match:
            output.append(value[index])
            index += 1
            continue
        name = match.group("braced") or match.group("bare")
        resolved = _resolve_env(name, seen) if name not in seen else None
        index = match.end()
        if match.group("bare"):
            output.append(resolved or "")
            continue
        operator = match.group("op") or ""
        argument_start, depth = index, 1
        while index < len(value) and depth:
            if value[index] == "{":
                depth += 1
            elif value[index] == "}":
                depth -= 1
            index += 1
        if depth:
            fail(f"unterminated variable reference in environment value: ${{{name}")
        argument = value[argument_start : index - 1]
        if not operator and argument:
            fail(f"invalid variable reference in environment value: ${{{name}")
        is_unset = resolved is None if operator in {"-", "?"} else not resolved
        if operator.endswith("-") and is_unset:
            output.append(_interpolate(argument, seen))
        elif operator.endswith("?") and is_unset:
            fail(argument or f"required variable {name} is missing a value")
        else:
            output.append(resolved or "")
    return "".join(output)


def _resolve_env(name: str, seen: frozenset[str] = frozenset()) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    raw = _dotenv_raw_value(name)
    if raw is None:
        return None
    return _expand_env_value(raw, seen | {name})


def env_or_dotenv(name: str, default: str) -> str:
    """Resolve a value from the environment, then the Compose env files.

    References such as ``llm.${STACK_DOMAIN}`` are expanded the same way Docker
    Compose expands them, so scripts never need their own hard-coded values.
    """
    if not _ENV_NAME_RE.fullmatch(name):
        fail(f"invalid environment variable name: {name}")
    return _resolve_env(name) or default


def public_host(name: str) -> str:
    """Return a configured public hostname or fail with a clear remedy."""
    value = env_or_dotenv(name, "")
    if not HOSTNAME_RE.fullmatch(value):
        shown = value or "<unset>"
        fail(
            f"{name}={shown} is not a DNS hostname; set STACK_DOMAIN in .env or "
            f"{name} to a full hostname without scheme, port, or path"
        )
    return value


def stack_env_exports() -> str:
    """Render resolved STACK_DOMAIN and public hostnames as shell exports."""
    lines = []
    for name in ("STACK_DOMAIN", *PUBLIC_HOST_VARIABLES):
        value = env_or_dotenv(name, "")
        if name != "STACK_DOMAIN":
            value = public_host(name)
        lines.append(f"export {name}={shlex.quote(value)}")
    return "\n".join(lines) + "\n"


def http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 30,
    context: ssl.SSLContext | None = None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        return 0, b""


def run_main(main: Callable[[], None]) -> None:
    handled_signals = [
        sig
        for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None))
        if sig is not None
    ]
    previous_handlers = {sig: signal.getsignal(sig) for sig in handled_signals}

    def terminate(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    for sig in handled_signals:
        signal.signal(sig, terminate)
    try:
        try:
            main()
        except ScriptError as error:
            emit_error(str(error))
            raise SystemExit(error.exit_code)
        except KeyboardInterrupt:
            raise SystemExit(130)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
