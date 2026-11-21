SYSTEM FLUSH LOGS;

TRUNCATE TABLE IF EXISTS system.trace_log;
TRUNCATE TABLE IF EXISTS system.query_thread_log;
TRUNCATE TABLE IF EXISTS system.metric_log;
TRUNCATE TABLE IF EXISTS system.instrumentation_trace_log;
TRUNCATE TABLE IF EXISTS system.query_metric_log;
TRUNCATE TABLE IF EXISTS system.asynchronous_metric_log;
TRUNCATE TABLE IF EXISTS system.opentelemetry_span_log;
TRUNCATE TABLE IF EXISTS system.processors_profile_log;

DROP TABLE IF EXISTS system.background_schedule_pool_log_0;
DROP TABLE IF EXISTS system.error_log_0;
DROP TABLE IF EXISTS system.part_log_0;
DROP TABLE IF EXISTS system.query_log_0;
DROP TABLE IF EXISTS system.query_views_log_0;
DROP TABLE IF EXISTS system.text_log_0;
