---
title: Home
hide:
  - navigation
  - toc
---

<div class="docs-hero" markdown>

# LiteLLM stack

A self-hosted LLM gateway on Docker Compose: separate **production** and
**development** LiteLLM proxies behind Traefik, with Presidio PII masking,
PostgreSQL, SearXNG search, sandboxed Xberg document extraction, a remote GPU
host for D3-mini routing and BGE-M3 embeddings, and OpenSearch as the default
platform for logs, metrics, usage, traces, alerting, and audit trails.

[:material-rocket-launch: Deploy the stack](docs/getting-started.md){ .md-button .md-button--primary }
[:material-sitemap: Architecture](ARCHITECTURE.md){ .md-button }
[:material-animation-play: Diagram gallery](diagrams/gallery.md){ .md-button }

</div>

## Where to start

<div class="grid cards" markdown>

-   :material-server-network:{ .lg .middle } **Deploy**

    ---

    Size the hosts, configure `.env` and `STACK_DOMAIN`, deliver TLS
    certificates, and start the `prod` or `dev` profile.

    [:octicons-arrow-right-24: First deployment](docs/getting-started.md) ·
    [Hardware sizing](docs/hardware-sizing.md) ·
    [Remote GPU host](gpu-host/README.md)

-   :material-cog-sync:{ .lg .middle } **Operate**

    ---

    Watch the stack in OpenSearch Dashboards, back up and restore the
    databases, and upgrade LiteLLM with a tested rollback point.

    [:octicons-arrow-right-24: Observability](docs/observability.md) ·
    [Backup and restore](docs/backup-and-restore.md) ·
    [Upgrades and rollback](docs/upgrades-and-rollback.md)

-   :material-shield-lock:{ .lg .middle } **Secure and comply**

    ---

    Edge rate limits and TLS policy, prompt-injection screening, image
    vulnerability gates, SBOMs, licences, and the EU control baseline.

    [:octicons-arrow-right-24: Edge and abuse protection](docs/edge-and-abuse-protection.md) ·
    [Image security](docs/image-security.md) ·
    [Licences](docs/third-party-licences.md)

-   :material-source-branch:{ .lg .middle } **Develop**

    ---

    The development container, the identical GitHub Actions and GitLab CI
    pipelines, releases, and the maintenance rules for every change.

    [:octicons-arrow-right-24: Development workflow](docs/development-workflow.md) ·
    [Maintenance skill](SKILL.md) ·
    [This site](docs/documentation-site.md)

</div>

## The stack at a glance

<div class="diagram-frame diagram-frame--compact">
  <iframe src="diagrams/01-system-context.html" title="System context" loading="lazy" referrerpolicy="no-referrer"></iframe>
</div>

Clients reach the stack only through Traefik over HTTPS. LiteLLM is the single
gateway for models, search, MCP tools, and guardrails; Presidio, SearXNG, Xberg,
and PostgreSQL stay on internal networks, and every container log, metric, and
trace lands in OpenSearch. The [diagram gallery](diagrams/gallery.md) has the
network, observability, data, and request-flow views.

## Runtime profiles

Shared services run without a profile; profiles select the LiteLLM proxies and
the optional modules.

| Profile | Runs | Use |
| --- | --- | --- |
| `prod` | `litellm-prod` with the `litellm_prod` database | Production proxy with the Presidio PII guardrail |
| `dev` | `litellm-dev` with the `litellm_dev` database, Qdrant cache, pgvector store | Development proxy and the dev model plane |
| `grafana` | Grafana, Loki, `alloy-loki` | Optional Grafana dashboards next to OpenSearch |
| `langfuse` | Langfuse web and worker with its own stores | Optional LLM tracing, datasets, and evaluations |
| `security` | Image scanner and a read-only Docker socket proxy | Scheduled image inventory and vulnerability policy |

See [Runtime profiles](ARCHITECTURE.md#runtime-profiles) in the architecture
guide for the full model.

## Ground rules

!!! warning "Before you change the stack"

    - Keep `.env`, the Prometheus master-key file, and everything under
      `traefik/certs/` private; rotate any credential that leaks.
    - Keep OpenSearch as the default observability platform; never disable its
      TLS, certificate verification, or security plugin.
    - Keep image digests immutable and update one image family at a time.
    - Update the Mermaid diagram sources whenever a service, network, profile,
      data store, or flow changes.

    The [stack maintenance skill](SKILL.md) is the full checklist.

## About this site

- **Versions.** Use the selector next to the title to switch between the
  **latest release**, older releases, and **dev**, which follows the default
  branch and may describe unreleased changes.
- **Private.** The site is published from a private repository to GitHub Pages
  or GitLab Pages with access restricted to repository members. Do not copy its
  content to public locations.
- **Source of truth.** Every page except this one, the
  [diagram gallery](diagrams/gallery.md), and the [changelog](changelog.md) is a
  Markdown file in the repository. The edit button on each page opens it.
  [How the site is built](docs/documentation-site.md).
