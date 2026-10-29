"""Long-running bridge: Prometheus and LiteLLM PostgreSQL to OpenSearch."""

from __future__ import annotations

import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import alert_rules, metrics, usage
from .client import LOG, HttpClient, HttpError, bulk, env, env_int, opensearch_from_env

HEALTH_FILE = Path(os.environ.get("OPENSEARCH_BRIDGE_HEALTH_FILE", "/tmp/opensearch-bridge.healthy"))
STOP = False


def _stop(*_: Any) -> None:
    global STOP
    STOP = True


def postgres_connector() -> Any:
    import psycopg

    host = env("LITELLM_DATABASE_HOST", "postgres")
    port = env_int("LITELLM_DATABASE_PORT", 5432, minimum=1, maximum=65535)
    user = env("LITELLM_DATABASE_USER", "opensearch_ro")
    password = env("LITELLM_DATABASE_PASSWORD", required=True)

    def connect(database: str) -> Any:
        return psycopg.connect(
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
            connect_timeout=10,
            application_name="opensearch-bridge",
            options="-c default_transaction_read_only=on -c statement_timeout=120000",
        )

    return connect


def evaluate_metrics(
    opensearch: HttpClient,
    prometheus: HttpClient,
    catalog: metrics.Catalog,
    rules: list[alert_rules.AlertRule],
    now: datetime,
) -> None:
    metric_index = metrics.index_for(now, "litellm-metrics")
    actions: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    failures = 0
    for expression in catalog.expressions:
        try:
            result = metrics.instant_query(prometheus, expression.query, now)
        except (OSError, RuntimeError) as error:
            failures += 1
            LOG.warning("Metric query %s failed: %s", expression.name, error)
            continue
        for identifier, document in metrics.documents_for_result(expression, result, now, catalog.max_series):
            actions.append(({"create": {"_index": metric_index, "_id": identifier}}, document))
    state_index = metrics.index_for(now, "litellm-alert-state")
    timestamp = now.isoformat(timespec="seconds")
    for rule in rules:
        error: str | None = None
        result: list[dict[str, Any]] | None = None
        try:
            result = metrics.instant_query(prometheus, rule.expr, now)
        except (OSError, RuntimeError) as exception:
            error = f"{type(exception).__name__}: {exception}"
        document = alert_rules.evaluate(rule, result, error)
        document["@timestamp"] = timestamp
        actions.append(
            ({"create": {"_index": state_index, "_id": f"{rule.uid}-{int(now.timestamp())}"}}, document)
        )
    for start in range(0, len(actions), 2000):
        counts = bulk(opensearch, actions[start : start + 2000])
        if counts["failed"]:
            raise RuntimeError(f"{counts['failed']} metric document(s) failed to index")
    LOG.debug("Indexed %d metric and alert-state document(s); %d query failure(s)", len(actions), failures)


def run() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    interval = env_int("OPENSEARCH_BRIDGE_INTERVAL_SECONDS", 60, minimum=15, maximum=600)
    entity_interval = env_int("OPENSEARCH_BRIDGE_ENTITY_INTERVAL_SECONDS", 300, minimum=60, maximum=86400)
    retention_days = env_int("OPENSEARCH_USAGE_RETENTION_DAYS", 180, minimum=1, maximum=3650)
    opensearch = opensearch_from_env()
    prometheus = HttpClient(env("PROMETHEUS_URL", "http://prometheus:9090"), timeout=30.0)
    catalog = metrics.load_catalog(Path(env("OPENSEARCH_METRICS_CATALOG", "/app/metrics.yml")))
    rules = alert_rules.load_rules(Path(env("OPENSEARCH_ALERT_RULES_DIR", "/app/alerting")))
    environments = {
        name: database
        for name, database in (
            ("prod", env("LITELLM_PROD_DATABASE", "litellm_prod")),
            ("dev", env("LITELLM_DEV_DATABASE", "litellm_dev")),
        )
        if database
    }
    sync = usage.UsageSync(
        opensearch,
        environments=environments,
        connect=postgres_connector(),
        backfill_days=env_int("OPENSEARCH_USAGE_BACKFILL_DAYS", retention_days, minimum=0, maximum=3650),
        overlap_minutes=env_int("OPENSEARCH_USAGE_OVERLAP_MINUTES", 30, minimum=1, maximum=1440),
        batch_size=env_int("OPENSEARCH_USAGE_BATCH_SIZE", 1000, minimum=10, maximum=10000),
        max_rows_per_cycle=env_int("OPENSEARCH_USAGE_MAX_ROWS_PER_CYCLE", 50000, minimum=100, maximum=1000000),
    )
    LOG.info(
        "OpenSearch bridge started: %d metric queries, %d alert rules, environments %s",
        len(catalog.expressions), len(rules), ",".join(environments) or "none",
    )
    last_entities = 0.0
    last_retention_day = None
    while not STOP:
        started = time.monotonic()
        # Align evaluations to the interval so documents line up across queries.
        now = datetime.fromtimestamp(int(time.time()) // interval * interval, tz=timezone.utc)
        healthy = True
        for label, task in (
            ("metrics", lambda: evaluate_metrics(opensearch, prometheus, catalog, rules, now)),
            ("usage", lambda: sync.run_events(now)),
        ):
            try:
                task()
            except Exception as error:  # noqa: BLE001 - keep the daemon alive; retry next cycle
                healthy = False
                LOG.error("Bridge %s cycle failed: %s", label, error)
        if time.monotonic() - last_entities >= entity_interval:
            try:
                sync.run_entities(now)
                last_entities = time.monotonic()
            except Exception as error:  # noqa: BLE001 - keep the daemon alive; retry next cycle
                healthy = False
                LOG.error("Bridge entity snapshot failed: %s", error)
        if last_retention_day != now.date():
            try:
                usage.apply_retention(opensearch, retention_days, now.date())
                last_retention_day = now.date()
            except (OSError, RuntimeError, HttpError) as error:
                LOG.error("Usage retention failed: %s", error)
        if healthy:
            HEALTH_FILE.touch()
        remaining = interval - (time.monotonic() - started)
        while remaining > 0 and not STOP:
            time.sleep(min(remaining, 1.0))
            remaining -= 1.0
    LOG.info("OpenSearch bridge stopped")
    return 0


def healthcheck() -> int:
    interval = env_int("OPENSEARCH_BRIDGE_INTERVAL_SECONDS", 60, minimum=15, maximum=600)
    try:
        age = time.time() - HEALTH_FILE.stat().st_mtime
    except FileNotFoundError:
        return 1
    return 0 if age <= 3 * interval + 120 else 1
