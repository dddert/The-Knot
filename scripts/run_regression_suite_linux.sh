#!/usr/bin/env bash
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
ROLE="${ROLE:-analyst}"
USER_ID="${USER_ID:-tester}"
TOKEN="${TOKEN:-analyst-token}"
OUT_DIR="${OUT_DIR:-regression-results-linux}"
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-10}"
MAX_TIME="${MAX_TIME:-300}"

mkdir -p "$OUT_DIR"

queries=(
  "Какие способы удаления SO2 из металлургических газов описаны в российских источниках после 2018 года?"
  "Какие технологии кучного выщелачивания применяются в условиях холодного климата?"
  "Какие способы переработки техногенного гипса описаны в корпусе?"
  "Найди процессы с извлечением никеля не менее 90% при температуре ниже 100 °C"
  "Найди процессы с извлечением меди не менее 90% при температуре выше 200 °C"
  "Сравни мокрые и сухие способы удаления SO2 из металлургических газов"
  "Какие эксперты и лаборатории занимаются автоклавным выщелачиванием?"
  "Найди противоречивые данные о влиянии температуры на извлечение меди из металлургических шлаков"
  "Какие пробелы в исследованиях кучного выщелачивания в холодном климате видны по найденным источникам?"
  "Какие патенты и технологические решения по автоклавному выщелачиванию упоминаются в корпусе?"
)

names=(
  "01_so2_after_2018"
  "02_heap_cold_climate"
  "03_technogenic_gypsum"
  "04_numeric_ni_negative"
  "05_numeric_cu_positive"
  "06_so2_comparison"
  "07_expert_autoclave"
  "08_contradictions_cu_temperature"
  "09_knowledge_gaps_cold_heap"
  "10_patents_autoclave"
)

# Preflight
for cmd in curl jq; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $cmd" >&2
    exit 2
  fi
done

if ! curl -fsS \
  --connect-timeout "$CONNECT_TIMEOUT" \
  --max-time 20 \
  "$BASE_URL/health" >/dev/null; then
  echo "ERROR: backend health check failed: $BASE_URL/health" >&2
  exit 2
fi

ok=0
failed=0
mock_detected=0

for i in "${!queries[@]}"; do
  name="${names[$i]}"
  query="${queries[$i]}"
  full="$OUT_DIR/$name.full.json"
  summary="$OUT_DIR/$name.summary.json"
  err="$OUT_DIR/$name.error.txt"
  body="$OUT_DIR/$name.response.tmp"

  echo "=== $name ==="
  echo "$query"

  payload="$(jq -cn --arg q "$query" '{query:$q,filters:{}}')"

  http_code="$(
    curl -sS \
      --connect-timeout "$CONNECT_TIMEOUT" \
      --max-time "$MAX_TIME" \
      -o "$body" \
      -w '%{http_code}' \
      -X POST \
      "$BASE_URL/api/search?role=$ROLE&user_id=$USER_ID" \
      -H 'Content-Type: application/json; charset=utf-8' \
      -H "X-Demo-Role-Token: $TOKEN" \
      --data-binary "$payload"
  )"
  curl_rc=$?

  if [[ $curl_rc -ne 0 ]]; then
    {
      echo "curl_exit=$curl_rc"
      echo "http_code=${http_code:-unknown}"
      [[ -f "$body" ]] && cat "$body"
    } > "$err"
    echo "FAIL: curl exit=$curl_rc"
    failed=$((failed + 1))
    echo
    continue
  fi

  if [[ "$http_code" != "200" ]]; then
    {
      echo "http_code=$http_code"
      cat "$body"
    } > "$err"
    echo "FAIL: HTTP $http_code"
    failed=$((failed + 1))
    echo
    continue
  fi

  if ! jq -e . "$body" >/dev/null 2>&1; then
    {
      echo "Invalid JSON response"
      cat "$body"
    } > "$err"
    echo "FAIL: invalid JSON"
    failed=$((failed + 1))
    echo
    continue
  fi

  mv "$body" "$full"

  jq '{
    query_plan,
    facts_count: (.facts | length),
    evidence_count: (.retrieved_evidence | length),
    graph_nodes: (.graph.nodes | length),
    graph_edges: (.graph.edges | length),
    answer: {
      summary: .answer.summary,
      confidence: .answer.confidence,
      source_count: .answer.source_count,
      related_experts: (.answer.related_experts // [])
    },
    top_hits: [
      (.retrieved_evidence // [])[0:10][]
      | {
          chunk_id,
          filename,
          page_start,
          score,
          dense_score,
          lexical_score,
          reranker_score,
          excerpt: ((.text // .excerpt // "")[0:360])
        }
    ]
  }' "$full" > "$summary"

  if jq -r '.answer.summary // ""' "$summary" \
      | grep -Eqi 'mock-режим|mock.?mode'; then
    echo "INVALID: mock mode detected"
    mock_detected=$((mock_detected + 1))
  else
    ok=$((ok + 1))
  fi

  jq '{
    intent: .query_plan.intent,
    facts: .facts_count,
    evidence: .evidence_count,
    nodes: .graph_nodes,
    edges: .graph_edges,
    confidence: .answer.confidence,
    sources: .answer.source_count,
    experts: (.answer.related_experts | length)
  }' "$summary"

  echo
done

rm -f "$OUT_DIR"/*.response.tmp 2>/dev/null || true

echo "=== DONE ==="
echo "OK:            $ok"
echo "Failed:        $failed"
echo "Mock detected: $mock_detected"
echo "Output:        $OUT_DIR"

if [[ $mock_detected -gt 0 ]]; then
  exit 3
fi

if [[ $failed -gt 0 ]]; then
  exit 1
fi
