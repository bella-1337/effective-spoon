# Reference stack: BerriAI Inference Stack

[← Back to the README](../README.md#documentation)

[BerriAI Inference Stack](https://github.com/BerriAI/inference-stack) is the
LiteLLM maintainers' own deployment of LiteLLM with self-hosted models (MIT
licence). Before you implement a feature request, compare it with this
repository as described in [Feature requests](#feature-requests). It shows how
LiteLLM's maintainers wire the same components, so its approach is a useful
first option. It is not a requirement: this stack has constraints the reference
does not have, above all the region lock.

## Feature requests

Follow these steps for every feature request, whether a person or an agent does
the work:

1. **Check the reference.** Look at the latest
   [Inference Stack release](https://github.com/BerriAI/inference-stack/releases)
   for the same capability. Check its LiteLLM configuration
   (`compose/litellm/config.yaml`), its callbacks
   (`charts/inference-stack/files/`), the launcher (`launcher/`), and the
   Compose and Helm files. Note the release or commit you checked.
2. **Check it against this stack.** Compare the reference approach with the
   constraints in [SKILL.md](../SKILL.md#guardrails), especially the region
   lock, the separate `prod` and `dev` profiles, digest-pinned images, HTTPS
   through Traefik, and the security baseline. The
   [comparison](#comparison) below lists differences that often decide this.
3. **Ask before implementing.** Give the requester the reference's approach
   (or say that the reference has none), any alternative that fits this stack
   better, the trade-offs, and a recommendation. Then ask which approach to
   implement. Start implementing only after they choose.
4. **Record the decision.** Say in the change description and, where it
   applies, in the changelog which approach was chosen and why, including
   when the reference approach was not used.

## Comparison

Compared with Inference Stack v0.1.2 (commit `523542c`, 21 November 2026).
Update this section when a later release changes a row.

| Area | This stack (Sovereign LLM Gateway) | Inference Stack |
| --- | --- | --- |
| Purpose | A region-locked gateway in front of cloud models, plus self-hosted classification and embedding models | Running Hugging Face models on your own GPUs with vLLM |
| Model source | Bedrock, Azure OpenAI, and Vertex AI models from `.env`, locked to `DATA_SOVEREIGNTY_REGION`. D3-mini and BGE-M3 on a separate GPU host, or D3-mini on the stack host with the optional `d3` profile (plain vLLM, declared statically in Compose). | vLLM servers that the launcher deploys from the Hugging Face catalogue |
| Model registration | Static config and environment, built at startup by `prepare_runtime_config.py`, which refuses to start out of region | Registered at runtime through LiteLLM's `/model/new` API (`store_model_in_db: true`) by the launcher or a Helm hook job |
| Routing | LiteLLM's complexity Auto Router (`coding-router`) chooses a model for each request | llm-d's endpoint picker, behind Envoy, chooses a vLLM replica of one model from queue depth, KV-cache use, and prefix-cache hits |
| Team fairness | Not implemented | A LiteLLM callback sends the team ID to llm-d (`x-llm-d-inference-fairness-id`) so capacity is shared between teams |
| Deployment | Docker Compose with `prod`, `dev`, and optional profiles | Docker Compose with one model at a time, or Kubernetes with Helm |
| Network edge | Traefik with HTTPS and named hosts | Ports bound to `127.0.0.1` over plain HTTP; HTTPS left to your own reverse proxy |
| LiteLLM image | Pinned by digest; `check-litellm-upgrade-compatibility.py` tests each upgrade | `main-stable` tag, which is not pinned |
| Data protection | Presidio policy, spend logs without prompts, message logging off | None configured |
| Observability | OpenSearch by default, plus Prometheus; optional Grafana and Loki, Langfuse, and Lens | Prometheus and Grafana with the LiteLLM and llm-d upstream dashboards |
| Tools | SearXNG search, Xberg MCP extraction, Qdrant semantic cache in development, pgvector | None |
| Admin interface | LiteLLM admin UI | A FastAPI launcher that searches Hugging Face, estimates GPU memory, deploys and removes models, and tunes llm-d; it controls the Docker socket or the Kubernetes namespace |
| Secrets | Required values in `.env`; the gateway refuses to start without them | The installer generates `.env` values, and PostgreSQL refuses short or example passwords |
| Releases | Tagged source bundle with `SHA256SUMS` | `install.sh`, which checks the release checksum before installing |
| Development models without GPUs | Mock `test-model` | llm-d inference simulator with canned responses |

### Worth considering here

These ideas from the reference fit this stack, but each still needs a decision
through the [feature request steps](#feature-requests):

- llm-d replica routing on the GPU host, if D3-mini, BGE-M3, or a development
  vLLM model ever runs more than one replica.
- The team fairness callback, if self-hosted capacity is shared between teams.
- Refusing short or example passwords when a container starts, not only empty
  ones.
- A Helm chart, if the stack must run on Kubernetes.

### Conflicts with this stack

Do not adopt these reference choices without an explicit decision, because
they would weaken a guarantee this stack makes:

- **Registering models at runtime through the API or database.** A model added
  this way skips the startup region check. This stack still allows it today,
  because both configs set `store_model_in_db: true`; see
  [Data sovereignty](data-sovereignty.md#what-this-does-not-cover).
- **An unpinned LiteLLM tag** such as `main-stable`.
- **An unauthenticated metrics endpoint** (`require_auth_for_metrics_endpoint:
  false`) or anonymous Grafana viewers.
- **Giving a web application the Docker socket.**
- **Plain HTTP** for anything other than a single private workstation.
