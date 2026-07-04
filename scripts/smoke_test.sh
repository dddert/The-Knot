#!/usr/bin/env bash
set -euo pipefail
BASE_URL="${BASE_URL:-http://localhost:8000}"
ADMIN=(-H "X-Demo-Role-Token: admin-token")
ANALYST=(-H "X-Demo-Role-Token: analyst-token")
MANAGER=(-H "X-Demo-Role-Token: manager-token")
PARTNER=(-H "X-Demo-Role-Token: partner-token")
JSON=(-H "Content-Type: application/json")

require_code() {
  local expected="$1"; shift
  local outfile
  outfile="$(mktemp)"
  local code
  code=$(curl -s -o "$outfile" -w "%{http_code}" "$@")
  if [[ "$code" != "$expected" ]]; then
    echo "Expected HTTP $expected, got $code for: curl $*" >&2
    cat "$outfile" >&2 || true
    rm -f "$outfile"
    exit 1
  fi
  cat "$outfile"
  rm -f "$outfile"
}

require_jq() {
  local expr="$1"
  local msg="$2"
  if ! jq -e "$expr" >/dev/null; then
    echo "Assertion failed: $msg" >&2
    exit 1
  fi
}

echo "1) health dependencies"
health=$(require_code 200 "$BASE_URL/health")
echo "$health" | jq .
echo "$health" | require_jq '.status == "ok" or .status == "degraded"' "health status present"
echo "$health" | require_jq '.dependencies.neo4j and .dependencies.postgres' "dependency statuses present"

echo "2) init schema as admin"
init=$(require_code 200 -X POST "$BASE_URL/api/graph/init-schema?user_id=smoke&role=admin" "${ADMIN[@]}")
echo "$init" | jq .
echo "$init" | require_jq '.status == "ok" and .constraints_and_indexes >= 22' "schema init succeeded"

echo "3) manager without token must fail"
require_code 401 -X POST "$BASE_URL/api/graph/init-schema?user_id=smoke&role=manager" | jq .

echo "4) manager with token still cannot init schema"
require_code 403 -X POST "$BASE_URL/api/graph/init-schema?user_id=smoke&role=manager" "${MANAGER[@]}" | jq .

echo "5) fake uploaded document cannot be processed"
require_code 404 -X POST "$BASE_URL/api/documents/doc_fake_missing/process?user_id=smoke&role=analyst" "${ANALYST[@]}" | jq .

echo "6) import explicit mock"
imported=$(require_code 200 -X POST "$BASE_URL/api/documents/process-mock?user_id=smoke&role=analyst" "${ANALYST[@]}")
echo "$imported" | jq .
echo "$imported" | require_jq '.facts_count > 0' "mock import returned facts"

echo "7) numeric search should return facts"
search=$(require_code 200 -X POST "$BASE_URL/api/search?user_id=smoke&role=analyst" \
  "${ANALYST[@]}" "${JSON[@]}" \
  -d '{"query":"Какие методы обессоливания воды подходят?","filters":{"geo_scope":"all","confidence_min":0,"numeric_parameter":"dry_residue","numeric_max":1000,"numeric_unit":"mg/L"},"graph_mode":"compact"}')
echo "$search" | jq '{facts: (.facts|length), sources: (.sources|length), debug: .debug}'
echo "$search" | require_jq '(.facts | length) > 0' "numeric search returned facts"

echo "8) partner must not see internal facts"
partner=$(require_code 200 -X POST "$BASE_URL/api/search?user_id=partner&role=external_partner" \
  "${PARTNER[@]}" "${JSON[@]}" \
  -d '{"query":"Какие методы обессоливания воды подходят?","filters":{"geo_scope":"all","confidence_min":0},"graph_mode":"compact"}')
echo "$partner" | jq '{facts: (.facts|length), sources: (.sources|length)}'
echo "$partner" | require_jq '(.facts | length) == 0 and (.sources | length) == 0' "partner cannot see internal mock facts"

echo "9) PDF export must return base64 and re-fetch facts by fact_ids"
fact_ids=$(echo "$search" | jq -c '[.facts[].id]')
pdf_payload=$(jq -n --argjson fact_ids "$fact_ids" '{answer:{summary:"Smoke PDF",sections:[],confidence:0.8},fact_ids:$fact_ids}')
pdf=$(require_code 200 -X POST "$BASE_URL/api/export/pdf?user_id=smoke&role=analyst" \
  "${ANALYST[@]}" "${JSON[@]}" \
  -d "$pdf_payload")
echo "$pdf" | jq '{format, filename, server_side_facts, len: (.content_base64 | length)}'
echo "$pdf" | require_jq '.format == "pdf" and .server_side_facts == true and (.content_base64 | length) > 100' "PDF export has base64 and server-side facts"

echo "10) subgraph requires fact_ids"
require_code 400 "$BASE_URL/api/graph/subgraph?user_id=smoke&role=analyst" "${ANALYST[@]}" | jq .

echo "11) audit is not available to analyst"
require_code 403 "$BASE_URL/api/audit?user_id=smoke&role=analyst" "${ANALYST[@]}" | jq .

echo "Smoke test passed."
