# Diagrams

[← Back to the README](../README.md#documentation)

Animated diagrams of the stack's components and flows. Each diagram is one
Mermaid source, `NN-name.mmd`, committed in this directory. **The source is
the only form in the repository and the file you edit.**

The animated [dashmotion](#updating-diagrams) rendering, `NN-name.html`, is
generated from the source and never committed. CI renders it when it builds
the [documentation site](../docs/documentation-site.md), so the links in the
tables below open the animations on the site. In a checkout,
`./scripts/render-diagrams.py` writes them beside the sources, where Git
ignores them.

The HTML files are self-contained, with inline SVG and no external assets.
Use the ⏯ button to pause the animation. Animation is turned off when the
operating system requests reduced motion.

## Conventions

In the architecture diagrams:

- **Clients** (cyan, top) and **external services** (slate, bottom) are drawn
  inside their own amber boundaries, outside the
  **LiteLLM stack — Docker Compose host** boundary.
- Rose dashed boundaries mark networks or optional Compose profiles inside the
  host.
- Solid animated connectors are synchronous requests. Dotted orange connectors
  are asynchronous telemetry or background work. A plain line without an arrow
  is a denied or static relationship.
- Moving dots follow an end-to-end request path.

The flows are sequence diagrams. Each participant has a lifeline, and time
runs from top to bottom. Messages are coloured by kind: cyan requests, green
dotted responses or results, amber dotted asynchronous messages, and rose
dashed rejections or failures ending in a cross. Amber notes add context.
Labelled frames mark alternatives (`alt`/`else`), optional steps (`opt`),
loops, and parallel work (`par`/`and`). A dot replays the messages in order.

## Architecture

| Diagram | Source | Shows |
| --- | --- | --- |
| [System context](01-system-context.html) | [01-system-context.mmd](01-system-context.mmd) | Clients, the services inside the stack, and the external providers, search engines, and websites it calls |
| [Network boundaries](02-network-boundaries.html) | [02-network-boundaries.mmd](02-network-boundaries.mmd) | The `edge`, `internal`, `xberg-sandbox`, `egress`, and Docker-monitor networks, and what each one can reach |
| [Observability pipeline](03-observability-pipeline.html) | [03-observability-pipeline.mmd](03-observability-pipeline.mmd) | How logs, metrics, traces, and LiteLLM usage reach OpenSearch, and how alerts leave the stack |
| [Langfuse profile](04-langfuse-profile.html) | [04-langfuse-profile.mmd](04-langfuse-profile.mmd) | The optional Langfuse services and the `langfuse_otel` trace path |
| [Grafana profile](05-grafana-profile.html) | [05-grafana-profile.mmd](05-grafana-profile.mmd) | The optional Grafana, Loki, and `alloy-loki` services and their read-only data sources |
| [Xberg MCP sandbox](06-xberg-mcp-sandbox.html) | [06-xberg-mcp-sandbox.mmd](06-xberg-mcp-sandbox.mmd) | MCP extraction through LiteLLM, the sandbox network, and allowlisted egress |
| [Development model plane](07-dev-model-plane.html) | [07-dev-model-plane.mmd](07-dev-model-plane.mmd) | The dev-only Qdrant cache, remote BGE-M3 embeddings, and pgvector knowledge store |
| [Image-security monitor](08-image-security.html) | [08-image-security.mmd](08-image-security.mmd) | Trivy and CISA KEV scanning, private evidence, and the alert path |
| [Data layer and backups](09-data-layer.html) | [09-data-layer.mmd](09-data-layer.mmd) | Database owners, read-only readers, and the backup path to off-host storage |
| [Production profile](21-prod-profile.html) | [21-prod-profile.mmd](21-prod-profile.mmd) | Everything the `prod` profile runs, with its clients and external providers |
| [Development profile](22-dev-profile.html) | [22-dev-profile.mmd](22-dev-profile.mmd) | Everything the `dev` profile adds, including the local model services |
| [Coding request path](23-coding-request-path.html) | [23-coding-request-path.mmd](23-coding-request-path.mmd) | The components a `coding-router` request passes through |

## Flows (sequence diagrams)

| Diagram | Source | Shows |
| --- | --- | --- |
| [Chat completion request](10-chat-request-flow.html) | [10-chat-request-flow.mmd](10-chat-request-flow.mmd) | Authentication, policy, Presidio masking, routing, provider call, and telemetry for one request |
| [coding-router tier selection](11-coding-router-flow.html) | [11-coding-router-flow.mmd](11-coding-router-flow.mmd) | D3-mini classification, tier mapping, and the `gpt-4o` fallback |
| [Semantic cache](12-semantic-cache-flow.html) | [12-semantic-cache-flow.mmd](12-semantic-cache-flow.mmd) | The development cache opt-in, similarity threshold, hit, and miss paths |
| [Xberg auto-indexing](13-xberg-auto-index-flow.html) | [13-xberg-auto-index-flow.mmd](13-xberg-auto-index-flow.mmd) | Extraction, the background indexing task, and its fail-closed Presidio step |
| [Prompt that triggers an Xberg tool call](24-xberg-tool-call-flow.html) | [24-xberg-tool-call-flow.mmd](24-xberg-tool-call-flow.mmd) | From the user's prompt: the app offers the xberg tools, the model decides to call `extract`, and LiteLLM runs it through the egress allowlist and asks the model again |
| [Prompt that triggers a search tool call](25-search-tool-call-flow.html) | [25-search-tool-call-flow.mmd](25-search-tool-call-flow.mmd) | From the user's prompt: the model decides to search, the agent runs `/v1/search/searxng-search`, and the model answers from the results |
| [Stack start-up order](14-stack-startup-flow.html) | [14-stack-startup-flow.mmd](14-stack-startup-flow.mmd) | One-shot init jobs and health-check dependencies when a profile starts |
| [Alerting](15-alerting-flow.html) | [15-alerting-flow.mmd](15-alerting-flow.mmd) | Rule evaluation, OpenSearch monitors, and notification routing |
| [Image-security scan](16-image-scan-flow.html) | [16-image-scan-flow.mmd](16-image-scan-flow.mmd) | One scanner run, from the lock to SBOM generation and the release gate |
| [Upgrade and rollback](17-upgrade-rollback-flow.html) | [17-upgrade-rollback-flow.mmd](17-upgrade-rollback-flow.mmd) | Compatibility check, rollback point, regression suite, and both rollback modes |
| [Backup and restore](18-backup-restore-flow.html) | [18-backup-restore-flow.mmd](18-backup-restore-flow.mmd) | Validated backups and the staging-database restore |
| [First deployment](19-first-deployment-flow.html) | [19-first-deployment-flow.mmd](19-first-deployment-flow.mmd) | The getting-started steps, including the TLS and Grafana choices |
| [CI and release](20-ci-release-flow.html) | [20-ci-release-flow.mmd](20-ci-release-flow.mmd) | GitHub Actions and GitLab CI checks, tagged releases, publishing of the documentation site, and the manual on-prem deployment |

## Updating diagrams

Keep the diagrams in step with the stack. Whenever a change adds, removes,
renames, or reconnects a service, network, profile, data store, external
dependency, or flow step, update every affected `.mmd` file in the same change
and commit only the `.mmd` files. To preview the result, render it locally:

```sh
./scripts/render-diagrams.py                         # all diagrams
./scripts/render-diagrams.py diagrams/03-observability-pipeline.mmd
./scripts/build-docs.py serve                        # or preview them in the site
```

The script converts each Mermaid source into a dashmotion graph, renders the
HTML beside it, and fails if dashmotion's structural check finds overlaps or
broken connectors, or if a node, edge, or label from the source is missing from
the result. It needs [Graphviz](https://graphviz.org/) (`dot`). The dashmotion
renderer (MIT, [csthink/dashmotion](https://github.com/csthink/dashmotion)) is
downloaded once into `~/.cache/litellm-stack/` at the commit pinned in
`scripts/render-diagrams.py` and checked against its SHA-256 digests, so every
machine renders with the same code. Offline, set `DASHMOTION_SKILL_DIR` to a
dashmotion skill directory instead.

The documentation build in CI (`scripts/ci/docs.sh`, on every pull request,
merge request, and default-branch pipeline) renders every diagram the same
way, so a source that does not render, or fails the structural check, fails the
pipeline. Release versions of the site are rendered from their own tag with
that tag's renderer.

Architecture diagrams are laid out by Graphviz `dot` through
`scripts/diagram_layout.py`. `dot` is the layered engine Mermaid's own layout
is modelled on: like Mermaid, it derives the rows from the edges, keeps each
subgraph together, orders each row to reduce crossings, and routes connectors
as curves around the nodes, so they never trace the dashed boundary
rectangles. The `.mmd` file is therefore the complete layout input, and a
diagram has the same structure here as in any Mermaid viewer. dashmotion still
sizes the nodes and draws the result.

The unit tests in `scripts/tests/test_render_diagrams.py` also parse every
committed `.mmd` file, so the validation job rejects a source the renderer
cannot read without needing Graphviz.

### Mermaid conventions

Each source is an ordinary Mermaid diagram with a front-matter `title`, so it
renders in any Mermaid viewer: a `flowchart TD` for the architecture diagrams
and a `sequenceDiagram` for the flows. dashmotion has no sequence mode, so
`scripts/sequence_diagrams.py` lays the sequences out in the same visual style;
dashmotion's structural checker still checks the result.

The renderer supports this flowchart subset:

| Element | Syntax |
| --- | --- |
| Step or component | `ID["Label<br/>second line"]` — in architecture diagrams the second line becomes the sublabel |
| Start or end | `ID(["LABEL"])` |
| Decision | `ID{"Question?"}` |
| Data store | `ID[("Label")]` (also sets the `database` type) |
| Request | `A --> B` or `A -->|"label"| B` |
| Main path | `A ==> B` |
| Asynchronous telemetry or background work | `A -.-> B` |
| Static or denied relationship | `A --- B` |
| Boundary | `subgraph ID["Label"]` … `end`; top-level boundaries are regions, nested ones are networks or profiles |
| Component type | `:::frontend`, `:::backend`, `:::database`, `:::cloud`, `:::security`, `:::bus`, or `:::external` |

Use `frontend` for clients and `external` for services outside the stack, and
keep them in the **Clients — outside the stack** and **External services —
outside the stack** boundaries.

Information that Mermaid has no syntax for is written as `%% @` comments, which
Mermaid ignores:

| Directive | Meaning |
| --- | --- |
| `%% @mode architecture` | Render as an architecture diagram; the default is a flow |
| `%% @subtitle text` | Line under the title |
| `%% @card cyan\|violet\|rose \| title \| item \| item` | One summary card; architecture diagrams need exactly three |
| `%% @journey #rrggbb: ID ID ID` | An animated request path; every consecutive pair must be an edge |

There is no layout directive: to move a component, change the edges or the
subgraph it belongs to, as you would for Mermaid.

#### Sequence diagrams

The renderer supports this `sequenceDiagram` subset:

| Element | Syntax |
| --- | --- |
| Participant | `participant ID as Label<br/>second line`; the second line becomes the sublabel. Participants are drawn in declaration order |
| Person or client | `actor ID as Label`, drawn as a participant of type `frontend` |
| Request | `A->>B: text` or `A->B: text` |
| Response or result | `A-->>B: text` or `A-->B: text` |
| Asynchronous message | `A-)B: text` or `A--)B: text` |
| Rejection or failure | `A-xB: text` or `A--xB: text` |
| Step inside one participant | `A->>A: text` |
| Note | `Note over A: text`, `Note over A,B: text`, `Note left of A: text`, `Note right of A: text` |
| Frames | `alt` … `else` … `end`, `opt`, `loop`, `par` … `and` … `end`, `critical` … `option` … `end`, `break` |
| Numbered messages | `autonumber` |

Activation markers (`+`/`-` and `activate`/`deactivate`) are accepted and not
drawn. Keep message labels short; they wrap at 24 characters. The directives
`%% @subtitle`, `%% @footer`, and this one apply:

| Directive | Meaning |
| --- | --- |
| `%% @type frontend\|backend\|database\|cloud\|security\|bus\|external: ID ID` | Colour of the listed participants; the default is `backend`, and `frontend` for an `actor` |
