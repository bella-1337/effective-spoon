---
name: litellm-stack-maintenance
description: Maintain, troubleshoot, validate, and safely upgrade this Docker Compose LiteLLM, OpenSearch, Prometheus, Grafana Alloy, and optional Grafana/Loki stack.
---

# LiteLLM Stack Maintenance

Use this skill for any change to the stack's Compose services, LiteLLM
configuration, observability, TLS, runtime profiles, or dependency images.

## Guardrails

1. Read [ARCHITECTURE.md](ARCHITECTURE.md) and the affected Compose,
   service, OpenSearch, and Grafana configuration before editing.
2. Preserve the `prod` and `dev` profiles, their separate databases, and the
   `postgres-init` one-shot dependency unless the task explicitly retires one.
3. Never commit `.env`, the Prometheus master-key file, or `mkcert` private
   material. Test archives must use placeholders, not live credentials.
4. Keep Alloy collection (OpenSearch and Loki) limited to the stack's own
   Compose project on shared Docker hosts.
5. Keep OpenSearch as the default observability platform and Grafana, Loki,
   and `alloy-loki` inside the optional `grafana` profile. Never disable
   OpenSearch TLS, certificate verification, or the security plugin.
6. Do not remove services, exporters, databases, datasources, or volumes just
   because they have no recent activity. Trace their consumers first.

## Change Workflow

### 1. Assess impact

Map the change across these surfaces:

- `docker-compose.yml`;
- `litellm/config.prod.yaml` and `litellm/config.dev.yaml`;
- `litellm/prometheus.yml`;
- `opensearch/` (cluster, security, Dashboards, Data Prepper, and the
  `opensearch/tools` setup/bridge package, metric catalog, and saved objects);
- Grafana datasources, dashboard JSON, and `grafana/alerting/` (the single
  source of alert rules for both OpenSearch and Grafana);
- `loki/config.yml`, `alloy/config.alloy`, and `alloy/loki.alloy`;
- `.env.example`, `.gitignore`, and `README.md`.

For every proposed removal, identify:

- its direct consumer;
- its persistent data volume or database;
- its Prometheus job, `metrics.yml` expression, OpenSearch saved object,
  Grafana datasource, dashboard, or log label dependency;
- the validation that proves it is safe.

### 2. Implement coherently

Use stable, explicit configuration and update all connected surfaces.

Example: changing a Presidio endpoint requires all of the following:

```yaml
# LiteLLM guardrail
presidio_anonymizer_api_base: http://presidio-anonymizer:3000

# Prometheus blackbox target
- http://presidio-anonymizer:3000/health
```

Do not probe `/` for Presidio: the health endpoint is `/health`.

### 3. Validate static configuration

Run:

```sh
docker compose --profile prod --profile dev --profile grafana --profile langfuse config --quiet
python3 -m unittest discover -s scripts/tests
jq empty grafana/dashboards/litellm-stack/litellm-stack.json
jq empty grafana/dashboards/litellm-cost/litellm-cost.json
```

Validate changed YAML with an installed YAML parser when available.

### 4. Deploy and validate runtime behavior

Apply the smallest affected service set when possible:

```sh
docker compose up -d --force-recreate <affected-services>
```

For cross-stack changes:

```sh
docker compose --profile prod --profile dev up -d --wait --wait-timeout 150
```

Verify public routes and active metrics targets:

```sh
curl --fail https://llm.example.test/health/liveliness
curl --fail https://llm-dev.example.test/health/liveliness
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  https://opensearch.example.test/api/status   # expect 401
docker compose exec -T prometheus wget -qO- \
  'http://localhost:9090/api/v1/targets?state=active'
```

### 5. Validate observability

Run the OpenSearch checks of the regression suite:

```sh
./scripts/verify-litellm-upgrade.py --only observability,opensearch,opensearch-logs
```

In OpenSearch Dashboards, the **Container Logs** dashboard or Discover query
`log.attributes.compose_service:"litellm-prod"` shows a service's logs. With the
optional `grafana` profile, also check Grafana data sources and query Loki:

```logql
{compose_project="litellm-stack", compose_service="litellm-prod"}
```

Do not interpret an empty panel or a quiet log stream as a broken pipeline
without checking active targets, the `[LiteLLM] ... not reaching OpenSearch`
monitors, and the workload state.

## Upgrades

1. Pin or change one image family at a time.
2. Read release notes for config-schema, metric-name, and database-migration
   changes.
3. Back up `pg-data`, `opensearch-data` (snapshot), `prometheus-data`, and,
   with the Grafana profile, `grafana-data` and `loki-data` before a
   destructive upgrade. Upgrade OpenSearch and OpenSearch Dashboards together
   to the same version.
4. Upgrade in this order when dependencies require it:
   PostgreSQL compatibility, LiteLLM, Prometheus/Alloy/Data Prepper,
   OpenSearch and Dashboards, optional Loki/Grafana, Traefik/exporters.
5. Re-run the static and runtime validation steps above.
6. Open the OpenSearch dashboards (and Grafana dashboards, if enabled) in a browser. Confirm dashboard datasource
   requests do not return errors; `No data` is acceptable only when the
   corresponding workload has produced no telemetry.

## Known Constraints

- `mkcert` is local/private TLS only; do not use it for a public deployment.
- The LiteLLM metrics scrape uses a secret file, not a committed metric token.
- Failed unknown-model requests appear in `LiteLLM_SpendLogs` with
  `status = 'failure'`; `LiteLLM_ErrorLogs` can remain empty.
- Prometheus retains historical series after a target changes. Use active target
  health, not historical time series, to assess current configuration.
- Grafana Alloy is the supported Docker log collector. Keep its discovery rules
  scoped to the stack's Compose project.
- OpenSearch Dashboards index patterns carry explicit field lists. When a new
  field must be filterable, add it to the index template in
  `opensearch/tools/litellm_opensearch/setup.py` and re-run
  `opensearch-setup` and `opensearch-dashboards-setup`.
