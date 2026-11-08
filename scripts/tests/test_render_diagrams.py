from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import diagram_layout  # noqa: E402
from maintenance_common import ScriptError  # noqa: E402

SPEC = importlib.util.spec_from_file_location("render_diagrams", SCRIPTS / "render-diagrams.py")
assert SPEC is not None and SPEC.loader is not None
diagrams = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = diagrams
SPEC.loader.exec_module(diagrams)

FLOW = """---
title: Sample flow
---
flowchart TD
  %% @subtitle A short flow
  S(["START"]) --> A["Do work"]
  A --> D{"OK?"}
  D -->|"yes"| E(["DONE"])
  D -->|no| F["Retry later"] & G["Alert"]
  F -.-> E
  G ==> E
"""

ARCH = """---
title: Sample stack
---
flowchart TD
  %% @mode architecture
  %% @card cyan | Path | one | two
  %% @card violet | Data | three
  %% @card rose | Edges | four
  %% @journey #22d3ee: C GW X
  subgraph CL["Clients"]
    C["Browser<br/>user agent"]:::frontend
  end
  subgraph ST["Stack"]
    GW["Gateway"]
    subgraph NET["internal network"]
      DB[("Postgres<br/>state")]
    end
  end
  subgraph EX["External"]
    X["Provider"]
  end
  class X external
  C ==>|"HTTPS"| GW
  GW --> DB
  GW --> X
  GW ---|"denied"| DB
  classDef frontend fill:#083344
"""


SEQUENCE = """---
title: Sample sequence
---
sequenceDiagram
  %% @subtitle A short exchange
  %% @type external: P
  actor C as Client
  participant G as Gateway<br/>auth + routing
  participant P as Provider
  C->>G: request
  alt key invalid
    G--xC: 401
  else key valid
    G->>+P: call the model
    P-->>-G: completion
    G-)G: write the spend log
  end
  par one
    G->>P: first
  and
    G->>P: second
  end
  Note over G,P: no content is logged
  G-->>C: response
"""


def graph(text: str, name: str = "sample") -> dict:
    return diagrams.parse_graph(text, name)


class FlowParsingTests(unittest.TestCase):
    def test_shapes_edges_and_labels(self) -> None:
        result = graph(FLOW)
        self.assertEqual(result["mode"], "flow")
        self.assertEqual(result["title"], "Sample flow")
        self.assertEqual(result["subtitle"], "A short flow")
        nodes = {node["id"]: node for node in result["nodes"]}
        self.assertEqual(nodes["S"]["shape"], "pill")
        self.assertEqual(nodes["E"]["shape"], "pill")
        self.assertEqual(nodes["D"]["shape"], "decision")
        self.assertNotIn("shape", nodes["A"])
        edges = [(edge["from"], edge["to"], edge["kind"], edge.get("label")) for edge in result["edges"]]
        self.assertIn(("D", "F", "sync", "no"), edges)
        self.assertIn(("D", "G", "sync", "no"), edges)
        self.assertIn(("F", "E", "async", None), edges)
        self.assertIn(("G", "E", "main", None), edges)
        self.assertEqual(len(edges), 7)

    def test_rounded_node_with_both_degrees_is_a_step(self) -> None:
        result = graph("---\ntitle: t\n---\nflowchart TD\n  A --> B([\"Middle\"]) --> C\n  A[\"a\"]\n  C[\"c\"]\n")
        nodes = {node["id"]: node for node in result["nodes"]}
        self.assertNotIn("shape", nodes["B"])


class ArchitectureParsingTests(unittest.TestCase):
    def test_groups_types_and_summary(self) -> None:
        result = graph(ARCH)
        nodes = {node["id"]: node for node in result["nodes"]}
        self.assertEqual(nodes["C"], {"id": "C", "label": "Browser", "sublabel": "user agent",
                                      "type": "frontend", "group": "CL"})
        self.assertEqual(nodes["DB"]["type"], "database")
        self.assertEqual(nodes["DB"]["group"], "NET")
        self.assertEqual(nodes["GW"]["type"], "backend")
        self.assertEqual(nodes["X"]["type"], "external")
        groups = {group["id"]: group for group in result["groups"]}
        self.assertEqual(groups["ST"]["kind"], "region")
        self.assertEqual(groups["NET"], {"id": "NET", "label": "internal network", "kind": "subnet", "parent": "ST"})
        self.assertEqual(result["journeys"], [{"color": "#22d3ee", "hops": [["C", "GW"], ["GW", "X"]]}])
        self.assertEqual([card["accent"] for card in result["summary"]], ["cyan", "violet", "rose"])
        self.assertIn({"from": "GW", "to": "DB", "kind": "static", "label": "denied"}, result["edges"])


class ValidationTests(unittest.TestCase):
    def assert_rejected(self, text: str, message: str) -> None:
        with self.assertRaisesRegex(diagrams.DiagramError, message):
            graph(text)

    def test_journey_must_follow_edges(self) -> None:
        self.assert_rejected(ARCH.replace("C GW X", "C X"), "journey hop C -> X")

    def test_layout_hints_are_not_accepted(self) -> None:
        # The layout comes from the Mermaid edges alone, as in any Mermaid viewer.
        self.assert_rejected(ARCH.replace("  %% @mode architecture\n", "  %% @mode architecture\n  %% @tier 0: C\n"),
                             "unknown directive @tier")

    def test_unlabelled_node_is_rejected(self) -> None:
        self.assert_rejected("---\ntitle: t\n---\nflowchart TD\n  A[\"a\"] --> B\n", "without a label: B")

    def test_unknown_directive_is_rejected(self) -> None:
        self.assert_rejected(FLOW.replace("@subtitle", "@colour"), "unknown directive")

    def test_title_is_required(self) -> None:
        self.assert_rejected("flowchart TD\n  A[\"a\"]\n", "front-matter title")

    def test_unsupported_diagram_type_is_rejected(self) -> None:
        self.assert_rejected("---\ntitle: t\n---\nclassDiagram\n  A <|-- B\n", "only 'flowchart' and 'sequenceDiagram'")

    def test_unsupported_edge_syntax_is_rejected(self) -> None:
        self.assert_rejected("---\ntitle: t\n---\nflowchart TD\n  A[\"a\"] -- text --> B[\"b\"]\n", "unsupported syntax")


class SequenceTests(unittest.TestCase):
    def test_participants_messages_notes_and_blocks(self) -> None:
        result = graph(SEQUENCE)
        self.assertEqual(result["mode"], "sequence")
        self.assertEqual(result["subtitle"], "A short exchange")
        self.assertEqual(result["participants"], [
            {"id": "C", "label": "Client", "type": "frontend"},
            {"id": "G", "label": "Gateway", "type": "backend", "sublabel": "auth + routing"},
            {"id": "P", "label": "Provider", "type": "external"},
        ])
        messages = [(e["from"], e["to"], e["style"]) for e in result["events"] if e["kind"] == "message"]
        self.assertEqual(messages, [("C", "G", "call"), ("G", "C", "deny"), ("G", "P", "call"),
                                    ("P", "G", "reply"), ("G", "G", "async"), ("G", "P", "call"),
                                    ("G", "P", "call"), ("G", "C", "reply")])
        kinds = [e["kind"] for e in result["events"]]
        self.assertEqual(kinds.count("start"), 2)
        self.assertEqual(kinds.count("end"), 2)
        self.assertEqual(kinds.count("divider"), 2)
        self.assertIn({"kind": "note", "placement": "over", "first": "G", "second": "P",
                       "label": "no content is logged"}, result["events"])

    def test_rendered_svg_keeps_every_label_and_message(self) -> None:
        result = graph(SEQUENCE)
        content = diagrams.sequence_diagrams.render_html(result)
        self.assertEqual(diagrams.sequence_diagrams.fidelity_problems(result, content), [])
        self.assertEqual(content.count('<animateMotion id="m'), 8)
        self.assertIn('begin="0.4s;m7.end+1.2s"', content)
        self.assertIn("prefers-reduced-motion", content)

    def test_fidelity_reports_a_missing_message(self) -> None:
        result = graph(SEQUENCE)
        content = diagrams.sequence_diagrams.render_html(result).replace(">completion<", "><", 1)
        problems = diagrams.sequence_diagrams.fidelity_problems(result, content)
        self.assertEqual(problems, ["label missing from the diagram: 'completion'"])

    def test_invalid_sequences_are_rejected(self) -> None:
        cases = {
            "  else orphan\n": "outside a matching block",
            "  loop forever\n    A->>B: x\n": "block is not closed",
            "  A->>B hi\n": "unsupported sequence syntax",
            "  Note right of A, B: x\n": "only 'Note over'",
            "  %% @type robot: A\n  A->>B: x\n": "@type needs",
            "  participant A\n  participant A\n  A->>B: x\n": "declared twice",
        }
        for body, message in cases.items():
            with self.subTest(body=body), self.assertRaisesRegex(diagrams.DiagramError, message):
                graph("---\ntitle: t\n---\nsequenceDiagram\n" + body)
        with self.assertRaisesRegex(diagrams.DiagramError, "at least one message"):
            graph("---\ntitle: t\n---\nsequenceDiagram\n  participant A\n")
        with self.assertRaisesRegex(diagrams.DiagramError, "front-matter title"):
            graph("sequenceDiagram\n  A->>B: x\n")


class FidelityTests(unittest.TestCase):
    def test_missing_label_and_connector_are_reported(self) -> None:
        sample = graph("---\ntitle: t\n---\nflowchart TD\n  A[\"Alpha\"] -->|\"go\"| B[\"Beta\"]\n")
        content = ('<svg><marker><path d="M0 0"/></marker><path d="M1 1"/>'
                   "<text>Alpha</text><text>go</text></svg>")
        self.assertEqual(diagrams.fidelity_problems(sample, content), ["label missing from the diagram: 'Beta'"])
        self.assertEqual(diagrams.fidelity_problems(sample, content.replace('<path d="M1 1"/>', "") + ""),
                         ["label missing from the diagram: 'Beta'", "1 edges in the source but 0 connectors drawn"])


class GraphvizLayoutTests(unittest.TestCase):
    def test_dot_source_has_one_cluster_per_boundary_and_no_fixed_rows(self) -> None:
        node = lambda node_id, group: SimpleNamespace(id=node_id, group=group, w=144.0, h=72.0)
        layout = SimpleNamespace(
            groups=[SimpleNamespace(id="ST", label="Stack", parent=None),
                    SimpleNamespace(id="NET", label='internal "net"', parent="ST")],
            nodes=[node("GW", "ST"), node("DB", "NET"), node("X", None)],
            edges=[SimpleNamespace(src="GW", dst="DB", kind="static", label="denied"),
                   SimpleNamespace(src="GW", dst="X", kind="sync", label=None)],
        )
        source = diagram_layout.to_dot(layout)
        self.assertIn('subgraph "cluster_ST" {', source)
        self.assertIn('label="internal \\"net\\""', source)
        self.assertLess(source.index('"cluster_ST"'), source.index('"cluster_NET"'))
        self.assertIn('"DB" [width=2.0000, height=1.0000];', source)
        self.assertIn('"GW" -> "DB" [id=e0, arrowhead=none, label="denied"];', source)
        self.assertIn('"GW" -> "X" [id=e1];', source)
        self.assertNotIn("rank=same", source)

    def test_spline_becomes_a_curve_that_stops_short_of_the_head(self) -> None:
        segs = diagram_layout._edge_segments("e,10,0 10,100 10,80 20,60 10,20", 100.0, 4.0)
        self.assertEqual(segs[0], ("M", (10.0, 0.0)))
        self.assertEqual(segs[1][0], "C")
        self.assertEqual(segs[2], ("L", (10.0, 96.0)))
        self.assertEqual(diagram_layout._path_data(segs), "M10 0 C10 20 20 40 10 80 V96")


class DashmotionDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bodies = {name: f"# {name}\n".encode() for name in diagrams.DASHMOTION_FILES}
        digests = {name: diagrams.hashlib.sha256(body).hexdigest() for name, body in self.bodies.items()}
        patcher = mock.patch.dict(diagrams.DASHMOTION_FILES, digests)
        patcher.start()
        self.addCleanup(patcher.stop)

    def serve(self, bodies: dict[str, bytes]) -> mock.MagicMock:
        def urlopen(url: str, timeout: float) -> io.BytesIO:
            self.assertTrue(url.startswith(diagrams.DASHMOTION_URL))
            return io.BytesIO(bodies[url.removeprefix(diagrams.DASHMOTION_URL)])
        return mock.patch.object(diagrams.urllib.request, "urlopen", side_effect=urlopen)

    def test_downloads_once_and_reuses_verified_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self.serve(self.bodies) as urlopen:
            cache = Path(temporary) / "dashmotion"
            self.assertEqual(diagrams.fetch_dashmotion(cache), cache)
            self.assertEqual((cache / "layout.py").read_bytes(), self.bodies["layout.py"])
            self.assertEqual(urlopen.call_count, 2)
            diagrams.fetch_dashmotion(cache)
            self.assertEqual(urlopen.call_count, 2)
            (cache / "layout.py").write_text("tampered\n", encoding="utf-8")
            diagrams.fetch_dashmotion(cache)
            self.assertEqual(urlopen.call_count, 3)
            self.assertEqual((cache / "layout.py").read_bytes(), self.bodies["layout.py"])

    def test_refuses_a_download_that_does_not_match_its_digest(self) -> None:
        tampered = {**self.bodies, "check_diagram.py": b"import os\n"}
        with tempfile.TemporaryDirectory() as temporary, self.serve(tampered):
            cache = Path(temporary) / "dashmotion"
            with self.assertRaisesRegex(ScriptError, "does not match its pinned SHA-256"):
                diagrams.fetch_dashmotion(cache)
            self.assertFalse((cache / "check_diagram.py").exists())

    def test_pinned_commit_and_digests_are_well_formed(self) -> None:
        self.assertRegex(diagrams.DASHMOTION_COMMIT, r"^[0-9a-f]{40}$")
        self.assertIn(diagrams.DASHMOTION_COMMIT, diagrams.DASHMOTION_URL)


class GeneratedHtmlTests(unittest.TestCase):
    def test_rendered_html_is_ignored_and_not_committed(self) -> None:
        tracked = subprocess.run(["git", "-C", str(diagrams.ROOT), "ls-files", "diagrams/*.html"],
                                 capture_output=True, text=True, check=False)
        if tracked.returncode:
            self.skipTest("Git metadata is unavailable")
        self.assertEqual(tracked.stdout.strip(), "")
        self.assertIn("/diagrams/*.html", (diagrams.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())


class RepositoryDiagramTests(unittest.TestCase):
    def test_every_committed_mermaid_source_is_valid(self) -> None:
        sources = sorted((diagrams.ROOT / "diagrams").glob("*.mmd"))
        self.assertTrue(sources)
        for source in sources:
            with self.subTest(source=source.name):
                graph(source.read_text(encoding="utf-8"), source.stem)


if __name__ == "__main__":
    unittest.main()
