# On-host D3-mini

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

The optional `d3` profile runs D3-mini, the classifier behind `coding-router`,
on an NVIDIA GPU in the stack host itself instead of on the
[remote GPU host](../gpu-host/README.md). The coding-router prompts it reads
never leave the host. It is sized for a single **NVIDIA RTX PRO 2000 Blackwell**
(16 GB GDDR7, compute capability 12.0), and works on any NVIDIA GPU that the
pinned vLLM image supports and that has enough memory.

The remote GPU host remains the other option, and it still serves BGE-M3 for
the dev profile. Choose one D3-mini location per stack: both gateways use the
same `D3_DECISION_API_BASE`.

## Architecture

The [on-host D3-mini profile diagram](../diagrams/README.md#architecture) shows
these services and their networks.

| Service | Role | Networks | Data |
| --- | --- | --- | --- |
| `d3-mini-preflight` | One-shot check of every `d3` setting; then hands the new model volume to the unprivileged D3 user (UID 10001) | none | `d3-mini-models` (ownership only) |
| `d3-mini-download` | One-shot download of the pinned `vllm-sr/d3-mini` revision. On a failed Hugging Face check it keeps a complete cached copy, so a restart does not depend on the hub | `d3-download` (internet) | `d3-mini-models` |
| `d3-mini` | vLLM's System One endpoint (`/v1/systemone`) on port 8000, offline (`HF_HUB_OFFLINE=1`) | `d3-local` (internal) | `d3-mini-models`, read-only |

- `d3-mini` runs the official `vllm/vllm-openai:v0.33.0` image, the first vLLM
  release that serves Decision 3.0 models, pinned by digest. vLLM requires
  `D3_DECISION_API_KEY` on every `/v1` route.
- `d3-local` is an internal network shared only by `d3-mini`, `litellm-prod`,
  and `litellm-dev`. `d3-mini` is not published on a host port or routed by
  Traefik, and it has no route to the internet. Its routes outside `/v1`, such
  as `/health` and `/metrics`, need no key and are reachable only by the two
  gateways.
- The gateways do not depend on `d3-mini`. Until it is healthy, or when it
  fails, times out (`CODING_ROUTER_CLASSIFIER_TIMEOUT_MS`), or rejects a prompt,
  `coding-router` classifies the request with LiteLLM's local heuristic.
- `d3-mini` and `d3-mini-download` run as UID 10001 with a read-only root
  filesystem and no Linux capabilities. vLLM usage statistics are off.
  Request logging stays off, so prompts are not written to the container log.

## Requirements

- An NVIDIA GPU with at least 16 GB of memory that no other process holds.
- **NVIDIA driver 580 or newer.** The image is built on CUDA 13.0 with CUDA
  forward compatibility turned off, so an older driver cannot run it.
- The [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
  so Docker can pass the GPU to the container.
- About 30 GB of free disk: the image is a 9.3 GiB download, and the model
  weights take 9.1 GB in the `d3-mini-models` volume.
- Host memory for the container's limit (`D3_MEMORY_LIMIT`, 16 GB by default)
  on top of the [stack's own needs](hardware-sizing.md).

Check the driver and the toolkit before you start:

```sh
nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv
docker run --rm --gpus all ubuntu:24.04 nvidia-smi
```

## Set up

1. In `.env`, point both gateways at the on-host service and set a new key:

   ```sh
   D3_DECISION_API_BASE=http://d3-mini:8000
   D3_DECISION_API_KEY=<output of: openssl rand -hex 32>
   ```

   Plain HTTP is accepted only for exactly this address. For any other
   address, the gateways refuse to start unless it uses HTTPS.
2. In `.env.optional`, keep the defaults or adjust the
   [settings](#settings). On a 16 GB card, keep `D3_MAX_INPUT_TOKENS=4096`
   until you have [checked a larger cap on the card](#verify-on-the-card).
3. Check the configuration, then start the profile together with prod:

   ```sh
   docker compose --env-file .env --env-file .env.optional \
     --profile prod --profile d3 config --quiet
   docker compose --env-file .env --env-file .env.optional \
     --profile prod --profile d3 up -d
   ```

   The first start downloads the model and compiles GPU kernels, which can
   take several minutes. Prod serves requests meanwhile and classifies them
   locally.
4. If the gateways were already running with the old address or key, recreate
   them so they read the new values:

   ```sh
   docker compose --env-file .env --env-file .env.optional \
     --profile prod up -d --force-recreate --no-deps litellm-prod
   ```

`d3-mini-preflight` stops the profile with exit code 64 and names the setting
when a value is out of range or `D3_DECISION_API_BASE` is not
`http://d3-mini:8000`.

## Settings

Set these in `.env.optional`; [Environment variables](environment-variables.md#optional-on-host-d3-mini-profile)
has the full reference.

| Variable | Default | Range |
| --- | --- | --- |
| `D3_MAX_INPUT_TOKENS` | `4096` | 512 to 8192 |
| `D3_GPU_MEMORY_UTILIZATION` | `0.90` | 0.50 to 0.95 |
| `D3_GPU_DEVICE_ID` | `0` | One `nvidia-smi` index or GPU UUID |
| `D3_CPU_LIMIT` | `4` | Docker CPU count |
| `D3_MEMORY_LIMIT` | `16g` | Docker size |
| `D3_SHM_SIZE` | `2g` | Docker size |

`D3_MAX_INPUT_TOKENS` is vLLM's `--max-model-len`. LiteLLM sends the whole
prompt, including the system prompt, as D3-mini's input. vLLM rejects a longer
input, and `coding-router` then classifies that request locally. Coding agents
often send system prompts longer than 4,096 tokens, so a lower cap sends more
requests to the heuristic.

## GPU memory on a 16 GB card

vLLM reserves `D3_GPU_MEMORY_UTILIZATION` of the card's memory when it starts.
It then runs a test pass at `D3_MAX_INPUT_TOKENS` and refuses to start if that
input does not fit. A cap that is too large therefore fails at start-up, not
on a production request.

| Part | Estimate |
| --- | --- |
| BF16 weights (9.1 GB) | ~8.5 GiB |
| CUDA context, PyTorch, and kernels | ~1 GiB |
| One 4,096-token input (activations and attention cache) | under 1 GiB |
| vLLM's reservation at `0.90` of about 15.9 GiB | ~14.3 GiB, of which the remainder holds cache and state for concurrent requests |

These figures are estimates, not measurements on this card. `--enforce-eager`
skips CUDA graph capture, which saves memory and start-up time at no cost for
single-pass decisions. If a display or another process uses the card, lower
`D3_GPU_MEMORY_UTILIZATION`: vLLM refuses to start when the requested share is
not free.

## Verify on the card

Nothing in this profile has been tested on an RTX PRO 2000 Blackwell yet. Run
these checks on the card before relying on it, and again after changing the
cap, the image, or the model revision.

1. **Start-up is clean.** The log shows the model loading on CUDA and ends with
   `Application startup complete`, with no `no kernel image is available`,
   CUDA, or Triton compilation errors:

   ```sh
   docker compose --env-file .env --env-file .env.optional --profile d3 logs d3-mini
   docker compose --env-file .env --env-file .env.optional --profile d3 ps d3-mini
   ```

2. **A classification near the cap works, and a longer one is refused.** From
   the prod gateway, which shares `d3-local`, send one input just under the
   cap and one over it. Expect a 200 with a `tier` answer and then a 400:

   ```sh
   docker compose --env-file .env --env-file .env.optional --profile prod exec -T litellm-prod python - <<'PY'
   import json, os, urllib.error, urllib.request
   def ask(words):
       body = {
           "model": "vllm-sr/d3-mini",
           "state": "Refactor this function. " + "token " * words,
           "questions": {"tier": {"type": "choice", "instructions": "How hard is this coding request?",
                                  "criteria": {"simple": "Simple", "complex": "Complex"}}},
       }
       request = urllib.request.Request(
           "http://d3-mini:8000/v1/systemone", data=json.dumps(body).encode(),
           headers={"Authorization": "Bearer " + os.environ["D3_DECISION_API_KEY"],
                    "Content-Type": "application/json"})
       try:
           with urllib.request.urlopen(request, timeout=60) as response:
               answer = json.load(response)
               print(response.status, answer["answers"]["tier"]["choice"], answer["usage"])
       except urllib.error.HTTPError as error:
           print(error.code, "rejected")
   ask(3800)   # just under a 4,096-token cap
   ask(5000)   # over it
   PY
   ```

   Adjust the word counts if you change `D3_MAX_INPUT_TOKENS`; `usage` shows
   the token count D3-mini read.
3. **Peak memory stays inside the card.** Watch memory while you repeat step 2
   a few times in parallel:

   ```sh
   nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv -l 1
   ```

   `memory.used` should stay close to vLLM's reservation and below
   `memory.total`, with no out-of-memory error in the `d3-mini` log.
4. **Latency fits the timeout.** Step 2's near-cap request should answer well
   within `CODING_ROUTER_CLASSIFIER_TIMEOUT_MS` (3 seconds by default).
   Otherwise every long prompt falls back to the heuristic.
5. **Routing uses D3-mini.** Send a short `coding-router` request through the
   prod gateway, then check that the `d3-mini` log shows a matching
   `POST /v1/systemone` access line with status 200. The access log records
   the route and status only, not the prompt.

## Operate

| Task | Command or action |
| --- | --- |
| Logs | OpenSearch Discover `log.attributes.compose_service:"d3-mini"`; `docker compose --profile d3 logs --tail=100 d3-mini` |
| Change a setting | Edit `.env.optional`, then `docker compose --env-file .env --env-file .env.optional --profile d3 up -d d3-mini`. The preflight checks the new value first. |
| Stop D3-mini | `docker compose --profile d3 stop d3-mini`. `coding-router` classifies locally until it is back. |
| Rotate the key | Set a new `D3_DECISION_API_KEY` in `.env`, recreate `d3-mini`, then recreate both gateways. Requests between the two steps fall back to the heuristic. |
| Move back to the remote GPU host | Set `D3_DECISION_API_BASE` and `D3_DECISION_API_KEY` to the remote values, stop the `d3` profile, and recreate both gateways. Remove the `d3-mini-models` volume yourself if you no longer need the cached weights. |
| Upgrade vLLM or the model | Change the image digest or `x-d3-mini-revision` in `compose.optional.yml`, keep the revision in step with `gpu-host/deploy.py`, update [component versions](component-versions.md) and the [licence register](third-party-licences.md), and repeat [Verify on the card](#verify-on-the-card). |

## Why vLLM instead of the Semantic Router runtime

The remote GPU host runs D3-mini on the vLLM Semantic Router model runtime.
The `d3` profile uses plain vLLM instead, the way the
[BerriAI Inference Stack](reference-inference-stack.md) runs every model:

- vLLM reserves its memory and checks the input cap when it starts, so an
  unsuitable cap fails at start-up. The Semantic Router runtime allocates
  memory on demand, so the same problem shows up as an out-of-memory error on
  a request.
- `--max-model-len` is a hard limit on the input.
- The official image is a mainstream release, pinned by digest, with CUDA 13.0
  kernels for compute capability 12.0.

vLLM's `/v1/systemone` takes the same request and returns the same answer
fields (`type`, `choice`, `probabilities`, `confidence`, `usage`) that
LiteLLM's `coding-router` client reads. Unlike the reference, the stack
declares the service statically in Compose rather than starting containers
through a launcher that holds the Docker socket.

## Not verified

- No run on an RTX PRO 2000 Blackwell or another compute capability 12.0 card:
  start-up, Triton kernels, peak memory, and latency are unmeasured.
- vLLM's decisions matched the original checkpoint in the upstream recipe on
  an AMD MI300X only, not on NVIDIA GPUs.
- That vLLM 0.33.0 accepts the `decision_config.json` of the pinned revision
  (`v3.1.0`). If it does not, `d3-mini` fails at start-up and routing falls back
  to the heuristic.
