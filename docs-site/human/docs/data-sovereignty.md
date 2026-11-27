<!-- human-version-of: docs/data-sovereignty.md sha256: f84d1bb6722202a5c5a555e01fe82a660bf5547b7653d770ce5b773f369aacc9 -->

# Data sovereignty and regional models

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

This page shows how to lock the gateways to one data-sovereignty region and
choose the cloud models they serve from it. Read it when you configure
provider models, add a region, or need to check where prompts are processed.

In short:

- The stack is a region-locked AI gateway. You choose one data-sovereignty
  region, then the models to serve from it.
- Prompts and responses are then processed only by provider locations inside
  that region.
- Everything else in the stack (LiteLLM, PostgreSQL, OpenSearch, Presidio, and
  the GPU host) runs on infrastructure you operate.

## How it works

1. `DATA_SOVEREIGNTY_REGION` in `.env` names the region. It is required.
2. Each cloud provider has its own optional model list in `.env`. No model is
   required. If every list is empty, the gateways start with no cloud models
   and serve only the self-hosted ones (`d3-mini`, plus `bge-m3` and the mock
   `test-model` in development).
3. Both gateways start through `litellm/modules/prepare_runtime_config.py`. It
   calls `litellm/modules/sovereign_models.py`, which turns the lists into
   LiteLLM model entries and adds them to the static model list in
   `litellm/config.prod.yaml` and `litellm/config.dev.yaml`.
4. The launcher refuses to start the gateway, and logs the reason, if:
    - a configured provider has a missing credential, or
    - its location could process a prompt outside the region.

    It never writes secret values into the runtime config, only `os.environ/`
    references.

The startup log names the region and every model it wired:

```text
Data sovereignty region: eu; provider models: claude-sonnet-5-5, gpt-5.5, gemini-3.5-flash
```

After changing any variable below, recreate both gateways:

```sh
docker compose up -d --force-recreate litellm-prod litellm-dev
```

## Regions and allowed locations

The region table lives in `REGION_POLICIES` in
`litellm/modules/sovereign_models.py`. To add a region, add one entry there,
with locations confirmed from each provider's residency documentation.

| Region | Bedrock profile and source Regions | Azure OpenAI resource regions | Vertex AI locations |
| --- | --- | --- | --- |
| `eu`: EU member states | `eu.` profile from `eu-central-1`, `eu-north-1`, `eu-south-1`, `eu-south-2`, `eu-west-1`, or `eu-west-3` | `francecentral`, `germanywestcentral`, `italynorth`, `northeurope`, `polandcentral`, `spaincentral`, `swedencentral`, `westeurope` | `eu` multi-region, or `europe-central2`, `europe-north1`, `europe-north2`, `europe-southwest1`, `europe-west1`, `europe-west3`, `europe-west4`, `europe-west8`, `europe-west9`, `europe-west10`, `europe-west12` |
| `us`: United States | `us.` profile from `us-east-1`, `us-east-2`, `us-west-1`, or `us-west-2` | `centralus`, `eastus`, `eastus2`, `northcentralus`, `southcentralus`, `westus`, `westus2`, `westus3` | `us` multi-region, or `us-central1`, `us-east1`, `us-east4`, `us-east5`, `us-south1`, `us-west1` to `us-west4` |

Locations are left out when they would leave the region. For example:

- The Bedrock `eu.` profile called from `eu-west-2` (London) or `eu-central-2`
  (Zurich) can route to those non-EU Regions.
- A `global.` Bedrock profile or the Vertex AI `global` endpoint can route
  anywhere.
- An Azure `GlobalStandard` deployment can process anywhere.

## Configure the providers

Rules for every provider's model list:

- Every list is comma-separated.
- By default, the public model name clients use is derived from the ID. Write
  `name=ID` to choose a different name.
- Names must be unique across providers and must not be `d3-mini`, `bge-m3`,
  `test-model`, or `coding-router`.

### Amazon Bedrock

```sh
BEDROCK_MODELS=anthropic.claude-opus-5-5,anthropic.claude-sonnet-5-5,anthropic.claude-haiku-5-5
BEDROCK_AWS_REGION=eu-central-1
BEDROCK_ACCESS_KEY_ID=...
BEDROCK_SECRET_ACCESS_KEY=...
```

Give model IDs without a routing prefix. The launcher adds the region's
geographic inference profile. For example, `anthropic.claude-sonnet-5-5`
becomes `bedrock/converse/eu.anthropic.claude-sonnet-5-5` and is served as
`claude-sonnet-5-5`. An ID that already starts with `eu.`, `us.`, or
`global.` is rejected.

Check where a profile routes from your source Region:

```sh
aws bedrock get-inference-profile --region eu-central-1 \
  --inference-profile-identifier eu.anthropic.claude-sonnet-5-5 \
  --query 'models[].modelArn'
```

Provider-side controls:

- Use a dedicated IAM user whose policy allows only `bedrock:InvokeModel` and
  `bedrock:InvokeModelWithResponseStream` on the profiles and their foundation
  models in the destination Regions.
- Add an SCP that denies Bedrock outside the region.
- Enable model access in the source Region.
- If you turn on invocation logging, keep it in a bucket inside the region.

### Azure OpenAI

```sh
AZURE_OPENAI_DEPLOYMENTS=gpt-5.5,gpt-5.4-mini
AZURE_OPENAI_REGION=swedencentral
AZURE_OPENAI_API_BASE=https://<resource>.openai.azure.com
AZURE_OPENAI_API_KEY=...
```

- List deployment names exactly as they appear on the resource.
- `AZURE_OPENAI_REGION` declares the resource's region, and the launcher
  checks it against the region table. The endpoint does not reveal the
  region, so keep the declaration accurate.
- Name each deployment after its model, because spend is priced from the
  deployment name.

On the Azure side:

- Create every deployment as `DataZoneStandard` (EU or US data zone) or
  `Standard` (regional).
- Block `GlobalStandard` with Azure Policy.
- Apply for modified abuse monitoring if prompts must not be kept for abuse
  review.

### Google Vertex AI

```sh
VERTEX_MODELS=gemini-4-argon,gemini-3.5-flash
VERTEX_LOCATION=eu
VERTEX_PROJECT=<project-id>
VERTEX_CREDENTIALS_FILE=vertex-service-account.json
```

`VERTEX_CREDENTIALS_FILE` is a file name, not a path. The file sits inside
`PROVIDER_SECRETS_DIR` (default `./litellm/provider-secrets`, ignored by Git),
which is mounted read-only at `/app/secrets`. The launcher fails if the file
is missing.

The development gateway runs as UID 65532, so make the key readable by it:

```sh
chown 65532:65532 litellm/provider-secrets/vertex-service-account.json
chmod 0400 litellm/provider-secrets/vertex-service-account.json
```

Provider-side controls:

- Grant the service account only `roles/aiplatform.user`.
- Set the `constraints/gcp.resourceLocations` organization policy to the
  region.
- Keep data logging and caching off unless they are documented.

Check that each model is offered in your chosen location. The `eu` and `us`
multi-regions offer the most models.

## coding-router

`coding-router` exists only when `CODING_ROUTER_DEFAULT_MODEL` names a
configured provider model. It classifies each request into a tier (SIMPLE,
MEDIUM, COMPLEX, or REASONING) and sends it to a model for that tier.

- A tier left empty uses the default model.
- Every model a tier names must be a configured provider model. So routing
  only ever chooses between models that passed the region check.

```sh
CODING_ROUTER_DEFAULT_MODEL=claude-sonnet-5-5
CODING_ROUTER_SIMPLE_MODEL=claude-haiku-5-5
CODING_ROUTER_COMPLEX_MODEL=claude-sonnet-5-5,gpt-5.5,gemini-3.5-flash
CODING_ROUTER_REASONING_MODEL=claude-opus-5-5
CODING_ROUTER_REASONING_KEYWORDS=architecture,design a system
CODING_ROUTER_ADAPTIVE=true
CODING_ROUTER_SESSION_AFFINITY=true
```

How each request is routed:

1. Keywords. A prompt containing a word from
   `CODING_ROUTER_<TIER>_KEYWORDS` goes straight to that tier, with no
   classification. If several tiers match, the highest wins.
2. Classification. D3-mini classifies the prompt, on the remote GPU host or,
   with the optional [`d3` profile](d3-mini-on-host.md), on the stack host
   itself, so the prompt never leaves it.
    - With `CODING_ROUTER_CLASSIFIER=heuristic`, LiteLLM scores it locally
      instead, with no model call.
    - If D3-mini fails or takes longer than
      `CODING_ROUTER_CLASSIFIER_TIMEOUT_MS` (default 3000), the request is
      scored locally.
3. Choosing a model in the tier. A tier that lists several models picks one
   at random. With `CODING_ROUTER_ADAPTIVE=true`, it learns from results which
   model works best, and stores what it learns in the gateway's PostgreSQL
   database.
4. Keeping a conversation together.
    - Clients that send a `session_id` keep each tier on the same model and
      deployment, which helps provider prompt caches.
    - `CODING_ROUTER_SESSION_AFFINITY=true` goes further: the whole
      conversation stays on the model its first turn chose, with no
      reclassification. Keywords then apply only to a conversation's first
      turn.
    - A user can still move a pinned conversation up one tier by including
      `LITELLM ESCALATE` in a message.
5. Requests a model cannot take.
    - A prompt larger than every model in its tier moves to the lowest tier
      with a model that fits.
    - An image sent to a text-only model moves to a higher tier with a model
      that accepts images.

`CODING_ROUTER_STALL_ESCALATION=true` moves an agent task up one tier when its
recent tool calls keep repeating or failing. It needs every turn classified,
so it cannot be combined with `CODING_ROUTER_SESSION_AFFINITY`.

!!! note "The router is beta"
    The router itself is beta in LiteLLM, and its settings can change between
    releases. `check-litellm-upgrade-compatibility.py` starts the candidate
    image with tier pools, keywords, adaptive selection, and session affinity
    enabled, so an upgrade that drops one of them fails the check.

## Verify a deployment

1. Read the region and the model list in the gateway log:

   ```sh
   docker compose logs litellm-prod | grep "Data sovereignty region"
   ```

2. List the served models: `curl -H "Authorization: Bearer $LITELLM_MASTER_KEY"
   "https://$LITELLM_PROD_HOST/v1/models"`.
3. Run the static check, which fails if a static config contains a cloud model
   or the region lock is bypassed:

   ```sh
   ./scripts/check-litellm-upgrade-compatibility.py --candidate-image <pinned image>
   ```

## What this does not cover

The optional `pii-assist` model is not a provider model. It detects personal
data before masking, so it reads prompts unmasked. That is why
`prepare_runtime_config.py`:

- accepts its vLLM server only on `host.docker.internal`, a Compose service,
  or a private address,
- requires that server's API key,
- and refuses to start the gateway otherwise.

See [Local-model assist](presidio-guardrails.md#local-model-assist).

!!! warning "Models added through the UI or API are not checked"
    The region lock checks only the models wired from the environment at
    startup. Both configs set `store_model_in_db: true`, so an administrator
    can still add a model through the LiteLLM admin UI or the `/model/new`
    API, and that model is not checked. Restrict the master key and admin
    roles accordingly.

The region lock controls where a provider processes requests. On its own, it
does not establish legal data sovereignty:

- AWS, Microsoft, and Google are US-headquartered.
- Each provider's retention, abuse-monitoring, and support access terms still
  apply.

Before you send personal data, confirm each account's settings and data
processing agreement. For the EU, also assess any remaining third-country
access risk. See [EU technical controls](eu-technical-controls.md).
