from __future__ import annotations

import importlib.util
import io
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import diagram_controls  # noqa: E402
import diagram_layout  # noqa: E402
import mermaid_viewer  # noqa: E402
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

    def test_semicolon_outside_a_comment_is_rejected(self) -> None:
        source = "---\ntitle: T\n---\nsequenceDiagram\n  %% @subtitle one; two\n  A->>B: one; two\n"
        with self.assertRaisesRegex(diagrams.DiagramError, "end of a statement"):
            diagrams.parse_graph(source, "semicolon")

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


class MermaidDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bodies = {name: f"// {name}\n".encode() for name in mermaid_viewer.MERMAID_FILES}
        digests = {name: mermaid_viewer.hashlib.sha256(body).hexdigest() for name, body in self.bodies.items()}
        patcher = mock.patch.dict(mermaid_viewer.MERMAID_FILES, digests)
        patcher.start()
        self.addCleanup(patcher.stop)

    def serve(self, bodies: dict[str, bytes]) -> mock.MagicMock:
        def urlopen(url: str, timeout: float) -> io.BytesIO:
            self.assertTrue(url.startswith(mermaid_viewer.MERMAID_URL))
            return io.BytesIO(bodies[url.removeprefix(mermaid_viewer.MERMAID_URL)])
        return mock.patch.object(mermaid_viewer.urllib.request, "urlopen", side_effect=urlopen)

    def test_downloads_once_and_installs_the_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self.serve(self.bodies) as urlopen:
            cache = mermaid_viewer.fetch_mermaid(Path(temporary) / "cache")
            mermaid_viewer.fetch_mermaid(cache)
            self.assertEqual(urlopen.call_count, len(self.bodies))
            assets = mermaid_viewer.install_assets(Path(temporary), cache)
            self.assertEqual((assets / "mermaid.min.js").read_bytes(), self.bodies["dist/mermaid.min.js"])
            self.assertEqual((assets / "mermaid-LICENSE.txt").read_bytes(), self.bodies["LICENSE"])
            self.assertIn("mermaid.run", (assets / mermaid_viewer.VIEWER_SCRIPT_NAME).read_text(encoding="utf-8"))
            self.assertEqual((assets / diagram_controls.SCRIPT_NAME).read_text(encoding="utf-8"),
                             diagram_controls.CONTROLS_SCRIPT)

    def test_refuses_a_download_that_does_not_match_its_digest(self) -> None:
        tampered = {**self.bodies, "dist/mermaid.min.js": b"alert(1)\n"}
        with tempfile.TemporaryDirectory() as temporary, self.serve(tampered):
            cache = Path(temporary) / "cache"
            with self.assertRaisesRegex(ScriptError, "does not match its pinned SHA-256"):
                mermaid_viewer.fetch_mermaid(cache)
            self.assertFalse((cache / "dist_mermaid.min.js").exists())

    def test_pinned_version_and_digests_are_well_formed(self) -> None:
        self.assertRegex(mermaid_viewer.MERMAID_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertIn(f"@{mermaid_viewer.MERMAID_VERSION}/", mermaid_viewer.MERMAID_URL)
        for digest in mermaid_viewer.MERMAID_FILES.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(set(mermaid_viewer.PUBLISHED_NAMES), set(mermaid_viewer.MERMAID_FILES))


class MermaidViewerTests(unittest.TestCase):
    def test_page_escapes_the_source_and_links_its_siblings(self) -> None:
        source = FLOW + '  A --> X["</pre><script>alert(1)</script>"]\n'
        page = mermaid_viewer.viewer_html("12-sample.mmd", source, "Sample <flow>", "A & B")
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;/pre&gt;&lt;script&gt;", page)
        self.assertIn("<title>Sample &lt;flow&gt; — Mermaid</title>", page)
        self.assertIn('<p class="lead">A &amp; B</p>', page)
        self.assertIn('<a href="12-sample.html">Animated version</a>', page)
        self.assertIn('<a href="12-sample.mmd" download>', page)
        self.assertEqual(page.count('<pre class="mermaid">'), 1)
        scripts = [line for line in page.splitlines() if "<script" in line]
        self.assertEqual(scripts, ['<script src="assets/mermaid.min.js"></script>',
                                   '<script src="assets/diagram-controls.js"></script>',
                                   '<script src="assets/mermaid-viewer.js"></script>'])
        self.assertIn("script-src 'self';", page)
        self.assertIn("img-src 'self' data: blob:;", page)
        self.assertIn('<figure class="dc" data-dc-name="12-sample"', page)
        self.assertIn('<div class="dc-viewport"><pre class="mermaid">', page)
        self.assertIn('data-dc="png"', page)

    def test_writes_the_page_beside_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "12-sample.mmd"
            source.write_text(FLOW, encoding="utf-8")
            page = mermaid_viewer.write_viewer(source, "Sample flow")
            self.assertEqual(page.name, "12-sample.mmd.html")
            self.assertIn("Do work", page.read_text(encoding="utf-8"))

    def test_assets_are_ignored_and_published(self) -> None:
        root = diagrams.ROOT
        self.assertIn("/diagrams/assets/", (root / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertIn("!/diagrams/assets/**", (root / "mkdocs.yml").read_text(encoding="utf-8"))

    def test_guide_links_every_source_to_its_viewer(self) -> None:
        guide = (diagrams.ROOT / "diagrams" / "README.md").read_text(encoding="utf-8")
        for source in sorted((diagrams.ROOT / "diagrams").glob("*.mmd")):
            with self.subTest(source=source.name):
                self.assertIn(f"[{source.name}]({source.name}.html)", guide)
        self.assertNotRegex(guide, r"\]\([^)]*\.mmd\)")


ARCH_PAGE = """<html><head><style>.flow { stroke-dasharray: 5 5; }</style></head><body>
<div class="container">
  <div class="diagram-card">
    <button class="pause-btn" id="pauseBtn" aria-pressed="false">⏸ pause</button>
    <svg id="flowsvg" width="100%" viewBox="0 0 400 300" role="img" aria-roledescription="architecture diagram"><g fill="none"><path d="M0 0 L10 10"/></g><g font-family="Mono, 'SF Mono'" font-size="11"><rect x="34" y="270" width="12" height="12" rx="3"/><text x="52" y="280">frontend</text></g></svg>
  </div>
</div>
<script>/* pause */</script>
</body>
</html>
"""


class DiagramControlsTests(unittest.TestCase):
    def test_architecture_legend_moves_out_of_the_zoomed_diagram(self) -> None:
        page = diagram_controls.enhance_animated(ARCH_PAGE, "01-sample.mmd")
        diagram = re.search(r'<svg id="flowsvg".*?</svg>', page, re.DOTALL).group(0)
        self.assertIn('viewBox="0 0 400 266" data-dc-viewbox="0 0 400 300"', diagram)
        self.assertNotIn("frontend", diagram)
        self.assertIn(f'<div class="dc-viewport">{diagram}</div><svg class="dc-legend" viewBox="0 266 400 24"', page)
        self.assertIn(">frontend</text></g></svg>", page)

    def test_toolbar_pause_button_style_and_script_are_added(self) -> None:
        page = diagram_controls.enhance_animated(ARCH_PAGE, "01-sample.mmd")
        self.assertIn('<div class="diagram-card dc" data-dc-name="01-sample" data-dc-style="page" data-dc-auto>', page)
        self.assertRegex(page, r'<div class="dc-bar"><div class="dc-toolbar"[^>]*hidden>.*</div><button class="pause-btn"')
        self.assertEqual(page.count('id="pauseBtn"'), 1)
        self.assertIn('<a class="dc-btn" href="01-sample.mmd" download', page)
        self.assertIn(".dc-viewport.dc-zoomed", page.split("</style>")[0])
        self.assertTrue(page.rstrip().endswith(f"<script>\n{diagram_controls.CONTROLS_SCRIPT}</script>\n</body>\n</html>"))

    def test_sequence_page_keeps_its_html_legend_outside_the_zoom_area(self) -> None:
        content = diagrams.sequence_diagrams.render_html(graph(SEQUENCE))
        page = diagram_controls.enhance_animated(content, "10-sample.mmd")
        self.assertNotIn('class="dc-legend"', page.split("<script>\n")[0])
        viewport = page.index('<div class="dc-viewport"><svg id="flowsvg"')
        self.assertGreater(page.index('<div class="legend">'), page.index("</svg></div>", viewport))
        self.assertNotIn("data-dc-viewbox", page)

    def test_unexpected_page_structure_is_refused(self) -> None:
        with self.assertRaisesRegex(ScriptError, "unexpected page structure"):
            diagram_controls.enhance_animated(ARCH_PAGE.replace('id="pauseBtn"', ""), "01-sample.mmd")

    def test_toolbar_escapes_the_source_name(self) -> None:
        self.assertIn('href="a&quot;b.mmd"', diagram_controls.toolbar_html('a"b.mmd'))


class RepositoryDiagramTests(unittest.TestCase):
    def test_every_committed_mermaid_source_is_valid(self) -> None:
        sources = sorted((diagrams.ROOT / "diagrams").glob("*.mmd"))
        self.assertTrue(sources)
        for source in sources:
            with self.subTest(source=source.name):
                graph(source.read_text(encoding="utf-8"), source.stem)


if __name__ == "__main__":
    unittest.main()
