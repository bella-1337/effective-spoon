\set ON_ERROR_STOP on

BEGIN;

INSERT INTO "LiteLLM_TeamTable" (
  team_id,
  team_alias,
  admins,
  members,
  members_with_roles,
  metadata,
  models,
  blocked,
  max_budget,
  spend
)
VALUES
  (
    'demo-team-atlas',
    'Demo Team Atlas',
    ARRAY['demo-alice'],
    ARRAY['demo-alice', 'demo-bob'],
    '[{"user_id":"demo-alice","role":"admin"},{"user_id":"demo-bob","role":"user"}]'::jsonb,
    '{"demo":true}'::jsonb,
    ARRAY['gpt-4o', 'gpt-4o-mini'],
    false,
    75.0,
    14.64
  ),
  (
    'demo-team-orion',
    'Demo Team Orion',
    ARRAY['demo-carol'],
    ARRAY['demo-carol', 'demo-dave'],
    '[{"user_id":"demo-carol","role":"admin"},{"user_id":"demo-dave","role":"user"}]'::jsonb,
    '{"demo":true}'::jsonb,
    ARRAY['gpt-4o', 'gpt-4o-mini'],
    false,
    125.0,
    29.87
  )
ON CONFLICT (team_id) DO UPDATE SET
  team_alias = EXCLUDED.team_alias,
  admins = EXCLUDED.admins,
  members = EXCLUDED.members,
  members_with_roles = EXCLUDED.members_with_roles,
  metadata = EXCLUDED.metadata,
  models = EXCLUDED.models,
  blocked = EXCLUDED.blocked,
  max_budget = EXCLUDED.max_budget,
  spend = EXCLUDED.spend;

INSERT INTO "LiteLLM_UserTable" (
  user_id,
  user_alias,
  user_email,
  user_role,
  team_id,
  teams,
  models,
  metadata,
  max_budget,
  spend
)
VALUES
  (
    'demo-alice',
    'Demo Alice',
    'alice.demo@example.test',
    'INTERNAL_USER',
    'demo-team-atlas',
    ARRAY['demo-team-atlas'],
    ARRAY['gpt-4o', 'gpt-4o-mini'],
    '{"demo":true}'::jsonb,
    40.0,
    9.93
  ),
  (
    'demo-bob',
    'Demo Bob',
    'bob.demo@example.test',
    'INTERNAL_USER',
    'demo-team-atlas',
    ARRAY['demo-team-atlas'],
    ARRAY['gpt-4o', 'gpt-4o-mini'],
    '{"demo":true}'::jsonb,
    35.0,
    4.71
  ),
  (
    'demo-carol',
    'Demo Carol',
    'carol.demo@example.test',
    'INTERNAL_USER',
    'demo-team-orion',
    ARRAY['demo-team-orion'],
    ARRAY['gpt-4o', 'gpt-4o-mini'],
    '{"demo":true}'::jsonb,
    70.0,
    17.58
  ),
  (
    'demo-dave',
    'Demo Dave',
    'dave.demo@example.test',
    'INTERNAL_USER',
    'demo-team-orion',
    ARRAY['demo-team-orion'],
    ARRAY['gpt-4o', 'gpt-4o-mini'],
    '{"demo":true}'::jsonb,
    55.0,
    12.29
  )
ON CONFLICT (user_id) DO UPDATE SET
  user_alias = EXCLUDED.user_alias,
  user_email = EXCLUDED.user_email,
  user_role = EXCLUDED.user_role,
  team_id = EXCLUDED.team_id,
  teams = EXCLUDED.teams,
  models = EXCLUDED.models,
  metadata = EXCLUDED.metadata,
  max_budget = EXCLUDED.max_budget,
  spend = EXCLUDED.spend;

INSERT INTO "LiteLLM_VerificationToken" (
  token,
  key_name,
  key_alias,
  user_id,
  team_id,
  models,
  metadata,
  spend,
  total_spend,
  max_budget,
  blocked,
  created_by,
  updated_by
)
VALUES
  ('demo-token-atlas-alice', 'demo-atlas-alice', 'Atlas - Alice', 'demo-alice', 'demo-team-atlas', ARRAY['gpt-4o', 'gpt-4o-mini'], '{"demo":true}'::jsonb, 9.93, 9.93, 40.0, true, 'system', 'system'),
  ('demo-token-atlas-bob', 'demo-atlas-bob', 'Atlas - Bob', 'demo-bob', 'demo-team-atlas', ARRAY['gpt-4o', 'gpt-4o-mini'], '{"demo":true}'::jsonb, 4.71, 4.71, 35.0, true, 'system', 'system'),
  ('demo-token-orion-carol', 'demo-orion-carol', 'Orion - Carol', 'demo-carol', 'demo-team-orion', ARRAY['gpt-4o', 'gpt-4o-mini'], '{"demo":true}'::jsonb, 17.58, 17.58, 70.0, true, 'system', 'system'),
  ('demo-token-orion-dave', 'demo-orion-dave', 'Orion - Dave', 'demo-dave', 'demo-team-orion', ARRAY['gpt-4o', 'gpt-4o-mini'], '{"demo":true}'::jsonb, 12.29, 12.29, 55.0, true, 'system', 'system')
ON CONFLICT (token) DO UPDATE SET
  key_name = EXCLUDED.key_name,
  key_alias = EXCLUDED.key_alias,
  user_id = EXCLUDED.user_id,
  team_id = EXCLUDED.team_id,
  models = EXCLUDED.models,
  metadata = EXCLUDED.metadata,
  spend = EXCLUDED.spend,
  total_spend = EXCLUDED.total_spend,
  max_budget = EXCLUDED.max_budget,
  blocked = EXCLUDED.blocked,
  updated_by = EXCLUDED.updated_by;

INSERT INTO "LiteLLM_SpendLogs" (
  request_id,
  call_type,
  api_key,
  spend,
  total_tokens,
  prompt_tokens,
  completion_tokens,
  "startTime",
  "endTime",
  model,
  model_group,
  custom_llm_provider,
  "user",
  team_id,
  status
)
VALUES
  ('demo-atlas-001', 'acompletion', 'demo-token-atlas-alice', 2.31, 19100, 15100, 4000, NOW() - INTERVAL '6 days', NOW() - INTERVAL '6 days' + INTERVAL '4 seconds', 'gpt-4o', 'gpt-4o', 'openai', 'demo-alice', 'demo-team-atlas', 'success'),
  ('demo-atlas-002', 'acompletion', 'demo-token-atlas-alice', 1.12, 28200, 24500, 3700, NOW() - INTERVAL '5 days', NOW() - INTERVAL '5 days' + INTERVAL '3 seconds', 'gpt-4o-mini', 'gpt-4o-mini', 'openai', 'demo-alice', 'demo-team-atlas', 'success'),
  ('demo-atlas-003', 'acompletion', 'demo-token-atlas-bob', 3.04, 25100, 18700, 6400, NOW() - INTERVAL '4 days', NOW() - INTERVAL '4 days' + INTERVAL '5 seconds', 'gpt-4o', 'gpt-4o', 'openai', 'demo-bob', 'demo-team-atlas', 'success'),
  ('demo-atlas-004', 'acompletion', 'demo-token-atlas-alice', 2.48, 20300, 16400, 3900, NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days' + INTERVAL '4 seconds', 'gpt-4o', 'gpt-4o', 'openai', 'demo-alice', 'demo-team-atlas', 'success'),
  ('demo-atlas-005', 'acompletion', 'demo-token-atlas-bob', 1.67, 39600, 34100, 5500, NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days' + INTERVAL '3 seconds', 'gpt-4o-mini', 'gpt-4o-mini', 'openai', 'demo-bob', 'demo-team-atlas', 'success'),
  ('demo-atlas-006', 'acompletion', 'demo-token-atlas-alice', 4.02, 33700, 24600, 9100, NOW() - INTERVAL '8 hours', NOW() - INTERVAL '8 hours' + INTERVAL '6 seconds', 'gpt-4o', 'gpt-4o', 'openai', 'demo-alice', 'demo-team-atlas', 'success'),
  ('demo-orion-001', 'acompletion', 'demo-token-orion-carol', 5.42, 44800, 33100, 11700, NOW() - INTERVAL '6 days', NOW() - INTERVAL '6 days' + INTERVAL '7 seconds', 'gpt-4o', 'gpt-4o', 'openai', 'demo-carol', 'demo-team-orion', 'success'),
  ('demo-orion-002', 'acompletion', 'demo-token-orion-dave', 2.09, 50100, 42700, 7400, NOW() - INTERVAL '5 days', NOW() - INTERVAL '5 days' + INTERVAL '4 seconds', 'gpt-4o-mini', 'gpt-4o-mini', 'openai', 'demo-dave', 'demo-team-orion', 'success'),
  ('demo-orion-003', 'acompletion', 'demo-token-orion-carol', 4.77, 39700, 29600, 10100, NOW() - INTERVAL '4 days', NOW() - INTERVAL '4 days' + INTERVAL '6 seconds', 'gpt-4o', 'gpt-4o', 'openai', 'demo-carol', 'demo-team-orion', 'success'),
  ('demo-orion-004', 'acompletion', 'demo-token-orion-dave', 3.88, 32100, 24300, 7800, NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days' + INTERVAL '5 seconds', 'gpt-4o', 'gpt-4o', 'openai', 'demo-dave', 'demo-team-orion', 'success'),
  ('demo-orion-005', 'acompletion', 'demo-token-orion-carol', 7.39, 61300, 45200, 16100, NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days' + INTERVAL '8 seconds', 'gpt-4o', 'gpt-4o', 'openai', 'demo-carol', 'demo-team-orion', 'success'),
  ('demo-orion-006', 'acompletion', 'demo-token-orion-dave', 6.32, 146200, 128400, 17800, NOW() - INTERVAL '4 hours', NOW() - INTERVAL '4 hours' + INTERVAL '7 seconds', 'gpt-4o-mini', 'gpt-4o-mini', 'openai', 'demo-dave', 'demo-team-orion', 'success')
ON CONFLICT (request_id) DO UPDATE SET
  spend = EXCLUDED.spend,
  total_tokens = EXCLUDED.total_tokens,
  prompt_tokens = EXCLUDED.prompt_tokens,
  completion_tokens = EXCLUDED.completion_tokens,
  "startTime" = EXCLUDED."startTime",
  "endTime" = EXCLUDED."endTime",
  model = EXCLUDED.model,
  model_group = EXCLUDED.model_group,
  custom_llm_provider = EXCLUDED.custom_llm_provider,
  "user" = EXCLUDED."user",
  team_id = EXCLUDED.team_id,
  status = EXCLUDED.status;

WITH demo_spend AS (
  SELECT team_id, SUM(spend) AS spend
  FROM "LiteLLM_SpendLogs"
  WHERE request_id LIKE 'demo-%'
    AND team_id IN ('demo-team-atlas', 'demo-team-orion')
  GROUP BY team_id
)
UPDATE "LiteLLM_TeamTable" AS team
SET spend = demo_spend.spend
FROM demo_spend
WHERE team.team_id = demo_spend.team_id;

WITH demo_spend AS (
  SELECT "user" AS user_id, SUM(spend) AS spend
  FROM "LiteLLM_SpendLogs"
  WHERE request_id LIKE 'demo-%'
    AND "user" IN ('demo-alice', 'demo-bob', 'demo-carol', 'demo-dave')
  GROUP BY "user"
)
UPDATE "LiteLLM_UserTable" AS demo_user
SET spend = demo_spend.spend
FROM demo_spend
WHERE demo_user.user_id = demo_spend.user_id;

WITH demo_spend AS (
  SELECT api_key AS token, SUM(spend) AS spend
  FROM "LiteLLM_SpendLogs"
  WHERE request_id LIKE 'demo-%'
    AND api_key IN (
      'demo-token-atlas-alice',
      'demo-token-atlas-bob',
      'demo-token-orion-carol',
      'demo-token-orion-dave'
    )
  GROUP BY api_key
)
UPDATE "LiteLLM_VerificationToken" AS demo_key
SET spend = demo_spend.spend,
    total_spend = demo_spend.spend
FROM demo_spend
WHERE demo_key.token = demo_spend.token;

COMMIT;
