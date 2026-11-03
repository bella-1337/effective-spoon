"""MkDocs hooks that publish the repository's Markdown where it already lives.

The guides stay where they are readable on GitHub and GitLab (README.md,
ARCHITECTURE.md, docs/, diagrams/, gpu-host/, ...). Instead of copying them
into a separate docs directory, this hook adds them to the site under their
repository paths, so every relative link between them keeps working. It also:

- publishes the animated diagram HTML and the images under docs/;
- rewrites links to repository files that are not published (Compose files,
  scripts, Mermaid sources) and line anchors such as ``file.md#L12`` to the
  source browser at the built revision; a report named ``*-COMMIT-<sha>.md``
  links to the files as they were at that commit;
- generates the diagram gallery and the changelog pages;
- points each page's edit link at its file in the repository.

Build metadata comes from ``extra.build`` in mkdocs.yml, which
scripts/build-docs.py fills from DOCS_* environment variables.
"""

from __future__ import annotations

import html
import os
import posixpath
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from xml.etree.ElementTree import Element

from markdown import Extension, Markdown
from markdown.treeprocessors import Treeprocessor
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import File, Files, InclusionLevel
from mkdocs.structure.pages import Page
from mkdocs.utils import get_relative_url

# Directories never scanned for publishable files: tool state, generated
# output, private runtime data, and the site's own docs_dir.
EXCLUDED_DIRECTORIES = frozenset(
    {
        "backups",
        "dist",
        "docs-site",
        "logs",
        "node_modules",
        "public",
        "security-metrics",
        "security-reports",
        "security-state",
        "site",
        "upgrade-state",
    }
)
IMAGE_SUFFIXES = (".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp")
# The README becomes the "Overview" page because the site has its own home.
DESTINATIONS = {"README.md": "overview/index.html"}
GALLERY = "diagrams/gallery.md"
CHANGELOG = "changelog.md"
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
LINE_ANCHOR = re.compile(r"L\d+(?:-L\d+)?")
PINNED_REVISION = re.compile(r"-COMMIT-([0-9a-f]{7,40})\.md$")
GITHUB_PULL = re.compile(r"^Merge pull request #(\d+) from ")
GITLAB_MERGE_REQUEST = re.compile(r"^See merge request (?:\S+)?!(\d+)\s*$", re.MULTILINE)
MAX_BRANCH_COMMITS = 30
GIT_TIMEOUT_SECONDS = 60


@dataclass
class _State:
    root: Path = Path(".")
    repo_url: str = ""
    repo_kind: str = "github"
    source_ref: str = "HEAD"
    edit_branch: str = ""
    published: set[str] = field(default_factory=set)
    page: Page | None = None


STATE = _State()


# --------------------------------------------------------------------------
# Repository helpers


def walk_repository(root: Path) -> list[str]:
    """Repository-relative POSIX paths of regular files outside excluded directories."""
    found: list[str] = []
    for directory, subdirectories, filenames in os.walk(root):
        relative = Path(directory).relative_to(root)
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if not name.startswith(".")
            and not (relative == Path(".") and name in EXCLUDED_DIRECTORIES)
        )
        for name in filenames:
            path = Path(directory) / name
            if not name.startswith(".") and path.is_file() and not path.is_symlink():
                found.append((relative / name).as_posix())
    return sorted(found)


def is_markdown_source(path: str) -> bool:
    return path.endswith(".md")


def is_static_asset(path: str) -> bool:
    """Rendered diagrams and the images that guides embed."""
    parts = path.split("/")
    if len(parts) == 2 and parts[0] == "diagrams" and path.endswith(".html"):
        return True
    return parts[0] == "docs" and path.lower().endswith(IMAGE_SUFFIXES)


def source_url(path: str, *, directory: bool = False, ref: str | None = None) -> str:
    """Link to a repository path in the GitHub or GitLab source browser."""
    base = STATE.repo_url.rstrip("/")
    kind = "tree" if directory else "blob"
    separator = "/-/" if STATE.repo_kind == "gitlab" else "/"
    revision = quote(ref or STATE.source_ref, safe="")
    return f"{base}{separator}{kind}/{revision}/{quote(path)}".rstrip("/")


def edit_url(path: str) -> str:
    """Edit link on the default branch, or the source view for a release."""
    if not STATE.edit_branch:
        return source_url(path)
    base = STATE.repo_url.rstrip("/")
    separator = "/-/" if STATE.repo_kind == "gitlab" else "/"
    return f"{base}{separator}edit/{quote(STATE.edit_branch, safe='')}/{quote(path)}"


def escape_markdown(text: str) -> str:
    """Render untrusted text (commit messages, titles) as literal Markdown text."""
    text = re.sub(r"([\\`*_{}\[\]()#+!|~])", r"\\\1", text)
    return html.escape(text, quote=False)


def git(git_dir: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(git_dir), *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


# --------------------------------------------------------------------------
# Link rewriting


class RepositoryLinks(Treeprocessor):
    """Point links at unpublished repository files to the source browser.

    Runs after inline parsing and before MkDocs's own link resolution, so
    MkDocs still validates every link that stays inside the site.
    """

    def run(self, root: Element) -> None:
        page = STATE.page
        if page is None or not STATE.repo_url:
            return
        for element in root.iter():
            attribute = {"a": "href", "img": "src"}.get(element.tag)
            value = element.get(attribute) if attribute else None
            if value:
                rewritten = rewrite_link(page.file.src_uri, value)
                if rewritten != value:
                    element.set(attribute, rewritten)


class RepositoryLinksExtension(Extension):
    def extendMarkdown(self, md: Markdown) -> None:  # noqa: N802 - Markdown API
        # Inline links exist after priority 20; MkDocs resolves them at 0.
        md.treeprocessors.register(RepositoryLinks(md), "repository_links", 5)


def rewrite_link(page_src: str, url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme or parts.netloc or not parts.path or parts.path.startswith("/"):
        return url
    target = posixpath.normpath(posixpath.join(posixpath.dirname(page_src), parts.path))
    if target == ".." or target.startswith("../"):
        return url
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    line_anchor = bool(LINE_ANCHOR.fullmatch(parts.fragment))
    # A report written against one commit links to the files as they were then.
    pinned = PINNED_REVISION.search(posixpath.basename(page_src))
    ref = pinned.group(1) if pinned else STATE.source_ref
    if target in STATE.published:
        return source_url(target, ref=ref) + fragment if line_anchor else url
    if pinned:
        return source_url(target, ref=ref) + fragment
    path = STATE.root / target
    if path.is_dir():
        for index in ("README.md", "index.md"):
            candidate = posixpath.join(target, index) if target != "." else index
            if candidate in STATE.published:
                relative = posixpath.relpath(candidate, posixpath.dirname(page_src) or ".")
                return urlunsplit(("", "", relative, parts.query, parts.fragment))
        return source_url("" if target == "." else target, directory=True)
    if path.is_file():
        return source_url(target) + fragment
    return url


# --------------------------------------------------------------------------
# Generated pages


def mermaid_metadata(source: str) -> tuple[str, str]:
    title_match = re.search(r"^title:\s*(.+?)\s*$", source, re.MULTILINE)
    subtitle_match = re.search(r"^\s*%%\s*@subtitle\s+(.+?)\s*$", source, re.MULTILINE)
    return (
        title_match.group(1) if title_match else "",
        subtitle_match.group(1) if subtitle_match else "",
    )


def gallery_markdown(root: Path, gallery_url: str, url_for: dict[str, str]) -> str:
    """One embedded, sandboxed frame per rendered diagram."""
    lines = [
        "# Diagram gallery",
        "",
        "Every animated diagram of the stack on one page, generated from the",
        "Mermaid sources in `diagrams/`. Use the button in each frame to pause the",
        "animation, or open a diagram on its own page for the full size. The",
        "[diagram index](README.md) explains the conventions and how to update them.",
        "",
    ]
    for source in sorted((root / "diagrams").glob("[0-9][0-9]-*.mmd")):
        rendered = f"diagrams/{source.stem}.html"
        if rendered not in url_for:
            continue
        title, subtitle = mermaid_metadata(source.read_text(encoding="utf-8"))
        title = re.sub(r"^LiteLLM stack\s+[—-]\s+", "", title) or source.stem
        frame = html.escape(get_relative_url(url_for[rendered], gallery_url), quote=True)
        lines += [
            f"## {escape_markdown(title[:1].upper() + title[1:])}",
            "",
            *([escape_markdown(subtitle), ""] if subtitle else []),
            '<div class="diagram-frame">',
            f'  <iframe src="{frame}" title="{html.escape(title, quote=True)}"'
            ' loading="lazy" referrerpolicy="no-referrer"></iframe>',
            "</div>",
            "",
            f"[:material-fullscreen: Open full size]({source.stem}.html){{ .md-button }}"
            f" [:material-file-code-outline: Mermaid source]({source.name}){{ .md-button }}",
            "",
        ]
    return "\n".join(lines)


@dataclass
class Change:
    sha: str
    date: str
    title: str
    number: str = ""
    kind: str = ""
    details: list[str] = field(default_factory=list)


def parse_changes(git_dir: Path, revision: str) -> list[Change]:
    log = git(
        git_dir,
        "log",
        "--first-parent",
        "--format=%H%x1f%as%x1f%P%x1f%s%x1f%b%x1e",
        revision,
    )
    changes: list[Change] = []
    for record in (log or "").split("\x1e"):
        if not record.strip():
            continue
        sha, date, parents, subject, body = (record.strip("\n").split("\x1f") + [""] * 5)[:5]
        change = Change(sha=sha, date=date, title=subject.strip())
        parent_list = parents.split()
        body_lines = [line.strip() for line in body.splitlines() if line.strip()]
        github = GITHUB_PULL.match(subject)
        gitlab = GITLAB_MERGE_REQUEST.search(body)
        if github:
            change.number, change.kind = github.group(1), "github"
        elif gitlab:
            change.number, change.kind = gitlab.group(1), "gitlab"
        if len(parent_list) > 1:
            title_lines = [line for line in body_lines if not GITLAB_MERGE_REQUEST.match(line)]
            if title_lines:
                change.title = title_lines[0]
            merged = git(
                git_dir,
                "log",
                "--no-merges",
                f"--max-count={MAX_BRANCH_COMMITS}",
                "--format=%s",
                f"{parent_list[0]}..{parent_list[1]}",
            )
            change.details = [line for line in (merged or "").splitlines() if line.strip()]
        changes.append(change)
    return changes


def release_tags(git_dir: Path) -> dict[str, str]:
    """Map each release tag's commit to the tag name."""
    output = git(
        git_dir,
        "for-each-ref",
        "--format=%(refname:short)%09%(objectname)%09%(*objectname)",
        "refs/tags/v*",
    )
    tags: dict[str, str] = {}
    for line in (output or "").splitlines():
        name, target, peeled = (line.split("\t") + ["", ""])[:3]
        if RELEASE_TAG.match(name):
            tags[peeled or target] = name
    return tags


def change_link(change: Change) -> str:
    base = STATE.repo_url.rstrip("/")
    if not base:
        return f"`{change.sha[:7]}`"
    if change.kind == "github":
        return f"[#{change.number}]({base}/pull/{change.number})"
    if change.kind == "gitlab":
        return f"[!{change.number}]({base}/-/merge_requests/{change.number})"
    separator = "/-/" if STATE.repo_kind == "gitlab" else "/"
    return f"[`{change.sha[:7]}`]({base}{separator}commit/{change.sha})"


def changelog_markdown(git_dir: Path, revision: str) -> str:
    lines = [
        "# Changelog",
        "",
        "Every change merged into the default branch, newest first, grouped by",
        "release. Generated from the Git history when the site is built; merge",
        "requests and pull requests list the commits they brought in.",
        "",
    ]
    if git(git_dir, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}") is None:
        lines.append(
            '!!! note "History unavailable"\n    This build had no Git history, '
            "so the changelog could not be generated."
        )
        return "\n".join(lines) + "\n"
    if (git(git_dir, "rev-parse", "--is-shallow-repository") or "").strip() == "true":
        lines += [
            '!!! warning "Truncated history"',
            "    This build used a shallow clone, so older changes are missing.",
            "",
        ]
    tags = release_tags(git_dir)
    section_open = False
    for change in parse_changes(git_dir, revision):
        tag = tags.get(change.sha)
        if tag:
            lines += ["", f"## {tag}", "", f"Released {change.date}.", ""]
            section_open = True
        elif not section_open:
            lines += ["", "## Unreleased", ""]
            section_open = True
        lines.append(f"- **{escape_markdown(change.title)}** ({change_link(change)}, {change.date})")
        details = [d for d in change.details if d.strip() != change.title.strip()]
        lines += [f"    - {escape_markdown(detail)}" for detail in details]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# MkDocs events


def on_config(config: MkDocsConfig) -> MkDocsConfig:
    build: dict[str, Any] = config.extra.get("build") or {}
    STATE.root = Path(config.config_file_path).resolve().parent
    STATE.repo_url = config.repo_url or ""
    STATE.repo_kind = str(build.get("repo_kind") or "github").lower()
    STATE.source_ref = str(build.get("source_ref") or "HEAD")
    STATE.edit_branch = "" if build.get("kind") == "release" else str(build.get("default_branch") or "")
    STATE.page = None
    config.markdown_extensions.append(RepositoryLinksExtension())
    return config


def on_files(files: Files, config: MkDocsConfig) -> Files:
    root = STATE.root
    use_directory_urls = config.use_directory_urls
    repository = walk_repository(root)
    for src_uri in filter(is_markdown_source, repository):
        files.append(
            File(
                src_uri,
                str(root),
                config.site_dir,
                use_directory_urls,
                dest_uri=DESTINATIONS.get(src_uri),
            )
        )
    for src_uri in filter(is_static_asset, repository):
        files.append(File(src_uri, str(root), config.site_dir, use_directory_urls))

    url_for = {file.src_uri: file.url for file in files}
    gallery_url = File(GALLERY, None, config.site_dir, use_directory_urls).url
    files.append(
        File.generated(config, GALLERY, content=gallery_markdown(root, gallery_url, url_for))
    )
    build: dict[str, Any] = config.extra.get("build") or {}
    git_dir = Path(str(build.get("git_dir") or root))
    revision = str(build.get("revision") or "HEAD")
    files.append(File.generated(config, CHANGELOG, content=changelog_markdown(git_dir, revision)))

    STATE.published = {
        file.src_uri for file in files if file.inclusion is not InclusionLevel.EXCLUDED
    }
    return files


def on_serve(server: Any, config: MkDocsConfig, builder: Any) -> Any:
    """Live-reload the preview when a published repository file changes."""
    root = STATE.root
    for src_uri in walk_repository(root):
        if is_markdown_source(src_uri) or is_static_asset(src_uri):
            server.watch(str(root / src_uri))
    server.watch(str(root / "docs-site"))
    return server


def on_page_markdown(markdown: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    STATE.page = page
    src_uri = page.file.src_uri
    if page.file.generated_by or not STATE.repo_url:
        page.edit_url = None
    elif page.file.abs_src_path and Path(page.file.abs_src_path).is_relative_to(
        Path(config.docs_dir).resolve()
    ):
        docs_dir = Path(config.docs_dir).resolve().relative_to(STATE.root).as_posix()
        page.edit_url = edit_url(f"{docs_dir}/{src_uri}")
    else:
        page.edit_url = edit_url(src_uri)
    return markdown
