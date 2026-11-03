# Diagrams

[← Back to the README](../README.md#documentation)

Animated diagrams of the stack's components and flows. Every diagram has two
files with the same name:

- `NN-name.mmd` — the Mermaid source. **This is the file you edit.**
- `NN-name.html` — the animated [dashmotion](#updating-diagrams) rendering,
  generated from the source by `./scripts/render-diagrams.py`. Do not edit it
  by hand.

The HTML files are self-contained, with inline SVG and no external assets.
GitHub shows their source rather than rendering them, so open them locally in a
browser. Use the ⏯ button to pause the animation. Animation is turned off when
the operating system requests reduced motion.

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

The flow diagrams read from top to bottom. Pills mark start and end states, and
dashed boxes are decisions.

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

## Flows

| Diagram | Source | Shows |
| --- | --- | --- |
| [Chat completion request](10-chat-request-flow.html) | [10-chat-request-flow.mmd](10-chat-request-flow.mmd) | Authentication, policy, Presidio masking, routing, provider call, and telemetry for one request |
| [coding-router tier selection](11-coding-router-flow.html) | [11-coding-router-flow.mmd](11-coding-router-flow.mmd) | D3-mini classification, tier mapping, and the `gpt-4o` fallback |
| [Semantic cache](12-semantic-cache-flow.html) | [12-semantic-cache-flow.mmd](12-semantic-cache-flow.mmd) | The development cache opt-in, similarity threshold, hit, and miss paths |
| [Xberg auto-indexing](13-xberg-auto-index-flow.html) | [13-xberg-auto-index-flow.mmd](13-xberg-auto-index-flow.mmd) | Extraction, the background indexing task, and its fail-closed Presidio step |
| [Stack start-up order](14-stack-startup-flow.html) | [14-stack-startup-flow.mmd](14-stack-startup-flow.mmd) | One-shot init jobs and health-check dependencies when a profile starts |
| [Alerting](15-alerting-flow.html) | [15-alerting-flow.mmd](15-alerting-flow.mmd) | Rule evaluation, OpenSearch monitors, and notification routing |
| [Image-security scan](16-image-scan-flow.html) | [16-image-scan-flow.mmd](16-image-scan-flow.mmd) | One scanner run, from the lock to SBOM generation and the release gate |
| [Upgrade and rollback](17-upgrade-rollback-flow.html) | [17-upgrade-rollback-flow.mmd](17-upgrade-rollback-flow.mmd) | Compatibility check, rollback point, regression suite, and both rollback modes |
| [Backup and restore](18-backup-restore-flow.html) | [18-backup-restore-flow.mmd](18-backup-restore-flow.mmd) | Validated backups and the staging-database restore |
| [First deployment](19-first-deployment-flow.html) | [19-first-deployment-flow.mmd](19-first-deployment-flow.mmd) | The getting-started steps, including the TLS and Grafana choices |
| [CI and release](20-ci-release-flow.html) | [20-ci-release-flow.mmd](20-ci-release-flow.mmd) | GitHub Actions and GitLab CI checks, tagged releases, publishing of the private documentation site, and the manual on-prem deployment |

## Updating diagrams

Keep the diagrams in step with the stack. Whenever a change adds, removes,
renames, or reconnects a service, network, profile, data store, external
dependency, or flow step, update every affected `.mmd` file in the same change.
Then regenerate the HTML:

```sh
./scripts/render-diagrams.py                         # all diagrams
./scripts/render-diagrams.py diagrams/03-observability-pipeline.mmd
```

The script converts each Mermaid source into a dashmotion graph, renders the
HTML beside it, and fails if dashmotion's structural check finds overlaps or
broken connectors, or if a node, edge, or label from the source is missing from
the result. It needs the
dashmotion agent skill. If the skill is not installed in
`~/.agents/skills/dashmotion`, set `DASHMOTION_SKILL_DIR` to its directory.
When dashmotion is not available, commit the Mermaid change alone; the HTML is
regenerated later.

List HTML files that are missing or older than their source (Python only):

```sh
./scripts/render-diagrams.py --check
```

The unit tests in `scripts/tests/test_render_diagrams.py` parse every committed
`.mmd` file, so CI rejects a source the renderer cannot read.

### Mermaid conventions

Each source is an ordinary Mermaid `flowchart TD` with a front-matter `title`.
It renders in any Mermaid viewer. The renderer supports this subset:

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
| `%% @tier N: ID ID` | Row of the listed components, `0` at the top; required on every component when there is more than one top-level boundary |
| `%% @journey #rrggbb: ID ID ID` | An animated request path; every consecutive pair must be an edge |

Each tier should belong to one top-level boundary, and a nested boundary should
not share a tier with components outside it. Otherwise the boundary boxes
overlap.
