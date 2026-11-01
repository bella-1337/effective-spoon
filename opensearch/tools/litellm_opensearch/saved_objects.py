"""OpenSearch Dashboards saved objects that mirror the Grafana dashboards.

Objects are generated rather than stored as NDJSON so their structure, stable
IDs, and references are validated by unit tests and stay consistent with the
documents written by opensearch-bridge and Data Prepper.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from .notebooks import NOTEBOOK_IDS

DEFAULT_INDEX_PATTERN = "litellm-ip-logs"
MIGRATION = {"visualization": "7.10.0", "index-pattern": "7.6.0", "search": "7.9.3", "dashboard": "7.9.3"}

INDEX_PATTERNS = {
    "litellm-ip-logs": ("litellm-logs-*", "@timestamp"),
    "litellm-ip-metrics": ("litellm-metrics-*", "@timestamp"),
    "litellm-ip-alert-state": ("litellm-alert-state-*", "@timestamp"),
    "litellm-ip-spend": ("litellm-spend-*", "@timestamp"),
    "litellm-ip-provider-errors": ("litellm-provider-errors-*", "@timestamp"),
    "litellm-ip-audit": ("litellm-audit-*", "@timestamp"),
    "litellm-ip-entities": ("litellm-entities", None),
    "litellm-ip-security-audit": ("security-auditlog-*", "@timestamp"),
    "litellm-ip-traces": ("otel-v1-apm-span-*", "startTime"),
}
METRICS = "litellm-ip-metrics"
LOGS = "litellm-ip-logs"
SPEND = "litellm-ip-spend"
ERRORS = "litellm-ip-provider-errors"
ENTITIES = "litellm-ip-entities"
AUDIT = "litellm-ip-audit"
ALERTS = "litellm-ip-alert-state"
SECURITY_AUDIT = "litellm-ip-security-audit"
TRACES = "litellm-ip-traces"

SERVICE = "log.attributes.compose_service"
STREAM = "log.attributes.stream"
LEVEL = "severityText"
ATTR = "span.attributes."

Metric = tuple[str, "str | None", str]


def _roi_number(name: str, default: float, *, maximum: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be numeric") from error
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be between 0 and {maximum:g}")
    return value


def roi_config() -> tuple[float, float, float, float, str]:
    request_minutes = _roi_number("ROI_MINUTES_SAVED_PER_REQUEST", 15, maximum=1440)
    session_minutes = _roi_number("ROI_MINUTES_SAVED_PER_SESSION", 0, maximum=10080)
    hourly_cost = _roi_number("ROI_LOADED_HOURLY_COST", 75, maximum=10000)
    realization = _roi_number("ROI_VALUE_REALIZATION_FACTOR", 0.25, maximum=1)
    currency = os.environ.get("ROI_CURRENCY", "USD")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("ROI_CURRENCY must be a three-letter uppercase ISO 4217 code")
    return request_minutes, session_minutes, hourly_cost, realization, currency


def expression_query(*names: str) -> str:
    """KQL for bridge expression documents; raw series can share a metric name."""
    joined = " or ".join(f'metric:"{name}"' for name in names)
    return f"kind:expression and ({joined})"


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _search_source(index_id: str | None, query: str = "") -> tuple[dict[str, str], list[dict[str, str]]]:
    source: dict[str, Any] = {"query": {"query": query, "language": "kuery"}, "filter": []}
    references: list[dict[str, str]] = []
    if index_id:
        source["indexRefName"] = "kibanaSavedObjectMeta.searchSourceJSON.index"
        references.append(
            {"name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern", "id": index_id}
        )
    return {"searchSourceJSON": _json(source)}, references


def _metric_style(font_size: int) -> dict[str, Any]:
    return {
        "addTooltip": True,
        "addLegend": False,
        "type": "metric",
        "metric": {
            "percentageMode": False,
            "useRanges": False,
            "colorSchema": "Green to Red",
            "metricColorMode": "None",
            "colorsRange": [{"from": 0, "to": 10000}],
            "labels": {"show": True},
            "invertColors": False,
            "style": {"bgFill": "#000", "bgColor": False, "labelColor": False, "subText": "", "fontSize": font_size},
        },
    }


def _latest_agg(field: str = "value", label: str = "Latest") -> dict[str, Any]:
    return {
        "type": "top_hits",
        "schema": "metric",
        "params": {
            "field": field,
            "aggregate": "concat",
            "size": 1,
            "sortField": "@timestamp",
            "sortOrder": "desc",
            "customLabel": label,
        },
    }


@dataclass(frozen=True)
class Panel:
    object_id: str
    object_type: str
    width: int = 24
    height: int = 12


class Builder:
    def __init__(self) -> None:
        self.objects: list[dict[str, Any]] = []
        self.ids: set[str] = set()

    def _add(self, item: dict[str, Any]) -> str:
        if item["id"] in self.ids:
            raise ValueError(f"duplicate saved object id {item['id']}")
        self.ids.add(item["id"])
        item["migrationVersion"] = {item["type"]: MIGRATION[item["type"]]}
        self.objects.append(item)
        return item["id"]

    # ---------------------------------------------------------------- basics
    def index_patterns(self) -> None:
        for object_id, (title, time_field) in INDEX_PATTERNS.items():
            attributes: dict[str, Any] = {"title": title}
            if time_field:
                attributes["timeFieldName"] = time_field
            self._add({"type": "index-pattern", "id": object_id, "attributes": attributes, "references": []})

    def visualization(
        self,
        object_id: str,
        title: str,
        vis_type: str,
        aggs: list[dict[str, Any]],
        params: dict[str, Any],
        *,
        index_id: str | None = None,
        query: str = "",
        extra_references: list[dict[str, str]] | None = None,
        width: int = 24,
        height: int = 12,
    ) -> Panel:
        for number, agg in enumerate(aggs, 1):
            agg.setdefault("id", str(number))
            agg.setdefault("enabled", True)
        meta, references = _search_source(index_id, query)
        self._add(
            {
                "type": "visualization",
                "id": object_id,
                "attributes": {
                    "title": title,
                    "description": "",
                    "version": 1,
                    "uiStateJSON": "{}",
                    "visState": _json({"title": title, "type": vis_type, "aggs": aggs, "params": params}),
                    "kibanaSavedObjectMeta": meta,
                },
                "references": references + (extra_references or []),
            }
        )
        return Panel(object_id, "visualization", width, height)

    def search(
        self,
        object_id: str,
        title: str,
        index_id: str,
        columns: list[str],
        *,
        query: str = "",
        sort: list[list[str]] | None = None,
    ) -> Panel:
        meta, references = _search_source(index_id, query)
        self._add(
            {
                "type": "search",
                "id": object_id,
                "attributes": {
                    "title": title,
                    "description": "",
                    "columns": columns,
                    "sort": sort or [],
                    "version": 1,
                    "kibanaSavedObjectMeta": meta,
                },
                "references": references,
            }
        )
        return Panel(object_id, "search", 48, 15)

    def dashboard(
        self,
        object_id: str,
        title: str,
        description: str,
        panels: list[Panel],
        *,
        time_from: str = "now-24h",
        refresh_ms: int = 60000,
    ) -> str:
        panel_specs = []
        references = []
        x = y = row_height = 0
        for number, panel in enumerate(panels):
            if x + panel.width > 48:
                x, y, row_height = 0, y + row_height, 0
            panel_index = str(number + 1)
            panel_specs.append(
                {
                    "version": "7.10.2",
                    "gridData": {"x": x, "y": y, "w": panel.width, "h": panel.height, "i": panel_index},
                    "panelIndex": panel_index,
                    "embeddableConfig": {},
                    "panelRefName": f"panel_{number}",
                }
            )
            references.append({"name": f"panel_{number}", "type": panel.object_type, "id": panel.object_id})
            x += panel.width
            row_height = max(row_height, panel.height)
        meta, _ = _search_source(None)
        return self._add(
            {
                "type": "dashboard",
                "id": object_id,
                "attributes": {
                    "title": title,
                    "description": description,
                    "hits": 0,
                    "version": 1,
                    "panelsJSON": _json(panel_specs),
                    "optionsJSON": _json({"hidePanelTitles": False, "useMargins": True}),
                    "timeRestore": True,
                    "timeFrom": time_from,
                    "timeTo": "now",
                    "refreshInterval": {"pause": False, "value": refresh_ms},
                    "kibanaSavedObjectMeta": meta,
                },
                "references": references,
            }
        )

    # ----------------------------------------------------- visualization kinds
    @staticmethod
    def _metric_agg(agg_type: str, field: str | None, label: str) -> dict[str, Any]:
        params: dict[str, Any] = {"customLabel": label}
        if field:
            params["field"] = field
        if agg_type == "percentiles":
            # "percentiles:95" style shorthand keeps metric tuples compact.
            params["percents"] = [95]
        elif agg_type.startswith("percentiles:"):
            params["percents"] = [float(value) for value in agg_type.split(":", 1)[1].split(",")]
            agg_type = "percentiles"
        return {"type": agg_type, "schema": "metric", "params": params}

    @staticmethod
    def _date_histogram(interval: str = "auto", time_field: str = "@timestamp") -> dict[str, Any]:
        return {
            "type": "date_histogram",
            "schema": "segment",
            "params": {
                "field": time_field,
                "useNormalizedOpenSearchInterval": True,
                "scaleMetricValues": False,
                "interval": interval,
                "drop_partials": False,
                "min_doc_count": 1,
                "extended_bounds": {},
            },
        }

    @staticmethod
    def _terms(field: str, schema: str, size: int, order_by: str = "1", label: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {
            "field": field,
            "size": size,
            "order": "asc" if order_by == "_key" else "desc",
            "orderBy": order_by,
            "otherBucket": False,
            "missingBucket": False,
            "customLabel": label,
        }
        if order_by == "_count":
            # Multi-value metrics such as percentiles cannot order buckets.
            params["orderBy"] = "custom"
            params["orderAgg"] = {"id": "order", "enabled": True, "type": "count", "schema": "orderAgg", "params": {}}
        return {"type": "terms", "schema": schema, "params": params}

    def xy(
        self,
        object_id: str,
        title: str,
        index_id: str,
        metrics: list[Metric],
        *,
        query: str = "",
        split: str | None = None,
        split_size: int = 15,
        chart: str = "line",
        interval: str = "auto",
        stacked: bool = False,
        y_label: str = "",
        width: int = 24,
        time_field: str = "@timestamp",
    ) -> Panel:
        if chart not in ("line", "area", "histogram"):
            raise ValueError(f"unsupported chart type {chart}")
        aggs = [self._metric_agg(*metric) for metric in metrics]
        aggs.append(self._date_histogram(interval, time_field))
        if split:
            order_by = "_count" if aggs[0]["type"] == "percentiles" else "1"
            aggs.append(self._terms(split, "group", split_size, order_by))
        series = [
            {
                "show": True,
                "type": chart,
                "mode": "stacked" if stacked else "normal",
                "data": {"label": label, "id": str(number)},
                "valueAxis": "ValueAxis-1",
                "drawLinesBetweenPoints": True,
                "lineWidth": 2,
                "interpolate": "linear",
                "showCircles": chart == "histogram",
            }
            for number, (_, _, label) in enumerate(metrics, 1)
        ]
        params = {
            "type": chart,
            "grid": {"categoryLines": False},
            "categoryAxes": [
                {
                    "id": "CategoryAxis-1",
                    "type": "category",
                    "position": "bottom",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear"},
                    "labels": {"show": True, "filter": True, "truncate": 100},
                    "title": {},
                }
            ],
            "valueAxes": [
                {
                    "id": "ValueAxis-1",
                    "name": "LeftAxis-1",
                    "type": "value",
                    "position": "left",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear", "mode": "normal"},
                    "labels": {"show": True, "rotate": 0, "filter": False, "truncate": 100},
                    "title": {"text": y_label},
                }
            ],
            "seriesParams": series,
            "addTooltip": True,
            "addLegend": True,
            "legendPosition": "bottom",
            "times": [],
            "addTimeMarker": False,
            "labels": {"show": False},
            "thresholdLine": {"show": False, "value": 0, "width": 1, "style": "full", "color": "#E7664C"},
        }
        return self.visualization(object_id, title, chart, aggs, params, index_id=index_id, query=query, width=width)

    def metric_series(
        self,
        object_id: str,
        title: str,
        metric: str,
        *,
        split: str | None = None,
        y_label: str = "",
        width: int = 24,
        chart: str = "line",
        stacked: bool = False,
        split_size: int = 15,
    ) -> Panel:
        """Chart one or more bridge metrics (space-separated names) split by series."""
        names = metric.split()
        query = expression_query(*names)
        # Legends stay readable when a single metric is split by labels only.
        split = split or ("metric_series" if len(names) > 1 else "series")
        return self.xy(
            object_id,
            title,
            METRICS,
            [("avg", "value", y_label or "Value")],
            query=query,
            split=split,
            split_size=split_size,
            y_label=y_label,
            width=width,
            chart=chart,
            stacked=stacked,
        )

    def stat(
        self,
        object_id: str,
        title: str,
        index_id: str,
        metrics: list[Metric],
        *,
        query: str = "",
        width: int = 8,
        height: int = 6,
    ) -> Panel:
        aggs = [self._metric_agg(*metric) for metric in metrics]
        return self.visualization(
            object_id, title, "metric", aggs, _metric_style(36), index_id=index_id, query=query, width=width, height=height
        )

    def latest(
        self,
        object_id: str,
        title: str,
        metric: str,
        *,
        group: str | None = None,
        width: int = 12,
        height: int = 6,
    ) -> Panel:
        """Most recent bridge value of a metric, optionally one tile per label."""
        aggs = [_latest_agg(label=title)]
        if group:
            aggs.append(self._terms(group, "group", 30, "_key"))
        return self.visualization(
            object_id,
            title,
            "metric",
            aggs,
            _metric_style(28),
            index_id=METRICS,
            query=expression_query(metric),
            width=width,
            height=height,
        )

    def pie(self, object_id: str, title: str, index_id: str, metric: Metric, field: str, *, query: str = "", size: int = 10) -> Panel:
        aggs = [self._metric_agg(*metric), self._terms(field, "segment", size)]
        params = {
            "type": "pie",
            "addTooltip": True,
            "addLegend": True,
            "legendPosition": "right",
            "isDonut": True,
            "labels": {"show": False, "values": True, "last_level": True, "truncate": 100},
        }
        return self.visualization(object_id, title, "pie", aggs, params, index_id=index_id, query=query)

    def hbar(self, object_id: str, title: str, index_id: str, metric: Metric, field: str, *, query: str = "", size: int = 10) -> Panel:
        aggs = [self._metric_agg(*metric), self._terms(field, "segment", size)]
        label = metric[2]
        params = {
            "type": "histogram",
            "grid": {"categoryLines": False},
            "categoryAxes": [
                {
                    "id": "CategoryAxis-1",
                    "type": "category",
                    "position": "left",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear"},
                    "labels": {"show": True, "rotate": 0, "filter": False, "truncate": 200},
                    "title": {},
                }
            ],
            "valueAxes": [
                {
                    "id": "ValueAxis-1",
                    "name": "BottomAxis-1",
                    "type": "value",
                    "position": "bottom",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear", "mode": "normal"},
                    "labels": {"show": True, "rotate": 75, "filter": True, "truncate": 100},
                    "title": {"text": label},
                }
            ],
            "seriesParams": [
                {
                    "show": True,
                    "type": "histogram",
                    "mode": "normal",
                    "data": {"label": label, "id": "1"},
                    "valueAxis": "ValueAxis-1",
                    "drawLinesBetweenPoints": True,
                    "lineWidth": 2,
                    "showCircles": True,
                }
            ],
            "addTooltip": True,
            "addLegend": False,
            "legendPosition": "right",
            "times": [],
            "addTimeMarker": False,
            "labels": {"show": True},
            "thresholdLine": {"show": False, "value": 0, "width": 1, "style": "full", "color": "#E7664C"},
        }
        return self.visualization(object_id, title, "horizontal_bar", aggs, params, index_id=index_id, query=query)

    def table(
        self,
        object_id: str,
        title: str,
        index_id: str,
        metrics: list[dict[str, Any]],
        buckets: list[tuple[str, int, str]],
        *,
        query: str = "",
        order_by: str = "1",
        width: int = 24,
    ) -> Panel:
        aggs = list(metrics)
        for field, size, label in buckets:
            aggs.append(self._terms(field, "bucket", size, order_by, label))
        params = {
            "perPage": 20,
            "showPartialRows": False,
            "showMetricsAtAllLevels": False,
            "sort": {"columnIndex": None, "direction": None},
            "showTotal": False,
            "totalFunc": "sum",
            "percentageCol": "",
        }
        return self.visualization(
            object_id, title, "table", aggs, params, index_id=index_id, query=query, width=width, height=14
        )

    def controls(self, object_id: str, title: str, controls: list[tuple[str, str, str]], *, size: int = 50) -> Panel:
        specs = []
        references = []
        for number, (index_id, field, label) in enumerate(controls):
            ref = f"control_{number}_index_pattern"
            specs.append(
                {
                    "id": str(number + 1),
                    "fieldName": field,
                    "label": label,
                    "type": "list",
                    "options": {
                        "type": "terms",
                        "multiselect": True,
                        "dynamicOptions": True,
                        "size": size,
                        "order": "desc",
                    },
                    "parent": "",
                    "indexPatternRefName": ref,
                }
            )
            references.append({"name": ref, "type": "index-pattern", "id": index_id})
        params = {"controls": specs, "updateFiltersOnChange": True, "useTimeFilter": True, "pinFilters": False}
        return self.visualization(
            object_id, title, "input_control_vis", [], params, extra_references=references, width=48, height=5
        )

    def markdown(self, object_id: str, title: str, text: str, *, width: int = 48, height: int = 8) -> Panel:
        return self.visualization(
            object_id,
            title,
            "markdown",
            [],
            {"markdown": text, "fontSize": 12, "openLinksInNewTab": False},
            width=width,
            height=height,
        )

    def vega(self, object_id: str, title: str, spec: dict[str, Any], *, width: int = 48, height: int = 10) -> Panel:
        return self.visualization(
            object_id,
            title,
            "vega",
            [],
            {"spec": json.dumps(spec, indent=2), "enableExternalUrls": False},
            width=width,
            height=height,
        )


DASHBOARD_LINKS = (
    ("litellm-dash-management", "LiteLLM Management Summary", "Executive usage, governance, reliability, and directional ROI"),
    ("litellm-dash-stack", "LiteLLM Stack (dev/prod)", "Requests, latency, failures, spend, deployments, guardrails, cache"),
    ("litellm-dash-cost", "LiteLLM Cost Attribution", "Spend, tokens, and expensive requests by model and key"),
    ("litellm-dash-admin", "LiteLLM Admin Overview", "Users, keys, teams, budgets, provider errors, audit changes"),
    ("litellm-dash-team", "LiteLLM Team Costing", "Per-team spend, members, models, and requests"),
    ("litellm-dash-logs", "Container Logs", "Every Compose service's stdout and stderr, with severity"),
    ("litellm-dash-access", "Traefik Access Logs", "Every HTTPS request: route, status, latency, path"),
    ("litellm-dash-traces", "LiteLLM Request Traces", "Request, model, guardrail, and database spans"),
    ("litellm-dash-host", "Infrastructure: Host & Containers", "CPU, memory, disk, network, and per-service containers"),
    ("litellm-dash-postgres", "Infrastructure: PostgreSQL", "LiteLLM PostgreSQL and pgvector health"),
    ("litellm-dash-edge", "Infrastructure: Traefik, Probes & Targets", "Ingress traffic, TLS expiry, probes, scrape targets"),
    ("litellm-dash-security", "Security, Backups & Alerts", "Image vulnerabilities, backup freshness, alert-rule state"),
    ("litellm-dash-explorer", "Metric Explorer", "Any mirrored Prometheus metric by name"),
    ("litellm-dash-audit", "OpenSearch Security Audit", "Logins, privilege denials, and security changes"),
)

NOTEBOOK_LINKS = (
    (NOTEBOOK_IDS["first-response"], "01 - Stack First Response", "UI, ingress, target, service, and restart triage"),
    (NOTEBOOK_IDS["requests"], "02 - LiteLLM Requests and Integrations", "Failures, latency, routing, guardrails, search, and MCP"),
    (NOTEBOOK_IDS["observability"], "03 - Missing Logs, Metrics, Traces, or Alerts", "End-to-end telemetry ingestion checks"),
    (NOTEBOOK_IDS["data-recovery"], "04 - Data, Cost, Backup, and Upgrade Recovery", "Database, spend, backup, and rollback checks"),
    (NOTEBOOK_IDS["access"], "05 - Access, Authentication, and Audit", "401/403, role, tenant, and privilege-denial checks"),
)


def _overview(b: Builder) -> None:
    links = "\n".join(f"- [{title}](#/view/{object_id}) — {text}" for object_id, title, text in DASHBOARD_LINKS)
    notebook_links = "\n".join(
        f"- [{title}](/app/observability-notebooks#/{object_id}?view=view_both) — {text}"
        for object_id, title, text in NOTEBOOK_LINKS
    )
    b.dashboard(
        "litellm-dash-overview",
        "LiteLLM Overview",
        "Entry point for the LiteLLM stack dashboards",
        [
            b.markdown(
                "litellm-vis-overview-links",
                "LiteLLM observability",
                "### LiteLLM stack dashboards\n\n"
                + links
                + "\n\n### Troubleshooting notebooks\n\n"
                + notebook_links
                + "\n\nAd hoc PromQL over every Prometheus series is available under **Observability → Metrics** "
                "and PPL through the `prometheus` data source. Monitors are under **Alerting**; notification "
                "channels under **Notifications**. Container logs can also be explored with PPL in "
                "**Observability → Logs** (`source = litellm-logs-*`), and request traces and the "
                "service map in **Observability → Trace Analytics**.",
                height=16,
            ),
            b.latest("litellm-vis-overview-up", "Scrape targets up", "target_up", group="labels.job", width=48, height=10),
        ],
        time_from="now-1h",
    )


def _management(b: Builder) -> None:
    request_minutes, session_minutes, hourly_cost, realization, currency = roi_config()
    roi_spec = {
        "$schema": "https://vega.github.io/schema/vega/v5.json",
        "description": "Directional management value estimate from successful LiteLLM requests and direct model spend.",
        "autosize": {"type": "fit", "contains": "padding"},
        "padding": 10,
        "data": [
            {
                "name": "summary",
                "url": {
                    "%context%": True,
                    "%timefield%": "@timestamp",
                    "index": "litellm-spend-*",
                    "body": {
                        "size": 0,
                        "aggs": {
                            "summary": {
                                "filters": {"filters": [{"match_all": {}}]},
                                "aggs": {
                                    "successful": {
                                        "filter": {"term": {"status": "success"}},
                                        "aggs": {
                                            "sessions": {
                                                "cardinality": {
                                                    "field": "session_hash",
                                                    "precision_threshold": 40000,
                                                }
                                            }
                                        },
                                    },
                                    "spend": {"sum": {"field": "spend"}},
                                },
                            }
                        },
                    },
                },
                "format": {"property": "aggregations.summary.buckets"},
                "transform": [
                    {
                        "type": "formula",
                        "as": "request_hours",
                        "expr": f"datum.successful.doc_count * {request_minutes} / 60 * {realization}",
                    },
                    {
                        "type": "formula",
                        "as": "session_hours",
                        "expr": f"datum.successful.sessions.value * {session_minutes} / 60 * {realization}",
                    },
                    {"type": "formula", "as": "hours", "expr": "datum.request_hours + datum.session_hours"},
                    {"type": "formula", "as": "gross", "expr": f"datum.hours * {hourly_cost}"},
                    {"type": "formula", "as": "net", "expr": "datum.gross - datum.spend.value"},
                    {
                        "type": "formula",
                        "as": "roi",
                        "expr": "datum.spend.value > 0 ? datum.net / datum.spend.value * 100 : 0",
                    },
                ],
            }
        ],
        "scales": [{"name": "cards", "type": "band", "domain": ["hours", "gross", "net", "roi"], "range": "width", "padding": 0.08}],
        "marks": [
            {
                "type": "rect",
                "from": {"data": "summary"},
                "encode": {
                    "enter": {
                        "x": {"signal": "scale('cards', 'hours')"},
                        "width": {"signal": "bandwidth('cards')"},
                        "y": {"value": 0},
                        "height": {"signal": "height"},
                        "fill": {"value": "#1f2937"},
                        "cornerRadius": {"value": 6},
                    }
                },
            },
            {
                "type": "rect",
                "from": {"data": "summary"},
                "encode": {
                    "enter": {
                        "x": {"signal": "scale('cards', 'gross')"},
                        "width": {"signal": "bandwidth('cards')"},
                        "y": {"value": 0},
                        "height": {"signal": "height"},
                        "fill": {"value": "#1f2937"},
                        "cornerRadius": {"value": 6},
                    }
                },
            },
            {
                "type": "rect",
                "from": {"data": "summary"},
                "encode": {
                    "enter": {
                        "x": {"signal": "scale('cards', 'net')"},
                        "width": {"signal": "bandwidth('cards')"},
                        "y": {"value": 0},
                        "height": {"signal": "height"},
                        "fill": {"value": "#1f2937"},
                        "cornerRadius": {"value": 6},
                    }
                },
            },
            {
                "type": "rect",
                "from": {"data": "summary"},
                "encode": {
                    "enter": {
                        "x": {"signal": "scale('cards', 'roi')"},
                        "width": {"signal": "bandwidth('cards')"},
                        "y": {"value": 0},
                        "height": {"signal": "height"},
                        "fill": {"value": "#1f2937"},
                        "cornerRadius": {"value": 6},
                    }
                },
            },
            {
                "type": "text",
                "from": {"data": "summary"},
                "encode": {
                    "enter": {
                        "x": {"signal": "scale('cards', 'hours') + bandwidth('cards') / 2"},
                        "y": {"signal": "height * 0.68"},
                        "align": {"value": "center"},
                        "fill": {"value": "#f9fafb"},
                        "fontSize": {"value": 25},
                    },
                    "update": {"text": {"signal": "format(datum.hours, ',.1f')"}},
                },
            },
            {
                "type": "text",
                "from": {"data": "summary"},
                "encode": {
                    "enter": {
                        "x": {"signal": "scale('cards', 'gross') + bandwidth('cards') / 2"},
                        "y": {"signal": "height * 0.68"},
                        "align": {"value": "center"},
                        "fill": {"value": "#f9fafb"},
                        "fontSize": {"value": 25},
                    },
                    "update": {"text": {"signal": f"'{currency} ' + format(datum.gross, ',.0f')"}},
                },
            },
            {
                "type": "text",
                "from": {"data": "summary"},
                "encode": {
                    "enter": {
                        "x": {"signal": "scale('cards', 'net') + bandwidth('cards') / 2"},
                        "y": {"signal": "height * 0.68"},
                        "align": {"value": "center"},
                        "fill": {"value": "#f9fafb"},
                        "fontSize": {"value": 25},
                    },
                    "update": {"text": {"signal": f"'{currency} ' + format(datum.net, ',.0f')"}},
                },
            },
            {
                "type": "text",
                "from": {"data": "summary"},
                "encode": {
                    "enter": {
                        "x": {"signal": "scale('cards', 'roi') + bandwidth('cards') / 2"},
                        "y": {"signal": "height * 0.68"},
                        "align": {"value": "center"},
                        "fill": {"value": "#f9fafb"},
                        "fontSize": {"value": 25},
                    },
                    "update": {"text": {"signal": "format(datum.roi, ',.0f') + '%'"}},
                },
            },
            *[
                {
                    "type": "text",
                    "from": {"data": "summary"},
                    "encode": {
                        "enter": {
                            "x": {"signal": f"scale('cards', '{key}') + bandwidth('cards') / 2"},
                            "y": {"signal": "height * 0.30"},
                            "align": {"value": "center"},
                            "fill": {"value": "#9ca3af"},
                            "fontSize": {"value": 13},
                            "text": {"value": label},
                        }
                    },
                }
                for key, label in (
                    ("hours", "Estimated hours saved"),
                    ("gross", "Estimated gross value"),
                    ("net", "Estimated net value"),
                    ("roi", "Indicative ROI"),
                )
            ],
        ],
    }
    b.dashboard(
        "litellm-dash-management",
        "LiteLLM Management Summary",
        "Executive usage, cost, reliability, governance, and directional ROI indicators",
        [
            b.markdown(
                "litellm-vis-management-guidance",
                "How to read this dashboard",
                "### Management summary\n\n"
                "Usage and spend are copied from LiteLLM's system-of-record PostgreSQL tables. "
                f"**ROI is directional, not an accounting measure.** Current `.env` assumptions are "
                f"{request_minutes:g} minutes per successful request plus {session_minutes:g} minutes per "
                f"distinct successful session, a {realization:g} realization factor, and {currency} "
                f"{hourly_cost:g} loaded hourly cost, then direct model spend is subtracted. Validate these "
                "assumptions against local workflows before using the estimate in a business case. Set either "
                "request or session minutes to zero when those measures would represent the same saved work.",
                height=9,
            ),
            b.controls(
                "litellm-vis-management-controls",
                "Filters",
                [(SPEND, "environment", "Environment"), (SPEND, "team", "Team"), (SPEND, "model_name", "Model")],
            ),
            b.stat("litellm-vis-management-requests", "Requests", SPEND, [("count", None, "Requests")], width=8),
            b.stat(
                "litellm-vis-management-success",
                "Successful requests",
                SPEND,
                [("count", None, "Successful")],
                query="status:success",
                width=8,
            ),
            b.stat(
                "litellm-vis-management-spend",
                "Model spend",
                SPEND,
                [("sum", "spend", "Spend ($)")],
                width=8,
            ),
            b.stat(
                "litellm-vis-management-sessions",
                "Distinct successful sessions",
                SPEND,
                [("cardinality", "session_hash", "Sessions")],
                query="status:success and session_hash:*",
                width=8,
            ),
            b.stat(
                "litellm-vis-management-users",
                "Provisioned users",
                ENTITIES,
                [("count", None, "Users")],
                query="entity_type:user",
                width=8,
            ),
            b.stat(
                "litellm-vis-management-keys",
                "Usable virtual keys",
                ENTITIES,
                [("count", None, "Keys")],
                query="entity_type:key and usable:true",
                width=8,
            ),
            b.stat(
                "litellm-vis-management-teams",
                "Active teams",
                ENTITIES,
                [("count", None, "Teams")],
                query="entity_type:team and blocked:false",
                width=8,
            ),
            b.vega("litellm-vis-management-roi", "Directional ROI indication", roi_spec),
            b.stat(
                "litellm-vis-management-budgeted-keys",
                "Keys with budgets",
                ENTITIES,
                [("count", None, "Budgeted keys")],
                query="entity_type:key and max_budget:*",
                width=8,
            ),
            b.stat(
                "litellm-vis-management-budgeted-teams",
                "Teams with budgets",
                ENTITIES,
                [("count", None, "Budgeted teams")],
                query="entity_type:team and max_budget:*",
                width=8,
            ),
            b.stat(
                "litellm-vis-management-unusable-keys",
                "Blocked or expired keys",
                ENTITIES,
                [("count", None, "Unavailable keys")],
                query="entity_type:key and usable:false",
                width=8,
            ),
            b.stat(
                "litellm-vis-management-unassigned",
                "Unassigned spend",
                SPEND,
                [("sum", "spend", "Spend ($)")],
                query='team:"unassigned"',
                width=8,
            ),
            b.stat(
                "litellm-vis-management-errors",
                "Provider errors",
                ERRORS,
                [("count", None, "Errors")],
                width=8,
            ),
            b.stat(
                "litellm-vis-management-changes",
                "Administrative changes",
                AUDIT,
                [("count", None, "Changes")],
                width=8,
            ),
            b.xy(
                "litellm-vis-management-volume",
                "Daily request volume",
                SPEND,
                [("count", None, "Requests")],
                split="status",
                chart="histogram",
                interval="1d",
                stacked=True,
            ),
            b.xy(
                "litellm-vis-management-value-trend",
                "Daily model spend",
                SPEND,
                [("sum", "spend", "Spend ($)")],
                split="environment",
                interval="1d",
                y_label="$",
            ),
            b.hbar(
                "litellm-vis-management-team-spend",
                "Spend by team",
                SPEND,
                ("sum", "spend", "Spend ($)"),
                "team",
                size=15,
            ),
            b.hbar(
                "litellm-vis-management-model-spend",
                "Spend by model",
                SPEND,
                ("sum", "spend", "Spend ($)"),
                "model_name",
                size=15,
            ),
            b.search(
                "litellm-search-management-budgets",
                "Budget governance",
                ENTITIES,
                ["environment", "entity_type", "name", "owner", "team", "spend", "max_budget", "blocked", "usable"],
                query="entity_type:(team or key)",
                sort=[["spend", "desc"]],
            ),
            b.search(
                "litellm-search-management-audit",
                "Recent administrative changes",
                AUDIT,
                ["environment", "changed_by", "action", "object_type", "object_id"],
                sort=[["@timestamp", "desc"]],
            ),
        ],
        time_from="now-30d",
        refresh_ms=300000,
    )


def _stack(b: Builder) -> None:
    b.dashboard(
        "litellm-dash-stack",
        "LiteLLM Stack (dev/prod)",
        "Request, latency, failure, spend, deployment, and guardrail metrics (Prometheus via opensearch-bridge)",
        [
            b.controls("litellm-vis-stack-controls", "Filters", [(METRICS, "labels.job", "Job"), (METRICS, "labels.model", "Model")]),
            b.metric_series("litellm-vis-stack-rpm", "Requests / min", "litellm_requests_per_minute", y_label="req/min"),
            b.metric_series(
                "litellm-vis-stack-latency",
                "Latency p95/p99",
                "litellm_latency_p95_seconds litellm_latency_p99_seconds",
                y_label="seconds",
            ),
            b.metric_series("litellm-vis-stack-failed", "Failed requests", "litellm_failed_requests_per_second", y_label="req/s"),
            b.metric_series("litellm-vis-stack-spend", "Spend ($/h)", "litellm_spend_per_hour", y_label="$ / hour"),
            b.metric_series(
                "litellm-vis-stack-deployments",
                "Deployment health (success vs failure)",
                "litellm_deployment_success_per_second litellm_deployment_failure_per_second",
                y_label="responses/s",
            ),
            b.latest(
                "litellm-vis-stack-presidio",
                "Presidio guardrail requests (24h)",
                "litellm_presidio_guardrail_requests_24h",
                group="labels.job",
                width=24,
                height=12,
            ),
            b.metric_series("litellm-vis-stack-tokens", "Tokens / min (input vs output)", "litellm_tokens_per_minute", y_label="tokens/min"),
            b.metric_series("litellm-vis-stack-spend-model", "Spend ($/h) by model", "litellm_spend_per_hour_by_model", y_label="$ / hour"),
            b.metric_series("litellm-vis-stack-provider-latency", "Provider API latency p95 by model", "litellm_llm_api_latency_p95_seconds", y_label="seconds"),
            b.metric_series("litellm-vis-stack-ttft", "Time to first token p95 by model", "litellm_time_to_first_token_p95_seconds", y_label="seconds"),
            b.metric_series("litellm-vis-stack-overhead", "Proxy overhead p95", "litellm_overhead_latency_p95_seconds", y_label="seconds"),
            b.metric_series("litellm-vis-stack-proxy-status", "Proxy requests by status code", "litellm_proxy_requests_per_second", y_label="req/s"),
            b.metric_series("litellm-vis-stack-proxy-failures", "Proxy failures by exception class", "litellm_proxy_failures_per_second", y_label="req/s"),
            b.metric_series("litellm-vis-stack-deployment-state", "Deployment state (0 ok, 1 partial, 2 outage)", "litellm_deployment_state"),
            b.metric_series("litellm-vis-stack-guardrail-errors", "Guardrail errors", "litellm_guardrail_errors_per_second", y_label="errors/s"),
            b.metric_series("litellm-vis-stack-cache", "Cache hit ratio", "litellm_cache_hit_ratio", y_label="ratio"),
            b.metric_series("litellm-vis-stack-inflight", "In-flight requests", "litellm_in_flight_requests"),
        ],
        time_from="now-6h",
    )


def _cost(b: Builder) -> None:
    b.dashboard(
        "litellm-dash-cost",
        "LiteLLM Cost Attribution",
        "Model, key, token, and request-level cost from LiteLLM spend logs",
        [
            b.controls("litellm-vis-cost-controls", "Filters", [(SPEND, "environment", "Environment"), (SPEND, "model_name", "Model")]),
            b.stat("litellm-vis-cost-total", "Total spend (selected window)", SPEND, [("sum", "spend", "Spend ($)")], width=16),
            b.stat("litellm-vis-cost-requests", "Requests (selected window)", SPEND, [("count", None, "Requests")], width=16),
            b.stat("litellm-vis-cost-avg", "Avg cost / request", SPEND, [("avg", "spend", "$ / request")], width=16),
            b.xy(
                "litellm-vis-cost-daily-model",
                "Daily spend by model",
                SPEND,
                [("sum", "spend", "Spend ($)")],
                split="model_name",
                chart="histogram",
                interval="1d",
                stacked=True,
                y_label="$",
            ),
            b.xy(
                "litellm-vis-cost-daily-tokens",
                "Tokens per day (prompt vs completion)",
                SPEND,
                [("sum", "prompt_tokens", "Prompt tokens"), ("sum", "completion_tokens", "Completion tokens")],
                interval="1d",
                y_label="tokens",
            ),
            b.pie("litellm-vis-cost-share", "Spend share by model", SPEND, ("sum", "spend", "Spend ($)"), "model_name"),
            b.hbar("litellm-vis-cost-keys", "Spend by API key alias (top 10)", SPEND, ("sum", "spend", "Spend ($)"), "key_alias"),
            b.search(
                "litellm-search-cost-expensive",
                "Most expensive requests (selected window)",
                SPEND,
                ["environment", "request_id", "model_name", "spend", "total_tokens", "key_alias", "team"],
                sort=[["spend", "desc"]],
            ),
        ],
        time_from="now-30d",
        refresh_ms=300000,
    )


def _admin(b: Builder) -> None:
    b.dashboard(
        "litellm-dash-admin",
        "LiteLLM Admin Overview",
        "Users, keys, teams, budgets, spend, provider errors, and audit changes. Restrict to operators.",
        [
            b.controls("litellm-vis-admin-controls", "Filters", [(SPEND, "environment", "Environment")]),
            b.stat("litellm-vis-admin-users", "Users", ENTITIES, [("count", None, "Users")], query="entity_type:user"),
            b.stat(
                "litellm-vis-admin-keys",
                "Usable virtual keys",
                ENTITIES,
                [("count", None, "Keys")],
                query="entity_type:key and usable:true",
            ),
            b.stat("litellm-vis-admin-teams", "Teams", ENTITIES, [("count", None, "Teams")], query="entity_type:team and blocked:false"),
            b.stat("litellm-vis-admin-requests", "Requests", SPEND, [("count", None, "Requests")]),
            b.stat("litellm-vis-admin-errors", "Provider errors", ERRORS, [("count", None, "Errors")]),
            b.stat("litellm-vis-admin-spend", "Spend", SPEND, [("sum", "spend", "Spend ($)")]),
            b.xy("litellm-vis-admin-requests-ts", "Request volume", SPEND, [("count", None, "Requests")], interval="1h", chart="histogram"),
            b.xy(
                "litellm-vis-admin-errors-ts",
                "Provider error volume",
                ERRORS,
                [("count", None, "Errors")],
                interval="1h",
                chart="histogram",
                split="model_group",
            ),
            b.xy("litellm-vis-admin-team-spend", "Spend by team", SPEND, [("sum", "spend", "Spend ($)")], interval="1h", split="team"),
            b.hbar("litellm-vis-admin-user-spend", "Spend by user (top 15)", SPEND, ("sum", "spend", "Spend ($)"), "key_owner", size=15),
            b.search(
                "litellm-search-admin-keys",
                "Virtual key budget and status",
                ENTITIES,
                ["environment", "name", "owner", "team", "spend", "total_spend", "max_budget", "expires", "blocked", "usable"],
                query="entity_type:key",
                sort=[["spend", "desc"]],
            ),
            b.search(
                "litellm-search-admin-errors",
                "Recent provider errors",
                ERRORS,
                ["environment", "model_group", "provider_model", "exception_type", "status_code"],
                sort=[["@timestamp", "desc"]],
            ),
            b.search(
                "litellm-search-admin-users",
                "Users and budgets",
                ENTITIES,
                ["environment", "name", "user_role", "team_id", "spend", "max_budget", "budget_reset_at", "created_at"],
                query="entity_type:user",
                sort=[["spend", "desc"]],
            ),
            b.search(
                "litellm-search-admin-audit",
                "Recent administrative changes",
                AUDIT,
                ["environment", "changed_by", "action", "object_type", "object_id"],
                sort=[["@timestamp", "desc"]],
            ),
        ],
        time_from="now-7d",
        refresh_ms=300000,
    )


def _team(b: Builder) -> None:
    b.dashboard(
        "litellm-dash-team",
        "LiteLLM Team Costing",
        "Select a team in the filter panel. New teams appear automatically.",
        [
            b.controls("litellm-vis-team-controls", "Team", [(SPEND, "team", "Team"), (SPEND, "environment", "Environment")]),
            b.stat("litellm-vis-team-spend", "Team spend", SPEND, [("sum", "spend", "Spend ($)")], width=12),
            b.stat("litellm-vis-team-requests", "Team requests", SPEND, [("count", None, "Requests")], width=12),
            b.stat("litellm-vis-team-avg", "Average cost / request", SPEND, [("avg", "spend", "$ / request")], width=12),
            b.stat("litellm-vis-team-tokens", "Total tokens", SPEND, [("sum", "total_tokens", "Tokens")], width=12),
            b.xy(
                "litellm-vis-team-daily-model",
                "Daily spend by model",
                SPEND,
                [("sum", "spend", "Spend ($)")],
                split="model_name",
                chart="histogram",
                interval="1d",
                stacked=True,
            ),
            b.xy(
                "litellm-vis-team-daily-tokens",
                "Daily prompt and completion tokens",
                SPEND,
                [("sum", "prompt_tokens", "Prompt tokens"), ("sum", "completion_tokens", "Completion tokens")],
                interval="1d",
            ),
            b.hbar("litellm-vis-team-members", "Spend by team member", SPEND, ("sum", "spend", "Spend ($)"), "member", size=25),
            b.pie("litellm-vis-team-share", "Spend share by model", SPEND, ("sum", "spend", "Spend ($)"), "model_name"),
            b.search(
                "litellm-search-team-expensive",
                "Most expensive team requests",
                SPEND,
                ["environment", "team", "request_id", "model_name", "member", "spend", "total_tokens", "status"],
                sort=[["spend", "desc"]],
            ),
        ],
        time_from="now-30d",
        refresh_ms=300000,
    )


def _logs(b: Builder) -> None:
    error_query = f"{LEVEL}:(ERROR or CRITICAL or FATAL or PANIC or EMERG)"
    b.dashboard(
        "litellm-dash-logs",
        "Container Logs",
        "stdout and stderr from every Compose service with multi-line entries joined and a "
        "normalized severity (Grafana Alloy → Data Prepper)",
        [
            b.controls(
                "litellm-vis-logs-controls",
                "Filters",
                [(LOGS, SERVICE, "Compose service"), (LOGS, LEVEL, "Severity"), (LOGS, STREAM, "Stream")],
            ),
            b.xy("litellm-vis-logs-volume", "Log lines by service", LOGS, [("count", None, "Lines")], split=SERVICE, split_size=40, chart="histogram", stacked=True),
            b.xy("litellm-vis-logs-levels", "Log lines by severity", LOGS, [("count", None, "Lines")], split=LEVEL, chart="histogram", stacked=True),
            b.xy(
                "litellm-vis-logs-errors",
                "Error lines by service",
                LOGS,
                [("count", None, "Lines")],
                query=error_query,
                split=SERVICE,
                split_size=40,
                chart="histogram",
                stacked=True,
            ),
            b.xy(
                "litellm-vis-logs-warnings",
                "Warning lines by service",
                LOGS,
                [("count", None, "Lines")],
                query=f"{LEVEL}:WARN",
                split=SERVICE,
                split_size=40,
                chart="histogram",
                stacked=True,
            ),
            b.xy(
                "litellm-vis-logs-stderr",
                "stderr lines by service",
                LOGS,
                [("count", None, "Lines")],
                query=f"{STREAM}:stderr",
                split=SERVICE,
                split_size=40,
                chart="histogram",
                stacked=True,
            ),
            b.pie("litellm-vis-logs-share", "Log share by service", LOGS, ("count", None, "Lines"), SERVICE, size=40),
            b.search("litellm-search-logs-errors", "Error log entries", LOGS, [SERVICE, LEVEL, STREAM, "body"], query=error_query, sort=[["@timestamp", "desc"]]),
            b.search("litellm-search-logs-all", "All container logs", LOGS, [SERVICE, LEVEL, STREAM, "body"], sort=[["@timestamp", "desc"]]),
        ],
        time_from="now-1h",
        refresh_ms=30000,
    )


def _access_logs(b: Builder) -> None:
    access = "http.RouterName:*"
    b.dashboard(
        "litellm-dash-access",
        "Traefik Access Logs",
        "Every request through Traefik. Client addresses, credentials, and headers are never logged; "
        "search query parameters are redacted.",
        [
            b.controls(
                "litellm-vis-access-controls",
                "Filters",
                [(LOGS, "http.RouterName", "Router"), (LOGS, "http.DownstreamStatus", "Status"), (LOGS, "http.RequestMethod", "Method")],
            ),
            b.stat("litellm-vis-access-requests", "Requests", LOGS, [("count", None, "Requests")], query=access, width=12),
            b.stat(
                "litellm-vis-access-5xx",
                "Server errors (5xx)",
                LOGS,
                [("count", None, "5xx")],
                query="http.DownstreamStatus >= 500",
                width=12,
            ),
            b.stat(
                "litellm-vis-access-4xx",
                "Client errors (4xx)",
                LOGS,
                [("count", None, "4xx")],
                query="http.DownstreamStatus >= 400 and http.DownstreamStatus < 500",
                width=12,
            ),
            b.stat("litellm-vis-access-p95", "Latency p95 (ms)", LOGS, [("percentiles", "http.duration_ms", "p95 ms")], query=access, width=12),
            b.xy("litellm-vis-access-by-router", "Requests by router", LOGS, [("count", None, "Requests")], query=access, split="http.RouterName", chart="histogram", stacked=True),
            b.xy("litellm-vis-access-by-status", "Requests by status", LOGS, [("count", None, "Requests")], query=access, split="http.DownstreamStatus", chart="histogram", stacked=True),
            b.xy(
                "litellm-vis-access-latency",
                "Latency p95 by router (ms)",
                LOGS,
                [("percentiles", "http.duration_ms", "p95 ms")],
                query=access,
                split="http.RouterName",
                y_label="ms",
            ),
            b.hbar("litellm-vis-access-paths", "Top request paths", LOGS, ("count", None, "Requests"), "http.RequestPath", query=access, size=15),
            b.table(
                "litellm-vis-access-errors",
                "Error responses by router, path, and status",
                LOGS,
                [{"type": "count", "schema": "metric", "params": {"customLabel": "Requests"}}],
                [("http.RouterName", 20, "Router"), ("http.RequestPath", 20, "Path"), ("http.DownstreamStatus", 10, "Status")],
                query="http.DownstreamStatus >= 400",
                width=48,
            ),
            b.search(
                "litellm-search-access-slow",
                "Slowest requests",
                LOGS,
                ["http.RouterName", "http.RequestMethod", "http.RequestPath", "http.DownstreamStatus", "http.duration_ms"],
                query=access,
                sort=[["http.duration_ms", "desc"]],
            ),
        ],
        time_from="now-6h",
        refresh_ms=60000,
    )


def _traces(b: Builder) -> None:
    requests = "not name:postgres* and not traceGroup:*metrics*"
    roots = "name:auth* and not traceGroup:*metrics*"
    b.dashboard(
        "litellm-dash-traces",
        "LiteLLM Request Traces",
        "Content-free OpenTelemetry spans from LiteLLM (operators only). Use Observability → Trace "
        "Analytics for trace waterfalls and the service map.",
        [
            b.controls(
                "litellm-vis-traces-controls",
                "Filters",
                [
                    (TRACES, "serviceName", "Service"),
                    (TRACES, "traceGroup", "Route"),
                    (TRACES, ATTR + "gen_ai@request@model", "Model"),
                ],
            ),
            b.stat("litellm-vis-traces-count", "Spans", TRACES, [("count", None, "Spans")], query=requests, width=12),
            b.stat("litellm-vis-traces-errors", "Error spans", TRACES, [("count", None, "Errors")], query="status.code:2", width=12),
            b.stat(
                "litellm-vis-traces-p95",
                "Request latency p95 (ms)",
                TRACES,
                [("percentiles", "durationInMillis", "p95 ms")],
                query=roots,
                width=12,
            ),
            b.stat(
                "litellm-vis-traces-masked",
                "Guardrail evaluations",
                TRACES,
                [("count", None, "Evaluations")],
                query="name:execute_guardrail*",
                width=12,
            ),
            b.xy(
                "litellm-vis-traces-by-route",
                "Requests by route",
                TRACES,
                [("count", None, "Requests")],
                query=roots,
                split="traceGroup",
                chart="histogram",
                stacked=True,
                time_field="startTime",
            ),
            b.xy(
                "litellm-vis-traces-latency",
                "Span latency p95 by operation (ms)",
                TRACES,
                [("percentiles", "durationInMillis", "p95 ms")],
                query=requests,
                split="name",
                time_field="startTime",
            ),
            b.xy(
                "litellm-vis-traces-models",
                "Model calls by model",
                TRACES,
                [("count", None, "Calls")],
                query=f"{ATTR}gen_ai@request@model:*",
                split=ATTR + "gen_ai@request@model",
                chart="histogram",
                stacked=True,
                time_field="startTime",
            ),
            b.xy(
                "litellm-vis-traces-errors-ts",
                "Error spans by operation",
                TRACES,
                [("count", None, "Errors")],
                query="status.code:2",
                split="name",
                chart="histogram",
                stacked=True,
                time_field="startTime",
            ),
            b.xy(
                "litellm-vis-traces-db",
                "Database latency p95 by table (ms)",
                TRACES,
                [("percentiles", "durationInMillis", "p95 ms")],
                query="name:postgres*",
                split=ATTR + "db@collection@name",
                time_field="startTime",
            ),
            b.hbar(
                "litellm-vis-traces-guardrails",
                "Guardrail outcomes",
                TRACES,
                ("count", None, "Evaluations"),
                ATTR + "litellm@guardrail@status",
                query="name:execute_guardrail*",
            ),
            b.search(
                "litellm-search-traces-slow",
                "Slowest request spans",
                TRACES,
                ["serviceName", "traceGroup", "name", "durationInMillis", "status.code", ATTR + "gen_ai@request@model", "traceId"],
                query=requests,
                sort=[["durationInMillis", "desc"]],
            ),
            b.search(
                "litellm-search-traces-errors",
                "Error spans",
                TRACES,
                ["serviceName", "traceGroup", "name", "status.message", "traceId"],
                query="status.code:2",
                sort=[["startTime", "desc"]],
            ),
        ],
        time_from="now-24h",
        refresh_ms=60000,
    )


def _infrastructure(b: Builder) -> None:
    b.dashboard(
        "litellm-dash-host",
        "Infrastructure: Host & Containers",
        "Node exporter and cAdvisor metrics",
        [
            b.metric_series("litellm-vis-host-cpu", "CPU busy %", "node_cpu_busy_percent", y_label="%"),
            b.metric_series("litellm-vis-host-cpu-mode", "CPU by mode %", "node_cpu_mode_percent", y_label="%", chart="area", stacked=True),
            b.metric_series("litellm-vis-host-mem", "Memory and swap used %", "node_memory_used_percent node_swap_used_percent", y_label="%"),
            b.metric_series("litellm-vis-host-load", "Load average", "node_load"),
            b.metric_series("litellm-vis-host-fs", "Filesystem used %", "node_filesystem_used_percent", y_label="%"),
            b.metric_series("litellm-vis-host-disk", "Disk throughput", "node_disk_io_bytes_per_second", y_label="bytes/s"),
            b.metric_series("litellm-vis-host-net", "Network throughput", "node_network_bytes_per_second", y_label="bytes/s"),
            b.latest("litellm-vis-host-uptime", "Host uptime (seconds)", "node_uptime_seconds", width=24, height=12),
            b.metric_series("litellm-vis-containers-cpu", "Container CPU (cores) by service", "container_cpu_cores", split="labels.service", split_size=40, y_label="cores"),
            b.metric_series("litellm-vis-containers-mem", "Container memory by service", "container_memory_bytes", split="labels.service", split_size=40, y_label="bytes"),
            b.metric_series("litellm-vis-containers-net", "Container network by service", "container_network_bytes_per_second", split_size=40, y_label="bytes/s"),
            b.metric_series("litellm-vis-containers-restarts", "Container restarts (1h)", "container_restarts_1h", split="labels.service", split_size=40),
            b.metric_series("litellm-vis-clickhouse-free", "Langfuse ClickHouse filesystem free ratio", "langfuse_clickhouse_data_free_ratio", y_label="ratio"),
            b.metric_series("litellm-vis-clickhouse-logs", "Langfuse ClickHouse retained logs", "langfuse_clickhouse_log_messages_per_second", y_label="logs/s"),
        ],
        time_from="now-6h",
    )
    b.dashboard(
        "litellm-dash-postgres",
        "Infrastructure: PostgreSQL",
        "postgres-exporter and pgvector-exporter metrics",
        [
            b.metric_series("litellm-vis-pg-up", "Exporter connectivity (pg_up)", "pg_up"),
            b.metric_series("litellm-vis-pg-connections", "Connections by database and state", "pg_connections pg_max_connections", split_size=30),
            b.metric_series("litellm-vis-pg-tps", "Commits and rollbacks per second", "pg_transactions_per_second", split_size=30),
            b.metric_series("litellm-vis-pg-rows", "Tuple operations per second", "pg_rows_per_second"),
            b.metric_series("litellm-vis-pg-cache", "Buffer cache hit ratio", "pg_cache_hit_ratio"),
            b.metric_series("litellm-vis-pg-size", "Database size", "pg_database_size_bytes", y_label="bytes"),
            b.metric_series("litellm-vis-pg-deadlocks", "Deadlocks (1h)", "pg_deadlocks_1h"),
            b.metric_series("litellm-vis-pg-locks", "Locks by mode", "pg_locks"),
        ],
        time_from="now-6h",
    )
    b.dashboard(
        "litellm-dash-edge",
        "Infrastructure: Traefik, Probes & Targets",
        "Ingress, certificate expiry, blackbox probes, and Prometheus targets",
        [
            b.metric_series("litellm-vis-edge-rps", "Traefik requests/s by service and code", "traefik_requests_per_second", split_size=30),
            b.metric_series("litellm-vis-edge-errors", "Traefik 5xx ratio by service", "traefik_error_ratio"),
            b.metric_series("litellm-vis-edge-latency", "Traefik latency p95 by service", "traefik_latency_p95_seconds", y_label="seconds"),
            b.metric_series("litellm-vis-edge-entrypoints", "Entry point requests/s", "traefik_entrypoint_requests_per_second"),
            b.metric_series("litellm-vis-edge-connections", "Open connections", "traefik_open_connections"),
            b.table(
                "litellm-vis-edge-tls",
                "TLS certificate days remaining",
                METRICS,
                [_latest_agg(label="Days remaining")],
                [("labels.cn", 20, "Certificate CN"), ("labels.sans", 5, "SANs")],
                query=expression_query("traefik_tls_cert_days_remaining"),
                order_by="_key",
            ),
            b.metric_series("litellm-vis-edge-probe", "Blackbox probe success", "probe_success", split="labels.instance"),
            b.metric_series("litellm-vis-edge-probe-duration", "Blackbox probe duration", "probe_duration_seconds", split="labels.instance", y_label="seconds"),
            b.table(
                "litellm-vis-edge-targets",
                "Scrape targets (1 up, 0 down)",
                METRICS,
                [_latest_agg(label="Up")],
                [("labels.job", 50, "Job"), ("labels.instance", 20, "Instance")],
                query=expression_query("target_up"),
                order_by="_key",
                width=48,
            ),
        ],
        time_from="now-6h",
    )


def _security(b: Builder) -> None:
    b.dashboard(
        "litellm-dash-security",
        "Security, Backups & Alerts",
        "Image-security textfile metrics, backup freshness, and the state of every provisioned alert rule",
        [
            b.table(
                "litellm-vis-security-alerts",
                "Alert rule state (latest evaluation)",
                ALERTS,
                [_latest_agg("state", "State")],
                [("title", 50, "Rule"), ("severity", 5, "Severity")],
                order_by="_key",
                width=48,
            ),
            b.xy(
                "litellm-vis-security-firing",
                "Rules whose condition is met",
                ALERTS,
                [("count", None, "Evaluations")],
                query="condition_met:1",
                split="title",
                chart="histogram",
                stacked=True,
                width=48,
            ),
            b.table(
                "litellm-vis-security-images",
                "Image vulnerabilities and scans by service (latest)",
                METRICS,
                [_latest_agg(label="Value")],
                [("metric", 10, "Metric"), ("labels.service", 50, "Service")],
                query=expression_query(
                    "image_security_unexcepted_fixable_critical",
                    "image_security_unexcepted_kev",
                    "image_security_scan_success",
                    "image_security_scan_age_hours",
                ),
                order_by="_key",
                width=48,
            ),
            b.latest("litellm-vis-security-backup", "Hours since last complete backup", "backup_age_hours", width=24, height=8),
            b.latest("litellm-vis-security-run", "Hours since last image-security run", "image_security_run_age_hours", width=24, height=8),
            b.search(
                "litellm-search-security-firing",
                "Alert evaluations with the condition met",
                ALERTS,
                ["title", "severity", "state", "active_series", "summary"],
                query="condition_met:1",
                sort=[["@timestamp", "desc"]],
            ),
        ],
        time_from="now-24h",
    )


def _explorer(b: Builder) -> None:
    b.dashboard(
        "litellm-dash-explorer",
        "Metric Explorer",
        "Browse every metric mirrored from Prometheus",
        [
            b.controls(
                "litellm-vis-explorer-controls",
                "Metric",
                [(METRICS, "metric", "Metric name"), (METRICS, "labels.job", "Job"), (METRICS, "series", "Series")],
                size=1000,
            ),
            b.xy(
                "litellm-vis-explorer-series",
                "Selected metric by series",
                METRICS,
                [("avg", "value", "Value")],
                split="metric_series",
                split_size=25,
                width=48,
            ),
            b.table(
                "litellm-vis-explorer-latest",
                "Latest value per series",
                METRICS,
                [_latest_agg()],
                [("metric", 200, "Metric"), ("series", 200, "Series")],
                order_by="_key",
                width=48,
            ),
            b.markdown(
                "litellm-vis-explorer-help",
                "About the metric explorer",
                "Select a metric name above. `kind: raw` documents are unchanged Prometheus samples "
                "(counters are cumulative); `kind: expression` documents are the evaluated PromQL "
                "expressions from `opensearch/tools/metrics.yml`. For ad hoc PromQL over every series, "
                "use **Observability → Metrics** or PPL with the `prometheus` data source, for example "
                "`source = prometheus.query_range('rate(litellm_requests_metric_total[5m])', now() - 1h, now(), '1m')`.",
                height=6,
            ),
        ],
        time_from="now-1h",
    )


def _audit(b: Builder) -> None:
    b.dashboard(
        "litellm-dash-audit",
        "OpenSearch Security Audit",
        "Authentication failures, privilege denials, and security configuration changes",
        [
            b.xy(
                "litellm-vis-audit-categories",
                "Audit events by category",
                SECURITY_AUDIT,
                [("count", None, "Events")],
                split="audit_category.keyword",
                chart="histogram",
                stacked=True,
                width=48,
            ),
            b.hbar(
                "litellm-vis-audit-failed-logins",
                "Failed logins by user",
                SECURITY_AUDIT,
                ("count", None, "Failed logins"),
                "audit_request_effective_user.keyword",
                query="audit_category:FAILED_LOGIN",
            ),
            b.hbar(
                "litellm-vis-audit-denied",
                "Privilege denials by user",
                SECURITY_AUDIT,
                ("count", None, "Denials"),
                "audit_request_effective_user.keyword",
                query="audit_category:MISSING_PRIVILEGES",
            ),
            b.search(
                "litellm-search-audit-events",
                "Security audit events",
                SECURITY_AUDIT,
                [
                    "audit_category",
                    "audit_request_effective_user",
                    "audit_request_privilege",
                    "audit_rest_request_path",
                    "audit_request_remote_address",
                ],
                sort=[["@timestamp", "desc"]],
            ),
        ],
        time_from="now-24h",
    )


def build_all() -> list[dict[str, Any]]:
    builder = Builder()
    builder.index_patterns()
    for section in (
        _overview,
        _management,
        _stack,
        _cost,
        _admin,
        _team,
        _logs,
        _access_logs,
        _traces,
        _infrastructure,
        _security,
        _explorer,
        _audit,
    ):
        section(builder)
    return builder.objects
