"""Copy LiteLLM usage, error, audit, and entity data from PostgreSQL to OpenSearch.

Only the non-content columns that the Grafana SQL dashboards display are
copied. Prompts, responses, request metadata, client IP addresses, virtual-key
hashes, and audit before/after values never leave PostgreSQL.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from .client import LOG, HttpClient, bulk

STATE_INDEX = "litellm-bridge-state"
ENTITY_INDEX = "litellm-entities"


@dataclass(frozen=True)
class EventSource:
    """An append-only LiteLLM table copied incrementally by time and id."""

    key: str
    table: str
    time_column: str
    id_column: str
    index_prefix: str
    columns: tuple[tuple[str, str], ...]  # (document field, table column)


SPEND = EventSource(
    key="spend",
    table="LiteLLM_SpendLogs",
    time_column="startTime",
    id_column="request_id",
    index_prefix="litellm-spend",
    columns=(
        ("request_id", "request_id"),
        ("start_time", "startTime"),
        ("end_time", "endTime"),
        ("call_type", "call_type"),
        ("spend", "spend"),
        ("total_tokens", "total_tokens"),
        ("prompt_tokens", "prompt_tokens"),
        ("completion_tokens", "completion_tokens"),
        ("model", "model"),
        ("model_group", "model_group"),
        ("model_id", "model_id"),
        ("provider", "custom_llm_provider"),
        ("user", "user"),
        ("end_user", "end_user"),
        ("team_id", "team_id"),
        ("organization_id", "organization_id"),
        ("status", "status"),
        ("cache_hit", "cache_hit"),
        ("request_tags", "request_tags"),
        ("session_id", "session_id"),
    ),
)
PROVIDER_ERRORS = EventSource(
    key="provider-errors",
    table="LiteLLM_ErrorLogs",
    time_column="startTime",
    id_column="request_id",
    index_prefix="litellm-provider-errors",
    columns=(
        ("request_id", "request_id"),
        ("start_time", "startTime"),
        ("end_time", "endTime"),
        ("model_group", "model_group"),
        ("provider_model", "litellm_model_name"),
        ("model_id", "model_id"),
        ("exception_type", "exception_type"),
        ("status_code", "status_code"),
    ),
)
AUDIT = EventSource(
    key="audit",
    table="LiteLLM_AuditLog",
    time_column="updated_at",
    id_column="id",
    index_prefix="litellm-audit",
    columns=(
        ("audit_id", "id"),
        ("updated_at", "updated_at"),
        ("changed_by", "changed_by"),
        ("action", "action"),
        ("object_type", "table_name"),
        ("object_id", "object_id"),
    ),
)
EVENT_SOURCES = (SPEND, PROVIDER_ERRORS, AUDIT)
USAGE_PREFIXES = tuple(source.index_prefix for source in EVENT_SOURCES)

ENTITY_COLUMNS = {
    "LiteLLM_UserTable": (
        ("user_id", "user_id"),
        ("user_alias", "user_alias"),
        ("user_role", "user_role"),
        ("team_id", "team_id"),
        ("spend", "spend"),
        ("max_budget", "max_budget"),
        ("budget_reset_at", "budget_reset_at"),
        ("created_at", "created_at"),
    ),
    "LiteLLM_TeamTable": (
        ("team_id", "team_id"),
        ("team_alias", "team_alias"),
        ("blocked", "blocked"),
        ("spend", "spend"),
        ("max_budget", "max_budget"),
        ("created_at", "created_at"),
    ),
    "LiteLLM_VerificationToken": (
        ("key_alias", "key_alias"),
        ("key_name", "key_name"),
        ("user_id", "user_id"),
        ("team_id", "team_id"),
        ("spend", "spend"),
        ("total_spend", "total_spend"),
        ("max_budget", "max_budget"),
        ("expires", "expires"),
        ("blocked", "blocked"),
        ("created_at", "created_at"),
    ),
}
ENTITY_TYPES = {
    "LiteLLM_UserTable": ("user", "user_id"),
    "LiteLLM_TeamTable": ("team", "team_id"),
    "LiteLLM_VerificationToken": ("key", "token"),
}


def utc(value: datetime) -> datetime:
    # LiteLLM stores timestamps without a zone; Prisma writes them in UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def to_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return utc(value).isoformat(timespec="milliseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [to_json(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def quote_identifier(name: str) -> str:
    # Identifiers come from fixed allow-lists above, never from input; quoting
    # still guards against unexpected characters.
    if not name or "\x00" in name:
        raise ValueError("invalid SQL identifier")
    return '"' + name.replace('"', '""') + '"'


def table_columns(connection: Any, table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        )
        return {row[0] for row in cursor.fetchall()}


def build_event_query(source: EventSource, available: dict[str, set[str]]) -> tuple[str, list[str]] | None:
    """Return the SQL and output field order for an event source, or None if absent."""
    columns = available.get(source.table) or set()
    if not {source.time_column, source.id_column} <= columns:
        return None
    select: list[str] = []
    fields: list[str] = []
    for field, column in source.columns:
        if column in columns:
            select.append(f"src.{quote_identifier(column)}")
            fields.append(field)
    joins = ""
    if source is SPEND:
        keys = available.get("LiteLLM_VerificationToken") or set()
        users = available.get("LiteLLM_UserTable") or set()
        teams = available.get("LiteLLM_TeamTable") or set()
        if "api_key" in columns and {"token", "key_alias", "user_id"} <= keys:
            joins += ' LEFT JOIN "LiteLLM_VerificationToken" k ON k.token = src.api_key'
            select += ["k.key_alias", "k.user_id"]
            fields += ["key_alias", "key_owner_id"]
            if {"user_id", "user_alias"} <= users:
                joins += ' LEFT JOIN "LiteLLM_UserTable" ku ON ku.user_id = k.user_id'
                select.append("ku.user_alias")
                fields.append("key_owner_alias")
        if "user" in columns and {"user_id", "user_alias"} <= users:
            joins += ' LEFT JOIN "LiteLLM_UserTable" mu ON mu.user_id = src."user"'
            select.append("mu.user_alias")
            fields.append("user_alias")
        if "team_id" in columns and {"team_id", "team_alias"} <= teams:
            joins += ' LEFT JOIN "LiteLLM_TeamTable" t ON t.team_id = src.team_id'
            select.append("t.team_alias")
            fields.append("team_alias")
    time_col = f"src.{quote_identifier(source.time_column)}"
    id_col = f"src.{quote_identifier(source.id_column)}"
    select += [f"{time_col} AS __cursor_time", f"{id_col}::text AS __cursor_id"]
    fields += ["__cursor_time", "__cursor_id"]
    sql = (
        f"SELECT {', '.join(select)} FROM {quote_identifier(source.table)} src{joins} "
        f"WHERE ({time_col}, {id_col}::text) > (%s, %s) "
        f"ORDER BY {time_col}, {id_col}::text LIMIT %s"
    )
    return sql, fields


def event_document(source: EventSource, environment: str, row: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Return (index, document id, source) for one event row."""
    timestamp = utc(row["__cursor_time"])
    document = {
        key: to_json(value)
        for key, value in row.items()
        if not key.startswith("__") and value is not None
    }
    document["@timestamp"] = timestamp.isoformat(timespec="milliseconds")
    document["environment"] = environment
    if source is SPEND:
        session_id = document.pop("session_id", None)
        if session_id:
            salt = os.environ.get("OPENSEARCH_SESSION_ID_SALT", "")
            if not salt:
                raise RuntimeError("OPENSEARCH_SESSION_ID_SALT is required to pseudonymize session identifiers")
            document["session_hash"] = hashlib.sha256(
                f"{environment}|{session_id}|{salt}".encode()
            ).hexdigest()
        document["key_owner"] = (
            row.get("key_owner_alias") or row.get("key_owner_id") or "unassigned"
        )
        document["member"] = row.get("user_alias") or row.get("user") or "unassigned"
        document["team"] = row.get("team_alias") or row.get("team_id") or "unassigned"
        document["model_name"] = row.get("model_group") or row.get("model") or "unknown"
        document.setdefault("key_alias", "unknown")
    identifier = hashlib.sha256(f"{environment}|{source.key}|{row['__cursor_id']}".encode()).hexdigest()[:40]
    # Monthly indices keep the shard count bounded over the 180-day retention.
    return f"{source.index_prefix}-{timestamp:%Y.%m}", identifier, document


def entity_documents(
    environment: str,
    table: str,
    rows: Iterable[dict[str, Any]],
    synced_at: datetime,
) -> list[tuple[str, dict[str, Any]]]:
    entity_type, primary_key = ENTITY_TYPES[table]
    documents = []
    for row in rows:
        primary = row.pop("__pk", None)
        if primary is None:
            continue
        document = {key: to_json(value) for key, value in row.items() if value is not None}
        document.update(
            {
                "@timestamp": synced_at.isoformat(timespec="seconds"),
                "synced_at": synced_at.isoformat(timespec="seconds"),
                "environment": environment,
                "entity_type": entity_type,
            }
        )
        if entity_type == "key":
            blocked = bool(row.get("blocked"))
            expires = row.get("expires")
            document["blocked"] = blocked
            document["usable"] = not blocked and (expires is None or utc(expires) > synced_at)
            document["name"] = row.get("key_alias") or row.get("key_name") or "unnamed"
            document["owner"] = row.get("owner_alias") or row.get("user_id") or "unassigned"
            document["team"] = row.get("team_alias") or row.get("team_id") or "none"
        elif entity_type == "user":
            document["name"] = row.get("user_alias") or row.get("user_id")
        else:
            document["blocked"] = bool(row.get("blocked"))
            document["name"] = row.get("team_alias") or row.get("team_id")
        identifier = hashlib.sha256(f"{environment}|{entity_type}|{primary}".encode()).hexdigest()[:40]
        documents.append((identifier, document))
    return documents


def build_entity_query(table: str, available: dict[str, set[str]]) -> tuple[str, list[str]] | None:
    columns = available.get(table) or set()
    _, primary_key = ENTITY_TYPES[table]
    if primary_key not in columns:
        return None
    select = [f"src.{quote_identifier(primary_key)}::text"]
    fields = ["__pk"]
    for field, column in ENTITY_COLUMNS[table]:
        if column in columns:
            select.append(f"src.{quote_identifier(column)}")
            fields.append(field)
    joins = ""
    if table == "LiteLLM_VerificationToken":
        users = available.get("LiteLLM_UserTable") or set()
        teams = available.get("LiteLLM_TeamTable") or set()
        if "user_id" in columns and {"user_id", "user_alias"} <= users:
            joins += ' LEFT JOIN "LiteLLM_UserTable" u ON u.user_id = src.user_id'
            select.append("u.user_alias")
            fields.append("owner_alias")
        if "team_id" in columns and {"team_id", "team_alias"} <= teams:
            joins += ' LEFT JOIN "LiteLLM_TeamTable" t ON t.team_id = src.team_id'
            select.append("t.team_alias")
            fields.append("team_alias")
    return f"SELECT {', '.join(select)} FROM {quote_identifier(table)} src{joins}", fields


class UsageSync:
    def __init__(
        self,
        opensearch: HttpClient,
        *,
        environments: dict[str, str],
        connect: Any,
        backfill_days: int,
        overlap_minutes: int,
        batch_size: int,
        max_rows_per_cycle: int,
    ) -> None:
        self.opensearch = opensearch
        self.environments = environments
        self.connect = connect
        self.backfill = timedelta(days=backfill_days)
        self.overlap = timedelta(minutes=overlap_minutes)
        self.batch_size = batch_size
        self.max_rows = max_rows_per_cycle
        self.unavailable_logged: set[str] = set()

    def _state(self, key: str) -> dict[str, Any] | None:
        status, payload = self.opensearch.request(
            "GET", f"/{STATE_INDEX}/_doc/{key}", allowed_status=(404,)
        )
        if status == 404 or not isinstance(payload, dict):
            return None
        return payload.get("_source")

    def _save_state(self, key: str, state: dict[str, Any]) -> None:
        self.opensearch.put(f"/{STATE_INDEX}/_doc/{key}", state)

    def _log_unavailable(self, key: str, message: str) -> None:
        if key not in self.unavailable_logged:
            LOG.info("%s", message)
            self.unavailable_logged.add(key)

    def run_events(self, now: datetime) -> None:
        for environment, database in self.environments.items():
            try:
                connection = self.connect(database)
            except Exception as error:  # noqa: BLE001 - every driver error means "skip"
                self._log_unavailable(
                    f"db:{environment}",
                    f"LiteLLM {environment} database '{database}' is unavailable ({type(error).__name__}); skipping",
                )
                continue
            self.unavailable_logged.discard(f"db:{environment}")
            with connection:
                available = {
                    table: table_columns(connection, table)
                    for table in {source.table for source in EVENT_SOURCES} | set(ENTITY_COLUMNS)
                }
                for source in EVENT_SOURCES:
                    self._sync_events(connection, source, environment, available, now)

    def _sync_events(
        self,
        connection: Any,
        source: EventSource,
        environment: str,
        available: dict[str, set[str]],
        now: datetime,
    ) -> None:
        built = build_event_query(source, available)
        state_key = f"{source.key}-{environment}"
        if built is None:
            self._log_unavailable(state_key, f"{source.table} is not present in {environment}; skipping")
            return
        sql, fields = built
        state = self._state(state_key)
        if state and state.get("cursor"):
            cursor_time = datetime.fromisoformat(state["cursor"]) - self.overlap
        else:
            cursor_time = now - self.backfill
        # LiteLLM writes rows in batches after requests finish, so each cycle
        # re-reads the overlap window and relies on idempotent create actions.
        cursor_id = ""
        newest = datetime.fromisoformat(state["cursor"]) if state and state.get("cursor") else cursor_time
        processed = 0
        created = 0
        while processed < self.max_rows:
            with connection.cursor() as cursor:
                cursor.execute(sql, (cursor_time.replace(tzinfo=None), cursor_id, self.batch_size))
                rows = [dict(zip(fields, row)) for row in cursor.fetchall()]
            if not rows:
                break
            actions = []
            for row in rows:
                index, identifier, document = event_document(source, environment, row)
                actions.append(({"create": {"_index": index, "_id": identifier}}, document))
            counts = bulk(self.opensearch, actions)
            if counts["failed"]:
                raise RuntimeError(f"{counts['failed']} {source.key} document(s) failed to index")
            created += counts["ok"]
            processed += len(rows)
            cursor_time = utc(rows[-1]["__cursor_time"]).replace(tzinfo=None)
            cursor_id = rows[-1]["__cursor_id"]
            newest = max(newest, utc(rows[-1]["__cursor_time"]))
            if len(rows) < self.batch_size:
                break
        self._save_state(
            state_key,
            {"cursor": newest.isoformat(), "updated_at": now.isoformat(timespec="seconds")},
        )
        if created:
            LOG.info("Indexed %d new %s row(s) from %s", created, source.key, environment)

    def run_entities(self, now: datetime) -> None:
        for environment, database in self.environments.items():
            try:
                connection = self.connect(database)
            except Exception:  # noqa: BLE001 - reported by run_events
                continue
            with connection:
                available = {table: table_columns(connection, table) for table in ENTITY_COLUMNS}
                for table in ENTITY_COLUMNS:
                    built = build_entity_query(table, available)
                    if built is None:
                        continue
                    sql, fields = built
                    with connection.cursor() as cursor:
                        cursor.execute(sql)
                        rows = [dict(zip(fields, row)) for row in cursor.fetchall()]
                    documents = entity_documents(environment, table, rows, now)
                    for start in range(0, len(documents), 1000):
                        chunk = documents[start : start + 1000]
                        counts = bulk(
                            self.opensearch,
                            [({"index": {"_index": ENTITY_INDEX, "_id": i}}, d) for i, d in chunk],
                        )
                        if counts["failed"]:
                            raise RuntimeError(f"{counts['failed']} entity document(s) failed to index")
                    entity_type, _ = ENTITY_TYPES[table]
                    # Remove entities deleted in LiteLLM since the previous snapshot.
                    self.opensearch.post(
                        f"/{ENTITY_INDEX}/_delete_by_query",
                        {
                            "query": {
                                "bool": {
                                    "filter": [
                                        {"term": {"environment": environment}},
                                        {"term": {"entity_type": entity_type}},
                                        {"range": {"synced_at": {"lt": now.isoformat(timespec="seconds")}}},
                                    ]
                                }
                            }
                        },
                        params={"conflicts": "proceed", "refresh": "true"},
                    )


def expired_indices(names: Iterable[str], prefixes: Iterable[str], today: date, retention_days: int) -> list[str]:
    """Return monthly usage indices whose entire month is older than the retention."""
    cutoff = today - timedelta(days=retention_days)
    expired = []
    for name in names:
        for prefix in prefixes:
            suffix = name[len(prefix) + 1 :] if name.startswith(prefix + "-") else None
            if suffix is None:
                continue
            try:
                month = datetime.strptime(suffix, "%Y.%m").date()
            except ValueError:
                continue
            next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
            if next_month <= cutoff:
                expired.append(name)
    return sorted(expired)


def apply_retention(opensearch: HttpClient, retention_days: int, today: date) -> None:
    pattern = ",".join(f"{prefix}-*" for prefix in USAGE_PREFIXES)
    rows = opensearch.get(f"/_cat/indices/{pattern}", params={"format": "json", "h": "index"}) or []
    names = [row["index"] for row in rows if isinstance(row, dict) and "index" in row]
    for name in expired_indices(names, USAGE_PREFIXES, today, retention_days):
        LOG.info("Deleting usage index %s, which is older than %d days", name, retention_days)
        opensearch.delete(f"/{name}")
    if names:
        # Trim the oldest remaining month to the exact retention boundary.
        cutoff = (today - timedelta(days=retention_days)).isoformat()
        opensearch.post(
            f"/{pattern}/_delete_by_query",
            {"query": {"range": {"@timestamp": {"lt": cutoff}}}},
            params={"conflicts": "proceed", "allow_no_indices": "true", "ignore_unavailable": "true"},
        )
