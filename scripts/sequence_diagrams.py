"""Parse and render Mermaid sequence diagrams as animated dashmotion-style HTML.

dashmotion lays out flowcharts and architecture diagrams but has no sequence
mode, so scripts/render-diagrams.py hands `sequenceDiagram` sources to this
module. It draws participants, lifelines, messages, notes, and alt/opt/loop/
par/critical/break frames in dashmotion's visual language: the same page,
palette, flowing dashed connectors, a traveling dot that replays the messages
in order, the pause button, and reduced-motion handling. The output still has
to pass dashmotion's structural checker.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from typing import Any

BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
ID = r"[A-Za-z_][A-Za-z0-9_]*"
PARTICIPANT_RE = re.compile(rf"^(participant|actor)\s+({ID})(?:\s+as\s+(.+))?$")
# Longest arrows first, so "-->>" is not read as "-->".
MESSAGE_RE = re.compile(rf"^({ID})\s*(-->>|->>|--x|-x|--\)|-\)|-->|->)\s*([+-]?)\s*({ID})\s*:\s*(.+)$")
NOTE_RE = re.compile(rf"^[Nn]ote\s+(over|left of|right of)\s+({ID})(?:\s*,\s*({ID}))?\s*:\s*(.+)$")
BLOCK_RE = re.compile(r"^(alt|opt|loop|par|critical|break)(?:\s+(.*))?$")
DIVIDER_RE = re.compile(r"^(else|and|option)(?:\s+(.*))?$")
ACTIVATION_RE = re.compile(rf"^(activate|deactivate)\s+{ID}$")
DIVIDERS = {"alt": "else", "par": "and", "critical": "option"}
TYPES = {"frontend", "backend", "database", "cloud", "security", "bus", "external"}

PALETTE = {
    "frontend": ("rgba(8,51,68,0.45)", "#22d3ee"),
    "backend": ("rgba(6,78,59,0.45)", "#34d399"),
    "database": ("rgba(76,29,149,0.45)", "#a78bfa"),
    "cloud": ("rgba(120,53,15,0.35)", "#fbbf24"),
    "security": ("rgba(136,19,55,0.45)", "#fb7185"),
    "bus": ("rgba(154,52,18,0.35)", "#fb923c"),
    "external": ("rgba(30,41,59,0.5)", "#94a3b8"),
}
# Arrow style: (CSS class, colour, end marker, legend text).
STYLES = {
    "call": ("flow", "#22d3ee", "arrow", "request"),
    "reply": ("flow-async", "#34d399", "arrow", "response or result"),
    "async": ("flow-async", "#fbbf24", "arrow", "asynchronous, no reply awaited"),
    "deny": ("flow-auth", "#fb7185", "cross", "rejected or failed"),
}
ARROWS = {"->>": "call", "->": "call", "-->>": "reply", "-->": "reply",
          "-)": "async", "--)": "async", "-x": "deny", "--x": "deny"}

FONT = "'JetBrains Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace"
CHAR = 0.62  # monospace advance per font-size unit, rounded up
WRAP = 24    # characters per message or note line; narrow columns keep the text legible
MARGIN = 24
TOP = 48     # room above the participants for the pause button


class SequenceError(ValueError):
    pass


@dataclass
class Participant:
    id: str
    text: str
    type: str = "backend"
    explicit_type: bool = False


@dataclass
class Sequence:
    title: str = ""
    subtitle: str = ""
    footer: str = ""
    autonumber: bool = False
    participants: dict[str, Participant] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


def is_sequence(text: str) -> bool:
    for line in _body(text.splitlines(), "")[0]:
        stripped = line.strip()
        if stripped and not stripped.startswith("%%"):
            return stripped == "sequenceDiagram"
    return False


def _body(lines: list[str], name: str) -> tuple[list[str], str, int]:
    """Strip the front matter; return the remaining lines, the title, and the offset."""
    if not lines or lines[0].strip() != "---":
        return lines, "", 0
    title = ""
    for index in range(1, len(lines)):
        stripped = lines[index].strip()
        if stripped == "---":
            return lines[index + 1:], title, index + 1
        if stripped.startswith("title:"):
            title = stripped[6:].strip().strip('"')
    raise SequenceError(f"{name}: unterminated front matter")


def _participant(sequence: Sequence, participant_id: str) -> Participant:
    if participant_id not in sequence.participants:
        sequence.participants[participant_id] = Participant(participant_id, participant_id)
    return sequence.participants[participant_id]


def parse(text: str, name: str = "diagram") -> Sequence:
    sequence = Sequence()
    lines, sequence.title, offset = _body(text.splitlines(), name)
    header_seen = False
    stack: list[str] = []
    types: dict[str, str] = {}
    for number, raw in enumerate(lines, start=offset + 1):
        where = f"{name}:{number}"
        line = raw.strip()
        if not line:
            continue
        if line.startswith("%%"):
            body = line[2:].strip()
            if body.startswith("@"):
                key, _, value = body.partition(" ")
                value = value.strip()
                if key == "@subtitle":
                    sequence.subtitle = value
                elif key == "@footer":
                    sequence.footer = value
                elif key == "@type":
                    kind, _, ids = value.partition(":")
                    if kind.strip() not in TYPES or not ids.split():
                        raise SequenceError(f"{where}: @type needs '{'|'.join(sorted(TYPES))}: ID ID ...'")
                    for participant_id in ids.split():
                        types[participant_id] = kind.strip()
                else:
                    raise SequenceError(f"{where}: unknown directive {key}")
            continue
        if not header_seen:
            if line != "sequenceDiagram":
                raise SequenceError(f"{where}: expected 'sequenceDiagram'")
            header_seen = True
            continue
        if line == "autonumber":
            sequence.autonumber = True
            continue
        if ACTIVATION_RE.match(line):
            continue
        match = PARTICIPANT_RE.match(line)
        if match:
            keyword, participant_id, label = match.groups()
            if participant_id in sequence.participants:
                raise SequenceError(f"{where}: participant {participant_id} is declared twice or after its first use")
            participant = _participant(sequence, participant_id)
            participant.text = (label or participant_id).strip()
            if keyword == "actor":
                participant.type = "frontend"
            continue
        match = MESSAGE_RE.match(line)
        if match:
            source, arrow, _, target, label = match.groups()
            _participant(sequence, source)
            _participant(sequence, target)
            sequence.events.append({"kind": "message", "from": source, "to": target,
                                    "style": ARROWS[arrow], "label": label.strip()})
            continue
        match = NOTE_RE.match(line)
        if match:
            placement, first, second, label = match.groups()
            if second and placement != "over":
                raise SequenceError(f"{where}: only 'Note over' can span two participants")
            _participant(sequence, first)
            if second:
                _participant(sequence, second)
            sequence.events.append({"kind": "note", "placement": placement, "first": first,
                                    "second": second, "label": label.strip()})
            continue
        match = BLOCK_RE.match(line)
        if match:
            stack.append(match.group(1))
            sequence.events.append({"kind": "start", "block": match.group(1), "label": (match.group(2) or "").strip()})
            continue
        match = DIVIDER_RE.match(line)
        if match:
            if not stack or DIVIDERS.get(stack[-1]) != match.group(1):
                raise SequenceError(f"{where}: '{match.group(1)}' outside a matching block")
            sequence.events.append({"kind": "divider", "block": match.group(1), "label": (match.group(2) or "").strip()})
            continue
        if line == "end":
            if not stack:
                raise SequenceError(f"{where}: 'end' without a block")
            stack.pop()
            sequence.events.append({"kind": "end"})
            continue
        raise SequenceError(f"{where}: unsupported sequence syntax {line!r}")
    if not header_seen:
        raise SequenceError(f"{name}: missing 'sequenceDiagram' header")
    if stack:
        raise SequenceError(f"{name}: '{stack[-1]}' block is not closed")
    if not sequence.title:
        raise SequenceError(f"{name}: add a front-matter title")
    if not any(event["kind"] == "message" for event in sequence.events):
        raise SequenceError(f"{name}: a sequence diagram needs at least one message")
    unknown = sorted(set(types) - set(sequence.participants))
    if unknown:
        raise SequenceError(f"{name}: @type names unknown participants: {', '.join(unknown)}")
    for participant_id, kind in types.items():
        sequence.participants[participant_id].type = kind
    if sequence.autonumber:
        count = 0
        for event in sequence.events:
            if event["kind"] == "message":
                count += 1
                event["number"] = count
    return sequence


def to_graph(sequence: Sequence, name: str) -> dict[str, Any]:
    """The semantic graph printed by --json and used by the fidelity check."""
    participants = []
    for participant in sequence.participants.values():
        label, sublabel = split_label(participant.text)
        item = {"id": participant.id, "label": label, "type": participant.type}
        if sublabel:
            item["sublabel"] = sublabel
        participants.append(item)
    graph: dict[str, Any] = {
        "mode": "sequence",
        "title": sequence.title,
        "footer": sequence.footer or f"Generated from {name}.mmd by scripts/render-diagrams.py — edit the Mermaid source, not this file",
        "participants": participants,
        "events": sequence.events,
    }
    if sequence.subtitle:
        graph["subtitle"] = sequence.subtitle
    return graph


# --------------------------------------------------------------------------
# Layout


def split_label(text: str) -> tuple[str, str | None]:
    parts = [part.strip() for part in BR_RE.split(text) if part.strip()]
    if not parts:
        return "", None
    return parts[0], " ".join(parts[1:]) or None


def wrap(text: str, width: int = WRAP) -> list[str]:
    lines: list[str] = []
    for paragraph in BR_RE.split(text):
        current = ""
        for word in paragraph.split():
            if current and len(current) + 1 + len(word) > width:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}" if current else word
        if current:
            lines.append(current)
    return lines or [""]


def text_width(lines: list[str], size: float) -> float:
    return max(len(line) for line in lines) * size * CHAR


def fmt(value: float) -> str:
    return f"{round(value, 1):g}"


def esc(text: str) -> str:
    return html_lib.escape(text, quote=True)


class Layout:
    """Coordinates for every element, computed before any SVG is written."""

    BOX_GAP = 20
    LABEL_PAD = 26
    LOOP_W = 40
    MSG_SIZE = 11
    MSG_LINE = 14
    NOTE_SIZE = 11

    def __init__(self, graph: dict[str, Any]) -> None:
        self.graph = graph
        self.order = [participant["id"] for participant in graph["participants"]]
        self.index = {participant_id: i for i, participant_id in enumerate(self.order)}
        self.boxes = self._boxes()
        self.labels = [self._message_lines(event) for event in graph["events"]]
        self.centers = self._columns()
        self.items: list[dict[str, Any]] = []
        self.frames: list[dict[str, Any]] = []
        self._rows()
        self._bounds()

    def _boxes(self) -> list[dict[str, Any]]:
        boxes = []
        for participant in self.graph["participants"]:
            lines = [participant["label"]]
            width = text_width(lines, 13) + 32
            if participant.get("sublabel"):
                width = max(width, text_width([participant["sublabel"]], 10) + 32)
            boxes.append({"width": max(100.0, width), "participant": participant})
        self.box_height = 50.0 if any(p.get("sublabel") for p in self.graph["participants"]) else 36.0
        return boxes

    def _message_lines(self, event: dict[str, Any]) -> list[str]:
        if event["kind"] not in {"message", "note"}:
            return []
        label = event["label"]
        if event.get("number"):
            label = f"{event['number']}. {label}"
        return wrap(label)

    def _columns(self) -> list[float]:
        count = len(self.order)
        gaps = [0.0] * count
        for i in range(1, count):
            gaps[i] = self.boxes[i - 1]["width"] / 2 + self.boxes[i]["width"] / 2 + self.BOX_GAP
        constraints: list[tuple[int, int, float]] = []
        self.right_extra = 0.0
        self.left_extra = 0.0
        for event, lines in zip(self.graph["events"], self.labels):
            if event["kind"] == "message":
                a, b = self.index[event["from"]], self.index[event["to"]]
                need = text_width(lines, self.MSG_SIZE) + self.LABEL_PAD
                if a == b:
                    need += self.LOOP_W
                    if a + 1 < count:
                        constraints.append((a, a + 1, need))
                    else:
                        self.right_extra = max(self.right_extra, need)
                else:
                    constraints.append((min(a, b), max(a, b), need))
            elif event["kind"] == "note":
                need = text_width(lines, self.NOTE_SIZE) + 24
                a = self.index[event["first"]]
                if event["placement"] == "right of":
                    if a + 1 < count:
                        constraints.append((a, a + 1, need + 16))
                    else:
                        self.right_extra = max(self.right_extra, need + 16)
                elif event["placement"] == "left of":
                    if a > 0:
                        constraints.append((a - 1, a, need + 16))
                    else:
                        self.left_extra = max(self.left_extra, need + 16)
                elif event["second"]:
                    b = self.index[event["second"]]
                    lo, hi = min(a, b), max(a, b)
                    if lo != hi:
                        constraints.append((lo, hi, need - 40))
        for low, high, need in sorted(constraints, key=lambda item: item[1] - item[0]):
            have = sum(gaps[low + 1:high + 1])
            if have < need:
                gaps[high] += need - have
        centers, x = [], 0.0
        for i in range(count):
            x += gaps[i]
            centers.append(x)
        return centers

    def _rows(self) -> None:
        y = TOP + self.box_height + 28
        stack: list[dict[str, Any]] = []
        for event, lines in zip(self.graph["events"], self.labels):
            kind = event["kind"]
            if kind == "message":
                a, b = self.index[event["from"]], self.index[event["to"]]
                xa, xb = self.centers[a], self.centers[b]
                item = {"kind": "message", "event": event, "lines": lines}
                if a == b:
                    # The label sits beside the loop, vertically centred on it.
                    text_height = len(lines) * self.MSG_LINE
                    loop_top = y + max(0.0, (text_height - 18) / 2) + 2
                    loop_x = xa + self.LOOP_W
                    item["d"] = (f"M{fmt(xa + 4)} {fmt(loop_top)} H{fmt(loop_x)} "
                                 f"V{fmt(loop_top + 18)} H{fmt(xa + 6)}")
                    item["label_top"] = loop_top + 9 - text_height / 2 - 3
                    item["label_x"] = loop_x + 10
                    item["anchor"] = "start"
                    item["extent"] = (xa, loop_x + 10 + text_width(lines, self.MSG_SIZE))
                    y = max(loop_top + 18, y + text_height) + 22
                else:
                    item["label_top"] = y
                    arrow_y = y + len(lines) * self.MSG_LINE + 6
                    sign = 1 if xb > xa else -1
                    item["d"] = f"M{fmt(xa + 4 * sign)} {fmt(arrow_y)} H{fmt(xb - 6 * sign)}"
                    item["label_x"] = (xa + xb) / 2
                    item["anchor"] = "middle"
                    item["extent"] = (min(xa, xb), max(xa, xb))
                    y = arrow_y + 20
                self.items.append(item)
                for frame in stack:
                    frame["members"].append(item["extent"])
            elif kind == "note":
                width = text_width(lines, self.NOTE_SIZE) + 24
                height = len(lines) * self.MSG_LINE + 14
                a = self.centers[self.index[event["first"]]]
                if event["placement"] == "right of":
                    x = a + 16
                elif event["placement"] == "left of":
                    x = a - 16 - width
                elif event["second"]:
                    b = self.centers[self.index[event["second"]]]
                    low, high = min(a, b), max(a, b)
                    width = max(width, high - low + 40)
                    x = (low + high) / 2 - width / 2
                else:
                    x = a - width / 2
                item = {"kind": "note", "lines": lines, "x": x, "y": y, "width": width, "height": height,
                        "extent": (x, x + width)}
                self.items.append(item)
                for frame in stack:
                    frame["members"].append(item["extent"])
                y += height + 16
            elif kind == "start":
                frame = {"block": event["block"], "label": event["label"], "top": y, "dividers": [],
                         "members": [], "depth": len(stack)}
                stack.append(frame)
                self.frames.append(frame)
                y += 32
            elif kind == "divider":
                stack[-1]["dividers"].append({"y": y, "label": event["label"]})
                y += 30 if event["label"] else 14
            else:
                frame = stack.pop()
                frame["bottom"] = y
                if not frame["members"]:
                    frame["members"].append((self.centers[0], self.centers[0]))
                y += 14
                for outer in stack:
                    outer["members"].extend(frame["members"])
        self.lifeline_end = y + 6
        self.footer_y = self.lifeline_end
        for frame in self.frames:
            # Frames nested deeper sit closer to their content.
            low = min(extent[0] for extent in frame["members"])
            high = max(extent[1] for extent in frame["members"])
            pad = 28 - 7 * frame["depth"]
            frame["x1"] = low - pad
            frame["x2"] = max(high + pad, frame["x1"] + self._frame_label_width(frame) + 24)

    def _frame_label_width(self, frame: dict[str, Any]) -> float:
        width = len(frame["block"]) * 11 * CHAR + 20
        labels = [frame["label"], *[divider["label"] for divider in frame["dividers"]]]
        return width + max(len(label) for label in labels) * 11 * CHAR + 16

    def _bounds(self) -> None:
        lows = [c - box["width"] / 2 for c, box in zip(self.centers, self.boxes)]
        highs = [c + box["width"] / 2 for c, box in zip(self.centers, self.boxes)]
        lows.append(self.centers[0] - self.left_extra)
        highs.append(self.centers[-1] + self.right_extra)
        for item in self.items:
            lows.append(item["extent"][0])
            highs.append(item["extent"][1])
        for frame in self.frames:
            lows.append(frame["x1"])
            highs.append(frame["x2"])
        self.dx = MARGIN - min(lows)
        self.width = max(highs) + self.dx + MARGIN
        self.height = self.footer_y + self.box_height + MARGIN


# --------------------------------------------------------------------------
# SVG and HTML


MARKERS = (
    '<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
    'orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" '
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>'
    '<marker id="cross" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto"><path d="M2 2L8 8M8 2L2 8" fill="none" stroke="context-stroke" '
    'stroke-width="1.6" stroke-linecap="round"/></marker>'
)
HALO = 'stroke="#030a1c" stroke-width="4" stroke-linejoin="round" paint-order="stroke"'


def _shift_path(d: str, dx: float) -> str:
    def shift(match: re.Match[str]) -> str:
        command, number = match.group(1), float(match.group(2))
        return f"{command}{fmt(number + dx)}"
    # Only x coordinates move: the first number after M and every H value.
    return re.sub(r"([MH])(-?[\d.]+)", shift, d)


def _participant_svg(layout: Layout, index: int, y: float) -> str:
    box = layout.boxes[index]
    participant = box["participant"]
    fill, stroke = PALETTE[participant["type"]]
    cx = layout.centers[index] + layout.dx
    x = cx - box["width"] / 2
    h = layout.box_height
    parts = [
        f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(box["width"])}" height="{fmt(h)}" rx="8" fill="#0b1226"/>',
        f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(box["width"])}" height="{fmt(h)}" rx="8" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>',
    ]
    if participant.get("sublabel"):
        parts.append(f'<text x="{fmt(cx)}" y="{fmt(y + 21)}" fill="#e2e8f0" font-size="13" '
                     f'font-weight="500">{esc(participant["label"])}</text>')
        parts.append(f'<text x="{fmt(cx)}" y="{fmt(y + 38)}" fill="#64748b" font-size="10">'
                     f'{esc(participant["sublabel"])}</text>')
    else:
        parts.append(f'<text x="{fmt(cx)}" y="{fmt(y + h / 2 + 4.5)}" fill="#e2e8f0" font-size="13" '
                     f'font-weight="500">{esc(participant["label"])}</text>')
    return "".join(parts)


def _text_lines(lines: list[str], x: float, top: float, size: float, fill: str, anchor: str) -> str:
    spans = []
    for i, line in enumerate(lines):
        spans.append(f'<text x="{fmt(x)}" y="{fmt(top + 11 + i * Layout.MSG_LINE)}" fill="{fill}" '
                     f'font-size="{fmt(size)}" text-anchor="{anchor}" {HALO}>{esc(line)}</text>')
    return "".join(spans)


def render_svg(graph: dict[str, Any]) -> tuple[str, int, int]:
    layout = Layout(graph)
    dx = layout.dx
    width, height = round(layout.width), round(layout.height)
    top = TOP
    bottom = layout.footer_y
    out = [
        f'<svg id="flowsvg" width="100%" viewBox="0 0 {width} {height}" role="img" '
        f'aria-roledescription="sequence diagram" aria-labelledby="flowsvg-title" aria-describedby="flowsvg-desc">',
        f'<title id="flowsvg-title">{esc(graph["title"])} animated sequence diagram</title>',
        f'<desc id="flowsvg-desc">{esc(_description(graph))}</desc>',
        f"<defs>{MARKERS}</defs>",
    ]
    # Frames first, so lifelines, messages, and notes paint over them.
    out.append('<g fill="none">')
    for frame in layout.frames:
        x1, x2 = frame["x1"] + dx, frame["x2"] + dx
        y1, y2 = frame["top"], frame["bottom"]
        out.append(f'<polygon points="{fmt(x1)},{fmt(y1)} {fmt(x2)},{fmt(y1)} {fmt(x2)},{fmt(y2)} '
                   f'{fmt(x1)},{fmt(y2)}" stroke="#475569" stroke-width="1"/>')
        for divider in frame["dividers"]:
            out.append(f'<line x1="{fmt(x1)}" y1="{fmt(divider["y"])}" x2="{fmt(x2)}" y2="{fmt(divider["y"])}" '
                       f'stroke="#475569" stroke-width="1" stroke-dasharray="4 4"/>')
    out.append("</g>")
    for frame in layout.frames:
        x1, y1 = frame["x1"] + dx, frame["top"]
        tab = len(frame["block"]) * 11 * CHAR + 16
        out.append(f'<polygon points="{fmt(x1)},{fmt(y1)} {fmt(x1 + tab)},{fmt(y1)} {fmt(x1 + tab)},{fmt(y1 + 12)} '
                   f'{fmt(x1 + tab - 6)},{fmt(y1 + 19)} {fmt(x1)},{fmt(y1 + 19)}" fill="#1e293b" stroke="#475569" '
                   f'stroke-width="1"/>')
        out.append(f'<text x="{fmt(x1 + 8)}" y="{fmt(y1 + 13.5)}" fill="#fbbf24" font-size="11" '
                   f'font-weight="600">{esc(frame["block"])}</text>')
        if frame["label"]:
            out.append(f'<text x="{fmt(x1 + tab + 8)}" y="{fmt(y1 + 13.5)}" fill="#94a3b8" font-size="11" '
                       f'{HALO}>[{esc(frame["label"])}]</text>')
        for divider in frame["dividers"]:
            if divider["label"]:
                out.append(f'<text x="{fmt(x1 + 8)}" y="{fmt(divider["y"] + 15)}" fill="#94a3b8" font-size="11" '
                           f'{HALO}>[{esc(divider["label"])}]</text>')
    for center in layout.centers:
        x = center + dx
        out.append(f'<line x1="{fmt(x)}" y1="{fmt(top + layout.box_height)}" x2="{fmt(x)}" y2="{fmt(bottom)}" '
                   f'stroke="#334155" stroke-width="1" stroke-dasharray="3 5"/>')
    messages = [item for item in layout.items if item["kind"] == "message"]
    out.append('<g fill="none" stroke-width="1.5" stroke-linecap="round">')
    for item in messages:
        css, colour, marker, _ = STYLES[item["event"]["style"]]
        item["path"] = _shift_path(item["d"], dx)
        out.append(f'<path class="{css}" d="{item["path"]}" stroke="{colour}" marker-end="url(#{marker})"/>')
    out.append("</g>")
    out.append('<g class="dots">')
    for i, item in enumerate(messages):
        colour = STYLES[item["event"]["style"]][1]
        if i == 0:
            begin = f"0.4s;m{len(messages) - 1}.end+1.2s"
        else:
            begin = f"m{i - 1}.end+0.15s"
        length = sum(abs(float(n)) for n in re.findall(r"[HV](-?[\d.]+)", item["path"])) or 1.0
        duration = max(0.45, min(1.6, length / 320))
        out.append(f'<circle r="3.5" class="dot" fill="{colour}" opacity="0">'
                   f'<animateMotion id="m{i}" dur="{duration:.2f}s" begin="{begin}" path="{item["path"]}"/>'
                   f'<set attributeName="opacity" to="1" begin="m{i}.begin" end="m{i}.end"/></circle>')
    out.append("</g>")
    out.append(f'<g font-family="{esc(FONT)}">')
    for item in layout.items:
        if item["kind"] == "message":
            out.append(_text_lines(item["lines"], item["label_x"] + dx, item["label_top"], Layout.MSG_SIZE,
                                   "#cbd5e1", item["anchor"]))
        else:
            x = item["x"] + dx
            out.append(f'<rect x="{fmt(x)}" y="{fmt(item["y"])}" width="{fmt(item["width"])}" '
                       f'height="{fmt(item["height"])}" rx="4" fill="#0b1226"/>')
            out.append(f'<rect x="{fmt(x)}" y="{fmt(item["y"])}" width="{fmt(item["width"])}" '
                       f'height="{fmt(item["height"])}" rx="4" fill="rgba(120,53,15,0.35)" stroke="#b45309" '
                       f'stroke-width="1"/>')
            out.append(_text_lines(item["lines"], x + item["width"] / 2, item["y"] + 4, Layout.NOTE_SIZE,
                                   "#fde68a", "middle"))
    out.append("</g>")
    out.append(f'<g font-family="{esc(FONT)}" text-anchor="middle">')
    for index in range(len(layout.order)):
        out.append(_participant_svg(layout, index, top))
        out.append(_participant_svg(layout, index, bottom))
    out.append("</g></svg>")
    return "".join(out), width, height


def _description(graph: dict[str, Any]) -> str:
    names = ", ".join(participant["label"] for participant in graph["participants"])
    count = sum(1 for event in graph["events"] if event["kind"] == "message")
    return (f"Sequence diagram with {len(graph['participants'])} participants ({names}) "
            f"exchanging {count} messages from top to bottom.")


CSS = """
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'JetBrains Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace; background: #020617;
         min-height: 100vh; padding: 2rem; color: #e2e8f0; }
  .container { max-width: %dpx; margin: 0 auto; }
  .header { display: flex; align-items: center; gap: 12px; margin-bottom: 0.4rem; }
  .pulse-dot { width: 10px; height: 10px; border-radius: 50%%; background: #22d3ee; }
  h1 { font-size: 1.25rem; font-weight: 600; letter-spacing: -0.02em; }
  .subtitle { color: #64748b; font-size: 0.8rem; margin-bottom: 1.5rem; }
  .diagram-card { border: 1px solid #1e293b; border-radius: 12px; position: relative;
    background: repeating-linear-gradient(0deg, #0f1b33 0 0.5px, transparent 0.5px 40px),
      repeating-linear-gradient(90deg, #0f1b33 0 0.5px, transparent 0.5px 40px), #030a1c;
    padding: 1.25rem 1rem; }
  .pause-btn { position: absolute; top: 12px; right: 12px; z-index: 2; background: transparent;
    border: 1px solid #1e293b; color: #64748b; border-radius: 6px; padding: 4px 10px;
    font: inherit; font-size: 11px; cursor: pointer; }
  .pause-btn:hover { color: #e2e8f0; border-color: #334155; }
  .legend { display: flex; flex-wrap: wrap; gap: 0.5rem 1.25rem; margin-top: 1rem; font-size: 11px; color: #94a3b8; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .legend svg { width: 28px; height: 8px; }
  .footer { color: #475569; font-size: 0.7rem; margin-top: 1rem; }
  .flow { stroke-dasharray: 5 5; }
  .flow-async { stroke-dasharray: 2 4; }
  .flow-auth { stroke-dasharray: 4 4; }
  @media (prefers-reduced-motion: no-preference) {
    .flow { animation: dashmove 0.75s linear infinite; }
    .flow-async { animation: dashasync 0.9s linear infinite; }
    .flow-auth { animation: dashauth 1.2s linear infinite; }
    .pulse-dot { animation: pulse 2s ease-in-out infinite; }
  }
  @keyframes dashmove { to { stroke-dashoffset: -10; } }
  @keyframes dashasync { to { stroke-dashoffset: -12; } }
  @keyframes dashauth { to { stroke-dashoffset: -8; } }
  @keyframes pulse { 0%%, 100%% { opacity: 1; } 50%% { opacity: 0.4; } }
  body.paused .flow, body.paused .flow-async, body.paused .flow-auth,
  body.paused .pulse-dot { animation-play-state: paused; }
"""

SCRIPT = """
<script>
(function () {
  var svg = document.getElementById('flowsvg');
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.querySelectorAll('.dot').forEach(function (d) { d.remove(); });
    var b = document.getElementById('pauseBtn'); if (b) b.remove(); return;
  }
  var btn = document.getElementById('pauseBtn'); var paused = false;
  btn.addEventListener('click', function () {
    paused = !paused; document.body.classList.toggle('paused', paused);
    paused ? svg.pauseAnimations() : svg.unpauseAnimations();
    btn.textContent = paused ? '▶ play' : '⏸ pause';
    btn.setAttribute('aria-pressed', String(paused));
  });
})();
</script>
"""


def _legend(graph: dict[str, Any]) -> str:
    used = [event["style"] for event in graph["events"] if event["kind"] == "message"]
    entries = []
    for style in STYLES:
        if style not in used:
            continue
        _, colour, _, text = STYLES[style]
        dash = "5 5" if style == "call" else ("4 4" if style == "deny" else "2 4")
        entries.append(f'<span><svg aria-hidden="true" viewBox="0 0 28 8"><line x1="0" y1="4" x2="28" y2="4" '
                       f'stroke="{colour}" stroke-width="1.5" stroke-dasharray="{dash}"/></svg>{esc(text)}</span>')
    return f'<div class="legend">{"".join(entries)}</div>'


def render_html(graph: dict[str, Any]) -> str:
    svg, width, _ = render_svg(graph)
    subtitle = f'\n  <p class="subtitle">{esc(graph["subtitle"])}</p>' if graph.get("subtitle") else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(graph["title"])} — dashmotion</title>
<style>{CSS % max(720, min(1280, width + 40))}</style>
</head>
<body>
<div class="container">
  <div class="header"><div class="pulse-dot"></div><h1>{esc(graph["title"])}</h1></div>{subtitle}
  <div class="diagram-card">
    <button class="pause-btn" id="pauseBtn" aria-pressed="false">⏸ pause</button>
    {svg}
  </div>
  {_legend(graph)}
  <p class="footer">{esc(graph["footer"])}</p>
</div>
{SCRIPT}
</body>
</html>
"""


def fidelity_problems(graph: dict[str, Any], content: str) -> list[str]:
    """Check that every label reached the SVG and every message became a connector."""
    match = re.search(r"<svg\b.*?</svg>", content, re.DOTALL)
    if not match:
        return ["no <svg> element"]
    svg = match.group(0)
    text = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", svg)).split())
    expected = [participant["label"] for participant in graph["participants"]]
    expected += [participant["sublabel"] for participant in graph["participants"] if participant.get("sublabel")]
    for event in graph["events"]:
        if event.get("label"):
            expected.append(" ".join(BR_RE.sub(" ", event["label"]).split()))
    problems = [f"label missing from the diagram: {label!r}" for label in expected
                if " ".join(label.split()) not in text]
    without_markers = re.sub(r"<marker\b.*?</marker>", "", svg, flags=re.DOTALL)
    connectors = len(re.findall(r"<path\b", without_markers))
    messages = sum(1 for event in graph["events"] if event["kind"] == "message")
    if connectors != messages:
        problems.append(f"{messages} messages in the source but {connectors} connectors drawn")
    return problems
