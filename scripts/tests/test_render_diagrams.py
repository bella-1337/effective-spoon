from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

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
  %% @tier 0: C
  %% @tier 1: GW
  %% @tier 2: DB
  %% @tier 3: X
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


def graph(text: str, name: str = "sample") -> dict:
    return diagrams.to_graph(diagrams.parse_mermaid(text, name), name)


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
    def test_groups_types_tiers_and_summary(self) -> None:
        result = graph(ARCH)
        nodes = {node["id"]: node for node in result["nodes"]}
        self.assertEqual(nodes["C"], {"id": "C", "label": "Browser", "sublabel": "user agent",
                                      "type": "frontend", "tier": 0, "group": "CL"})
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

    def test_multi_group_architecture_needs_every_tier(self) -> None:
        self.assert_rejected(ARCH.replace("  %% @tier 3: X\n", ""), "needs @tier for X")

    def test_unlabelled_node_is_rejected(self) -> None:
        self.assert_rejected("---\ntitle: t\n---\nflowchart TD\n  A[\"a\"] --> B\n", "without a label: B")

    def test_unknown_directive_is_rejected(self) -> None:
        self.assert_rejected(FLOW.replace("@subtitle", "@colour"), "unknown directive")

    def test_title_is_required(self) -> None:
        self.assert_rejected("flowchart TD\n  A[\"a\"]\n", "front-matter title")

    def test_unsupported_diagram_type_is_rejected(self) -> None:
        self.assert_rejected("---\ntitle: t\n---\nsequenceDiagram\n  A->>B: hi\n", "only 'flowchart'")

    def test_unsupported_edge_syntax_is_rejected(self) -> None:
        self.assert_rejected("---\ntitle: t\n---\nflowchart TD\n  A[\"a\"] -- text --> B[\"b\"]\n", "unsupported syntax")


class FidelityTests(unittest.TestCase):
    def test_missing_label_and_connector_are_reported(self) -> None:
        sample = graph("---\ntitle: t\n---\nflowchart TD\n  A[\"Alpha\"] -->|\"go\"| B[\"Beta\"]\n")
        content = ('<svg><marker><path d="M0 0"/></marker><path d="M1 1"/>'
                   "<text>Alpha</text><text>go</text></svg>")
        self.assertEqual(diagrams.fidelity_problems(sample, content), ["label missing from the diagram: 'Beta'"])
        self.assertEqual(diagrams.fidelity_problems(sample, content.replace('<path d="M1 1"/>', "") + ""),
                         ["label missing from the diagram: 'Beta'", "1 edges in the source but 0 connectors drawn"])


class SourceDigestTests(unittest.TestCase):
    def test_check_reports_missing_and_stale_html(self) -> None:
        with tempfile.TemporaryDirectory(dir=diagrams.ROOT) as temporary:
            source = Path(temporary) / "sample.mmd"
            source.write_text(FLOW, encoding="utf-8")
            html = source.with_suffix(".html")
            self.assertEqual(len(diagrams.stale_diagrams([source])), 1)
            html.write_text(f'<meta name="dashmotion-source" content="sha256:{diagrams.source_digest(source)}">',
                            encoding="utf-8")
            self.assertEqual(diagrams.stale_diagrams([source]), [])
            source.write_text(FLOW + "  G --> A\n", encoding="utf-8")
            self.assertEqual(len(diagrams.stale_diagrams([source])), 1)


class RepositoryDiagramTests(unittest.TestCase):
    def test_every_committed_mermaid_source_is_valid(self) -> None:
        sources = sorted((diagrams.ROOT / "diagrams").glob("*.mmd"))
        self.assertTrue(sources)
        for source in sources:
            with self.subTest(source=source.name):
                graph(source.read_text(encoding="utf-8"), source.stem)


if __name__ == "__main__":
    unittest.main()
