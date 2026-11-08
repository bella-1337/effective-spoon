"""Graphviz layout for the architecture diagrams, drawn by dashmotion's renderer.

dashmotion's own engine centres every tier and routes any edge that skips a tier
through vertical lanes in the margins. Those right-angled lanes run parallel to
the dashed network boundaries, so a connector reads like a boundary side.

This module keeps dashmotion for sizing and rendering (styles, legend, cards,
animated dots) and hands placement and routing to Graphviz ``dot``, the layered
engine Mermaid's dagre layout is modelled on. ``dot`` keeps each subgraph's
members together, orders every tier to reduce crossings, reserves room for edge
labels, and routes each connector as a spline around the nodes, so connectors
curve and run diagonally instead of tracing rectangles.

Like Mermaid, dot derives the rows from the edges alone, so the Mermaid source
is the whole layout input: a diagram looks the same in any Mermaid viewer and
here, apart from styling.
"""

from __future__ import annotations

import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

POINTS_PER_INCH = 72.0  # dot sizes nodes in inches; 1 pt == 1 SVG px here
NODE_SEP = 0.5          # inches between neighbours in a tier
RANK_SEP = 1.0          # inches between tiers
CLUSTER_MARGIN = 20     # points of padding inside a boundary (dashmotion's BOUND_PAD)
LABEL_FONT = "Courier"  # monospace, close to the JetBrains Mono the HTML uses
CURVE_SAMPLES = 12      # flattening steps per Bezier piece (bounds, length, labels)
HALO = 'stroke="#030a1c" stroke-width="4" stroke-linejoin="round" paint-order="stroke"'


class LayoutError(RuntimeError):
    pass


class CurvePoints(list):
    """A flattened connector that also carries its exact SVG path data."""

    d: str = ""


def load_dashmotion(scripts: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("dashmotion_layout", scripts / "layout.py")
    if spec is None or spec.loader is None:
        raise LayoutError(f"cannot load {scripts / 'layout.py'}")
    module = importlib.util.module_from_spec(spec)
    # Leave no bytecode cache in the dashmotion directory, which may be shared.
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fmt(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def to_dot(layout: Any) -> str:
    """The DOT source for a sized dashmotion layout, one cluster per group."""
    lines = [
        "digraph G {",
        f"  graph [rankdir=TB, splines=spline, nodesep={NODE_SEP}, ranksep={RANK_SEP},"
        f' fontname="{LABEL_FONT}", fontsize=11];',
        '  node [shape=box, fixedsize=true, label=""];',
        f'  edge [arrowsize=0.5, fontname="{LABEL_FONT}", fontsize=10];',
    ]
    children: dict[str | None, list[Any]] = {}
    for group in layout.groups:
        children.setdefault(group.parent, []).append(group)
    members: dict[str | None, list[Any]] = {}
    for node in layout.nodes:
        members.setdefault(node.group, []).append(node)

    def emit(group_id: str | None, indent: str) -> None:
        for node in members.get(group_id, []):
            lines.append(f"{indent}{_quote(node.id)} [width={node.w / POINTS_PER_INCH:.4f},"
                         f" height={node.h / POINTS_PER_INCH:.4f}];")
        for group in children.get(group_id, []):
            lines.append(f"{indent}subgraph {_quote('cluster_' + group.id)} {{")
            lines.append(f"{indent}  label={_quote(group.label)}; labeljust=l; margin={CLUSTER_MARGIN};")
            emit(group.id, indent + "  ")
            lines.append(f"{indent}}}")

    emit(None, "  ")
    for index, edge in enumerate(layout.edges):
        attrs = [f"id=e{index}"]
        if edge.kind == "static":
            attrs.append("arrowhead=none")
        if edge.label:
            attrs.append(f"label={_quote(edge.label)}")
        lines.append(f"  {_quote(edge.src)} -> {_quote(edge.dst)} [{', '.join(attrs)}];")
    lines.append("}")
    return "\n".join(lines) + "\n"


def run_dot(source: str) -> dict[str, Any]:
    binary = shutil.which("dot")
    if binary is None:
        raise LayoutError("Graphviz 'dot' was not found; install Graphviz (for example 'brew install graphviz')")
    result = subprocess.run([binary, "-Tjson"], input=source, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise LayoutError(f"dot failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _point(text: str, height: float) -> tuple[float, float]:
    x, y = text.split(",")
    return float(x), height - float(y)


def _flatten(segs: list[tuple]) -> list[tuple[float, float]]:
    pts = [segs[0][1]]
    for seg in segs[1:]:
        if seg[0] == "L":
            pts.append(seg[1])
            continue
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts[-1], seg[1], seg[2], seg[3]
        for step in range(1, CURVE_SAMPLES + 1):
            t = step / CURVE_SAMPLES
            s = 1 - t
            pts.append((s ** 3 * x0 + 3 * s * s * t * x1 + 3 * s * t * t * x2 + t ** 3 * x3,
                        s ** 3 * y0 + 3 * s * s * t * y1 + 3 * s * t * t * y2 + t ** 3 * y3))
    return pts


def _edge_segments(pos: str, height: float, short: float) -> list[tuple]:
    """dot's spline as M/C/L pieces, ending ``short`` px before the head node."""
    tip = None
    points = []
    for part in pos.split():
        if part.startswith("e,"):
            tip = _point(part[2:], height)
        elif not part.startswith("s,"):
            points.append(_point(part, height))
    segs: list[tuple] = [("M", points[0])]
    for i in range(1, len(points) - 2, 3):
        segs.append(("C", points[i], points[i + 1], points[i + 2]))
    if tip is not None:
        last = points[-1]
        gap = math.dist(last, tip)
        if gap > short:
            ratio = (gap - short) / gap
            segs.append(("L", (last[0] + (tip[0] - last[0]) * ratio, last[1] + (tip[1] - last[1]) * ratio)))
    return segs


def _path_data(segs: list[tuple]) -> str:
    parts = []
    cursor = segs[0][1]
    for seg in segs:
        if seg[0] == "M":
            parts.append(f"M{_fmt(seg[1][0])} {_fmt(seg[1][1])}")
        elif seg[0] == "L":
            x, y = seg[1]
            if abs(x - cursor[0]) < 0.05:
                parts.append(f"V{_fmt(y)}")
            elif abs(y - cursor[1]) < 0.05:
                parts.append(f"H{_fmt(x)}")
            else:
                parts.append(f"L{_fmt(x)} {_fmt(y)}")
        else:
            parts.append("C" + " ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in seg[1:]))
        cursor = seg[-1]
    return " ".join(parts)


def apply_dot(layout: Any, result: dict[str, Any], dm: ModuleType) -> None:
    """Copy dot's node, cluster, and edge geometry onto the dashmotion layout."""
    height = float(result["bb"].split(",")[3])
    for obj in result.get("objects", []):
        name = obj.get("name", "")
        if name in layout.by_id:
            node = layout.by_id[name]
            cx, cy = _point(obj["pos"], height)
            node.x, node.y = cx - node.w / 2.0, cy - node.h / 2.0
        elif name.startswith("cluster_") and name[8:] in layout.group_by_id:
            x1, y1, x2, y2 = (float(v) for v in obj["bb"].split(","))
            layout.group_by_id[name[8:]].box = (x1, height - y2, x2 - x1, y2 - y1)
    by_index = {}
    for edge in result.get("edges", []):
        key = str(edge.get("id", ""))
        # Edges are emitted in source order, so dot's _gvid is the fallback index.
        index = int(key[1:]) if key.startswith("e") and key[1:].isdigit() else edge.get("_gvid")
        if isinstance(index, int):
            by_index[index] = edge
    for index, edge in enumerate(layout.edges):
        drawn = by_index.get(index)
        if drawn is None or "pos" not in drawn:
            raise LayoutError(f"dot did not route {edge.src} -> {edge.dst}")
        segs = _edge_segments(drawn["pos"], height, dm.EDGE_SHORT)
        source = layout.by_id[edge.src]
        if math.dist(segs[0][1], (source.cx, source.cy)) > math.dist(_flatten(segs)[-1], (source.cx, source.cy)):
            raise LayoutError(f"dot routed {edge.src} -> {edge.dst} backwards")
        edge.segs = segs
        edge.pts = _flatten(segs)
        if edge.label and "lp" in drawn:
            lx, ly = _point(drawn["lp"], height)
            # dot gives the label centre; dashmotion draws start-anchored text at 10px
            edge.label_pos = (lx - dm.text_w(edge.label) * 10 / 13 / 2.0, ly + 3)


def render_html(graph: dict[str, Any], scripts: Path) -> str:
    """Render a semantic graph to dashmotion HTML, laying architecture diagrams out with Graphviz."""
    dm = load_dashmotion(scripts)
    try:
        layout = dm.Layout(graph)
    except ValueError as error:
        raise LayoutError(str(error)) from None
    if graph.get("mode") != "architecture":
        layout.run()
        return dm.render(layout, dm.geometry(layout))
    layout.size_nodes()
    layout.assign_layers()
    apply_dot(layout, run_dot(to_dot(layout)), dm)
    anchor = layout.nodes[0]
    before = (anchor.x, anchor.y)
    layout.normalize()
    dx, dy = anchor.x - before[0], anchor.y - before[1]
    for edge in layout.edges:
        segs = [(seg[0], *[(x + dx, y + dy) for x, y in seg[1:]]) for seg in edge.segs]
        points = CurvePoints(edge.pts)
        points.d = _path_data(segs)
        edge.pts = points
    straight = dm.to_d
    dm.to_d = lambda pts: getattr(pts, "d", "") or straight(pts)
    html = dm.render(layout, dm.geometry(layout))
    # Boundary titles move above the connectors, and a dark halo keeps them and
    # the edge labels readable where a connector crosses them.
    titles = []
    for size in (10, 11):
        pattern = re.compile(rf'<text [^>]*font-size="{size}" opacity="0\.9">[^<]*</text>')
        titles += [m.replace(' opacity="0.9">', f' opacity="0.9" {HALO}>') for m in pattern.findall(html)]
        html = pattern.sub("", html)
    connectors = html.index('<g fill="none">')
    end = html.index("</g>", connectors) + len("</g>")
    html = html[:end] + "".join(titles) + html[end:]
    for edge in layout.edges:
        if edge.label and edge.label_pos:
            x, y = (dm.fmt(v) for v in edge.label_pos)
            plain = f'<text x="{x}" y="{y}" fill="#64748b" font-size="10">'
            html = html.replace(plain, f'<text x="{x}" y="{y}" fill="#94a3b8" font-size="10" {HALO}>', 1)
    return html
