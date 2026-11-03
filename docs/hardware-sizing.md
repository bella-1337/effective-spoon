# Hardware sizing

[← Back to the README](../README.md#documentation).

Rough planning figures for two environments, each running every optional
profile (`grafana`, `langfuse`, `security`). They assume:

- about 100,000 production requests a day with 30–60 in flight at peak;
- prompts are not stored in spend logs, and completions run at OpenAI;
- one environment per host, because Traefik binds host ports `80`/`443` and
  node-exporter and cAdvisor read the whole host;
- D3-mini and BGE-M3 both run on a remote GPU host, so neither stack host
  needs a GPU.

Validate these figures with a load test before purchasing.

| Host | Runs | CPU | RAM | Disk | GPU |
| --- | --- | --- | --- | --- | --- |
| **Prod** | Shared services, `prod`, all optional profiles | 24 cores / 48 threads (16 / 32 minimum) | 128 GB (96 GB minimum) | 2 × 2 TB NVMe, RAID 1 | None |
| **Dev** | Shared services, `prod` and `dev`, all optional profiles | 12 cores / 24 threads (8 / 16 minimum) | 64 GB (48 GB minimum) | 2 × 1 TB NVMe, RAID 1 | None |
| **Remote GPU** | [`gpu-host/`](../gpu-host/README.md): D3-mini for both environments, BGE-M3 for dev | 16 cores | 64 GB | 1 TB NVMe | 1 × 48 GB (L40S, RTX 6000 Ada, or A6000) shared by both models |

Cloud equivalents: prod `m7i.8xlarge` (32 vCPU, 128 GB), dev `m7i.4xlarge`
(16 vCPU, 64 GB), and `g6e.4xlarge` (one 48 GB L40S, 16 vCPU, 128 GB) for the
GPU host.

## Prod host budget

Planning peaks, not idle use. The core and prod profile idles at about 7 GiB.

| Block | Services | RAM | vCPU |
| --- | --- | --- | --- |
| Edge and data | Traefik, socket proxies, PostgreSQL, vector-postgres, SearXNG, Xberg sandbox | 7 GB | 4 |
| Presidio | Analyzer (3 GB limit) and anonymizer, 4 workers each | 3.5 GB | 4 |
| Metrics and logs | Prometheus, Alloy, exporters, cAdvisor, blackbox, opensearch-bridge | 4 GB | 2 |
| OpenSearch | OpenSearch (4 GB heap, 8 GB limit), Dashboards, Data Prepper | 12 GB | 5 |
| LiteLLM prod | 4 workers × (1 vCPU, 4 GB) | 16 GB | 4 |
| `grafana` | Grafana, Loki, alloy-loki | 3 GB | 1.5 |
| `langfuse` | Web, worker, PostgreSQL, ClickHouse, Redis, MinIO | 11 GB | 4 |
| `security` | Image scanner (bursty) | 2.5 GB | 2 |
| **Total** | | **~60 GB** | **~27** |

Add about 4 GB for the OS and Docker and at least 25 GB of page cache for
OpenSearch, PostgreSQL, and ClickHouse. That makes 96 GB the minimum. At
128 GB, LiteLLM can grow to 8 workers.

| Prod data | Retention | Disk |
| --- | --- | --- |
| OpenSearch (single node; stay under the 85% watermark) | Logs 7 d, metrics and traces 15 d, audit 90 d, usage 180 d | 150–200 GB |
| PostgreSQL spend logs | 180 d | 50–100 GB |
| Langfuse ClickHouse and MinIO | Unbounded by default | 200–400 GB |
| Prometheus and Loki | 10 GB cap, 7 d | 30 GB |
| Images, build cache, container logs | — | 100 GB |
| Local backup staging, if `BACKUP_DIR` is on the host | 30 d | 100 GB |

## Resource limits

Every long-running service has a CPU and memory cap set from `.env`. Most use
one of four tiers (`RESOURCE_TINY_*`, `RESOURCE_SMALL_*`, `RESOURCE_MEDIUM_*`,
`RESOURCE_DATABASE_*`); LiteLLM and services that size a JVM heap or worker
pool have their own variables. The `.env.example` defaults suit the dev host.
On the prod host, set:

| Variable | Dev host (default) | Prod host |
| --- | --- | --- |
| `RESOURCE_TINY_CPU_LIMIT` / `_MEMORY_LIMIT` | `0.5` / `256m` | `0.5` / `256m` |
| `RESOURCE_SMALL_CPU_LIMIT` / `_MEMORY_LIMIT` | `1` / `1g` | `2` / `1g` |
| `RESOURCE_MEDIUM_CPU_LIMIT` / `_MEMORY_LIMIT` | `2` / `2g` | `4` / `4g` |
| `RESOURCE_DATABASE_CPU_LIMIT` / `_MEMORY_LIMIT` | `2` / `4g` | `6` / `10g` |
| `LITELLM_CPU_LIMIT` / `LITELLM_MEMORY_LIMIT` | `2` / `4g` | `4` / `16g` |
| `PRESIDIO_ANALYZER_CPU_LIMIT` / `_MEMORY_LIMIT` | `2` / `2g` | `4` / `3g` |
| `PRESIDIO_ANALYZER_WORKERS` / `PRESIDIO_ANONYMIZER_WORKERS` | `2` / `2` | `4` / `4` |
| `OPENSEARCH_JAVA_HEAP` / `OPENSEARCH_MEMORY_LIMIT` | `1g` / `3g` | `4g` / `8g` |
| `DATA_PREPPER_JAVA_OPTS` / `DATA_PREPPER_MEMORY_LIMIT` | `-Xms256m -Xmx512m` / `2g` | `-Xms1g -Xmx2g` / `3g` |

- **Each cap contains one runaway service.** On prod, all memory caps together
  come to about 112 GB, under the 128 GB host, so even every service at its cap
  cannot exhaust host memory. On the dev host they come to about 62 GB, which is
  nearly the whole 64 GB host.
- **CPU caps are oversubscribed on purpose.** CPU is shared, not lost, so a cap
  only stops one service from taking every core.
- **Database memory caps include the page cache.** A smaller cap costs cache
  hits, not crashes.
- **LiteLLM still runs one worker.** The prod row is sized for 4 workers, which
  need `--num_workers 4`, Redis so the per-key `rpm_limit` and
  `max_parallel_requests` are shared across workers, multi-process Prometheus
  metrics, and `proxy_batch_write_at: 60`. Until then, `2` / `8g` is enough.

The full list of which service uses which tier is in the
[environment variable reference](environment-variables.md#resource-limits).

## Dev host budget

The dev host is the only environment besides prod, so it runs the `prod`
profile with one worker as a pre-production check, alongside `dev`. It uses
the default resource limits.

| Block | RAM | vCPU |
| --- | --- | --- |
| Edge, data, and Presidio (2 workers each) | 7.5 GB | 5 |
| Metrics and logs | 3 GB | 1.5 |
| OpenSearch and Data Prepper defaults | 6 GB | 3 |
| LiteLLM prod and dev, 1 worker each | 8 GB | 2 |
| Qdrant (2 GB limit plus 1 GB `tmpfs`) and litellm-pgvector | 3.5 GB | 1.5 |
| `grafana`, `langfuse`, `security` | 11.5 GB | 5 |
| **Total** | **~40 GB** | **~18** |

BGE-M3 runs on the remote GPU host, so the dev host needs no GPU.

## Remote GPU host

One 48 GB GPU serves both models through the separate
[`gpu-host/`](../gpu-host/README.md) Compose project:

| Model | Engine | Endpoint | GPU memory (typical / worst) |
| --- | --- | --- | --- |
| D3-mini | vLLM Semantic Router model runtime | HTTPS on 8443, prod and dev | ~12 / ~18 GiB at the 8,192-token input cap |
| BGE-M3 | Hugging Face Text Embeddings Inference | HTTPS on 9443, dev only | ~2–3 / ~4 GiB |

One Nginx proxy terminates TLS for both with a certificate from the host's
dedicated mkcert CA; the LiteLLM hosts trust that CA through
`GPU_HOST_CA_BUNDLE`.

- **About 23 GiB at worst** leaves more than 20 GB free. Neither engine reserves
  memory up front, so no per-process fractions are needed.
- **D3-mini is on prod's request path.** `coding-router` calls it on every
  request; if it fails or takes longer than 15 seconds, routing falls back to
  `gpt-4o`. Keep the host on the same site network and firewalled to both
  LiteLLM hosts.
- **BGE-M3** has its own API key, which guards every compute route. Keep its
  port firewalled to the dev host. Promoting the semantic cache or RAG to prod
  adds prod load to this GPU.
- **Dev and prod share D3-mini.** A dev load test can slow prod routing. With
  the headroom on a 48 GB card, a second D3-mini instance for dev fits too.
