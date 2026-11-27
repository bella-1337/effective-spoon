<!-- human-version-of: docs/presidio-guardrails.md sha256: c73008581da5a612a8c621994a7611e09964124766a2dd7c4d5b5b20f2b0aea6 -->

# Presidio PII masking and guardrails

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

This page explains how the gateways mask personal data (PII) in prompts and
responses with Presidio, and how to check, tune, add, or remove guardrails.
Read it before you change a guardrail or policy, or when masking catches more
or less than you expect.

Commands that use hostname variables such as `$LITELLM_PROD_HOST` expect them
to be loaded first with `eval "$(python3 scripts/maintenance.py stack-env)"`;
see [Public hostnames and `STACK_DOMAIN`](getting-started.md#public-hostnames-and-stack_domain).

Both LiteLLM profiles use the internal Presidio Analyzer and Anonymizer
through the `presidio-pii` guardrail:

- The globally attached `eu-data-protection` policy runs it in `pre_call` and
  `post_call` modes.
- It masks detected PII in English, German, or French before input reaches
  the model provider and before output reaches the user.
- When a local model is configured, the
  [`pii-assist` guardrail](#local-model-assist) runs first. It chooses each
  request's language and masks personal data Presidio's recognizers miss.
- The analyzer's NLP configuration maps spaCy's person, place, date, and
  nationality labels to `PERSON`, `LOCATION`, `DATE_TIME`, and `NRP`.
- It intentionally does not map organisation names (`ORG`), so the gateway
  does not mask company or client names. `scripts/verify-litellm-upgrade.py`
  checks this.

Clients may also select the guardrail explicitly by including:

```json
{
  "guardrails": ["presidio-pii"]
}
```

!!! warning "Pseudonymisation, not anonymisation"

    Presidio masking is pseudonymisation, not anonymisation. It is language,
    endpoint, model, and confidence dependent; test it against the data and
    API surfaces used by each deployment. Do not claim that all personal data
    is detected or that no personal data reaches a provider.

## Check what the guardrail masks

Administrators can see what the guardrail does to a piece of text without
sending it to a model. There are two ways, and neither calls a model provider:

| Way | Who | What it shows |
| --- | --- | --- |
| `POST /guardrails/apply_guardrail` | Proxy administrators (master key or an admin's key), through the API | The exact text the `presidio-pii` guardrail produces, with this gateway's settings. |
| Admin UI → **Guardrails** → **Test Playground** | Proxy administrators | The same result as the API, in the LiteLLM admin UI. |

LiteLLM treats the apply-guardrail route as an administrator route: an
ordinary virtual key receives `401 Only proxy admin can be used`. For end
users, the separate PII review tool offers a self-service page that signs
people in with their virtual key.

The API takes the guardrail name and the text, and returns the masked text in
`response_text`:

```sh
curl --fail --silent "https://$LITELLM_PROD_HOST/guardrails/apply_guardrail" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"guardrail_name":"presidio-pii","text":"Write to Jane Example at jane@example.com"}'
```

```json
{"response_text": "Write to <PERSON> at <EMAIL_ADDRESS>"}
```

Use them to check the gateway's exact result, for example after adding
`presidio_score_thresholds` or `pii_entities_config` to the guardrail.

## Languages and UK formats

The analyzer (`presidio-analyzer`) serves English, German, and French:

| File | Purpose |
| --- | --- |
| `presidio/Dockerfile`, `presidio/spacy-models.txt` | The upstream analyzer image plus the German and French spaCy pipelines (`de_core_news_lg`, `fr_core_news_lg` 3.8.0), pinned by hash. |
| `presidio/analyzer-engine.yaml` | The languages the analyzer accepts. |
| `presidio/analyzer.yaml` | The spaCy model per language and the label mapping. `ORG` is deliberately not mapped. |
| `presidio/recognizers.yaml` | Presidio's default recognizers for these languages, credit cards also in German and French, and UK National Insurance numbers (`UK_NINO`), passports (`UK_PASSPORT`), and postcodes (`UK_POSTCODE`) switched on. The NHS number (`UK_NHS`) was already on. UK vehicle registrations stay off because their short codes cause many false positives. Two custom recognizers mask whole German and French street addresses (street, house number, and an optional postcode and city) as `LOCATION`; the NER models alone find only the street or city. Stricter URL and IP address recognizers replace the defaults; see [Code, file names, and paths](#code-file-names-and-paths). |

### How a request's language is chosen

A request is analysed in English unless its language is set, either by the
`pii-assist` guardrail or by the client in the request metadata:

```json
{"metadata": {"guardrail_config": {"language": "de"}}}
```

Other values are ignored.

LiteLLM itself drops this setting on chat requests (its `apply_guardrail`
path passes no per-request config, checked up to 1.111.0-dev.4).
`litellm/modules/pii_assist.py` installs a small compatibility shim that:

- restores the setting;
- stops the German and French models from re-detecting placeholders such as
  `<LOCATION>`.

!!! warning "After a LiteLLM upgrade"

    If a LiteLLM upgrade changes the patched method, the shim is not
    installed, a warning is logged, and requests are analysed in English.
    `scripts/verify-litellm-upgrade.py` checks German and French analysis and
    the guardrail order.

### German and French street addresses

The address recognizers match a street name with a German suffix (`-straße`,
`-weg`, `-platz`, `-allee`, and similar) or a French street type (`rue`,
`avenue`, `boulevard`, `place`, and similar) next to a house number.

They do not treat these as addresses:

- amounts (`12345 Euro`);
- years after words like `Arbeitsmarkt 2024`;
- counts (`75000 personnes`).

House numbers above 999 and addresses without a recognised street word are
left to the NER models. `scripts/tests/test_presidio_recognizers.py` lists the
examples that must and must not match, and checks that crafted 100 KB inputs
stay fast.

### Analyzer memory

The German and French pipelines make the analyzer larger: about 2.4 GiB,
shared by its workers because gunicorn preloads them (`GUNICORN_CMD_ARGS:
--preload`). Two workers use about 2.7 GiB. Keep
`PRESIDIO_ANALYZER_MEMORY_LIMIT` at 4g or more.

## Code, file names, and paths

Coding prompts used to produce many false detections. Three of Presidio's
default recognizers are changed so that code, file names, and paths stay
readable:

| Was masked | Cause | Change |
| --- | --- | --- |
| `main.rs`, `config.py`, `README.md`, `self.name` as `URL` | `UrlRecognizer` matches any `name.tld` without a scheme, and `.rs`, `.py`, `.md`, and `.sh` are country domains. | `WebUrlRecognizer` in `presidio/recognizers.yaml` needs `https://`, `http://`, `ftp://`, or `www.` and a dotted host. |
| `::` in `std::io`, `de::` in `serde::de`, `f32::` as `IP_ADDRESS` | `IpRecognizer`'s IPv6 pattern matches a bare `::`. | `IpAddressRecognizer` keeps the IPv4 pattern and needs two hex groups and a digit for IPv6. It also masks whole compressed addresses such as `2001:db8::1`, which the default cut short. |
| `i64`, `u128`, `f32`, `E0382` as `US_DRIVER_LICENSE` | A weak pattern (score 0.3) matches any letter followed by digits. | `presidio_score_thresholds` in `litellm/modules/guardrails/presidio.yaml` ignores it below 0.4; a context word such as "license" raises a real one to 0.65. |

Xberg auto-indexing uses the same analyzer and gets the same recognizers.

What still happens:

- Bare domains such as `example.com` are no longer masked as URLs. Email
  addresses are still masked by `EMAIL_ADDRESS`.
- The spaCy models still guess on code: identifiers such as `Vec` or `tokio`
  can be masked as `PERSON` or `NRP`, and a whole Windows path as `LOCATION`.
  Their score (0.85) is the same as for real names, so a threshold cannot
  separate them.
- Other weak patterns, such as `US_SSN`, `US_BANK_NUMBER`, and `UK_POSTCODE`
  without a context word, still mask long numbers. A higher threshold for them
  would also stop masking bare UK postcodes and passport numbers.

`scripts/tests/test_presidio_recognizers.py` lists the URLs, addresses, file
names, and code that must and must not match.

## Reversible masking

By default, masking is one-way: the provider gets `<PERSON>`, and so does the
client, because the answer can only repeat the placeholder.

To get the original values back in the response, set
`PII_REVERSIBLE_MASKING=true` in `.env` and recreate both gateways. The
gateway then encrypts what it masks and decrypts it again in the response:

| Step | What happens |
| --- | --- |
| Request | `pii-assist` and `presidio-pii` replace each value with a numbered token: `Please write to John Smith at john.smith@example.com` becomes `Please write to <PERSON_1> at <EMAIL_ADDRESS_1>`. The same value keeps the same token in every message of the request, so the model can tell two people apart. |
| Vault | Each original is encrypted with AES-256-GCM, with its token as authenticated data, and kept in the request's metadata (`metadata.pii_vault`) together with an HMAC-SHA-256 of each value to find repeats. Nothing in the vault is readable without the key. |
| Provider | Sees only the tokens. |
| Response | `presidio-pii` masks personal data the model produced itself, as before, with plain placeholders. Then the `pii-restore` guardrail decrypts the request's tokens back to their values: `Dear <PERSON_1>` reaches the client as `Dear John Smith`. Tokens that are not in the request's vault stay as they are. |

The key:

- is 256 random bits, created when a gateway process starts;
- is never written to disk, logs, or `.env`;
- is lost when the process stops.

The request and its response are handled by the same process, so nothing
needs to share the key. A vault copied into a log or trace cannot be
decrypted after the request. The gateway cannot decrypt anything later
either: there is no decryption endpoint or stored key.

`litellm/modules/pii_vault.py` implements it. It wraps two methods of
LiteLLM's Presidio guardrail (checked with 1.108.9 and 1.111.0-dev.4). If an
upgrade changes them, the wrapper is not installed, a warning is logged, and
masking stays one-way. `pii-restore` is defined right after `presidio-pii` in
`litellm/modules/guardrails/presidio.yaml` and listed after it in the policy,
so it runs after the output masking.

Things to know:

- Restoring covers chat completions only: `/v1/chat/completions`, streamed or
  not, message text and tool-call arguments. Other APIs, such as
  `/v1/responses` or `/v1/messages`, return the tokens unrestored.
- The model must copy the token. A token the model changes (`<PERSON 1>`) or
  splits is not restored. Short tokens are copied reliably; check with your
  models.
- Streaming holds back an unfinished `<TYPE_n` at the end of a chunk until the
  next chunk shows whether it is a token, at most 48 characters.
- Every request is masked afresh. The client sends its conversation with the
  restored values, and the next request tokenises them again. Numbering
  follows the order of detection, so it usually stays the same between turns.
- It is still pseudonymisation. The provider gets text with personal data
  replaced, not anonymised text, and the client gets the originals back.
- The admin `apply_guardrail` test shows the tokens, because it covers only
  the request side.

## Local-model assist

The `pii-assist` guardrail (`litellm/modules/pii_assist.py`) asks a local
model about every new prompt text before Presidio runs:

1. Language: the newest text's language (`en`, `de`, or `fr`) becomes the
   request's Presidio language, unless the client set one.
2. Personal data: names, addresses, cities, email addresses, phone numbers,
   and identity, tax, insurance, or account numbers the model finds are
   replaced with `<PERSON>`, `<LOCATION>`, `<ID_NUMBER>`, and so on.
    - Only text that occurs verbatim in the prompt is replaced, never inside a
      longer word.
    - Organisation names and the vague `OTHER` type are not masked.
    - A five-digit postcode directly before a masked city, and a house number
      directly after a masked street, are folded into the placeholder. This
      is because masking the model's items first hides the full address from
      Presidio's address recognizers.
3. Presidio: Presidio then masks everything else its recognizers find, as
   before.

### Set up the vLLM server

Enable it by running your own [vLLM](https://docs.vllm.ai/) server on the
Docker host or a private network and pointing the gateways at it. The stack
does not run or pin this server. For example, on the Docker host:

```sh
openssl rand -hex 32   # the key, also set as PII_ASSIST_API_KEY
vllm serve <Hugging Face repository> --served-model-name gemma4 \
  --api-key "$PII_ASSIST_API_KEY" --port 8000
# .env
PII_ASSIST_API_BASE=http://host.docker.internal:8000
PII_ASSIST_API_KEY=<the same key>
PII_ASSIST_VLLM_MODEL=gemma4
```

- `PII_ASSIST_VLLM_MODEL` is the name vLLM serves the model under: the
  `--served-model-name`, or the repository id when that flag is not given.
- `/v1` is added to the base URL.
- Pin the repository to a revision (`--revision`), check its licence, and test
  its detection in English, German, and French before relying on it.

Then recreate both gateways. At start-up, `prepare_runtime_config.py` adds
the model as `pii-assist` (`hosted_vllm/<name>`) and logs `PII assist model:
pii-assist (vLLM gemma4)`.

How the gateways treat the settings:

- Without `PII_ASSIST_API_BASE`, the guardrail does nothing and Presidio runs
  alone, in English.
- With it, the gateways refuse to start unless `PII_ASSIST_API_KEY` is 32-512
  URL-safe characters.
- The key is read from the environment and not written into the runtime
  config.
- The gateways reach the Docker host through `host.docker.internal` (built
  into Docker Desktop, added by `extra_hosts` on Linux).
- For an `https` base, the server's certificate must be trusted by the
  gateways' `SSL_CERT_FILE` bundle.

Each call asks for JSON that matches the guardrail's schema
(`response_format`), which vLLM enforces with structured output. It also
sends `chat_template_kwargs: {"enable_thinking": false}`, which chat templates
with a thinking mode (such as Qwen3) read and others ignore. It does not send
`reasoning_effort`.

### Limits and behaviour of the assist model

- Data stays on the host. Prompts reach this model unmasked. The gateways
  refuse a `PII_ASSIST_API_BASE` that is not `host.docker.internal`, a Compose
  service name, or a private address, so it cannot point at a cloud API. They
  also require the server's API key.
- Latency is not yet measured with vLLM. vLLM keeps the model loaded, so there
  is no cold load after start-up; measure a new prompt's added time on your
  host. Results are cached per text, so a conversation's history is not
  analysed again.
- Budget: long prompts are split into `PII_ASSIST_MAX_CHUNK_CHARS` pieces, and
  at most `PII_ASSIST_MAX_CALLS` new pieces per request go to the model,
  newest first. Presidio alone masks the rest.
- It fails open to Presidio. A timeout, error, or invalid answer skips the
  model for that request; Presidio still masks it, in English.
- The model's own requests are not masked. The `eu-data-protection` policy is
  the default attachment, and the `local-pii-assist` policy exempts only the
  `pii-assist` model, which must see text unmasked. A repository contract
  rejects any other policy attachment, because a default attachment stops
  applying to requests another attachment matches.
- The guardrail logs the language, counts, and duration, never prompt text or
  what it found.
- Model output can be wrong. Presidio remains the baseline, and the masking is
  still pseudonymisation, not anonymisation.

## Manage the configured guardrails

Guardrail behavior is owned by three repository files:

1. `litellm/modules/guardrails/presidio.yaml` defines the LiteLLM guardrail,
   its execution modes, Presidio endpoints, language, filtering scope, and
   per-entity confidence thresholds.
2. `litellm/modules/policies/eu-data-protection.yaml` adds that guardrail to a
   policy and attaches the policy globally.
3. `presidio/analyzer.yaml` controls the analyzer's NLP model and maps model
   labels such as `PER` and `ORG` to Presidio entities.

Both `litellm/config.prod.yaml` and `litellm/config.dev.yaml` include the
guardrail and policy modules. A name must match exactly across the guardrail
definition, policy, and any request that selects it explicitly.

### Change the existing Presidio guardrail

Edit `litellm/modules/guardrails/presidio.yaml` to change when or where it runs:

```yaml
guardrails:
  - guardrail_name: presidio-pii
    litellm_params:
      guardrail: presidio
      mode: [pre_call, post_call]
      presidio_language: en
      presidio_filter_scope: both
      presidio_score_thresholds:
        US_DRIVER_LICENSE: 0.4  # configured; see "Code, file names, and paths"
        PERSON: 0.7  # example: ignore person matches below 0.7
```

- Remove `post_call` only if model output must intentionally remain unscanned.
- Remove `pre_call` only if unmasked input may intentionally reach the model
  provider.
- Change `presidio_filter_scope` only after testing the effect on request and
  response fields.
- Raise an entity threshold to reduce false positives; lower it to increase
  detection sensitivity. Validate both false positives and false negatives.

Edit `presidio/analyzer.yaml` when changing the spaCy model or entity-label
mapping. A new language also needs:

- its model in `presidio/spacy-models.txt`;
- an entry in `presidio/analyzer-engine.yaml` and `presidio/recognizers.yaml`;
- the language code in `litellm/modules/pii_assist_prompt.json`.

Adding `ORG: ORGANIZATION`, for example, would make the gateway mask
organisation names. Xberg auto-indexing, which calls this analyzer directly,
would mask them too. Update the upgrade check if you choose that.

This file configures NLP mapping, not arbitrary LiteLLM policies. Exact
company/client deny lists or new custom entity types require a supported
Presidio recognizer implementation and representative tests. Do not place
sensitive production client lists in Git.

!!! warning "Keep the chunk size at 100 KB"

    For prompts larger than `presidio_analyze_chunk_size_bytes` (100 KB in
    this stack), LiteLLM analyses overlapping chunks concurrently. Do not
    raise it toward LiteLLM's 500 KB default: a single 1.5 MB prompt then
    exhausted the analyzer's 2 GiB memory limit and stopped masking for every
    caller.

MCP tool results returned to the caller of `/mcp-rest/tools/call` are not
masked: Presidio cannot rewrite Xberg's structured tool output and fails
closed. Tool results are masked when an agent sends them back to a model,
because they then pass the `pre_call` guardrail as `tool` messages.
`litellm/modules/guardrails/prompt-injection.yaml` screens the same tool
results for indirect prompt injection; see
[Edge and abuse protection](edge-and-abuse-protection.md).

### Add another LiteLLM guardrail

1. Add a new item under `guardrails:` in a file under
   `litellm/modules/guardrails/`. Or create a separate module and add its path
   to the `include:` list in both profile configurations.
2. Give it a unique `guardrail_name`, and use only parameters supported by the
   selected LiteLLM guardrail provider.

To apply it automatically, add its exact name to the policy:

```yaml
policies:
  eu-data-protection:
    guardrails:
      add:
        - pii-assist
        - presidio-pii
        - pii-restore
        - new-guardrail-name
```

The repository contract in `scripts/maintenance_contracts.py`
(`DATA_PROTECTION_POLICY_LINES`) pins this list, so update it in the same
change.

Defining a guardrail does not apply it globally by itself. It must be added
by an attached policy, selected explicitly in a request, or attached through
another supported LiteLLM scope. Keep global privacy controls in the shared
policy rather than duplicating them in individual model definitions.

### Disable or remove a guardrail

Use the least destructive option that matches the intended change:

| Goal | What to do |
| --- | --- |
| Stop automatic application but keep it available | Remove its name from `guardrails.add` in `eu-data-protection.yaml`. Callers can still request a guardrail that remains defined. |
| Disable the entire global policy | Remove its entry from `policy_attachments`. The policy remains defined but is not applied. |
| Remove the guardrail completely | First remove every policy and request reference, then remove its definition. A dangling policy reference can stop LiteLLM from loading successfully. |
| Remove Presidio from the stack | Only after removing the guardrail and policy references, remove the Analyzer and Anonymizer services and their Compose dependencies. Do not leave a configured guardrail pointing at absent services. |

!!! warning

    Disabling `pre_call`, removing the global attachment, or removing
    Presidio changes the data-protection boundary and may allow personal data
    to reach model providers. Review that change before deployment rather
    than treating it as a routine operational toggle.

### Apply and verify a change

Restart the components that consume the changed files:

```sh
# LiteLLM guardrail or policy module changed
docker compose --profile prod up -d --force-recreate litellm-prod
docker compose --profile dev up -d --force-recreate litellm-dev

# Presidio analyzer configuration changed (presidio/*.yaml); add --build
# when presidio/Dockerfile or presidio/spacy-models.txt changed
docker compose up -d --force-recreate presidio-analyzer
```

Check both profiles after shared-module changes. Resolve the live policy and
send representative positive and negative examples:

```sh
curl --fail --silent "https://$LITELLM_PROD_HOST/policies/resolve" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"<configured model>"}'

curl --fail --silent "https://$LITELLM_DEV_HOST/v1/chat/completions" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"<configured model>",
    "messages":[{"role":"user","content":"Reply exactly: Example Client Limited"}],
    "temperature":0
  }'
```

Then check:

- [ ] The resolved policy lists every automatically applied guardrail.
- [ ] For the masking test, the response does not contain the original
      organisation name.
- [ ] Harmless text that should remain unchanged stays unchanged.
- [ ] LiteLLM and Presidio logs show no errors.
- [ ] Prompt/response bodies were not written to logs.

!!! warning

    Never use real personal or client data in a guardrail test.
