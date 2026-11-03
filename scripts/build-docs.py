#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Build, preview, and assemble the versioned documentation site.

The pages are the repository's own Markdown files, published by Zensical
from mkdocs.yml. The toolchain is installed from the hash-locked
docs-site/requirements.txt of each version into a cached uv virtual
environment, so an old release is rebuilt with the toolchain it was released
with.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from maintenance_common import LOG, ROOT, capture, fail, run_main

USAGE = """Usage: scripts/build-docs.py COMMAND [OPTIONS]

Build the documentation site with the hash-locked Zensical toolchain from
docs-site/requirements.txt, installed with uv into .docs-cache/.

Commands:
  serve [--dev-addr HOST:PORT]
      Preview the working tree with live reload (default 127.0.0.1:8000).
  build
      Build the working tree once in strict mode into site/.
  site [--output DIR] [--dev-ref REF] [--site-url URL]
      Build the versioned site (default: public/): "dev" from the default
      branch, one version per release tag vMAJOR.MINOR.PATCH that contains
      mkdocs.yml, a "latest" copy of the newest release, versions.json for the
      version selector, and a root page that redirects to latest (or dev).
  versions [--dev-ref REF]
      Print the versions the site command would build, as JSON.
  verify-private URL
      Fail when URL serves content to an anonymous visitor.

Options for build, site, and versions:
  --repo-url URL        Repository for source and edit links
                        (default: from GitHub Actions or GitLab CI, else mkdocs.yml)
  --repo-kind KIND      github or gitlab (default: from CI, else github)
  --default-branch NAME Default branch (default: from CI or origin/HEAD, else master)

Environment:
  DOCS_ALLOW_PUBLIC_PAGES=true  let verify-private accept a public site
"""

CONFIG = ROOT / "mkdocs.yml"
REQUIREMENTS = Path("docs-site") / "requirements.txt"
CACHE = ROOT / ".docs-cache"
DOCS_PYTHON = "3.13"
OUTPUT_MARKER = ".docs-build"
SITE_DIR = "site"  # site_dir in mkdocs.yml; Zensical has no option to override it
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
VERSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REPO_KINDS = ("github", "gitlab")
LATEST = "latest"
DEV = "dev"
HTTP_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Version:
    name: str
    ref: str
    commit: str
    kind: str  # "dev" or "release"
    source_ref: str


@dataclass(frozen=True)
class Context:
    repo_url: str
    repo_kind: str
    default_branch: str


# --------------------------------------------------------------------------
# Git and repository context


def git(*args: str) -> str:
    return capture(["git", *args]).strip()


def git_ok(*args: str) -> bool:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, check=False, text=True
    )
    return result.returncode == 0


def detect_context(args: argparse.Namespace) -> Context:
    env = os.environ
    repo_url, repo_kind, default_branch = "", "github", ""
    if env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_REPOSITORY"):
        server = env.get("GITHUB_SERVER_URL", "https://github.com")
        repo_url = f"{server}/{env['GITHUB_REPOSITORY']}"
        default_branch = env.get("DOCS_DEFAULT_BRANCH", "")
    elif env.get("GITLAB_CI") == "true" and env.get("CI_PROJECT_URL"):
        repo_url, repo_kind = env["CI_PROJECT_URL"], "gitlab"
        default_branch = env.get("CI_DEFAULT_BRANCH", "")
    repo_url = args.repo_url or repo_url
    repo_kind = (args.repo_kind or repo_kind).lower()
    if repo_kind not in REPO_KINDS:
        fail(f"--repo-kind must be one of: {', '.join(REPO_KINDS)}")
    if repo_url and not repo_url.startswith("https://"):
        fail(f"repository URL must use https://: {repo_url}")
    default_branch = args.default_branch or default_branch or origin_head() or "master"
    return Context(repo_url=repo_url, repo_kind=repo_kind, default_branch=default_branch)


def origin_head() -> str:
    if not git_ok("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"):
        return ""
    return git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").removeprefix(
        "origin/"
    )


def resolve_dev_ref(requested: str | None, default_branch: str) -> str:
    candidates = (
        [requested]
        if requested
        else [f"refs/remotes/origin/{default_branch}", f"refs/heads/{default_branch}"]
    )
    for candidate in candidates:
        if git_ok("rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"):
            return candidate
    fail(
        f"cannot find the dev revision ({' or '.join(candidates)}); fetch the default "
        "branch or pass --dev-ref"
    )
    raise AssertionError("unreachable")


def has_docs(ref: str) -> bool:
    return all(
        git_ok("cat-file", "-e", f"{ref}:{path}") for path in ("mkdocs.yml", REQUIREMENTS.as_posix())
    )


def release_tags() -> list[str]:
    """Release tags, newest first."""
    names = [name for name in git("tag", "--list", "v*").splitlines() if RELEASE_TAG.match(name)]
    return sorted(names, key=lambda name: tuple(int(n) for n in RELEASE_TAG.match(name).groups()), reverse=True)


def plan_versions(dev_ref: str, default_branch: str) -> list[Version]:
    versions = [
        Version(
            name=DEV,
            ref=dev_ref,
            commit=git("rev-parse", f"{dev_ref}^{{commit}}"),
            kind="dev",
            source_ref=default_branch,
        )
    ]
    for tag in release_tags():
        if not has_docs(f"refs/tags/{tag}"):
            LOG.info(f"Skipping {tag}: it predates the documentation site.")
            continue
        versions.append(
            Version(
                name=tag,
                ref=f"refs/tags/{tag}",
                commit=git("rev-parse", f"refs/tags/{tag}^{{commit}}"),
                kind="release",
                source_ref=tag,
            )
        )
    return versions


def versions_manifest(versions: Sequence[Version]) -> list[dict[str, object]]:
    """The versions.json read by the Material version selector (mike format)."""
    releases = [version for version in versions if version.kind == "release"]
    manifest: list[dict[str, object]] = []
    for version in versions:
        if version.kind == "dev":
            manifest.append({"version": DEV, "title": "dev (unreleased)", "aliases": []})
        elif releases and version is releases[0]:
            manifest.append(
                {"version": version.name, "title": f"{version.name} (latest)", "aliases": [LATEST]}
            )
        else:
            manifest.append({"version": version.name, "title": version.name, "aliases": []})
    return manifest


# --------------------------------------------------------------------------
# Toolchain and Zensical


def ensure_toolchain(requirements: Path) -> Path:
    """Install the hash-locked toolchain once per requirements file content."""
    if not requirements.is_file() or requirements.is_symlink():
        fail(f"documentation requirements are missing or unsafe: {requirements}")
    if shutil.which("uv") is None:
        fail("uv is required; see docs/maintenance-runtime.md")
    # One environment per toolchain and platform; a cache shared between a host
    # and a container must not reuse the other's interpreter.
    platform_tag = f"{sys.platform}-{platform.machine()}-{DOCS_PYTHON}".encode()
    digest = hashlib.sha256(requirements.read_bytes() + platform_tag).hexdigest()[:16]
    venv = CACHE / "venvs" / digest
    python = venv / "bin" / "python"
    marker = venv / ".installed"
    if marker.is_file() and python.is_file():
        return python
    LOG.info(f"Installing the documentation toolchain ({digest}).")
    CACHE.mkdir(mode=0o700, exist_ok=True)
    shutil.rmtree(venv, ignore_errors=True)
    for command in (
        ["uv", "venv", "--quiet", "--python", DOCS_PYTHON, str(venv)],
        [
            "uv", "pip", "install", "--quiet", "--require-hashes",
            "--python", str(python), "--requirement", str(requirements),
        ],
    ):
        if subprocess.run(command, cwd=ROOT, check=False).returncode:
            fail(f"could not install the documentation toolchain from {requirements}")
    marker.write_text(digest + "\n", encoding="utf-8")
    return python


def docs_environment(
    context: Context,
    *,
    version: str,
    kind: str,
    source_ref: str,
    commit: str,
    site_url: str = "",
    latest: str = "",
) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "DOCS_VERSION": version,
            "DOCS_VERSION_KIND": kind,
            "DOCS_SOURCE_REF": source_ref,
            "DOCS_GIT_COMMIT": commit,
            "DOCS_EDIT_URI": edit_uri(context, kind, source_ref),
            "DOCS_DEFAULT_BRANCH": context.default_branch,
            "DOCS_BUILD_DATE": datetime.now(timezone.utc).strftime("%Y-%m-%d UTC"),
            "DOCS_LATEST_VERSION": latest,
            "DOCS_SITE_URL": site_url,
        }
    )
    if context.repo_url:
        env["DOCS_REPO_URL"] = context.repo_url
    return env


def edit_uri(context: Context, kind: str, source_ref: str) -> str:
    """Edit on the default branch; a release links to its file at the tag."""
    prefix = "-/" if context.repo_kind == "gitlab" else ""
    if kind == "release":
        return f"{prefix}blob/{source_ref}/"
    return f"{prefix}edit/{context.default_branch}/"


def zensical(python: Path, arguments: Sequence[str], *, config: Path, env: dict[str, str]) -> None:
    command = [str(python.parent / "zensical"), *arguments, "--config-file", str(config)]
    if subprocess.run(command, cwd=config.parent, env=env, check=False).returncode:
        fail(f"zensical {arguments[0]} failed for {config}")


def check_published(site: Path) -> None:
    """Fail when a build published a hidden path, such as the toolchain cache."""
    leaked = sorted(
        path.relative_to(site).as_posix()
        for path in site.rglob("*")
        if any(part.startswith(".") for part in path.relative_to(site).parts)
    )
    if leaked:
        fail(
            "the build published hidden paths; fix exclude_docs in mkdocs.yml: "
            + ", ".join(leaked[:5])
        )


def build_into(python: Path, source: Path, output: Path, env: dict[str, str]) -> None:
    """Build source in strict mode and move its site directory to output."""
    site = source / SITE_DIR
    if site.is_symlink() or (site.exists() and not site.is_dir()):
        fail(f"{site} is not a directory")
    shutil.rmtree(site, ignore_errors=True)
    zensical(python, ["build", "--strict", "--clean"], config=source / "mkdocs.yml", env=env)
    if not (site / "index.html").is_file():
        fail(f"zensical produced no {SITE_DIR}/index.html in {source}")
    check_published(site)
    shutil.rmtree(output, ignore_errors=True)
    shutil.move(str(site), str(output))


def prepare_output(raw: str) -> Path:
    """Create or reuse an output directory this script owns, emptied."""
    output = Path(raw).expanduser()
    output = (output if output.is_absolute() else ROOT / output).resolve()
    if output in (ROOT, Path("/"), Path.home()) or ROOT.is_relative_to(output):
        fail(f"refusing to use {output} as the output directory")
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        fail(f"output path is not a directory: {output}")
    if output.exists() and any(output.iterdir()) and not (output / OUTPUT_MARKER).is_file():
        fail(f"{output} is not empty and was not created by this script; remove it first")
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    (output / OUTPUT_MARKER).write_text("Generated by scripts/build-docs.py\n", encoding="utf-8")
    return output


def export_tree(ref: str, destination: Path) -> None:
    """Extract the committed tree of ref with the safe tarfile data filter."""
    if not hasattr(tarfile, "data_filter"):
        fail("Python with the tarfile data filter (3.11.4 or newer) is required")
    with subprocess.Popen(
        ["git", "archive", "--format=tar", ref], cwd=ROOT, stdout=subprocess.PIPE
    ) as archive:
        assert archive.stdout is not None
        with tarfile.open(fileobj=archive.stdout, mode="r|") as tar:
            tar.extractall(destination, filter="data")
    if archive.returncode:
        fail(f"git archive failed for {ref}")


# --------------------------------------------------------------------------
# Site assembly


def redirect_page(target: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<meta http-equiv="refresh" content="0; url={target}">
<link rel="canonical" href="{target}">
<title>LiteLLM stack documentation</title>
</head>
<body>
<p><a href="{target}">Continue to the LiteLLM stack documentation</a>.</p>
</body>
</html>
"""


def not_found_page(home: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<title>Page not found · LiteLLM stack documentation</title>
<style>body{{font-family:system-ui,sans-serif;max-width:36rem;margin:4rem auto;padding:0 1rem;color:#0f172a}}a{{color:#0e7490}}</style>
</head>
<body>
<h1>Page not found</h1>
<p>This page does not exist in this version of the documentation. It may have
moved or been added in a different version.</p>
<p><a href="{home}">Go to the documentation home</a>.</p>
</body>
</html>
"""


def write_root_files(output: Path, versions: Sequence[Version], site_url: str) -> None:
    releases = [version for version in versions if version.kind == "release"]
    default = f"{LATEST}/" if releases else f"{DEV}/"
    (output / "versions.json").write_text(
        json.dumps(versions_manifest(versions), indent=2) + "\n", encoding="utf-8"
    )
    (output / "index.html").write_text(redirect_page(default), encoding="utf-8")
    home = f"{site_url.rstrip('/')}/" if site_url else "/"
    (output / "404.html").write_text(not_found_page(home), encoding="utf-8")
    (output / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")


def build_site(args: argparse.Namespace) -> None:
    context = detect_context(args)
    if args.site_url and not args.site_url.startswith("https://"):
        fail(f"--site-url must use https://: {args.site_url}")
    dev_ref = resolve_dev_ref(args.dev_ref, context.default_branch)
    versions = plan_versions(dev_ref, context.default_branch)
    releases = [version for version in versions if version.kind == "release"]
    latest = releases[0].name if releases else ""
    output = prepare_output(args.output or "public")
    CACHE.mkdir(mode=0o700, exist_ok=True)
    for version in versions:
        LOG.info(f"Building {version.name} from {version.ref} ({version.commit[:12]}).")
        # Not under .docs-cache: Zensical matches exclude_docs against the
        # full path, so a hidden parent directory would exclude every page.
        with tempfile.TemporaryDirectory(prefix="litellm-docs-") as work:
            source = Path(work) / "src"
            source.mkdir()
            export_tree(version.commit, source)
            python = ensure_toolchain(source / REQUIREMENTS)
            site_url = f"{args.site_url.rstrip('/')}/{version.name}/" if args.site_url else ""
            env = docs_environment(
                context,
                version=version.name,
                kind=version.kind,
                source_ref=version.source_ref,
                commit=version.commit,
                site_url=site_url,
                latest=latest,
            )
            build_into(python, source, output / version.name, env)
    if latest:
        shutil.copytree(output / latest, output / LATEST)
    write_root_files(output, versions, args.site_url or "")
    LOG.info(
        f"PASS: built {', '.join(v.name for v in versions)}"
        f"{f' ({LATEST} = {latest})' if latest else ''} into {output}"
    )


def build_working_tree(args: argparse.Namespace) -> None:
    context = detect_context(args)
    python = ensure_toolchain(ROOT / REQUIREMENTS)
    commit = git("rev-parse", "HEAD") if git_ok("rev-parse", "--verify", "--quiet", "HEAD") else ""
    env = docs_environment(
        context,
        version="local",
        kind="local",
        source_ref=context.default_branch,
        commit=commit,
    )
    zensical(python, ["build", "--strict", "--clean"], config=CONFIG, env=env)
    check_published(ROOT / SITE_DIR)
    LOG.info(f"PASS: built the working tree into {ROOT / SITE_DIR}")


def serve(args: argparse.Namespace) -> None:
    context = detect_context(args)
    python = ensure_toolchain(ROOT / REQUIREMENTS)
    env = docs_environment(
        context,
        version="local",
        kind="local",
        source_ref=context.default_branch,
        commit="",
    )
    zensical(python, ["serve", "--dev-addr", args.dev_addr], config=CONFIG, env=env)


def print_versions(args: argparse.Namespace) -> None:
    context = detect_context(args)
    versions = plan_versions(resolve_dev_ref(args.dev_ref, context.default_branch), context.default_branch)
    print(json.dumps(versions_manifest(versions), indent=2))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def anonymous_status(url: str) -> int:
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(
        url, method="GET", headers={"User-Agent": "litellm-stack-docs-visibility-check"}
    )
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except (urllib.error.URLError, OSError) as error:
        fail(f"cannot reach {url} to check its visibility: {error}")
    raise AssertionError("unreachable")


def verify_private(args: argparse.Namespace) -> None:
    url = args.url
    if not url:
        fail("no Pages URL to check; enable Pages for the project first")
    if not url.startswith("https://"):
        fail(f"the Pages URL must use https://: {url}")
    allow_public = os.environ.get("DOCS_ALLOW_PUBLIC_PAGES", "").strip().lower() == "true"
    status = anonymous_status(url)
    if 200 <= status < 300:
        if allow_public:
            LOG.warning(f"WARNING: {url} is public; DOCS_ALLOW_PUBLIC_PAGES=true accepts that.")
            return
        fail(
            f"{url} serves content to anonymous visitors (HTTP {status}). Restrict Pages "
            "access to project members (see docs/documentation-site.md) or set "
            "DOCS_ALLOW_PUBLIC_PAGES=true to publish publicly on purpose."
        )
    if status == 404:
        LOG.warning(
            f"WARNING: {url} returned 404, so its visibility cannot be checked before the "
            "first deployment. Confirm the Pages access setting after it is published."
        )
        return
    if 300 <= status < 400 or status in (401, 403):
        LOG.info(f"PASS: {url} requires authentication (HTTP {status}).")
        return
    fail(f"unexpected HTTP {status} from {url}; cannot confirm that it is private")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="scripts/build-docs.py", add_help=False)
    commands = root.add_subparsers(dest="command")

    def with_context(command: argparse.ArgumentParser) -> argparse.ArgumentParser:
        command.add_argument("--repo-url")
        command.add_argument("--repo-kind", choices=REPO_KINDS)
        command.add_argument("--default-branch")
        return command

    with_context(commands.add_parser("serve", add_help=False)).add_argument(
        "--dev-addr", default="127.0.0.1:8000"
    )
    with_context(commands.add_parser("build", add_help=False))
    site = with_context(commands.add_parser("site", add_help=False))
    site.add_argument("--output")
    site.add_argument("--dev-ref")
    site.add_argument("--site-url", default="")
    with_context(commands.add_parser("versions", add_help=False)).add_argument("--dev-ref")
    commands.add_parser("verify-private", add_help=False).add_argument("url")
    return root


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(USAGE, end="")
        return
    try:
        args = parser().parse_args(argv)
    except SystemExit:
        fail("invalid arguments; run scripts/build-docs.py --help", exit_code=2)
    if args.command is None:
        fail("missing command; run scripts/build-docs.py --help", exit_code=2)
    handlers = {
        "serve": serve,
        "build": build_working_tree,
        "site": build_site,
        "versions": print_versions,
        "verify-private": verify_private,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    run_main(main)
