#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Render the Mermaid sources in diagrams/ into animated dashmotion HTML.

The Mermaid files are the source of truth and the only committed form; the
HTML is generated beside them, ignored by Git, and rendered by the
documentation build in CI. This script converts each flowchart into
dashmotion's semantic graph. Architecture diagrams are laid out by Graphviz dot
through diagram_layout.py, which hands the geometry to dashmotion's renderer.
dashmotion has no sequence mode, so sequenceDiagram sources are laid out by
sequence_diagrams.py in the same visual style. Every result must pass
dashmotion's structural checker.

dashmotion (MIT, github.com/csthink/dashmotion) is downloaded at a pinned
commit and verified against the SHA-256 digests below, so every machine renders
with the same code.
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
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import diagram_layout
import sequence_diagrams
from maintenance_common import LOG, ROOT, fail, run_main

USAGE = """Usage:
  ./scripts/render-diagrams.py [DIAGRAM.mmd ...]   render all (or the named) diagrams beside their sources
  ./scripts/render-diagrams.py --json DIAGRAM.mmd  print the semantic graph without rendering

Rendering needs Graphviz (dot) and downloads the pinned dashmotion scripts once
into the user cache directory. Offline, set DASHMOTION_SKILL_DIR to a dashmotion
skill directory instead. --json needs only Python.
"""

DIAGRAMS_DIR = ROOT / "diagrams"
ARCH_TYPES = {"frontend", "backend", "database", "cloud", "security", "bus", "external"}
CARD_ACCENTS = {"cyan", "violet", "rose"}
DASHMOTION_COMMIT = "ae2fe4f28c60caa53da8b15fa3537f4ea576f4ad"  # dashmotion 2.2.4
DASHMOTION_URL = f"https://raw.githubusercontent.com/csthink/dashmotion/{DASHMOTION_COMMIT}/skills/dashmotion/scripts/"
DASHMOTION_FILES = {
    "layout.py": "a2691344d00dae614bd4f1b9a53d980e1e1a59dbb8f68345e46efe51f08b2b24",
    "check_diagram.py": "1a93bcab4852c79c91279cf59f1ba01a20d0d4c8e62e118dfc0b61677b3d4e8b",
}
HTTP_TIMEOUT_SECONDS = 30

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
        for op, kind in EDGE_OPS:  # noqa: B007 - `kind` is read after the loop breaks
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
                raise DiagramError(f"{where}: only 'flowchart' and 'sequenceDiagram' diagrams are supported")
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
    if not architecture and diagram.cards:
        raise DiagramError(f"{name}: @card applies only to architecture diagrams")

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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dashmotion_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "litellm-stack" / "dashmotion" / DASHMOTION_COMMIT


def fetch_dashmotion(cache: Path) -> Path:
    """The pinned dashmotion scripts, downloaded once and verified on every use."""
    cache.mkdir(parents=True, exist_ok=True)
    for name, digest in DASHMOTION_FILES.items():
        target = cache / name
        if target.is_file() and not target.is_symlink() and _sha256(target) == digest:
            continue
        try:
            with urllib.request.urlopen(DASHMOTION_URL + name, timeout=HTTP_TIMEOUT_SECONDS) as response:
                body = response.read()
        except OSError as error:
            fail(f"could not download dashmotion {name}: {error}. Offline, set DASHMOTION_SKILL_DIR.")
        if hashlib.sha256(body).hexdigest() != digest:
            fail(f"dashmotion {name} does not match its pinned SHA-256 digest; refusing to run it")
        partial = target.with_suffix(".partial")
        partial.write_bytes(body)
        os.replace(partial, target)
    return cache


def dashmotion_scripts() -> Path:
    configured = os.environ.get("DASHMOTION_SKILL_DIR")
    if configured:
        scripts = Path(configured).expanduser() / "scripts"
        if not all((scripts / name).is_file() for name in DASHMOTION_FILES):
            fail(f"DASHMOTION_SKILL_DIR has no scripts/layout.py and scripts/check_diagram.py: {configured}")
        return scripts
    return fetch_dashmotion(dashmotion_cache())


def parse_graph(text: str, name: str) -> dict[str, Any]:
    """The semantic graph of a flowchart or sequence diagram source."""
    if sequence_diagrams.is_sequence(text):
        try:
            return sequence_diagrams.to_graph(sequence_diagrams.parse(text, name), name)
        except sequence_diagrams.SequenceError as error:
            raise DiagramError(str(error)) from None
    return to_graph(parse_mermaid(text, name), name)


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.suffix != ".mmd":
        fail(f"not a regular .mmd file: {path}")
    try:
        return parse_graph(path.read_text(encoding="utf-8"), path.stem)
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
        rendered = Path(temporary) / "diagram.html"
        sequence = graph["mode"] == "sequence"
        if sequence:
            content = sequence_diagrams.render_html(graph)
        else:
            try:
                content = diagram_layout.render_html(graph, scripts)
            except diagram_layout.LayoutError as error:
                fail(f"dashmotion layout failed for {path.name}: {error}")
        content = "\n".join(line.rstrip() for line in content.splitlines()) + "\n"
        rendered.write_text(content, encoding="utf-8")
        check = subprocess.run(
            [sys.executable, str(scripts / "check_diagram.py"), str(rendered)],
            capture_output=True, text=True, check=False,
        )
        if check.returncode != 0 or "(0 violations)" not in check.stdout:
            fail(f"dashmotion structural check failed for {path.name}:\n{check.stdout}{check.stderr}")
        problems = (sequence_diagrams.fidelity_problems if sequence else fidelity_problems)(graph, content)
        if problems:
            fail(f"{path.name} does not match its Mermaid source:\n  " + "\n  ".join(problems))
        os.replace(rendered, html)
    LOG.info(f"rendered {html.relative_to(ROOT) if html.is_relative_to(ROOT) else html}")


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
