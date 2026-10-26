# Prompt: create a development-to-production release delta

Use this prompt whenever a new stable LiteLLM image may support capabilities
currently enabled only in development. The requested output is a new release
delta file; do not overwrite the promotion register during discovery.

## Prompt

```text
Create a versioned development-to-production capability delta for this
repository.

Inputs:
- Current production LiteLLM image tag and digest: <PROD_IMAGE>
- Current development LiteLLM image tag and digest: <DEV_IMAGE>
- Candidate stable LiteLLM image tag and digest: <CANDIDATE_IMAGE>
- Candidate release notes or changelog URL: <RELEASE_NOTES_URL>
- Output file:
  docs/dev-to-prod-deltas/<PROD_VERSION>-to-<CANDIDATE_VERSION>.md

Authoritative baseline:
- docs/dev-to-prod-promotion-register.md

Requirements:

1. Read the promotion register and preserve every permanent D2P identifier.
2. Inspect, rather than assume, all relevant repository surfaces:
   - docker-compose.yml;
   - litellm/config.prod.yaml and litellm/config.dev.yaml;
   - litellm/modules/**;
   - vllm-semantic-router/**;
   - pgvector/**;
   - Prometheus, Grafana dashboards, datasources, and alerts;
   - backup, restore, upgrade, rollback, and verification scripts;
   - .env.example and README.md.
3. Compare the complete prod and dev behavior. Include small differences such as
   model aliases, callback lists, timeout values, environment variables,
   profiles, seed jobs, routes, storage type, metrics, tests, and hardcoded
   service names.
4. Review the candidate release notes, but do not claim stable support from
   release notes alone. Run:

   ./scripts/check-litellm-upgrade-compatibility.py \
     --candidate-image "<CANDIDATE_IMAGE>" \
     --pull

   If Docker or the candidate image is unavailable, mark runtime validation
   BLOCKED. Do not present an unverified capability as supported.
5. Never read, print, copy, or commit .env contents, live credentials, private
   keys, resolved Compose output containing secrets, database contents, or
   production personal data.
6. For every D2P item, record:
   - prior state;
   - candidate state;
   - support evidence;
   - exact files and settings affected;
   - dependent services and data stores;
   - security and privacy requirements;
   - migrations and compatibility risks;
   - monitoring and alert changes;
   - backup and restore changes;
   - verification commands and acceptance criteria;
   - feature-off and full rollback procedures;
   - decision: promote, canary, defer, retire, never-promote, or blocked.
7. Preserve these permanent boundaries:
   - test-model is never promoted;
   - dev demo data is never automatically seeded in normal production;
   - dev database, credentials, hostname, keys, users, and spend logs are never
     copied to production;
   - candidate and companion images are pinned to reviewed immutable digests;
   - newly supported capabilities are activated separately from the image
     upgrade and have independent off switches.
8. Detect repository checks that assume a feature is dev-only. A promotion plan
   is incomplete until those assertions and hardcoded dev-only verification
   paths have a safe replacement.
9. Do not modify production configuration or the promotion register while
   generating the delta. Write only the requested new delta file unless
   explicitly asked to implement an approved promotion.
10. Do not invent version support, compatibility, test results, or operational
    readiness. Use VERIFIED, UNVERIFIED, BLOCKED, or NOT APPLICABLE.

The output file must contain:

# Dev-to-prod delta: <PROD_VERSION> to <CANDIDATE_VERSION>

## Delta metadata

Include:
- delta document tag: dev-to-prod-delta/v1.0.0;
- generated date;
- repository commit;
- promotion-register tag;
- prod, dev, and candidate image tags plus digests;
- release-notes source;
- validation commands and results.

## Executive decision

State whether the candidate is rejected, image-only acceptable, or acceptable
for selected capability canaries. List blocking issues.

## Capability matrix

One row for every D2P identifier with prior state, candidate state, evidence,
decision, and dependencies. Do not omit unchanged or never-promote items.

## Newly stable-supported capabilities

For each capability, provide the complete promotion bundle: configuration,
services, secrets, data, monitoring, backup, verification, activation, and
rollback.

## Deferred, blocked, retired, and never-promote capabilities

Give a specific reason and the evidence required to reconsider each decision.

## File-level implementation plan

List exact repository files and the intended change. Separate mandatory changes
from optional follow-up work.

## Validation and acceptance checklist

Provide executable repository commands where they already exist. Include
candidate config validation, dev regression, prod canary, security/isolation,
observability, restart, backup/restore, and rollback acceptance criteria.

## Rollout order

Use this order unless the evidence requires a stricter sequence:
1. validate candidate;
2. prepare rollback and backup;
3. deploy image with old behavior;
4. verify baseline;
5. provision dependencies;
6. canary one capability;
7. observe;
8. expand or disable;
9. update the register after approval.

End with a concise list of facts that still require a human operational,
security, privacy, capacity, or product decision.
```

## After the delta is reviewed

An approved implementation should:

1. make the file-level changes in the delta;
2. run the listed validation;
3. update capability states in the promotion register;
4. add the delta to the register's release delta history;
5. increment the register tag when required by its versioning rules.
