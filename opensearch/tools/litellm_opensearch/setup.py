"""Idempotent OpenSearch and OpenSearch Dashboards provisioning."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import alert_rules, notebooks, saved_objects
from .client import LOG, Auth, HttpClient, HttpError, env, env_int, opensearch_from_env

USAGE_PATTERNS = ["litellm-spend-*", "litellm-provider-errors-*", "litellm-entities"]
OPS_PATTERNS = ["litellm-metrics-*", "litellm-alert-state-*", "litellm-logs-*"]
# LiteLLM spans carry the same team, cost, and pseudonymous user attributes as
# the administrator-only Langfuse project, so only operators may read them.
TRACE_PATTERNS = ["otel-v1-apm-span*", "otel-v1-apm-service-map*"]
TRACE_RETENTION_POLICY = "raw-span-policy"
READ_ACTIONS = ["read", "indices:admin/mappings/get", "indices:admin/resolve/index", "indices:monitor/settings/get"]
WRITE_ACTIONS = [
    "crud",
    "create_index",
    "indices:admin/get",
    "indices:admin/aliases/get",
    "indices:admin/mapping/put",
    "indices:admin/mappings/get",
]

def keywords(*names: str) -> dict[str, Any]:
    return {name: {"type": "keyword", "ignore_above": 1024} for name in names}


KEYWORD_STRINGS = {
    "strings_as_keywords": {
        "match_mapping_type": "string",
        "mapping": {"type": "keyword", "ignore_above": 1024},
    }
}


def roles() -> dict[str, dict[str, Any]]:
    global_read = [{"tenant_patterns": ["global_tenant"], "allowed_actions": ["kibana_all_read"]}]
    global_write = [{"tenant_patterns": ["global_tenant"], "allowed_actions": ["kibana_all_write"]}]
    return {
        "litellm_log_writer": {
            "description": "Data Prepper: write container logs and LiteLLM trace spans",
            "cluster_permissions": [
                "cluster_monitor",
                "cluster_composite_ops",
                "indices:admin/template/get",
                "indices:admin/index_template/get",
                # Trace Analytics sinks install their own templates and verify
                # that the retention policy provisioned by opensearch-setup exists.
                "indices:admin/template/put",
                "indices:admin/index_template/put",
                "cluster:admin/opendistro/ism/policy/get",
                "cluster:admin/opendistro/ism/policy/write",
            ],
            "index_permissions": [
                {
                    "index_patterns": ["litellm-logs-*"],
                    "allowed_actions": WRITE_ACTIONS + ["indices:admin/data_stream/get"],
                },
                {
                    "index_patterns": TRACE_PATTERNS,
                    "allowed_actions": WRITE_ACTIONS
                    + ["indices:admin/aliases", "indices:admin/settings/update", "indices:admin/data_stream/get"],
                },
                # The sink checks for an alias with the index name across all indices.
                {"index_patterns": ["*"], "allowed_actions": ["indices:admin/aliases/get"]},
            ],
        },
        "litellm_bridge_writer": {
            "description": "opensearch-bridge: write metrics, alert state, and LiteLLM usage",
            # delete_by_query (entity snapshots, usage retention) clears its scroll.
            "cluster_permissions": ["cluster_monitor", "cluster_composite_ops", "indices:data/read/scroll/clear"],
            "index_permissions": [
                {
                    "index_patterns": [
                        "litellm-metrics-*",
                        "litellm-alert-state-*",
                        "litellm-spend-*",
                        "litellm-provider-errors-*",
                        "litellm-audit-*",
                        "litellm-entities",
                        "litellm-bridge-state",
                    ],
                    "allowed_actions": WRITE_ACTIONS + ["delete_index", "indices_monitor"],
                }
            ],
        },
        "litellm_viewer": {
            "description": "Read every LiteLLM dashboard, log, metric, and cost view (read-only global tenant)",
            "cluster_permissions": ["cluster_composite_ops_ro"],
            "index_permissions": [
                {"index_patterns": OPS_PATTERNS + USAGE_PATTERNS, "allowed_actions": READ_ACTIONS}
            ],
            "tenant_permissions": global_read,
        },
        "litellm_ops_viewer": {
            "description": "Read logs, metrics, and alert state only (no cost or identity data)",
            "cluster_permissions": ["cluster_composite_ops_ro"],
            "index_permissions": [{"index_patterns": OPS_PATTERNS, "allowed_actions": READ_ACTIONS}],
            "tenant_permissions": global_read,
        },
        "litellm_cost_viewer": {
            "description": "Read LiteLLM spend, provider errors, and entities only",
            "cluster_permissions": ["cluster_composite_ops_ro"],
            "index_permissions": [{"index_patterns": USAGE_PATTERNS, "allowed_actions": READ_ACTIONS}],
            "tenant_permissions": global_read,
        },
        "litellm_operator": {
            "description": "Read all LiteLLM data, request traces, and audit trails, edit shared dashboards",
            "cluster_permissions": ["cluster_composite_ops_ro"],
            "index_permissions": [
                {
                    "index_patterns": OPS_PATTERNS
                    + USAGE_PATTERNS
                    + TRACE_PATTERNS
                    + ["litellm-audit-*", "security-auditlog-*"],
                    "allowed_actions": READ_ACTIONS,
                }
            ],
            "tenant_permissions": global_write,
        },
        "litellm_dashboards_read_only": {
            "description": "Marker role: OpenSearch Dashboards read-only mode",
            "cluster_permissions": [],
            "index_permissions": [],
            "tenant_permissions": global_read,
        },
    }


def index_templates(replicas: int) -> dict[str, dict[str, Any]]:
    settings = {"number_of_shards": 1, "number_of_replicas": replicas, "refresh_interval": "15s"}
    return {
        "litellm-logs": {
            "index_patterns": ["litellm-logs-*"],
            "priority": 200,
            "template": {
                "settings": {**settings, "index.mapping.total_fields.limit": 2000, "refresh_interval": "5s"},
                "mappings": {
                    "dynamic_templates": [
                        {"log_body": {"match": "body", "mapping": {"type": "text"}}},
                        KEYWORD_STRINGS,
                    ],
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "body": {"type": "text"},
                        "observedTimestamp": {"type": "date"},
                        "severityText": {"type": "keyword"},
                        "http": {
                            "properties": {
                                **keywords(
                                    "RouterName", "ServiceName", "RequestMethod", "RequestPath",
                                    "RequestHost", "RequestProtocol", "RequestScheme", "entryPointName",
                                    "TLSVersion", "TLSCipher", "ServiceURL", "level", "msg",
                                ),
                                "DownstreamStatus": {"type": "integer"},
                                "OriginStatus": {"type": "integer"},
                                "Duration": {"type": "long"},
                                "OriginDuration": {"type": "long"},
                                "Overhead": {"type": "long"},
                                "duration_ms": {"type": "double"},
                                "DownstreamContentSize": {"type": "long"},
                                "OriginContentSize": {"type": "long"},
                                "RequestContentSize": {"type": "long"},
                                "RetryAttempts": {"type": "integer"},
                                "StartUTC": {"type": "date"},
                            }
                        },
                        "log": {
                            "properties": {
                                "attributes": {
                                    "properties": {
                                        "compose_service": {"type": "keyword"},
                                        "compose_project": {"type": "keyword"},
                                        "container": {"type": "keyword"},
                                        "stream": {"type": "keyword"},
                                        "level": {"type": "keyword"},
                                        "job": {"type": "keyword"},
                                    }
                                }
                            }
                        },
                    },
                },
            },
        },
        "litellm-metrics": {
            "index_patterns": ["litellm-metrics-*"],
            "priority": 200,
            "template": {
                "settings": {**settings, "index.mapping.total_fields.limit": 3000},
                "mappings": {
                    "dynamic_templates": [
                        {
                            "metric_labels": {
                                "path_match": "labels.*",
                                "mapping": {"type": "keyword", "ignore_above": 256},
                            }
                        },
                        KEYWORD_STRINGS,
                    ],
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "metric": {"type": "keyword"},
                        "kind": {"type": "keyword"},
                        "source": {"type": "keyword"},
                        "value": {"type": "double"},
                        "series": {"type": "keyword", "ignore_above": 1024},
                        "metric_series": {"type": "keyword", "ignore_above": 1024},
                        "job": {"type": "keyword"},
                        "labels": {
                            "properties": keywords(
                                "job", "instance", "model", "service", "cn", "sans", "mode", "device",
                                "direction", "mountpoint", "datname", "state", "status_code",
                                "exception_class", "guardrail_name", "litellm_model_name", "entrypoint",
                                "code", "kind", "operation", "window", "reason",
                            )
                        },
                    },
                },
            },
        },
        "litellm-alert-state": {
            "index_patterns": ["litellm-alert-state-*"],
            "priority": 200,
            "template": {
                "settings": settings,
                "mappings": {
                    "dynamic_templates": [KEYWORD_STRINGS],
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "rule_uid": {"type": "keyword"},
                        "title": {"type": "keyword"},
                        "severity": {"type": "keyword"},
                        "state": {"type": "keyword"},
                        "condition_met": {"type": "integer"},
                        "active_series": {"type": "keyword", "ignore_above": 1024},
                        "active_series_count": {"type": "integer"},
                        "summary": {"type": "keyword", "ignore_above": 1024},
                        "error": {"type": "text"},
                    },
                },
            },
        },
        "litellm-usage": {
            "index_patterns": ["litellm-spend-*", "litellm-provider-errors-*", "litellm-audit-*"],
            "priority": 200,
            "template": {
                "settings": settings,
                "mappings": {
                    "dynamic_templates": [KEYWORD_STRINGS],
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "start_time": {"type": "date"},
                        "end_time": {"type": "date"},
                        "updated_at": {"type": "date"},
                        "spend": {"type": "double"},
                        "total_tokens": {"type": "long"},
                        "prompt_tokens": {"type": "long"},
                        "completion_tokens": {"type": "long"},
                        "status_code": {"type": "keyword"},
                        "cache_hit": {"type": "keyword"},
                        **keywords(
                            "environment", "request_id", "call_type", "model", "model_group", "model_name",
                            "model_id", "provider", "user", "user_alias", "member", "end_user", "team_id",
                            "team_alias", "team", "organization_id", "status", "request_tags", "key_alias",
                            "key_owner", "key_owner_id", "key_owner_alias", "provider_model",
                            "exception_type", "audit_id", "changed_by", "action", "object_type", "object_id",
                        ),
                    },
                },
            },
        },
        "litellm-entities": {
            "index_patterns": ["litellm-entities", "litellm-bridge-state"],
            "priority": 200,
            "template": {
                "settings": settings,
                "mappings": {
                    "dynamic_templates": [KEYWORD_STRINGS],
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "synced_at": {"type": "date"},
                        "created_at": {"type": "date"},
                        "expires": {"type": "date"},
                        "budget_reset_at": {"type": "date"},
                        "spend": {"type": "double"},
                        "total_spend": {"type": "double"},
                        "max_budget": {"type": "double"},
                        "blocked": {"type": "boolean"},
                        "usable": {"type": "boolean"},
                        "cursor": {"type": "date"},
                        **keywords(
                            "environment", "entity_type", "name", "owner", "team", "user_id", "user_alias",
                            "user_role", "team_id", "team_alias", "key_alias", "key_name", "owner_alias",
                        ),
                    },
                },
            },
        },
    }


def ism_policies() -> dict[str, tuple[list[str], int, str]]:
    return {
        "litellm-logs-retention": (
            ["litellm-logs-*"],
            env_int("OPENSEARCH_LOG_RETENTION_DAYS", 7, minimum=1, maximum=3650),
            "Delete container log indices after the configured retention",
        ),
        "litellm-metrics-retention": (
            ["litellm-metrics-*", "litellm-alert-state-*"],
            env_int("OPENSEARCH_METRICS_RETENTION_DAYS", 15, minimum=1, maximum=3650),
            "Delete metric and alert-state indices after the configured retention",
        ),
        "security-auditlog-retention": (
            ["security-auditlog-*"],
            env_int("OPENSEARCH_AUDIT_RETENTION_DAYS", 90, minimum=1, maximum=3650),
            "Delete OpenSearch security audit indices after the configured retention",
        ),
    }


def trace_policy_body(days: int) -> dict[str, Any]:
    """Data Prepper's raw-span rollover policy plus deletion after the retention."""
    return {
        "policy": {
            "description": "LiteLLM trace spans: daily rollover, delete after the configured retention",
            "default_state": "current_write_index",
            "states": [
                {
                    "name": "current_write_index",
                    "actions": [{"rollover": {"min_size": "50gb", "min_index_age": "24h"}}],
                    "transitions": [{"state_name": "delete", "conditions": {"min_index_age": f"{days}d"}}],
                },
                {"name": "delete", "actions": [{"delete": {}}], "transitions": []},
            ],
            "ism_template": [{"index_patterns": ["otel-v1-apm-span-*"], "priority": 100}],
        }
    }


def ism_policy_body(patterns: list[str], days: int, description: str) -> dict[str, Any]:
    return {
        "policy": {
            "description": description,
            "default_state": "hot",
            "states": [
                {
                    "name": "hot",
                    "actions": [],
                    "transitions": [{"state_name": "delete", "conditions": {"min_index_age": f"{days}d"}}],
                },
                {"name": "delete", "actions": [{"delete": {}}], "transitions": []},
            ],
            "ism_template": [{"index_patterns": patterns, "priority": 100}],
        }
    }


OSD_TYPES = {
    "keyword": "string",
    "text": "string",
    "date": "date",
    "date_nanos": "date",
    "double": "number",
    "long": "number",
    "integer": "number",
    "float": "number",
    "boolean": "boolean",
}
META_FIELDS = ("_id", "_index", "_score", "_source", "_type")
META_TYPES = {"_id": "string", "_index": "string", "_score": "number", "_source": "_source", "_type": "string"}


def field_spec(name: str, es_type: str) -> dict[str, Any]:
    aggregatable = es_type not in ("text", "_source", "_score")
    return {
        "name": name,
        "type": META_TYPES.get(es_type) or OSD_TYPES.get(es_type, "unknown"),
        "esTypes": [es_type],
        "searchable": es_type != "_source",
        "aggregatable": aggregatable,
        "readFromDocValues": aggregatable and not es_type.startswith("_"),
        "count": 0,
        "scripted": False,
    }


def fields_from_properties(properties: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for key, mapping in properties.items():
        name = f"{prefix}{key}"
        if "properties" in mapping:
            fields.extend(fields_from_properties(mapping["properties"], f"{name}."))
            continue
        es_type = mapping.get("type", "object")
        if es_type == "object":
            continue
        fields.append(field_spec(name, es_type))
        for sub, sub_mapping in (mapping.get("fields") or {}).items():
            fields.append(field_spec(f"{name}.{sub}", sub_mapping.get("type", "keyword")))
    return fields


SECURITY_AUDIT_FIELDS = {
    "@timestamp": {"type": "date"},
    **{
        name: {"type": "text", "fields": {"keyword": {"type": "keyword"}}}
        for name in (
            "audit_category",
            "audit_request_effective_user",
            "audit_request_privilege",
            "audit_rest_request_path",
            "audit_request_remote_address",
            "audit_request_layer",
            "audit_request_origin",
        )
    },
}


# Core fields of Data Prepper's trace-analytics-raw template plus the LiteLLM
# span attributes used by the dashboards (span attributes are keywords).
OTEL_SPAN_FIELDS = {
    "traceId": {"type": "keyword"},
    "spanId": {"type": "keyword"},
    "parentSpanId": {"type": "keyword"},
    "name": {"type": "keyword"},
    "traceGroup": {"type": "keyword"},
    "kind": {"type": "keyword"},
    "serviceName": {"type": "keyword"},
    "startTime": {"type": "date_nanos"},
    "endTime": {"type": "date_nanos"},
    "durationInNanos": {"type": "long"},
    # Added by the raw-traces pipeline for millisecond dashboards.
    "durationInMillis": {"type": "double"},
    "status": {"properties": {"code": {"type": "integer"}, "message": {"type": "keyword"}}},
    "traceGroupFields": {
        "properties": {
            "endTime": {"type": "date_nanos"},
            "durationInNanos": {"type": "long"},
            "statusCode": {"type": "integer"},
        }
    },
    "span": {
        "properties": {
            "attributes": {
                "properties": keywords(
                    "gen_ai@request@model",
                    "gen_ai@provider@name",
                    "gen_ai@operation@name",
                    "gen_ai@usage@input_tokens",
                    "gen_ai@usage@output_tokens",
                    "litellm@call_type",
                    "litellm@request@route",
                    "litellm@provider@model",
                    "litellm@cost@total",
                    "litellm@guardrail@name",
                    "litellm@guardrail@status",
                    "litellm@guardrail@masked_entity_count",
                    "litellm@service@name",
                    "db@collection@name",
                    "error@type",
                )
            }
        }
    },
}


def static_fields(pattern: str) -> list[dict[str, Any]]:
    """Fields known from the index templates, used before any data exists."""
    if pattern == "security-auditlog-*":
        properties = SECURITY_AUDIT_FIELDS
    elif pattern == "otel-v1-apm-span-*":
        properties = OTEL_SPAN_FIELDS
    else:
        properties = {}
        for template in index_templates(0).values():
            if pattern in template["index_patterns"]:
                properties = template["template"]["mappings"]["properties"]
                break
    meta = [field_spec(name, name) for name in META_FIELDS]
    return meta + fields_from_properties(properties)


def merge_fields(static: list[dict[str, Any]], live: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {field["name"]: field for field in static}
    for field in live:
        merged[field["name"]] = {**field, "count": 0, "scripted": False}
    return sorted(merged.values(), key=lambda field: field["name"])


class ClusterSetup:
    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def wait(self) -> None:
        self.client.wait_until("/_cluster/health", ok_status=(200,), timeout=600, label="OpenSearch")
        self.client.get("/_cluster/health", params={"wait_for_status": "yellow", "timeout": "120s"})

    def users(self) -> None:
        users = {
            "admin": (
                env("OPENSEARCH_ADMIN_PASSWORD", required=True),
                "Initial OpenSearch administrator (rotate here by changing .env)",
                [],
            ),
            # The security plugin's config.yml recognizes this exact name as the
            # Dashboards server identity for multi-tenancy.
            "kibanaserver": (
                env("OPENSEARCH_DASHBOARDS_SERVER_PASSWORD", required=True),
                "OpenSearch Dashboards server identity",
                [],
            ),
            "data_prepper": (
                env("OPENSEARCH_INGEST_PASSWORD", required=True),
                "Data Prepper container log writer",
                ["litellm_log_writer"],
            ),
            "litellm_bridge": (
                env("OPENSEARCH_BRIDGE_PASSWORD", required=True),
                "opensearch-bridge metrics and usage writer",
                ["litellm_bridge_writer"],
            ),
        }
        for name, (password, description, security_roles) in users.items():
            body = {"password": password, "description": description}
            if security_roles:
                body["opendistro_security_roles"] = security_roles
            self.client.put(f"/_plugins/_security/api/internalusers/{name}", body)
        mappings = {
            "all_access": ["admin"],
            "security_rest_api_access": ["admin"],
            "kibana_server": ["kibanaserver"],
        }
        for role, names in mappings.items():
            status, current = self.client.request(
                "GET", f"/_plugins/_security/api/rolesmapping/{role}", allowed_status=(404,)
            )
            existing = (current or {}).get(role, {}) if status == 200 else {}
            merged_users = sorted(set(existing.get("users", [])) | set(names))
            self.client.put(
                f"/_plugins/_security/api/rolesmapping/{role}",
                {
                    "users": merged_users,
                    "backend_roles": existing.get("backend_roles", []),
                    "hosts": existing.get("hosts", []),
                },
            )
        LOG.info("Provisioned %d service users", len(users))

    def roles(self) -> None:
        for name, body in roles().items():
            self.client.put(f"/_plugins/_security/api/roles/{name}", body)
        LOG.info("Provisioned %d LiteLLM roles", len(roles()))

    def templates(self) -> None:
        replicas = env_int("OPENSEARCH_INDEX_REPLICAS", 0, minimum=0, maximum=5)
        for name, body in index_templates(replicas).items():
            self.client.put(f"/_index_template/{name}", body)
        # Bridge state and entity snapshots use fixed index names.
        for index in ("litellm-entities", "litellm-bridge-state"):
            self.client.request("PUT", f"/{index}", {}, allowed_status=(400,))
        # Security audit indices are single-node friendly too.
        self.client.put(
            "/_index_template/security-auditlog",
            {
                "index_patterns": ["security-auditlog-*"],
                "priority": 200,
                "template": {"settings": {"number_of_shards": 1, "number_of_replicas": replicas}},
            },
        )
        # Plugin system indices default to one replica, which can never be
        # allocated on the single-node cluster and keeps its health yellow.
        self.client.put(
            "/_cluster/settings", {"persistent": {"cluster.default_number_of_replicas": replicas}}
        )
        if replicas == 0:
            rows = self.client.get(
                "/_cat/indices",
                params={"health": "yellow", "expand_wildcards": "all", "format": "json", "h": "index"},
            ) or []
            for row in rows:
                index = row.get("index", "") if isinstance(row, dict) else ""
                if index:
                    self.client.put(f"/{index}/_settings", {"index": {"number_of_replicas": 0}})
        LOG.info("Provisioned index templates")

    def _put_policy(self, policy_id: str, body: dict[str, Any]) -> None:
        status, current = self.client.request(
            "GET", f"/_plugins/_ism/policies/{policy_id}", allowed_status=(404,)
        )
        if status == 404:
            self.client.put(f"/_plugins/_ism/policies/{policy_id}", body)
        else:
            self.client.put(
                f"/_plugins/_ism/policies/{policy_id}",
                body,
                params={"if_seq_no": current["_seq_no"], "if_primary_term": current["_primary_term"]},
            )

    def ism(self) -> None:
        # Created before Data Prepper starts, so its trace sink keeps this
        # policy instead of installing its rollover-only default.
        trace_days = env_int("OPENSEARCH_TRACE_RETENTION_DAYS", 15, minimum=1, maximum=3650)
        self._put_policy(TRACE_RETENTION_POLICY, trace_policy_body(trace_days))
        self.client.request(
            "POST",
            "/_plugins/_ism/change_policy/otel-v1-apm-span-*",
            {"policy_id": TRACE_RETENTION_POLICY},
            allowed_status=(400, 404),
        )
        for policy_id, (patterns, days, description) in ism_policies().items():
            self._put_policy(policy_id, ism_policy_body(patterns, days, description))
            for pattern in patterns:
                # Attach unmanaged indices and move managed ones to the new version.
                self.client.request(
                    "POST", f"/_plugins/_ism/add/{pattern}", {"policy_id": policy_id}, allowed_status=(400, 404)
                )
                self.client.request(
                    "POST",
                    f"/_plugins/_ism/change_policy/{pattern}",
                    {"policy_id": policy_id},
                    allowed_status=(400, 404),
                )
        LOG.info("Provisioned index lifecycle policies")

    def prometheus_datasource(self) -> None:
        uri = env("PROMETHEUS_URL", "http://prometheus:9090")
        body = {
            "name": "prometheus",
            "description": "LiteLLM stack Prometheus (all scraped series, PromQL via PPL)",
            "connector": "prometheus",
            "properties": {"prometheus.uri": uri},
            "allowedRoles": ["litellm_viewer", "litellm_ops_viewer", "litellm_operator"],
        }
        status, _ = self.client.request(
            "GET", "/_plugins/_query/_datasources/prometheus", allowed_status=(400, 404)
        )
        try:
            if status == 200:
                self.client.put("/_plugins/_query/_datasources", body)
            else:
                self.client.post("/_plugins/_query/_datasources", body)
            LOG.info("Registered the Prometheus direct-query data source")
        except HttpError as error:
            # Prometheus may still be starting; metrics in OpenSearch do not depend on it.
            LOG.warning("Could not register the Prometheus data source yet: %s", error)

    def monitors(self) -> None:
        interval = env_int("OPENSEARCH_BRIDGE_INTERVAL_SECONDS", 60, minimum=15, maximum=600)
        channel = env("OPENSEARCH_ALERT_CHANNEL_ID", "") or None
        rules = alert_rules.load_rules(Path(env("OPENSEARCH_ALERT_RULES_DIR", "/app/alerting")))
        desired = [alert_rules.monitor_for_rule(rule, interval, channel) for rule in rules]
        desired += alert_rules.staleness_monitors(channel)
        result = self.client.post(
            "/_plugins/_alerting/monitors/_search",
            {"size": 1000, "query": {"exists": {"field": "monitor.name"}}},
        )
        existing: dict[str, str] = {}
        for hit in (result or {}).get("hits", {}).get("hits", []):
            name = hit.get("_source", {}).get("name", "")
            if name.startswith(alert_rules.MONITOR_PREFIX):
                existing[name] = hit["_id"]
        for monitor in desired:
            monitor_id = existing.pop(monitor["name"], None)
            if monitor_id:
                self.client.put(f"/_plugins/_alerting/monitors/{monitor_id}", monitor)
            else:
                self.client.post("/_plugins/_alerting/monitors", monitor)
        for name, monitor_id in existing.items():
            LOG.info("Removing obsolete monitor %s", name)
            self.client.delete(f"/_plugins/_alerting/monitors/{monitor_id}")
        LOG.info(
            "Provisioned %d alerting monitors%s",
            len(desired),
            "" if channel else " without notification actions (OPENSEARCH_ALERT_CHANNEL_ID is unset)",
        )


def setup_cluster() -> int:
    client = opensearch_from_env(admin=True)
    setup = ClusterSetup(client)
    setup.wait()
    setup.roles()
    setup.users()
    setup.templates()
    setup.ism()
    setup.prometheus_datasource()
    setup.monitors()
    LOG.info("OpenSearch cluster provisioning complete")
    return 0


def setup_dashboards() -> int:
    dashboards = HttpClient(
        env("OPENSEARCH_DASHBOARDS_URL", "http://opensearch-dashboards:5601"),
        auth=Auth(username="admin", password=env("OPENSEARCH_ADMIN_PASSWORD", required=True)),
        timeout=120.0,
        # The Dashboards security plugin reuses the inbound Host header as the TLS
        # server name for some OpenSearch calls during imports; "localhost" is a
        # SAN of the node certificate, the internal service name is not.
        extra_headers={"osd-xsrf": "true", "securitytenant": "global", "Host": "localhost:5601"},
    )
    dashboards.wait_until("/api/status", ok_status=(200,), timeout=900, label="OpenSearch Dashboards")
    objects = saved_objects.build_all() + notebooks.build_all()
    # OpenSearch Dashboards does not discover index-pattern fields on its own,
    # so combine the template mappings with whatever data already exists.
    for item in objects:
        if item["type"] != "index-pattern":
            continue
        pattern = item["attributes"]["title"]
        status, live = dashboards.request(
            "GET",
            "/api/index_patterns/_fields_for_wildcard",
            params={"pattern": pattern, "meta_fields": list(META_FIELDS)},
            allowed_status=(404,),
        )
        live_fields = live.get("fields", []) if status == 200 and isinstance(live, dict) else []
        item["attributes"]["fields"] = json.dumps(merge_fields(static_fields(pattern), live_fields))
    payload = "\n".join(json.dumps(item, separators=(",", ":")) for item in objects) + "\n"
    boundary = "litellm-stack-saved-objects"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="litellm.ndjson"\r\n'
        "Content-Type: application/ndjson\r\n\r\n"
        f"{payload}\r\n--{boundary}--\r\n"
    ).encode()
    for attempt in range(1, 6):
        try:
            result = dashboards.post(
                "/api/saved_objects/_import",
                raw_body=body,
                params={"overwrite": "true"},
                content_type=f"multipart/form-data; boundary={boundary}",
            )
            break
        except HttpError as error:
            if attempt == 5:
                raise
            LOG.warning("Saved-object import attempt %d failed: %s", attempt, error)
            time.sleep(10)
    if not result.get("success"):
        errors = [
            f"{item.get('type')}/{item.get('id')}: {item.get('error', {}).get('type')}"
            for item in result.get("errors", [])
        ]
        raise SystemExit("Saved-object import failed: " + "; ".join(errors[:20]))
    for object_id in notebooks.LEGACY_NOTEBOOK_IDS:
        dashboards.delete(
            f"/api/saved_objects/{notebooks.NOTEBOOK_TYPE}/{object_id}",
            allowed_status=(404,),
        )
    dashboards.post(
        "/api/opensearch-dashboards/settings",
        {
            "changes": {
                "defaultIndex": saved_objects.DEFAULT_INDEX_PATTERN,
                # Dashboard controls filter fields that only some panels contain.
                "courier:ignoreFilterIfFieldNotInIndex": True,
            }
        },
    )
    LOG.info("Imported %d OpenSearch Dashboards saved objects into the Global tenant", result.get("successCount", 0))
    return 0
