"""Materialize Prometheus series and expressions as OpenSearch documents."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import LOG, HttpClient

NAME = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


@dataclass(frozen=True)
class Expression:
    name: str
    query: str
    title: str
    kind: str  # "expression" or "raw"


@dataclass(frozen=True)
class Catalog:
    expressions: tuple[Expression, ...]
    max_series: int


def catalog_from_document(document: dict[str, Any]) -> Catalog:
    items: list[Expression] = []
    names: set[str] = set()
    for entry in document.get("raw", []) or []:
        items.append(Expression(str(entry["name"]), str(entry["selector"]), str(entry["name"]), "raw"))
    for entry in document.get("expressions", []) or []:
        name = str(entry["name"])
        if not NAME.fullmatch(name):
            raise ValueError(f"invalid metric expression name: {name}")
        if name in names:
            raise ValueError(f"duplicate metric expression name: {name}")
        names.add(name)
        items.append(Expression(name, str(entry["query"]), str(entry.get("title", name)), "expression"))
    max_series = int(document.get("max_series_per_query", 5000))
    if not 1 <= max_series <= 100000:
        raise ValueError("max_series_per_query must be between 1 and 100000")
    return Catalog(tuple(items), max_series)


def load_catalog(path: Path) -> Catalog:
    import yaml

    with path.open(encoding="utf-8") as source:
        return catalog_from_document(yaml.safe_load(source) or {})


def index_for(timestamp: datetime, prefix: str) -> str:
    return f"{prefix}-{timestamp.astimezone(timezone.utc):%Y.%m.%d}"


def documents_for_result(
    expression: Expression,
    result: list[dict[str, Any]],
    evaluated_at: datetime,
    max_series: int,
) -> list[tuple[str, dict[str, Any]]]:
    """Return (document id, source) pairs for one Prometheus vector result."""
    documents: list[tuple[str, dict[str, Any]]] = []
    timestamp = evaluated_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    if len(result) > max_series:
        LOG.warning(
            "Metric query %s returned %d series; only the first %d are stored",
            expression.name, len(result), max_series,
        )
    for sample in result[:max_series]:
        metric = dict(sample.get("metric") or {})
        try:
            value = float(sample["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if math.isnan(value) or math.isinf(value):
            continue
        raw_name = metric.pop("__name__", None)
        name = raw_name if expression.kind == "raw" else expression.name
        if not name:
            continue
        labels = {key: str(metric[key])[:256] for key in sorted(metric)}
        series = ",".join(f"{key}={labels[key]}" for key in labels) or "{}"
        digest = hashlib.sha256(f"{name}|{series}|{timestamp}".encode()).hexdigest()[:40]
        documents.append(
            (
                digest,
                {
                    "@timestamp": timestamp,
                    "metric": name,
                    "kind": expression.kind,
                    "source": expression.name,
                    "value": value,
                    "labels": labels,
                    "series": series[:1024],
                    "metric_series": f"{name} {series}"[:1024],
                    "job": labels.get("job"),
                },
            )
        )
    return documents


def instant_query(prometheus: HttpClient, query: str, at: datetime) -> list[dict[str, Any]]:
    payload = prometheus.get(
        "/api/v1/query",
        params={"query": query, "time": f"{at.timestamp():.3f}"},
    )
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise RuntimeError("Prometheus returned an unsuccessful query response")
    data = payload.get("data") or {}
    if data.get("resultType") == "scalar":
        return [{"metric": {}, "value": data.get("result")}]
    if data.get("resultType") != "vector":
        raise RuntimeError(f"unsupported Prometheus result type {data.get('resultType')}")
    return list(data.get("result") or [])
