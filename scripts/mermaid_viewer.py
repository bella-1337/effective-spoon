"""Viewer pages that draw each Mermaid source in the browser.

GitHub Pages serves a ``.mmd`` file with a generic content type, so a browser
downloads it instead of showing it. For every source this module writes
``NN-name.mmd.html`` beside it: the diagram drawn by Mermaid itself, the source
text, zoom and download controls (see diagram_controls.py), and a download
link.

Mermaid.js (MIT, github.com/mermaid-js/mermaid) is served from the site, never
from a CDN, so the pages make no third-party requests. It is downloaded once at
the pinned version, verified against the SHA-256 digests below, and copied with
its licence into ``diagrams/assets/``, which Git ignores like the rendered HTML.
"""

from __future__ import annotations

import hashlib
import html
import os
import urllib.request
from pathlib import Path

import diagram_controls
from maintenance_common import fail

MERMAID_VERSION = "12.3.0"
# jsDelivr serves the files of the npm package unchanged; the digests were
# checked against the registry tarball, whose integrity npm publishes.
MERMAID_URL = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/"
MERMAID_FILES = {
    "dist/mermaid.min.js": "336df803d87a38b70a01e736e666c8f7601d2ce42f5e47c8fafe5d721074b9a3",
    "LICENSE": "ec9fb67dcb25eccc416ed56e1aab819222c805a2a4bfe4cb19e7556bf2ffde80",
}
# Where each file is published, relative to the diagrams directory.
ASSETS_DIR = "assets"
PUBLISHED_NAMES = {"dist/mermaid.min.js": "mermaid.min.js", "LICENSE": "mermaid-LICENSE.txt"}
VIEWER_SCRIPT_NAME = "mermaid-viewer.js"
VIEWER_SUFFIX = ".mmd.html"
HTTP_TIMEOUT_SECONDS = 60

# Mermaid adds inline styles to the SVG it draws, so styles may be inline;
# scripts may come only from the site itself. The PNG download draws the SVG
# from a blob: URL.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self'; base-uri 'none'; form-action 'none'"
)

VIEWER_SCRIPT = """\
"use strict";
// Draws every <pre class="mermaid"> on a diagram viewer page, then attaches the
// zoom and download controls. Labels are SVG text, not HTML, so the diagram can
// be drawn onto a canvas for the PNG download.
(() => {
  const status = document.getElementById("status");
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: dark ? "dark" : "default",
    htmlLabels: false,
    // SVG text wraps at this width; 320 keeps long names such as file paths whole.
    flowchart: { htmlLabels: false, wrappingWidth: 320 },
  });
  mermaid.run({ querySelector: "pre.mermaid" }).then(() => {
    document.querySelectorAll(".dc").forEach((root) => window.DiagramControls.attach(root));
  }).catch((error) => {
    status.textContent = `Mermaid could not draw this diagram: ${error && error.message ? error.message : error}`;
    status.hidden = false;
  });
})();
"""

STYLE = """\
:root { color-scheme: light dark; --bg: #ffffff; --fg: #1f2933; --muted: #52606d;
  --panel: #f5f7fa; --border: #d9e2ec; --link: #0b6bcb; --error: #b42318; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #0f172a; --fg: #e2e8f0; --muted: #94a3b8; --panel: #111c33;
    --border: #1e2a44; --link: #7cc4fa; --error: #fda29b; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
header, main { max-width: 1200px; margin: 0 auto; padding: 0 16px; }
header { padding-top: 16px; }
nav { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 14px; }
a { color: var(--link); }
h1 { font-size: 24px; margin: 12px 0 4px; }
.lead { color: var(--muted); margin: 0 0 16px; }
figure { margin: 0 0 16px; padding: 12px 16px 16px; background: var(--panel);
  border: 1px solid var(--border); border-radius: 8px;
  --dc-border: var(--border); --dc-border-hover: var(--muted); --dc-fg: var(--muted);
  --dc-fg-hover: var(--fg); --dc-btn-bg: var(--bg); --dc-focus: var(--link); --dc-error: var(--error);
  --dc-export-bg: var(--panel); }
pre.mermaid { margin: 0; text-align: center; background: transparent; }
pre.mermaid:not([data-processed]) { visibility: hidden; min-height: 120px; }
#status { color: var(--error); }
details { margin-bottom: 32px; }
summary { cursor: pointer; font-weight: 600; }
{CONTROLS}details pre { margin: 8px 0 0; padding: 12px; overflow-x: auto; background: var(--panel);
  border: 1px solid var(--border); border-radius: 8px; font: 13px/1.45 ui-monospace, Menlo, Consolas, monospace; }
"""


def viewer_name(source: Path) -> str:
    return source.stem + VIEWER_SUFFIX


def mermaid_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "litellm-stack" / "mermaid" / MERMAID_VERSION


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cache_name(name: str) -> str:
    return name.replace("/", "_")


def fetch_mermaid(cache: Path) -> Path:
    """The pinned Mermaid files, downloaded once and verified on every use."""
    cache.mkdir(parents=True, exist_ok=True)
    for name, digest in MERMAID_FILES.items():
        target = cache / _cache_name(name)
        if target.is_file() and not target.is_symlink() and _sha256(target) == digest:
            continue
        try:
            with urllib.request.urlopen(MERMAID_URL + name, timeout=HTTP_TIMEOUT_SECONDS) as response:
                body = response.read()
        except OSError as error:
            fail(f"could not download Mermaid {name}: {error}. Offline, copy the pinned files into {cache}.")
        if hashlib.sha256(body).hexdigest() != digest:
            fail(f"Mermaid {name} does not match its pinned SHA-256 digest; refusing to publish it")
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(body)
        os.replace(partial, target)
    return cache


def _write(target: Path, data: bytes) -> None:
    if target.is_symlink():
        fail(f"refusing to replace symbolic link: {target}")
    if target.is_file() and target.read_bytes() == data:
        return
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(data)
    os.replace(partial, target)


def install_assets(diagrams_dir: Path, cache: Path) -> Path:
    """Copy Mermaid, its licence, and the page scripts into diagrams/assets/."""
    assets = diagrams_dir / ASSETS_DIR
    if assets.is_symlink() or (assets.exists() and not assets.is_dir()):
        fail(f"{assets} is not a directory")
    assets.mkdir(exist_ok=True)
    for name, published in PUBLISHED_NAMES.items():
        _write(assets / published, (cache / _cache_name(name)).read_bytes())
    _write(assets / VIEWER_SCRIPT_NAME, VIEWER_SCRIPT.encode("utf-8"))
    _write(assets / diagram_controls.SCRIPT_NAME, diagram_controls.CONTROLS_SCRIPT.encode("utf-8"))
    return assets


def viewer_html(source_name: str, text: str, title: str, subtitle: str = "") -> str:
    """The viewer page for one Mermaid source; every value is HTML-escaped."""
    stem = source_name.removesuffix(".mmd")
    esc = html.escape
    source = esc(text.rstrip("\n"))
    lead = f'\n<p class="lead">{esc(subtitle)}</p>' if subtitle else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CONTENT_SECURITY_POLICY}">
<meta name="referrer" content="no-referrer">
<title>{esc(title)} — Mermaid</title>
<style>
{STYLE.replace("{CONTROLS}", diagram_controls.CONTROLS_STYLE)}</style>
</head>
<body>
<header>
<nav aria-label="Diagram links">
<a href="./">All diagrams</a>
<a href="{esc(stem)}.html">Animated version</a>
<a href="{esc(source_name)}" download>Download {esc(source_name)}</a>
</nav>
<h1>{esc(title)}</h1>{lead}
</header>
<main>
<figure class="dc" data-dc-name="{esc(stem)}" aria-label="{esc(title)} drawn by Mermaid">
<div class="dc-bar">{diagram_controls.toolbar_html(source_name)}</div>
<div class="dc-viewport"><pre class="mermaid">{source}</pre></div>
</figure>
<p id="status" role="alert" hidden></p>
<details open>
<summary>Mermaid source: {esc(source_name)}</summary>
<pre><code>{source}</code></pre>
</details>
</main>
<script src="{ASSETS_DIR}/{PUBLISHED_NAMES["dist/mermaid.min.js"]}"></script>
<script src="{ASSETS_DIR}/{diagram_controls.SCRIPT_NAME}"></script>
<script src="{ASSETS_DIR}/{VIEWER_SCRIPT_NAME}"></script>
</body>
</html>
"""


def write_viewer(source: Path, title: str, subtitle: str = "") -> Path:
    target = source.with_name(viewer_name(source))
    page = viewer_html(source.name, source.read_text(encoding="utf-8"), title, subtitle)
    _write(target, page.encode("utf-8"))
    return target
