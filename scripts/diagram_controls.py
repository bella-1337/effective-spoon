"""Zoom and download controls for the diagram pages.

Every rendered diagram page gets a toolbar: zoom out, reset, zoom in, and
downloads of the diagram as SVG and PNG and of its Mermaid source. Only the
diagram zooms. The legend stays at its size: an architecture diagram's legend,
which dashmotion draws inside the diagram's SVG, is moved into its own SVG
below the zoom area, and a sequence diagram's legend is already HTML outside
it. A downloaded SVG or PNG still contains the legend.

The same script serves both kinds of page. The animated pages carry it inline,
so they stay self-contained; the Mermaid viewer pages load it from
diagrams/assets/, because their Content-Security-Policy allows no inline
script.
"""

from __future__ import annotations

import html
import re

from maintenance_common import fail

SCRIPT_NAME = "diagram-controls.js"

CONTROLS_SCRIPT = """\
"use strict";
// Zoom and download controls for a diagram page. Each .dc element holds a
// .dc-toolbar of [data-dc] buttons and a .dc-viewport around one <svg>. Only the
// viewport zooms, so a legend outside it keeps its size.
(() => {
  const LEVELS = [0.5, 0.75, 1, 1.5, 2, 3, 4];
  const MAX_PNG_SIDE = 8192;
  const SVG_NS = "http://www.w3.org/2000/svg";

  function save(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  function viewBox(svg) {
    const box = (svg.getAttribute("viewBox") || "").trim().split(/[\\s,]+/).map(Number);
    return box.length === 4 && box.every(Number.isFinite) && box[2] > 0 && box[3] > 0 ? box : null;
  }

  function attach(root) {
    const toolbar = root.querySelector(".dc-toolbar");
    const viewport = root.querySelector(".dc-viewport");
    const svg = viewport && viewport.querySelector("svg");
    if (!toolbar || !svg || !viewBox(svg) || root.dcAttached) return;
    root.dcAttached = true;
    svg.classList.add("dc-diagram");
    const button = (name) => toolbar.querySelector(`[data-dc="${name}"]`);
    const status = toolbar.querySelector(".dc-status");
    const name = root.dataset.dcName || "diagram";
    let index = LEVELS.indexOf(1);
    let base = 0;

    function report(message) {
      if (status) status.textContent = message;
    }

    function measure() {
      svg.style.width = "";
      svg.style.maxWidth = "";
      base = svg.getBoundingClientRect().width;
    }

    function apply() {
      const zoom = LEVELS[index];
      const x = (viewport.scrollLeft + viewport.clientWidth / 2) / Math.max(viewport.scrollWidth, 1);
      const y = (viewport.scrollTop + viewport.clientHeight / 2) / Math.max(viewport.scrollHeight, 1);
      svg.style.width = zoom === 1 ? "" : `${base * zoom}px`;
      svg.style.maxWidth = zoom === 1 ? "" : "none";
      viewport.classList.toggle("dc-zoomed", zoom > 1);
      button("reset").textContent = `${Math.round(zoom * 100)}%`;
      button("out").disabled = index === 0;
      button("in").disabled = index === LEVELS.length - 1;
      viewport.scrollLeft = x * viewport.scrollWidth - viewport.clientWidth / 2;
      viewport.scrollTop = y * viewport.scrollHeight - viewport.clientHeight / 2;
    }

    function step(delta) {
      const next = Math.min(LEVELS.length - 1, Math.max(0, index + delta));
      if (next === index) return;
      if (LEVELS[index] === 1) measure();
      index = next;
      apply();
    }

    // The SVG to download: the whole diagram with its legend and a background.
    function exported() {
      const clone = svg.cloneNode(true);
      clone.removeAttribute("style");
      clone.classList.remove("dc-diagram");
      clone.setAttribute("xmlns", SVG_NS);
      const full = svg.dataset.dcViewbox;
      const legend = root.querySelector(".dc-legend g");
      if (full && legend) {
        clone.setAttribute("viewBox", full);
        clone.appendChild(legend.cloneNode(true));
      }
      const [x, y, width, height] = viewBox(clone);
      clone.setAttribute("width", String(width));
      clone.setAttribute("height", String(height));
      const background = getComputedStyle(root).getPropertyValue("--dc-export-bg").trim();
      if (background) {
        const rect = document.createElementNS(SVG_NS, "rect");
        for (const [key, value] of Object.entries({ x, y, width, height, fill: background })) {
          rect.setAttribute(key, String(value));
        }
        clone.insertBefore(rect, clone.firstChild);
      }
      if (root.dataset.dcStyle === "page") {
        const style = document.createElementNS(SVG_NS, "style");
        style.textContent = Array.from(document.querySelectorAll("head style"), (s) => s.textContent).join("\\n");
        clone.insertBefore(style, clone.firstChild);
      }
      const text = `<?xml version="1.0" encoding="UTF-8"?>\\n${new XMLSerializer().serializeToString(clone)}\\n`;
      return { text, width, height };
    }

    function downloadSvg() {
      save(new Blob([exported().text], { type: "image/svg+xml" }), `${name}.svg`);
    }

    function downloadPng() {
      const { text, width, height } = exported();
      const scale = Math.min(2, MAX_PNG_SIDE / Math.max(width, height));
      const url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml" }));
      const image = new Image();
      image.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        try {
          canvas.toBlob((blob) => (blob ? save(blob, `${name}.png`) : report("PNG export failed.")), "image/png");
        } catch (error) {
          report("PNG export is not available for this diagram; download the SVG instead.");
        }
      };
      image.onerror = () => {
        URL.revokeObjectURL(url);
        report("PNG export failed; download the SVG instead.");
      };
      image.src = url;
    }

    button("out").addEventListener("click", () => step(-1));
    button("in").addEventListener("click", () => step(1));
    button("reset").addEventListener("click", () => step(LEVELS.indexOf(1) - index));
    button("svg").addEventListener("click", downloadSvg);
    button("png").addEventListener("click", downloadPng);

    // Ctrl or Cmd with the wheel (or a trackpad pinch) zooms the diagram.
    let wheel = 0;
    viewport.addEventListener("wheel", (event) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      wheel += event.deltaY;
      if (Math.abs(wheel) >= 60) {
        step(wheel < 0 ? 1 : -1);
        wheel = 0;
      }
    }, { passive: false });

    // When zoomed in, drag the diagram to pan.
    let drag = null;
    viewport.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !viewport.classList.contains("dc-zoomed")) return;
      drag = { x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
      viewport.setPointerCapture(event.pointerId);
      viewport.classList.add("dc-dragging");
    });
    viewport.addEventListener("pointermove", (event) => {
      if (!drag) return;
      viewport.scrollLeft = drag.left - (event.clientX - drag.x);
      viewport.scrollTop = drag.top - (event.clientY - drag.y);
    });
    const stop = () => {
      drag = null;
      viewport.classList.remove("dc-dragging");
    };
    viewport.addEventListener("pointerup", stop);
    viewport.addEventListener("pointercancel", stop);

    window.addEventListener("resize", () => {
      if (LEVELS[index] === 1) return;
      measure();
      apply();
    });
    measure();
    apply();
    toolbar.hidden = false;
  }

  window.DiagramControls = { attach };
  document.querySelectorAll(".dc[data-dc-auto]").forEach(attach);
})();
"""

CONTROLS_STYLE = """\
.dc-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px; }
.dc-bar .pause-btn { position: static; margin-left: auto; }
.dc-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
.dc-toolbar[hidden] { display: none; }
.dc-btn { display: inline-flex; align-items: center; justify-content: center; min-width: 32px; height: 28px;
  padding: 0 10px; border: 1px solid var(--dc-border, #1e293b); border-radius: 6px;
  background: var(--dc-btn-bg, transparent); color: var(--dc-fg, #94a3b8); font: inherit; font-size: 12px;
  line-height: 1; text-decoration: none; cursor: pointer; }
.dc-btn:hover:not(:disabled) { color: var(--dc-fg-hover, #e2e8f0); border-color: var(--dc-border-hover, #334155); }
.dc-btn:focus-visible { outline: 2px solid var(--dc-focus, #22d3ee); outline-offset: 1px; }
.dc-btn:disabled { opacity: 0.4; cursor: default; }
.dc-btn[data-dc="reset"] { min-width: 56px; font-variant-numeric: tabular-nums; }
.dc-sep { width: 1px; height: 20px; margin: 0 4px; background: var(--dc-border, #1e293b); }
.dc-status { font-size: 12px; color: var(--dc-error, #fb7185); }
.dc-viewport { overflow: auto; }
.dc-viewport.dc-zoomed { max-height: 80vh; cursor: grab; }
.dc-viewport.dc-dragging { cursor: grabbing; user-select: none; }
.dc-diagram { display: block; margin: 0 auto; }
.dc-legend { display: block; width: 100%; height: auto; margin-top: 4px; }
"""


def toolbar_html(source_name: str) -> str:
    """The toolbar, hidden until the script attaches to it."""
    source = html.escape(source_name)
    return (
        '<div class="dc-toolbar" role="toolbar" aria-label="Diagram zoom and download" hidden>'
        '<button type="button" class="dc-btn" data-dc="out" aria-label="Zoom out" title="Zoom out">−</button>'
        '<button type="button" class="dc-btn" data-dc="reset" aria-label="Reset zoom" title="Reset zoom">100%</button>'
        '<button type="button" class="dc-btn" data-dc="in" aria-label="Zoom in" title="Zoom in">+</button>'
        '<span class="dc-sep" aria-hidden="true"></span>'
        '<button type="button" class="dc-btn" data-dc="svg" title="Download the diagram as SVG">⤓ SVG</button>'
        '<button type="button" class="dc-btn" data-dc="png" title="Download the diagram as PNG">⤓ PNG</button>'
        f'<a class="dc-btn" href="{source}" download title="Download the Mermaid source">⤓ {source}</a>'
        '<span class="dc-status" role="status"></span>'
        "</div>"
    )


FLOWSVG_RE = re.compile(r'<svg id="flowsvg"[^>]*>.*?</svg>', re.DOTALL)
VIEWBOX_RE = re.compile(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"')
# dashmotion's architecture legend: the last group of the diagram, below everything.
LEGEND_RE = re.compile(r'(<g font-family="[^"]*" font-size="11">)((?:(?!<g\b).)*?)</g></svg>$', re.DOTALL)
LEGEND_TOP_RE = re.compile(r'<rect x="[0-9.]+" y="([0-9.]+)"')
PAUSE_RE = re.compile(r'\s*<button class="pause-btn" id="pauseBtn"[^>]*>.*?</button>', re.DOTALL)
CARD = '<div class="diagram-card">'


def _number(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _split_legend(svg: str) -> tuple[str, str]:
    """Move an architecture legend out of the diagram SVG into its own SVG."""
    if 'aria-roledescription="architecture diagram"' not in svg[: svg.index(">")]:
        return svg, ""
    legend = LEGEND_RE.search(svg)
    box = VIEWBOX_RE.search(svg)
    top = LEGEND_TOP_RE.search(legend.group(2)) if legend else None
    if not legend or not box or not top:
        return svg, ""
    width, height = box.group(1), float(box.group(2))
    legend_top = float(top.group(1))
    diagram_height = legend_top - 4
    if not 0 < diagram_height < height:
        return svg, ""
    diagram = svg[: legend.start()] + "</svg>"
    diagram = diagram.replace(
        box.group(0),
        f'viewBox="0 0 {width} {_number(diagram_height)}" data-dc-viewbox="0 0 {width} {box.group(2)}"',
        1,
    )
    legend_svg = (
        f'<svg class="dc-legend" viewBox="0 {_number(legend_top - 4)} {width} 24" role="img" aria-label="Legend">'
        f"{legend.group(1)}{legend.group(2)}</g></svg>"
    )
    return diagram, legend_svg


def enhance_animated(content: str, source_name: str) -> str:
    """Add the toolbar and a diagram-only zoom area to an animated dashmotion page."""
    match = FLOWSVG_RE.search(content)
    pause = PAUSE_RE.search(content)
    if not match or not pause or content.count(CARD) != 1 or "</style>" not in content or "</body>" not in content:
        fail(f"cannot add the diagram controls to {source_name}: unexpected page structure")
    diagram, legend = _split_legend(match.group(0))
    stem = html.escape(source_name.removesuffix(".mmd"))
    page = content[: match.start()] + f'<div class="dc-viewport">{diagram}</div>{legend}' + content[match.end():]
    page = PAUSE_RE.sub("", page, count=1)
    page = page.replace(
        CARD,
        f'<div class="diagram-card dc" data-dc-name="{stem}" data-dc-style="page" data-dc-auto>\n'
        f'    <div class="dc-bar">{toolbar_html(source_name)}{pause.group(0).strip()}</div>',
        1,
    )
    page = page.replace("</style>", f"  .dc {{ --dc-export-bg: #030a1c; }}\n{CONTROLS_STYLE}</style>", 1)
    head, _, tail = page.rpartition("</body>")
    return f"{head}<script>\n{CONTROLS_SCRIPT}</script>\n</body>{tail}"
