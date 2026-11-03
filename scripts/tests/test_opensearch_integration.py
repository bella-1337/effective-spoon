"""Hermetic tests for the OpenSearch provisioning and bridge package."""

from __future__ import annotations

import json
import math
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "opensearch" / "tools"))

from litellm_opensearch import alert_rules, metrics, notebooks, saved_objects, setup, usage  # noqa: E402

try:
    import yaml  # noqa: F401

    HAS_YAML = True
except ImportError:  # pragma: no cover - depends on the local interpreter
    HAS_YAML = False

try:
    import orjson  # noqa: F401
    import pythonjsonlogger  # noqa: F401

    HAS_JSON_LOGGER = True
except ImportError:  # pragma: no cover - depends on the local interpreter
    HAS_JSON_LOGGER = False


def rule_document(expr: str = "up == bool 0", **overrides: object) -> dict:
    rule = {
        "uid": "test-rule",
        "title": "Test rule",
        "condition": "A",
        "data": [
            {
                "refId": "A",
                "model": {"datasource": {"type": "prometheus", "uid": "prom"}, "expr": expr},
            }
        ],
        "noDataState": "OK",
        "execErrState": "Alerting",
        "for": "5m",
        "annotations": {"summary": "Summary", "description": "Description"},
        "labels": {"severity": "critical"},
    }
    rule.update(overrides)
    return {"groups": [{"rules": [rule]}]}


class AlertRuleTests(unittest.TestCase):
    def test_parse_duration(self) -> None:
        self.assertEqual(alert_rules.parse_duration("5m"), 300)
        self.assertEqual(alert_rules.parse_duration("1h"), 3600)
        self.assertEqual(alert_rules.parse_duration("1h30m"), 5400)
        self.assertEqual(alert_rules.parse_duration(None), 0)
        with self.assertRaises(ValueError):
            alert_rules.parse_duration("5 minutes")

    def test_rule_requires_single_prometheus_condition(self) -> None:
        document = rule_document()
        document["groups"][0]["rules"][0]["data"][0]["model"]["datasource"]["type"] = "loki"
        with self.assertRaises(ValueError):
            alert_rules.rules_from_document(document)

    def test_evaluation_matches_grafana_semantics(self) -> None:
        (rule,) = alert_rules.rules_from_document(rule_document())
        zero = [{"metric": {"job": "a"}, "value": [0, "0"]}]
        firing = [{"metric": {"job": "a"}, "value": [0, "1"]}, {"metric": {"job": "b"}, "value": [0, "0"]}]
        self.assertEqual(alert_rules.evaluate(rule, zero, None)["state"], "normal")
        result = alert_rules.evaluate(rule, firing, None)
        self.assertEqual((result["state"], result["condition_met"]), ("firing", 1))
        self.assertEqual(result["active_series"], ["job=a"])
        self.assertEqual(alert_rules.evaluate(rule, [], None)["condition_met"], 0)
        self.assertEqual(alert_rules.evaluate(rule, None, "timeout")["condition_met"], 1)

        (no_data_alerting,) = alert_rules.rules_from_document(rule_document(noDataState="Alerting"))
        self.assertEqual(alert_rules.evaluate(no_data_alerting, [], None)["condition_met"], 1)

    def test_monitor_requires_the_full_pending_period(self) -> None:
        (rule,) = alert_rules.rules_from_document(rule_document())
        monitor = alert_rules.monitor_for_rule(rule, 60, None)
        self.assertTrue(monitor["name"].startswith(alert_rules.MONITOR_PREFIX))
        trigger = monitor["triggers"][0]["query_level_trigger"]
        self.assertIn(">= 4", trigger["condition"]["script"]["source"])
        self.assertEqual(trigger["severity"], "1")
        self.assertEqual(trigger["actions"], [])
        query = monitor["inputs"][0]["search"]["query"]
        self.assertEqual(query["query"]["bool"]["filter"][0], {"term": {"rule_uid": "test-rule"}})

        with_channel = alert_rules.monitor_for_rule(rule, 60, "channel-1")
        action = with_channel["triggers"][0]["query_level_trigger"]["actions"][0]
        self.assertEqual(action["destination_id"], "channel-1")

    def test_required_samples(self) -> None:
        self.assertEqual(alert_rules.required_samples(0, 60), (120, 1))
        self.assertEqual(alert_rules.required_samples(900, 60), (900, 14))

    def test_staleness_monitors_cover_every_pipeline(self) -> None:
        indices = {m["inputs"][0]["search"]["indices"][0] for m in alert_rules.staleness_monitors(None)}
        self.assertEqual(
            indices, {"litellm-alert-state-*", "litellm-metrics-*", "litellm-logs-*", "otel-v1-apm-span-*"}
        )

    @unittest.skipUnless(HAS_YAML, "PyYAML is not installed")
    def test_repository_alert_rules_translate(self) -> None:
        rules = alert_rules.load_rules(ROOT / "grafana" / "alerting")
        self.assertGreaterEqual(len(rules), 4)
        self.assertIn("litellm-prod-unavailable", {rule.uid for rule in rules})

    def test_repository_alert_rules_never_fire_on_zero_only_filters(self) -> None:
        # Grafana treats a returned value of 0 as Normal, so filter-style
        # comparisons such as "up == 0" would never fire.
        for path in (ROOT / "grafana" / "alerting").glob("*.yml"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("expr:"):
                    self.assertNotRegex(line, r"==\s*0\)", f"{path.name}: {line.strip()}")


class MetricsTests(unittest.TestCase):
    def test_catalog_validation(self) -> None:
        catalog = metrics.catalog_from_document(
            {"raw": [{"name": "r", "selector": "{__name__=~'x'}"}], "expressions": [{"name": "a_b", "query": "up"}]}
        )
        self.assertEqual([item.kind for item in catalog.expressions], ["raw", "expression"])
        with self.assertRaises(ValueError):
            metrics.catalog_from_document({"expressions": [{"name": "Bad-Name", "query": "up"}]})
        with self.assertRaises(ValueError):
            metrics.catalog_from_document({"expressions": [{"name": "a", "query": "up"}, {"name": "a", "query": "up"}]})

    def test_documents_skip_non_finite_values_and_keep_labels(self) -> None:
        expression = metrics.Expression("raw-set", "{}", "raw", "raw")
        result = [
            {"metric": {"__name__": "up", "job": "node"}, "value": [0, "1"]},
            {"metric": {"__name__": "up", "job": "x"}, "value": [0, "NaN"]},
            {"metric": {"__name__": "up", "job": "y"}, "value": [0, "+Inf"]},
        ]
        at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        documents = metrics.documents_for_result(expression, result, at, 100)
        self.assertEqual(len(documents), 1)
        identifier, source = documents[0]
        self.assertEqual(source["metric"], "up")
        self.assertEqual(source["labels"], {"job": "node"})
        self.assertEqual(source["metric_series"], "up job=node")
        self.assertFalse(math.isnan(source["value"]))
        again = metrics.documents_for_result(expression, result, at, 100)
        self.assertEqual(again[0][0], identifier)
        self.assertEqual(metrics.index_for(at, "litellm-metrics"), "litellm-metrics-2026.01.02")

    def test_expression_documents_drop_the_series_name(self) -> None:
        expression = metrics.Expression("node_load", "q", "t", "expression")
        result = [{"metric": {"__name__": "node_load1", "window": "1m"}, "value": [0, "0.5"]}]
        (_, source), = metrics.documents_for_result(expression, result, datetime.now(timezone.utc), 10)
        self.assertEqual(source["metric"], "node_load")
        self.assertEqual(source["labels"], {"window": "1m"})

    def test_series_limit(self) -> None:
        expression = metrics.Expression("e", "q", "e", "expression")
        result = [{"metric": {"i": str(i)}, "value": [0, "1"]} for i in range(10)]
        documents = metrics.documents_for_result(expression, result, datetime.now(timezone.utc), 3)
        self.assertEqual(len(documents), 3)

    def test_catalog_file_names_are_valid(self) -> None:
        text = (ROOT / "opensearch" / "tools" / "metrics.yml").read_text(encoding="utf-8")
        names = re.findall(r"^  - name: (\S+)$", text.split("\nexpressions:", 1)[1], re.MULTILINE)
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertRegex(name, metrics.NAME)


class ClickHouseObservabilityTests(unittest.TestCase):
    def test_diagnostics_are_bounded_without_disabling_incident_evidence(self) -> None:
        root = ET.parse(
            ROOT / "langfuse" / "clickhouse" / "config.d" / "observability.xml"
        ).getroot()
        logger = root.find("logger")
        self.assertIsNotNone(logger)
        self.assertEqual(logger.findtext("level"), "warning")
        self.assertEqual(logger.findtext("size"), "100M")
        self.assertEqual(logger.findtext("count"), "3")
        for name, days in {
            "query_log": 7,
            "query_views_log": 7,
            "part_log": 7,
            "background_schedule_pool_log": 7,
            "text_log": 7,
            "error_log": 30,
        }.items():
            self.assertIn(f"INTERVAL {days} DAY DELETE", root.findtext(f"{name}/ttl", ""))
        for name in {
            "trace_log",
            "query_thread_log",
            "metric_log",
            "instrumentation_trace_log",
            "query_metric_log",
            "asynchronous_metric_log",
            "opentelemetry_span_log",
            "processors_profile_log",
        }:
            self.assertEqual(root.find(name).get("remove"), "1")

    def test_prometheus_replaces_clickhouse_metric_log_tables(self) -> None:
        config = ET.parse(
            ROOT / "langfuse" / "clickhouse" / "config.d" / "observability.xml"
        ).getroot()
        self.assertEqual(config.findtext("prometheus/port"), "9363")
        prometheus = (ROOT / "litellm" / "prometheus.yml").read_text(encoding="utf-8")
        self.assertIn("job_name: langfuse-clickhouse", prometheus)
        self.assertIn('names: ["langfuse-clickhouse"]', prometheus)
        self.assertIn("port: 9363", prometheus)

    def test_cleanup_is_limited_to_disabled_clickhouse_system_logs(self) -> None:
        cleanup = (
            ROOT / "langfuse" / "clickhouse" / "cleanup-system-logs.sql"
        ).read_text(encoding="utf-8")
        self.assertNotIn("default.", cleanup)
        self.assertNotIn("events_", cleanup)
        for name in {
            "trace_log",
            "metric_log",
            "asynchronous_metric_log",
            "opentelemetry_span_log",
            "processors_profile_log",
        }:
            self.assertIn(f"system.{name}", cleanup)
        for name in {
            "background_schedule_pool_log_0",
            "error_log_0",
            "part_log_0",
            "query_log_0",
            "query_views_log_0",
            "text_log_0",
        }:
            self.assertIn(f"DROP TABLE IF EXISTS system.{name}", cleanup)


class UsageTests(unittest.TestCase):
    FORBIDDEN = {"messages", "response", "metadata", "api_key", "requester_ip_address", "proxy_server_request"}

    def available(self) -> dict[str, set[str]]:
        return {
            "LiteLLM_SpendLogs": {
                "request_id", "startTime", "endTime", "spend", "model", "model_group", "api_key",
                "user", "team_id", "messages", "response", "metadata", "requester_ip_address",
            },
            "LiteLLM_VerificationToken": {"token", "key_alias", "user_id", "team_id", "spend", "blocked", "expires"},
            "LiteLLM_UserTable": {"user_id", "user_alias", "spend"},
            "LiteLLM_TeamTable": {"team_id", "team_alias", "blocked"},
            "LiteLLM_ErrorLogs": {"request_id", "startTime", "exception_type", "exception_string"},
            "LiteLLM_AuditLog": {"id", "updated_at", "action", "before_value", "updated_values"},
        }

    def test_queries_never_select_content_or_secrets(self) -> None:
        available = self.available()
        for source in usage.EVENT_SOURCES:
            sql, fields = usage.build_event_query(source, available)
            selected = set(re.findall(r'src\."([^"]+)"', sql.split(" FROM ")[0]))
            self.assertFalse(selected & self.FORBIDDEN, source.key)
            self.assertNotIn("exception_string", selected)
            self.assertNotIn("before_value", selected)
            self.assertIn("__cursor_time", fields)
        sql, fields = usage.build_entity_query("LiteLLM_VerificationToken", available)
        self.assertEqual(fields[0], "__pk")
        self.assertNotIn("token", fields)

    def test_missing_tables_are_skipped(self) -> None:
        self.assertIsNone(usage.build_event_query(usage.SPEND, {}))
        self.assertIsNone(usage.build_entity_query("LiteLLM_UserTable", {}))

    def test_event_documents_are_idempotent_and_denormalized(self) -> None:
        row = {
            "request_id": "r1",
            "spend": usage.Decimal("1.5"),
            "model": "gpt",
            "model_group": None,
            "user": "u1",
            "user_alias": "Alice",
            "key_alias": None,
            "team_id": "t1",
            "team_alias": None,
            "__cursor_time": datetime(2026, 3, 4, 5, 6),
            "__cursor_id": "r1",
        }
        index, identifier, document = usage.event_document(usage.SPEND, "prod", row)
        self.assertEqual(index, "litellm-spend-2026.03")
        self.assertEqual(document["spend"], 1.5)
        self.assertEqual(document["member"], "Alice")
        self.assertEqual(document["team"], "t1")
        self.assertEqual(document["model_name"], "gpt")
        self.assertEqual(document["key_alias"], "unknown")
        self.assertEqual(document["@timestamp"], "2026-03-04T05:06:00.000+00:00")
        self.assertNotIn("__cursor_id", document)
        self.assertEqual(usage.event_document(usage.SPEND, "prod", dict(row))[1], identifier)
        self.assertNotEqual(usage.event_document(usage.SPEND, "dev", dict(row))[1], identifier)

    def test_session_identifiers_are_pseudonymized_before_indexing(self) -> None:
        row = {
            "request_id": "r1",
            "session_id": "private-session",
            "__cursor_time": datetime(2026, 3, 4, 5, 6),
            "__cursor_id": "r1",
        }
        with patch.dict(os.environ, {"OPENSEARCH_SESSION_ID_SALT": "test-only-salt"}):
            _, _, document = usage.event_document(usage.SPEND, "prod", row)
        self.assertNotIn("session_id", document)
        self.assertNotIn("private-session", document.values())
        self.assertRegex(document["session_hash"], r"^[0-9a-f]{64}$")

    def test_entity_usable_flag(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [
            {"__pk": "a", "key_alias": "A", "blocked": False, "expires": None},
            {"__pk": "b", "key_alias": "B", "blocked": True, "expires": None},
            {"__pk": "c", "key_alias": "C", "blocked": False, "expires": datetime(2025, 1, 1)},
        ]
        documents = usage.entity_documents("prod", "LiteLLM_VerificationToken", rows, now)
        self.assertEqual([d["usable"] for _, d in documents], [True, False, False])
        self.assertTrue(all(d["entity_type"] == "key" for _, d in documents))

    def test_retention_selects_only_expired_dated_indices(self) -> None:
        names = [
            "litellm-spend-2025.12",
            "litellm-spend-2026.01",
            "litellm-spend-2026.06",
            "litellm-audit-2025.11",
            "litellm-spend-latest",
            "other-2020.01",
        ]
        # 180 days before 2026-07-01 is 2026-01-02: December ended before it,
        # January still holds retained documents.
        expired = usage.expired_indices(names, usage.USAGE_PREFIXES, date(2026, 7, 1), 180)
        self.assertEqual(expired, ["litellm-audit-2025.11", "litellm-spend-2025.12"])


class SetupTests(unittest.TestCase):
    def test_service_roles_are_least_privilege(self) -> None:
        roles = setup.roles()
        for name in ("litellm_log_writer", "litellm_bridge_writer"):
            for permission in roles[name]["index_permissions"]:
                if permission["index_patterns"] == ["*"]:
                    self.assertEqual(permission["allowed_actions"], ["indices:admin/aliases/get"])
                    continue
                for pattern in permission["index_patterns"]:
                    self.assertTrue(pattern.startswith("litellm-") or pattern in setup.TRACE_PATTERNS, pattern)
            self.assertNotIn("all_access", roles[name]["cluster_permissions"])
        for name in ("litellm_viewer", "litellm_ops_viewer", "litellm_cost_viewer"):
            for permission in roles[name]["index_permissions"]:
                self.assertNotIn("crud", permission["allowed_actions"])
                self.assertNotIn("litellm-audit-*", permission["index_patterns"])
            self.assertEqual(roles[name]["tenant_permissions"][0]["allowed_actions"], ["kibana_all_read"])
        # Spans carry team, cost, and pseudonymous user attributes: operators only.
        for name, role in roles.items():
            patterns = {p for permission in role["index_permissions"] for p in permission["index_patterns"]}
            if patterns & set(setup.TRACE_PATTERNS):
                self.assertIn(name, ("litellm_operator", "litellm_log_writer"))

    def test_ism_policy_deletes_after_retention(self) -> None:
        body = setup.ism_policy_body(["litellm-logs-*"], 7, "test")
        hot = body["policy"]["states"][0]
        self.assertEqual(hot["transitions"][0]["conditions"], {"min_index_age": "7d"})
        self.assertEqual(body["policy"]["ism_template"][0]["index_patterns"], ["litellm-logs-*"])

    def test_templates_cover_every_written_index(self) -> None:
        patterns = {p for t in setup.index_templates(0).values() for p in t["index_patterns"]}
        for expected in (
            "litellm-logs-*",
            "litellm-metrics-*",
            "litellm-alert-state-*",
            "litellm-spend-*",
            "litellm-provider-errors-*",
            "litellm-audit-*",
            "litellm-entities",
        ):
            self.assertIn(expected, patterns)


class SavedObjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.objects = saved_objects.build_all()
        self.by_id = {(item["type"], item["id"]): item for item in self.objects}

    def test_ids_are_unique_and_references_resolve(self) -> None:
        self.assertEqual(len(self.by_id), len(self.objects))
        for item in self.objects:
            for reference in item["references"]:
                self.assertIn((reference["type"], reference["id"]), self.by_id, f"{item['id']} -> {reference}")

    def test_visualizations_are_valid_json(self) -> None:
        for item in self.objects:
            attributes = item["attributes"]
            if item["type"] == "visualization":
                state = json.loads(attributes["visState"])
                self.assertEqual(state["title"], attributes["title"])
                if state["type"] == "vega":
                    spec = json.loads(state["params"]["spec"])
                    self.assertEqual(spec["$schema"], "https://vega.github.io/schema/vega/v5.json")
                    self.assertFalse(state["params"]["enableExternalUrls"])
            if item["type"] in ("visualization", "search", "dashboard"):
                json.loads(attributes["kibanaSavedObjectMeta"]["searchSourceJSON"])
            if item["type"] == "dashboard":
                panels = json.loads(attributes["panelsJSON"])
                self.assertEqual(len(panels), len(item["references"]))
                for panel in panels:
                    grid = panel["gridData"]
                    self.assertLessEqual(grid["x"] + grid["w"], 48)

    def test_every_grafana_dashboard_has_an_equivalent(self) -> None:
        titles = {item["attributes"]["title"] for item in self.objects if item["type"] == "dashboard"}
        for path in (ROOT / "grafana" / "dashboards").glob("litellm-*/*.json"):
            grafana_title = json.loads(path.read_text(encoding="utf-8"))["title"]
            self.assertIn(grafana_title, titles)

    def test_aggregated_fields_exist_before_any_data(self) -> None:
        for item in self.objects:
            if item["type"] != "visualization":
                continue
            state = json.loads(item["attributes"]["visState"])
            fields = [agg["params"]["field"] for agg in state["aggs"] if "field" in agg.get("params", {})]
            fields += [control["fieldName"] for control in state["params"].get("controls", [])]
            if not fields:
                continue
            index_refs = [r["id"] for r in item["references"] if r["type"] == "index-pattern"]
            known: set[str] = set()
            for index_id in index_refs:
                pattern = saved_objects.INDEX_PATTERNS[index_id][0]
                known |= {f["name"] for f in setup.static_fields(pattern) if f["aggregatable"]}
            for field in fields:
                self.assertIn(field, known, f"{item['id']} aggregates unknown field {field}")

    def test_referenced_metrics_exist_in_catalog(self) -> None:
        text = (ROOT / "opensearch" / "tools" / "metrics.yml").read_text(encoding="utf-8")
        catalog = set(re.findall(r"^  - name: (\S+)$", text, re.MULTILINE))
        for item in self.objects:
            if item["type"] not in ("visualization",):
                continue
            meta = json.loads(item["attributes"]["kibanaSavedObjectMeta"]["searchSourceJSON"])
            query = meta.get("query", {}).get("query", "")
            names = re.findall(r'metric:"([^"]+)"', query)
            if names:
                # Raw mirrored series may share a name with an expression.
                self.assertTrue(query.startswith("kind:expression and ("), item["id"])
            for name in names:
                self.assertIn(name, catalog, item["id"])


class NotebookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.objects = notebooks.build_all()

    def test_notebooks_have_stable_unique_ids_and_top_down_order(self) -> None:
        self.assertEqual(
            [item["id"] for item in self.objects],
            list(notebooks.NOTEBOOK_IDS.values()),
        )
        self.assertEqual(len({item["id"] for item in self.objects}), len(self.objects))
        self.assertFalse(set(notebooks.NOTEBOOK_IDS.values()) & set(notebooks.LEGACY_NOTEBOOK_IDS))
        for item in self.objects:
            self.assertEqual(str(uuid.UUID(item["id"])), item["id"])
            self.assertEqual(item["type"], notebooks.NOTEBOOK_TYPE)
            saved = item["attributes"]["savedNotebook"]
            self.assertRegex(saved["name"], r"^0[1-5] - ")
            self.assertGreaterEqual(len(saved["paragraphs"]), 5)
            self.assertEqual(
                len({paragraph["id"] for paragraph in saved["paragraphs"]}),
                len(saved["paragraphs"]),
            )
        verifier = (ROOT / "scripts" / "verify-litellm-upgrade.py").read_text(encoding="utf-8")
        for object_id in notebooks.NOTEBOOK_IDS.values():
            self.assertIn(object_id, verifier)

    def test_paragraph_schema_supports_read_only_query_execution(self) -> None:
        for item in self.objects:
            for paragraph in item["attributes"]["savedNotebook"]["paragraphs"]:
                text = paragraph["input"]["inputText"]
                expected = "QUERY" if text.startswith("%ppl\n") else "MARKDOWN"
                self.assertEqual(paragraph["input"]["inputType"], expected)
                self.assertEqual(paragraph["output"][0]["outputType"], expected)

    def test_query_paragraphs_use_known_indices_and_do_not_query_content(self) -> None:
        allowed_sources = {
            "litellm-logs-*",
            "litellm-metrics-*",
            "litellm-alert-state-*",
            "litellm-provider-errors-*",
            "litellm-spend-*",
            "otel-v1-apm-span-*",
            "security-auditlog-*",
        }
        forbidden_fields = {"messages", "response", "api_key", "authorization", "requester_ip_address"}
        query_count = 0
        for item in self.objects:
            for paragraph in item["attributes"]["savedNotebook"]["paragraphs"]:
                text = paragraph["input"]["inputText"]
                if not text.startswith("%ppl\n"):
                    continue
                query_count += 1
                query = text.removeprefix("%ppl\n")
                match = re.search(r"^source\s*=\s*(\S+)", query)
                self.assertIsNotNone(match, item["id"])
                self.assertIn(match.group(1), allowed_sources)
                selected = query.split("| fields ", 1)[1].split("|", 1)[0] if "| fields " in query else ""
                self.assertFalse(forbidden_fields & set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", selected)))
        self.assertGreaterEqual(query_count, 15)
        all_queries = "\n".join(
            paragraph["input"]["inputText"]
            for item in self.objects
            for paragraph in item["attributes"]["savedNotebook"]["paragraphs"]
            if paragraph["input"]["inputText"].startswith("%ppl\n")
        )
        self.assertNotIn("dedup `labels.job`", all_queries)
        self.assertIn("provider, status\n", all_queries)
        self.assertNotIn("provider, status_code\n", all_queries)

    def test_markdown_documents_repository_troubleshooting_entry_points(self) -> None:
        markdown = "\n".join(
            paragraph["input"]["inputText"]
            for item in self.objects
            for paragraph in item["attributes"]["savedNotebook"]["paragraphs"]
            if paragraph["input"]["inputText"].startswith("%md\n")
        )
        for command in (
            "maintenance.py game-day-preflight",
            "maintenance.py backup",
            "maintenance.py upgrade-verify",
        ):
            self.assertIn(command, markdown)
        self.assertNotIn("OPENSEARCH_ADMIN_PASSWORD=", markdown)


@unittest.skipUnless(HAS_JSON_LOGGER, "python-json-logger and orjson are not installed")
class JsonLoggingTests(unittest.TestCase):
    def test_each_record_is_one_json_line_with_the_level_first(self) -> None:
        import io
        import logging

        from litellm_opensearch.__main__ import configure_logging

        stream = io.StringIO()
        root = logging.getLogger()
        previous = (root.handlers[:], root.level)
        try:
            with patch.object(sys, "stderr", stream):
                configure_logging()
            logger = logging.getLogger("litellm_opensearch")
            logger.info("Indexed %d new %s row(s) from %s", 3, "spend", "prod")
            try:
                raise RuntimeError("opensearch unavailable")
            except RuntimeError:
                logger.error("Bridge usage cycle failed", exc_info=True)
        finally:
            root.handlers[:] = previous[0]
            root.setLevel(previous[1])

        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        info, error = (json.loads(line) for line in lines)
        self.assertTrue(lines[0].startswith('{"level":"INFO"'))
        self.assertEqual(info["logger"], "litellm_opensearch")
        self.assertEqual(info["message"], "Indexed 3 new spend row(s) from prod")
        self.assertIn("time", info)
        self.assertEqual(error["level"], "ERROR")
        self.assertIn("RuntimeError: opensearch unavailable", error["exc_info"])


class BridgeFailureLoggingTests(unittest.TestCase):
    def test_cycle_failures_are_logged_with_their_tracebacks(self) -> None:
        from litellm_opensearch import bridge

        sync = Mock()
        sync.run_events.side_effect = RuntimeError("usage sync failed")
        sync.run_entities.side_effect = RuntimeError("entity snapshot failed")

        def stop_after_first_cycle(_seconds: float) -> None:
            bridge.STOP = True

        self.addCleanup(setattr, bridge, "STOP", False)
        with patch.object(bridge.signal, "signal"), \
                patch.object(bridge, "postgres_connector"), \
                patch.object(bridge, "opensearch_from_env"), \
                patch.object(bridge, "HttpClient"), \
                patch.object(bridge.metrics, "load_catalog"), \
                patch.object(bridge.alert_rules, "load_rules", return_value=[]), \
                patch.object(bridge.usage, "UsageSync", return_value=sync), \
                patch.object(bridge, "evaluate_metrics", side_effect=OSError("prometheus unreachable")), \
                patch.object(bridge.usage, "apply_retention", side_effect=RuntimeError("retention failed")), \
                patch.object(bridge.time, "sleep", side_effect=stop_after_first_cycle), \
                self.assertLogs("litellm_opensearch", level="ERROR") as captured:
            self.assertEqual(bridge.run(), 0)

        failures = {record.getMessage(): record for record in captured.records}
        self.assertEqual(
            set(failures),
            {
                "Bridge metrics cycle failed: prometheus unreachable",
                "Bridge usage cycle failed: usage sync failed",
                "Bridge entity snapshot failed: entity snapshot failed",
                "Usage retention failed: retention failed",
            },
        )
        for message, record in failures.items():
            self.assertIsNotNone(record.exc_info, message)
            self.assertIn(message.rsplit(": ", 1)[1], str(record.exc_info[1]))


if __name__ == "__main__":
    unittest.main()
