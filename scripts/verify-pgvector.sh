#!/bin/sh

set -eu

store_id="sme-knowledge"

scalar() {
  docker compose exec -T vector-postgres \
    psql --no-psqlrc --quiet --tuples-only --no-align \
    --username vector --dbname vector_store --command "$1"
}

assert_rejected() {
  description="$1"
  sql="$2"

  if docker compose exec -T vector-postgres \
    psql --no-psqlrc --quiet --set ON_ERROR_STOP=1 \
    --username vector --dbname vector_store --command "$sql" >/dev/null 2>&1; then
    printf 'FAIL: %s was accepted\n' "$description" >&2
    exit 1
  fi
  printf 'PASS: %s was rejected\n' "$description"
}

extension_version="$(scalar "SELECT extversion FROM pg_extension WHERE extname = 'vector';")"
initial_count="$(scalar "SELECT COUNT(*) FROM embeddings WHERE vector_store_id = '$store_id';")"

test -n "$extension_version"
test "$initial_count" = "0"
printf 'PASS: pgvector %s is installed and the knowledge store is empty\n' "$extension_version"

unauthenticated_status="$(
  curl --silent --output /dev/null --write-out '%{http_code}' \
    --request POST https://llm.example.test/v1/vector_stores \
    --header 'Content-Type: application/json' \
    --data '{"name":"must-not-exist"}'
)"
test "$unauthenticated_status" = "401"
printf 'PASS: unauthenticated vector-store request returned HTTP 401\n'

connector_unauthenticated_status="$(
  docker compose exec -T litellm-pgvector \
    curl --silent --output /dev/null --write-out '%{http_code}' \
      http://localhost:8000/v1/vector_stores
)"
case "$connector_unauthenticated_status" in
  401|403)
    printf 'PASS: unauthenticated pgvector connector request returned HTTP %s\n' \
      "$connector_unauthenticated_status"
    ;;
  *)
    printf 'FAIL: unauthenticated pgvector connector request returned HTTP %s\n' \
      "$connector_unauthenticated_status" >&2
    exit 1
    ;;
esac

master_key="$(docker compose exec -T litellm-dev printenv LITELLM_MASTER_KEY | tr -d '\r')"
registry_response="$(
  curl --silent --show-error --fail \
    https://llm-dev.example.test/vector_store/list \
    --header "Authorization: Bearer $master_key"
)"
printf '%s' "$registry_response" |
  jq --exit-status \
    '.data[] | select(.vector_store_id == "sme-knowledge" and .custom_llm_provider == "pg_vector")' \
    >/dev/null
printf 'PASS: LiteLLM lists the managed sme-knowledge pgvector store\n'

search_response="$(
  curl --silent --show-error --fail \
    --request POST \
    https://llm-dev.example.test/v1/vector_stores/sme-knowledge/search \
    --header "Authorization: Bearer $master_key" \
    --header 'Content-Type: application/json' \
    --data '{"query":"empty store verification","max_num_results":1}'
)"
printf '%s' "$search_response" |
  jq --exit-status '.object == "vector_store.search_results.page" and (.data | length) == 0' \
    >/dev/null
printf 'PASS: LiteLLM searched the empty store through the official connector\n'

malformed_status="$(
  curl --silent --output /dev/null --write-out '%{http_code}' \
    --request POST https://llm-dev.example.test/v1/vector_stores \
    --header "Authorization: Bearer $master_key" \
    --header 'Content-Type: application/json' \
    --data '{'
)"
case "$malformed_status" in
  400|422)
    printf 'PASS: authenticated invalid JSON request returned HTTP %s\n' "$malformed_status"
    ;;
  *)
    printf 'FAIL: authenticated invalid JSON request returned HTTP %s\n' "$malformed_status" >&2
    exit 1
    ;;
esac
unset master_key

assert_rejected \
  "embedding for an unknown store" \
  "INSERT INTO embeddings (id, vector_store_id, content, embedding) VALUES (gen_random_uuid()::text, 'unknown-store', 'invalid', array_fill(0::real, ARRAY[1024])::vector);"

assert_rejected \
  "embedding with the wrong dimensions" \
  "INSERT INTO embeddings (id, vector_store_id, content, embedding) VALUES (gen_random_uuid()::text, '$store_id', 'invalid', '[0,0,0]'::vector);"

assert_rejected \
  "empty content" \
  "INSERT INTO embeddings (id, vector_store_id, content, embedding) VALUES (gen_random_uuid()::text, '$store_id', '', array_fill(0::real, ARRAY[1024])::vector);"

final_count="$(scalar "SELECT COUNT(*) FROM embeddings WHERE vector_store_id = '$store_id';")"
test "$final_count" = "$initial_count"
printf 'PASS: rejected inputs created no vector rows\n'
