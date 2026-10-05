-- Inspect the exact columns provided by the installed LiteLLM version before
-- relying on the cost dashboard queries.
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('LiteLLM_SpendLogs', 'LiteLLM_VerificationToken')
ORDER BY table_name, ordinal_position;

-- Optional performance indexes once the table grows beyond roughly 1-2M rows:
-- CREATE INDEX CONCURRENTLY "spendlogs_model_start_idx"
--   ON "LiteLLM_SpendLogs" (model, "startTime");
-- CREATE INDEX CONCURRENTLY "spendlogs_api_key_start_idx"
--   ON "LiteLLM_SpendLogs" (api_key, "startTime");

-- Optional read-only user for Grafana (preferred over DB-owner access):
-- CREATE ROLE grafana_ro LOGIN PASSWORD '<random>';
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public
--   GRANT SELECT ON TABLES TO grafana_ro;
