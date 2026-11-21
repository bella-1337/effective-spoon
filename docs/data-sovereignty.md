# Data sovereignty and regional models

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

This stack is a region-locked AI gateway. You choose one data-sovereignty region
and then choose which models to serve from it. Prompts and responses are then
processed only by provider locations inside that region. Everything else in the
stack (LiteLLM, PostgreSQL, OpenSearch, Presidio, and the GPU host) runs on
infrastructure you operate.

## How it works

1. `DATA_SOVEREIGNTY_REGION` in `.env` names the region. It is required.
2. Each cloud provider has its own optional model list in `.env`. No model is
   required: if every list is empty, the gateways start with no cloud models
   and serve only the self-hosted ones (`d3-mini`, plus `bge-m3` and the mock
   `test-model` in development).
3. Both gateways start through `litellm/modules/prepare_runtime_config.py`. It
   calls `litellm/modules/sovereign_models.py`, which turns the lists into
   LiteLLM model entries and adds them to the static model list in
   `litellm/config.prod.yaml` and `litellm/config.dev.yaml`.
4. The launcher refuses to start the gateway, and logs the reason, if a
   configured provider has a missing credential, or if its location could
   process a prompt outside the region. It never writes secret values into the
   runtime config, only `os.environ/` references.

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
`litellm/modules/sovereign_models.py`. Adding a region means adding one entry
there, with locations confirmed from each provider's residency documentation.

| Region | Bedrock profile and source Regions | Azure OpenAI resource regions | Vertex AI locations |
| --- | --- | --- | --- |
| `eu`: EU member states | `eu.` profile from `eu-central-1`, `eu-north-1`, `eu-south-1`, `eu-south-2`, `eu-west-1`, or `eu-west-3` | `francecentral`, `germanywestcentral`, `italynorth`, `northeurope`, `polandcentral`, `spaincentral`, `swedencentral`, `westeurope` | `eu` multi-region, or `europe-central2`, `europe-north1`, `europe-north2`, `europe-southwest1`, `europe-west1`, `europe-west3`, `europe-west4`, `europe-west8`, `europe-west9`, `europe-west10`, `europe-west12` |
| `us`: United States | `us.` profile from `us-east-1`, `us-east-2`, `us-west-1`, or `us-west-2` | `centralus`, `eastus`, `eastus2`, `northcentralus`, `southcentralus`, `westus`, `westus2`, `westus3` | `us` multi-region, or `us-central1`, `us-east1`, `us-east4`, `us-east5`, `us-south1`, `us-west1` to `us-west4` |

Locations are left out when they would leave the region. For example, the
Bedrock `eu.` profile called from `eu-west-2` (London) or `eu-central-2`
(Zurich) can route to those non-EU Regions. A `global.` Bedrock profile or the
Vertex AI `global` endpoint can route anywhere, and an Azure `GlobalStandard`
deployment can process anywhere.

## Configure the providers

Every list is comma-separated. By default, the public model name clients use is
derived from the ID. Write `name=ID` to choose a different name. Names must be
unique across providers and must not be `d3-mini`, `bge-m3`, `test-model`, or
`coding-router`.

### Amazon Bedrock

```sh
BEDROCK_MODELS=anthropic.claude-opus-5-5,anthropic.claude-sonnet-5-5,anthropic.claude-haiku-5-5
BEDROCK_AWS_REGION=eu-central-1
BEDROCK_ACCESS_KEY_ID=...
BEDROCK_SECRET_ACCESS_KEY=...
```

Give model IDs without a routing prefix. The launcher adds the region's
geographic inference profile, so `anthropic.claude-sonnet-5-5` becomes
`bedrock/converse/eu.anthropic.claude-sonnet-5-5` and is served as
`claude-sonnet-5-5`. An ID that already starts with `eu.`, `us.`, or `global.`
is rejected. Check where a profile routes from your source Region:

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

List deployment names exactly as they appear on the resource.
`AZURE_OPENAI_REGION` declares the resource's region, and the launcher checks
it against the region table. The endpoint does not reveal the region, so keep
the declaration accurate. Create every deployment as `DataZoneStandard` (EU or
US data zone) or `Standard` (regional), and block `GlobalStandard` with Azure
Policy. Apply for modified abuse monitoring if prompts must not be kept for
abuse review. Spend is priced from the deployment name, so name each deployment
after its model.

### Google Vertex AI

```sh
VERTEX_MODELS=gemini-4-argon,gemini-3.5-flash
VERTEX_LOCATION=eu
VERTEX_PROJECT=<project-id>
VERTEX_CREDENTIALS_FILE=vertex-service-account.json
```

`VERTEX_CREDENTIALS_FILE` is a file name, not a path, inside
`PROVIDER_SECRETS_DIR` (default `./litellm/provider-secrets`, ignored by Git). That
directory is mounted read-only at `/app/secrets`. The launcher fails if the
file is missing. The development gateway runs as UID 65532, so make the key
readable by it:

```sh
chown 65532:65532 litellm/provider-secrets/vertex-service-account.json
chmod 0400 litellm/provider-secrets/vertex-service-account.json
```

Provider-side controls:

- Grant the service account only `roles/aiplatform.user`.
- Set the `constraints/gcp.resourceLocations` organization policy to the
  region.
- Keep data logging and caching off unless they are documented.

Check that each model is offered in your chosen location; the `eu` and `us`
multi-regions offer the most models.

## coding-router

`coding-router` exists only when `CODING_ROUTER_DEFAULT_MODEL` names a
configured provider model. D3-mini classifies each request, and the router sends
it to the model for its tier. Each tier defaults to the default model, which is
also the fallback when classification fails.

```sh
CODING_ROUTER_DEFAULT_MODEL=claude-sonnet-5-5
CODING_ROUTER_SIMPLE_MODEL=claude-haiku-5-5
CODING_ROUTER_REASONING_MODEL=claude-opus-5-5
```

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

The region lock controls where a provider processes requests. On its own, it
does not establish legal data sovereignty. AWS, Microsoft, and Google are
US-headquartered, and each provider's retention, abuse-monitoring, and support
access terms still apply. Before you send personal data, confirm each account's
settings and data processing agreement. For the EU, also assess any remaining
third-country access risk. See
[EU technical controls](eu-technical-controls.md).
