#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Render the Mermaid sources in diagrams/ into animated dashmotion HTML.

The Mermaid files are the source of truth. This script converts each one into
dashmotion's semantic graph, calls dashmotion's layout engine to write the
HTML beside it, and runs dashmotion's structural checker on the result.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maintenance_common import LOG, ROOT, fail, run_main

USAGE = """Usage:
  ./scripts/render-diagrams.py [DIAGRAM.mmd ...]   render all (or the named) diagrams
  ./scripts/render-diagrams.py --check             list HTML files that are missing or older than their Mermaid source
  ./scripts/render-diagrams.py --json DIAGRAM.mmd  print the semantic graph without rendering

Rendering needs the dashmotion skill. Set DASHMOTION_SKILL_DIR when it is not
installed in ~/.agents/skills/dashmotion. --check and --json need only Python.
"""

DIAGRAMS_DIR = ROOT / "diagrams"
ARCH_TYPES = {"frontend", "backend", "database", "cloud", "security", "bus", "external"}
CARD_ACCENTS = {"cyan", "violet", "rose"}
SOURCE_META = "dashmotion-source"
SKILL_CANDIDATES = (
    Path.home() / ".agents/skills/dashmotion",
    Path.home() / ".copilot/skills/dashmotion",
    Path.home() / ".claude/skills/dashmotion",
)

# Longest openers first so "([" is not read as "(".
SHAPES = (
    ("([", "])", "pill"),
    ("((", "))", "pill"),
    ("[(", ")]", "database"),
    ("[[", "]]", "step"),
    ("{{", "}}", "step"),
    ("[", "]", "step"),
    ("(", ")", "step"),
    ("{", "}", "decision"),
)
EDGE_OPS = (("-.->", "async"), ("-->", "sync"), ("==>", "main"), ("---", "static"))
ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class DiagramError(ValueError):
    pass


@dataclass
class Node:
    id: str
    text: str | None = None
    shape: str = "step"
    classes: list[str] = field(default_factory=list)
    group: str | None = None


@dataclass
class Diagram:
    title: str = ""
    mode: str = "flow"
    subtitle: str = ""
    footer: str = ""
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[dict[str, str]] = field(default_factory=list)
    groups: list[dict[str, str]] = field(default_factory=list)
    tiers: dict[str, int] = field(default_factory=dict)
    journeys: list[dict[str, Any]] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)


def _read_text(source: str, i: int, closer: str, where: str) -> tuple[str, int]:
    while i < len(source) and source[i] == " ":
        i += 1
    if source.startswith('"', i):
        end = source.find('"', i + 1)
        if end < 0:
            raise DiagramError(f"{where}: unterminated quoted label")
        text, i = source[i + 1:end], end + 1
        while i < len(source) and source[i] == " ":
            i += 1
        if not source.startswith(closer, i):
            raise DiagramError(f"{where}: expected {closer!r} after quoted label")
        return text, i + len(closer)
    end = source.find(closer, i)
    if end < 0:
        raise DiagramError(f"{where}: missing {closer!r}")
    return source[i:end].strip(), end + len(closer)


def _parse_node(diagram: Diagram, line: str, i: int, group: str | None, where: str) -> tuple[str, int]:
    match = ID_RE.match(line, i)
    if not match:
        raise DiagramError(f"{where}: expected a node ID at {line[i:]!r}")
    node_id, i = match.group(0), match.end()
    node = diagram.nodes.get(node_id)
    if node is None:
        node = diagram.nodes[node_id] = Node(node_id, group=group)
    for opener, closer, shape in SHAPES:
        if line.startswith(opener, i):
            text, i = _read_text(line, i + len(opener), closer, where)
            if node.text is not None:
                raise DiagramError(f"{where}: node {node_id} is defined twice")
            node.text, node.shape, node.group = text, shape, group
            break
    if line.startswith(":::", i):
        match = ID_RE.match(line, i + 3)
        if not match:
            raise DiagramError(f"{where}: expected a class name after ':::'")
        node.classes.append(match.group(0))
        i = match.end()
    return node_id, i


def _parse_node_list(diagram: Diagram, line: str, i: int, group: str | None, where: str) -> tuple[list[str], int]:
    ids: list[str] = []
    while True:
        node_id, i = _parse_node(diagram, line, i, group, where)
        ids.append(node_id)
        rest = line[i:].lstrip()
        if not rest.startswith("&"):
            return ids, i
        i = len(line) - len(rest) + 1
        while i < len(line) and line[i] == " ":
            i += 1


def _parse_statement(diagram: Diagram, line: str, group: str | None, where: str) -> None:
    sources, i = _parse_node_list(diagram, line, 0, group, where)
    while True:
        while i < len(line) and line[i] == " ":
            i += 1
        if i == len(line):
            return
        for op, kind in EDGE_OPS:
            if line.startswith(op, i):
                i += len(op)
                break
        else:
            raise DiagramError(f"{where}: unsupported syntax at {line[i:]!r}")
        label = None
        if line.startswith("|", i):
            label, i = _read_text(line, i + 1, "|", where)
        while i < len(line) and line[i] == " ":
            i += 1
        targets, i = _parse_node_list(diagram, line, i, group, where)
        for source in sources:
            for target in targets:
                edge = {"from": source, "to": target, "kind": kind}
                if label:
                    edge["label"] = label
                diagram.edges.append(edge)
        sources = targets


def _parse_directive(diagram: Diagram, body: str, where: str) -> None:
    key, _, value = body.partition(" ")
    value = value.strip()
    if key == "@mode":
        if value not in {"flow", "architecture"}:
            raise DiagramError(f"{where}: @mode must be flow or architecture")
        diagram.mode = value
    elif key == "@subtitle":
        diagram.subtitle = value
    elif key == "@footer":
        diagram.footer = value
    elif key == "@tier":
        number, _, ids = value.partition(":")
        if not number.strip().isdigit():
            raise DiagramError(f"{where}: @tier needs 'N: ID ID ...'")
        for node_id in ids.split():
            if node_id in diagram.tiers:
                raise DiagramError(f"{where}: {node_id} has more than one tier")
            diagram.tiers[node_id] = int(number)
    elif key == "@journey":
        color, _, ids = value.partition(":")
        path = ids.split()
        if not HEX_RE.match(color.strip()) or len(path) < 2:
            raise DiagramError(f"{where}: @journey needs '#rrggbb: ID ID ...'")
        diagram.journeys.append({"color": color.strip(), "hops": [list(pair) for pair in zip(path, path[1:])]})
    elif key == "@card":
        parts = [part.strip() for part in value.split("|")]
        if len(parts) < 3 or parts[0] not in CARD_ACCENTS:
            raise DiagramError(f"{where}: @card needs 'cyan|violet|rose | title | item | ...'")
        diagram.cards.append({"accent": parts[0], "title": parts[1], "items": parts[2:]})
    else:
        raise DiagramError(f"{where}: unknown directive {key}")


def parse_mermaid(text: str, name: str = "diagram") -> Diagram:
    diagram = Diagram()
    lines = text.splitlines()
    index = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            stripped = lines[index].strip()
            if stripped == "---":
                break
            if stripped.startswith("title:"):
                diagram.title = stripped[6:].strip().strip('"')
        else:
            raise DiagramError(f"{name}: unterminated front matter")
        index += 1
    header_seen = False
    stack: list[str] = []
    for number, raw in enumerate(lines[index:], start=index + 1):
        where = f"{name}:{number}"
        line = raw.strip()
        if not line:
            continue
        if line.startswith("%%"):
            body = line[2:].strip()
            if body.startswith("@"):
                _parse_directive(diagram, body, where)
            continue
        if not header_seen:
            if not re.match(r"^(flowchart|graph)(\s+(TD|TB|BT|LR|RL))?$", line):
                raise DiagramError(f"{where}: only 'flowchart' diagrams are supported")
            header_seen = True
            continue
        if line.startswith("subgraph "):
            match = re.match(r'^subgraph\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\["([^"]*)"\]|\[([^\]]*)\])?\s*$', line)
            if not match:
                raise DiagramError(f"{where}: use 'subgraph ID[\"label\"]'")
            group_id = match.group(1)
            if any(group["id"] == group_id for group in diagram.groups):
                raise DiagramError(f"{where}: subgraph {group_id} is declared twice")
            group = {"id": group_id, "label": match.group(2) or match.group(3) or group_id,
                     "kind": "subnet" if stack else "region"}
            if stack:
                group["parent"] = stack[-1]
            diagram.groups.append(group)
            stack.append(group_id)
            continue
        if line == "end":
            if not stack:
                raise DiagramError(f"{where}: 'end' without a subgraph")
            stack.pop()
            continue
        if re.match(r"^(classDef|style|linkStyle|click|direction)\b", line):
            continue
        if line.startswith("class "):
            match = re.match(r"^class\s+([A-Za-z0-9_,\s]+?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;?$", line)
            if not match:
                raise DiagramError(f"{where}: use 'class ID,ID className'")
            for node_id in match.group(1).replace(" ", "").split(","):
                diagram.nodes.setdefault(node_id, Node(node_id)).classes.append(match.group(2))
            continue
        _parse_statement(diagram, line.rstrip(";"), stack[-1] if stack else None, where)
    if not header_seen:
        raise DiagramError(f"{name}: missing 'flowchart TD' header")
    if stack:
        raise DiagramError(f"{name}: subgraph {stack[-1]} is not closed")
    return diagram


def _split_label(text: str) -> tuple[str, str | None]:
    parts = [part.strip() for part in BR_RE.split(text) if part.strip()]
    if not parts:
        return "", None
    return parts[0], " ".join(parts[1:]) or None


def to_graph(diagram: Diagram, name: str) -> dict[str, Any]:
    if not diagram.title:
        raise DiagramError(f"{name}: add a front-matter title")
    undefined = sorted(node_id for node_id, node in diagram.nodes.items() if node.text is None)
    if undefined:
        raise DiagramError(f"{name}: nodes without a label: {', '.join(undefined)}")
    edge_pairs = {(edge["from"], edge["to"]) for edge in diagram.edges}
    in_degree = {node_id: 0 for node_id in diagram.nodes}
    out_degree = dict(in_degree)
    for source, target in edge_pairs:
        out_degree[source] += 1
        in_degree[target] += 1
    architecture = diagram.mode == "architecture"
    if architecture and diagram.groups:
        top_level = [group for group in diagram.groups if "parent" not in group]
        missing = sorted(set(diagram.nodes) - set(diagram.tiers))
        if len(top_level) > 1 and missing:
            raise DiagramError(f"{name}: multi-group architecture needs @tier for {', '.join(missing)}")
    unknown = sorted(set(diagram.tiers) - set(diagram.nodes))
    if unknown:
        raise DiagramError(f"{name}: @tier names unknown nodes: {', '.join(unknown)}")
    if not architecture and (diagram.tiers or diagram.cards):
        raise DiagramError(f"{name}: @tier and @card apply only to architecture diagrams")

    nodes = []
    for node in diagram.nodes.values():
        label, sublabel = _split_label(node.text or "")
        if not label:
            raise DiagramError(f"{name}: node {node.id} has an empty label")
        item: dict[str, Any] = {"id": node.id}
        if architecture:
            item["label"] = label
            if sublabel:
                item["sublabel"] = sublabel
            types = [cls for cls in node.classes if cls in ARCH_TYPES]
            item["type"] = types[-1] if types else ("database" if node.shape == "database" else "backend")
            if node.id in diagram.tiers:
                item["tier"] = diagram.tiers[node.id]
        else:
            item["label"] = f"{label} {sublabel}" if sublabel else label
            terminal = in_degree[node.id] == 0 or out_degree[node.id] == 0
            if node.shape == "pill" and terminal:
                item["shape"] = "pill"
            elif node.shape == "decision":
                item["shape"] = "decision"
        if node.group:
            item["group"] = node.group
        nodes.append(item)

    for journey in diagram.journeys:
        for source, target in journey["hops"]:
            if (source, target) not in edge_pairs:
                raise DiagramError(f"{name}: journey hop {source} -> {target} is not an edge")
    if architecture and diagram.cards and len(diagram.cards) != 3:
        raise DiagramError(f"{name}: architecture diagrams need exactly three @card lines")

    graph: dict[str, Any] = {
        "mode": diagram.mode,
        "title": diagram.title,
        "footer": diagram.footer or f"Generated from {name}.mmd by scripts/render-diagrams.py — edit the Mermaid source, not this file",
        "nodes": nodes,
        "edges": diagram.edges,
    }
    if diagram.subtitle:
        graph["subtitle"] = diagram.subtitle
    if diagram.groups:
        graph["groups"] = diagram.groups
    if diagram.journeys:
        graph["journeys"] = diagram.journeys
    if diagram.cards:
        graph["summary"] = diagram.cards
    return graph


def source_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recorded_digest(html: Path) -> str | None:
    if not html.is_file():
        return None
    match = re.search(rf'<meta name="{SOURCE_META}" content="sha256:([0-9a-f]{{64}})">', html.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def dashmotion_scripts() -> Path:
    configured = os.environ.get("DASHMOTION_SKILL_DIR")
    candidates = [Path(configured).expanduser()] if configured else list(SKILL_CANDIDATES)
    for candidate in candidates:
        if (candidate / "scripts/layout.py").is_file() and (candidate / "scripts/check_diagram.py").is_file():
            return candidate / "scripts"
    fail(
        "the dashmotion skill was not found; install it or set DASHMOTION_SKILL_DIR. "
        "The Mermaid sources stay authoritative; re-render when dashmotion is available."
    )
    raise AssertionError


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.suffix != ".mmd":
        fail(f"not a regular .mmd file: {path}")
    try:
        return to_graph(parse_mermaid(path.read_text(encoding="utf-8"), path.stem), path.stem)
    except DiagramError as error:
        fail(str(error))
    raise AssertionError


def fidelity_problems(graph: dict[str, Any], content: str) -> list[str]:
    """Check that every label reached the SVG and every edge became a connector."""
    match = re.search(r"<svg\b.*?</svg>", content, re.DOTALL)
    if not match:
        return ["no <svg> element"]
    svg = match.group(0)
    text = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", svg)).split())
    problems = []
    expected = [node["label"] for node in graph["nodes"]]
    expected += [node["sublabel"] for node in graph["nodes"] if node.get("sublabel")]
    expected += [edge["label"] for edge in graph["edges"] if edge.get("label")]
    for label in expected:
        if " ".join(label.split()) not in text:
            problems.append(f"label missing from the diagram: {label!r}")
    without_markers = re.sub(r"<marker\b.*?</marker>", "", svg, flags=re.DOTALL)
    connectors = len(re.findall(r"<path\b", without_markers)) + svg.count("↻")
    if connectors != len(graph["edges"]):
        problems.append(f"{len(graph['edges'])} edges in the source but {connectors} connectors drawn")
    return problems


def render(path: Path, scripts: Path) -> None:
    graph = load(path)
    html = path.with_suffix(".html")
    if html.is_symlink():
        fail(f"refusing to replace symbolic link: {html}")
    with tempfile.TemporaryDirectory(prefix="dashmotion-") as temporary:
        graph_file = Path(temporary) / "graph.json"
        rendered = Path(temporary) / "diagram.html"
        graph_file.write_text(json.dumps(graph), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(scripts / "layout.py"), str(graph_file), "--render", str(rendered)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            fail(f"dashmotion layout failed for {path.name}: {result.stderr.strip()}")
        content = rendered.read_text(encoding="utf-8")
        if "<head>" not in content:
            fail(f"dashmotion output for {path.name} has no <head> element")
        meta = f'<meta name="{SOURCE_META}" content="sha256:{source_digest(path)}">'
        content = content.replace("<head>", f"<head>\n{meta}", 1)
        content = "\n".join(line.rstrip() for line in content.splitlines()) + "\n"
        rendered.write_text(content, encoding="utf-8")
        check = subprocess.run(
            [sys.executable, str(scripts / "check_diagram.py"), str(rendered)],
            capture_output=True, text=True, check=False,
        )
        if check.returncode != 0 or "(0 violations)" not in check.stdout:
            fail(f"dashmotion structural check failed for {path.name}:\n{check.stdout}{check.stderr}")
        problems = fidelity_problems(graph, content)
        if problems:
            fail(f"{path.name} does not match its Mermaid source:\n  " + "\n  ".join(problems))
        os.replace(rendered, html)
    LOG.info(f"rendered {html.relative_to(ROOT)}")


def stale_diagrams(paths: list[Path]) -> list[str]:
    stale = []
    for path in paths:
        load(path)
        if recorded_digest(path.with_suffix(".html")) != source_digest(path):
            stale.append(str(path.with_suffix(".html").relative_to(ROOT)))
    return stale


def main() -> None:
    args = sys.argv[1:]
    if args in (["-h"], ["--help"]):
        print(USAGE, end="")
        return
    if args[:1] == ["--json"]:
        if len(args) != 2:
            fail("--json takes exactly one .mmd file", 2)
        print(json.dumps(load(Path(args[1]).resolve()), indent=2, ensure_ascii=False))
        return
    if args == ["--check"]:
        stale = stale_diagrams(sorted(DIAGRAMS_DIR.glob("*.mmd")))
        if stale:
            LOG.info("Re-render with ./scripts/render-diagrams.py; stale or missing:")
            LOG.info("\n".join(f"  {item}" for item in stale))
            fail(f"{len(stale)} diagram(s) out of date")
        LOG.info("All diagrams match their Mermaid sources.")
        return
    if any(arg.startswith("-") for arg in args):
        fail("unknown option; see --help", 2)
    paths = [Path(arg).resolve() for arg in args] or sorted(DIAGRAMS_DIR.glob("*.mmd"))
    if not paths:
        fail(f"no .mmd files found in {DIAGRAMS_DIR}")
    scripts = dashmotion_scripts()
    for path in paths:
        render(path, scripts)


if __name__ == "__main__":
    run_main(main)
