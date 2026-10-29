"""Translate the repository's Grafana alert rules into OpenSearch equivalents.

The Grafana provisioning files under grafana/alerting/ stay the single source of
truth. opensearch-bridge evaluates each rule's PromQL condition against
Prometheus every interval and records the state in litellm-alert-state-*.
opensearch-setup creates one OpenSearch Alerting monitor per rule that fires
only when the condition held for the rule's complete "for" duration.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

MONITOR_PREFIX = "[LiteLLM] "
ALERT_STATE_PATTERN = "litellm-alert-state-*"
DURATION = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
SEVERITY = {"critical": "1", "high": "2", "warning": "3", "info": "4"}


@dataclass(frozen=True)
class AlertRule:
    uid: str
    title: str
    expr: str
    for_seconds: int
    no_data_state: str
    exec_err_state: str
    severity: str
    summary: str
    description: str
    labels: dict[str, str] = field(default_factory=dict)


def parse_duration(value: Any) -> int:
    text = str(value or "0s").strip()
    if text in ("", "0"):
        return 0
    match = DURATION.fullmatch(text)
    if not match or not any(match.groups()):
        raise ValueError(f"unsupported Grafana duration: {text}")
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def rules_from_document(document: dict[str, Any]) -> list[AlertRule]:
    rules: list[AlertRule] = []
    for group in document.get("groups", []) or []:
        for rule in group.get("rules", []) or []:
            condition = rule.get("condition")
            query = next(
                (item for item in rule.get("data", []) or [] if item.get("refId") == condition),
                None,
            )
            model = (query or {}).get("model", {})
            expr = model.get("expr")
            datasource = (model.get("datasource") or {}).get("type")
            if not expr or datasource != "prometheus":
                raise ValueError(f"alert rule {rule.get('uid')} is not a single Prometheus condition")
            labels = {str(key): str(value) for key, value in (rule.get("labels") or {}).items()}
            annotations = rule.get("annotations") or {}
            rules.append(
                AlertRule(
                    uid=str(rule["uid"]),
                    title=str(rule["title"]),
                    expr=str(expr),
                    for_seconds=parse_duration(rule.get("for")),
                    no_data_state=str(rule.get("noDataState", "NoData")),
                    exec_err_state=str(rule.get("execErrState", "Error")),
                    severity=labels.get("severity", "warning"),
                    summary=str(annotations.get("summary", rule["title"])),
                    description=str(annotations.get("description", "")),
                    labels=labels,
                )
            )
    return rules


def load_rules(directory: Path) -> list[AlertRule]:
    import yaml  # Imported lazily so hermetic unit tests do not require PyYAML.

    rules: list[AlertRule] = []
    for path in sorted(directory.glob("*.y*ml")):
        with path.open(encoding="utf-8") as source:
            rules.extend(rules_from_document(yaml.safe_load(source) or {}))
    seen: set[str] = set()
    for rule in rules:
        if rule.uid in seen:
            raise ValueError(f"duplicate alert rule uid: {rule.uid}")
        seen.add(rule.uid)
    return rules


def series_label(metric: dict[str, str]) -> str:
    return ",".join(f"{key}={metric[key]}" for key in sorted(metric) if key != "__name__") or "{}"


def evaluate(rule: AlertRule, result: list[dict[str, Any]] | None, error: str | None) -> dict[str, Any]:
    """Return the alert-state document fields for one evaluation."""
    if error is not None:
        state = "error"
        met = rule.exec_err_state in ("Alerting", "Error")
        active: list[str] = []
    elif not result:
        state = "nodata"
        met = rule.no_data_state == "Alerting"
        active = []
    else:
        active = []
        for sample in result:
            try:
                value = float(sample["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if not math.isnan(value) and value != 0:
                active.append(series_label(sample.get("metric", {})))
        state = "firing" if active else "normal"
        met = bool(active)
    return {
        "rule_uid": rule.uid,
        "title": rule.title,
        "severity": rule.severity,
        "state": state,
        "condition_met": 1 if met else 0,
        "active_series": active[:50],
        "active_series_count": len(active),
        "summary": rule.summary,
        "error": error[:500] if error else None,
    }


def required_samples(for_seconds: int, interval_seconds: int) -> tuple[int, int]:
    """Return the look-back window and minimum consecutive samples for a rule."""
    window = max(for_seconds, 2 * interval_seconds)
    if for_seconds <= 0:
        return window, 1
    # Tolerate one missed evaluation caused by scheduling jitter.
    return window, max(1, for_seconds // interval_seconds - 1)


def monitor_for_rule(rule: AlertRule, interval_seconds: int, channel_id: str | None) -> dict[str, Any]:
    window, minimum = required_samples(rule.for_seconds, interval_seconds)
    condition = (
        f"ctx.results[0].aggregations.samples.value >= {minimum} && "
        "ctx.results[0].aggregations.min_met.value != null && "
        "ctx.results[0].aggregations.min_met.value >= 1"
    )
    query = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"rule_uid": rule.uid}},
                    {
                        "range": {
                            "@timestamp": {
                                "gte": "{{period_end}}||-" + f"{window}s",
                                "lte": "{{period_end}}",
                                "format": "epoch_millis",
                            }
                        }
                    },
                ]
            }
        },
        "aggs": {
            "samples": {"value_count": {"field": "condition_met"}},
            "min_met": {"min": {"field": "condition_met"}},
            "latest": {
                "top_hits": {
                    "size": 1,
                    "sort": [{"@timestamp": {"order": "desc"}}],
                    "_source": ["state", "active_series", "active_series_count", "summary"],
                }
            },
        },
    }
    message = (
        f"{rule.summary}\n\n{rule.description}\n\n"
        "Monitor: {{ctx.monitor.name}}\n"
        "Period end: {{ctx.periodEnd}}\n"
        "Active series: {{#ctx.results.0.aggregations.latest.hits.hits.0._source.active_series}}"
        "{{.}}; {{/ctx.results.0.aggregations.latest.hits.hits.0._source.active_series}}\n"
        f"Rule UID: {rule.uid}"
    )
    return _monitor(
        name=MONITOR_PREFIX + rule.title,
        indices=[ALERT_STATE_PATTERN],
        query=query,
        trigger_name=rule.title,
        severity=SEVERITY.get(rule.severity, "3"),
        condition=condition,
        message=message,
        channel_id=channel_id,
    )


def staleness_monitors(channel_id: str | None) -> list[dict[str, Any]]:
    """Fail loudly when the pipelines that feed the monitors stop delivering."""
    specs = (
        (
            "OpenSearch bridge is not evaluating alert rules",
            ALERT_STATE_PATTERN,
            "@timestamp",
            "5m",
            "No alert-rule evaluation has reached OpenSearch for five minutes. "
            "Check opensearch-bridge and Prometheus.",
        ),
        (
            "Prometheus metrics are not reaching OpenSearch",
            "litellm-metrics-*",
            "@timestamp",
            "10m",
            "No metric documents have reached OpenSearch for ten minutes. "
            "Check opensearch-bridge and Prometheus.",
        ),
        (
            "Container logs are not reaching OpenSearch",
            "litellm-logs-*",
            "@timestamp",
            "15m",
            "No container log line has reached OpenSearch for fifteen minutes. "
            "Check alloy and data-prepper.",
        ),
        (
            # LiteLLM emits health and database spans continuously while running.
            "LiteLLM traces are not reaching OpenSearch",
            "otel-v1-apm-span-*",
            "startTime",
            "15m",
            "No LiteLLM span has reached OpenSearch for fifteen minutes although spans arrived "
            "within the last day. Check the LiteLLM proxies and data-prepper.",
        ),
    )
    monitors = []
    for title, pattern, time_field, window, description in specs:
        # Fire only when the pipeline delivered within the last day but not in
        # the window, so a freshly started stack does not alert before data.
        query = {
            "size": 0,
            "query": {
                "range": {
                    time_field: {
                        "gte": "{{period_end}}||-24h",
                        "lte": "{{period_end}}",
                        "format": "epoch_millis",
                    }
                }
            },
            "aggs": {
                "recent": {
                    "filter": {
                        "range": {
                            time_field: {
                                "gte": "{{period_end}}||-" + window,
                                "lte": "{{period_end}}",
                                "format": "epoch_millis",
                            }
                        }
                    }
                }
            },
        }
        monitors.append(
            _monitor(
                name=MONITOR_PREFIX + title,
                indices=[pattern],
                query=query,
                trigger_name=title,
                severity="1",
                condition=(
                    "ctx.results[0].hits.total.value > 0 && "
                    "ctx.results[0].aggregations.recent.doc_count == 0"
                ),
                message=description + "\n\nMonitor: {{ctx.monitor.name}}\nPeriod end: {{ctx.periodEnd}}",
                channel_id=channel_id,
            )
        )
    return monitors


def _monitor(
    *,
    name: str,
    indices: list[str],
    query: dict[str, Any],
    trigger_name: str,
    severity: str,
    condition: str,
    message: str,
    channel_id: str | None,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    if channel_id:
        actions.append(
            {
                "name": "Notify operators",
                "destination_id": channel_id,
                "subject_template": {"source": "{{ctx.monitor.name}}", "lang": "mustache"},
                "message_template": {"source": message, "lang": "mustache"},
                "throttle_enabled": True,
                "throttle": {"value": 60, "unit": "MINUTES"},
            }
        )
    return {
        "type": "monitor",
        "monitor_type": "query_level_monitor",
        "name": name,
        "enabled": True,
        "schedule": {"period": {"interval": 1, "unit": "MINUTES"}},
        "inputs": [{"search": {"indices": indices, "query": query}}],
        "triggers": [
            {
                "query_level_trigger": {
                    "name": trigger_name,
                    "severity": severity,
                    "condition": {"script": {"source": condition, "lang": "painless"}},
                    "actions": actions,
                }
            }
        ],
    }


def monitor_names(monitors: Iterable[dict[str, Any]]) -> set[str]:
    return {monitor["name"] for monitor in monitors}
