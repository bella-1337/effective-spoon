# Presidio PII masking and guardrails

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

Commands that use hostname variables such as `$LITELLM_PROD_HOST` expect them
to be loaded first with `eval "$(python3 scripts/maintenance.py stack-env)"`;
see [Public hostnames and `STACK_DOMAIN`](getting-started.md#public-hostnames-and-stack_domain).

Both LiteLLM profiles use the internal Presidio Analyzer and Anonymizer through
the `presidio-pii` guardrail. It runs in `pre_call` and `post_call` modes by
the globally attached `eu-data-protection` policy, masks detected
English-language PII before input reaches the model provider and before output
reaches the user. The analyzer's NLP configuration includes `PERSON` for
individual/client names and `ORGANIZATION` for company/client organization
names; organization matches require a minimum confidence of 0.7.

Clients may also select the guardrail explicitly by including:

```json
{
  "guardrails": ["presidio-pii"]
}
```

Presidio masking is pseudonymisation, not anonymisation. It is language,
endpoint, model, and confidence dependent; test it against the data and API
surfaces used by each deployment. Do not claim that all personal data is
detected or that no personal data reaches a provider.

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
        ORGANIZATION: 0.7
```

- Remove `post_call` only if model output must intentionally remain unscanned.
- Remove `pre_call` only if unmasked input may intentionally reach the model
  provider.
- Change `presidio_filter_scope` only after testing the effect on request and
  response fields.
- Raise an entity threshold to reduce false positives; lower it to increase
  detection sensitivity. Validate both false positives and false negatives.

Edit `presidio/analyzer.yaml` when changing the spaCy model or entity-label
mapping. Removing `ORG: ORGANIZATION`, for example, disables that model label as
a source of organisation detections; it does not guarantee that no other
Presidio recognizer can detect an organisation. This file configures NLP
mapping, not arbitrary LiteLLM policies. Exact company/client deny lists or new
custom entity types require a supported Presidio recognizer implementation and
representative tests; do not place sensitive production client lists in Git.

### Add another LiteLLM guardrail

Add a new item under `guardrails:` in a file under
`litellm/modules/guardrails/`, or create a separate module and add its path to
the `include:` list in both profile configurations. Give it a unique
`guardrail_name` and use only parameters supported by the selected LiteLLM
guardrail provider.

To apply it automatically, add its exact name to the policy:

```yaml
policies:
  eu-data-protection:
    guardrails:
      add:
        - presidio-pii
        - new-guardrail-name
```

Defining a guardrail does not apply it globally by itself. It must be added by
an attached policy, selected explicitly in a request, or attached through
another supported LiteLLM scope. Keep global privacy controls in the shared
policy rather than duplicating them in individual model definitions.

### Disable or remove a guardrail

Use the least destructive option that matches the intended change:

- **Stop automatic application but keep it available:** remove its name from
  `guardrails.add` in `eu-data-protection.yaml`. Callers can still request a
  guardrail that remains defined.
- **Disable the entire global policy:** remove its entry from
  `policy_attachments`. The policy remains defined but is not applied.
- **Remove the guardrail completely:** first remove every policy and request
  reference, then remove its definition. A dangling policy reference can stop
  LiteLLM from loading successfully.
- **Remove Presidio from the stack:** only after removing the guardrail and
  policy references, remove the Analyzer and Anonymizer services and their
  Compose dependencies. Do not leave a configured guardrail pointing at absent
  services.

Disabling `pre_call`, removing the global attachment, or removing Presidio
changes the data-protection boundary and may allow personal data to reach model
providers. Review that change before deployment rather than treating it as a
routine operational toggle.

### Apply and verify a change

Restart the components that consume the changed files:

```sh
# LiteLLM guardrail or policy module changed
docker compose --profile prod up -d --force-recreate litellm-prod
docker compose --profile dev up -d --force-recreate litellm-dev

# Presidio analyzer configuration changed
docker compose up -d --force-recreate presidio-analyzer
```

Check both profiles after shared-module changes. Resolve the live policy and
send representative positive and negative examples:

```sh
curl --fail --silent "https://$LITELLM_PROD_HOST/policies/resolve" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini"}'

curl --fail --silent "https://$LITELLM_DEV_HOST/v1/chat/completions" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"gpt-4o-mini",
    "messages":[{"role":"user","content":"Reply exactly: Example Client Limited"}],
    "temperature":0
  }'
```

The resolved policy must list every automatically applied guardrail. For the
masking test, the response must not contain the original organisation name.
Also test harmless text that should remain unchanged, inspect LiteLLM and
Presidio logs for errors, and confirm prompt/response bodies were not written to
logs. Never use real personal or client data in a guardrail test.
