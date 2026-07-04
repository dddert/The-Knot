#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
ROLE="${ROLE:-analyst}"
USER_ID="${USER_ID:-tester}"
TOKEN="${TOKEN:-analyst-token}"
OUT_DIR="${OUT_DIR:-regression-results}"

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

for i in "${!queries[@]}"; do
  name="${names[$i]}"
  query="${queries[$i]}"
  echo "=== $name ==="
  echo "$query"

  curl -fsS \
    -X POST \
    "$BASE_URL/api/search?role=$ROLE&user_id=$USER_ID" \
    -H 'Content-Type: application/json' \
    -H "X-Demo-Role-Token: $TOKEN" \
    -d "$(jq -n --arg q "$query" '{query:$q, filters:{}}')" \
    > "$OUT_DIR/$name.full.json"

  jq '{
    query_plan,
    facts_count: (.facts | length),
    evidence_count: (.retrieved_evidence | length),
    answer: {
      summary: .answer.summary,
      confidence: .answer.confidence,
      source_count: .answer.source_count,
      related_experts: .answer.related_experts
    },
    top_hits: [
      .retrieved_evidence[0:10][]
      | {
          chunk_id,
          filename,
          page_start,
          score,
          dense_score,
          lexical_score,
          reranker_score,
          excerpt: (.text[0:360])
        }
    ]
  }' \
    "$OUT_DIR/$name.full.json" \
    | tee "$OUT_DIR/$name.summary.json"

  echo
done

echo "Saved results to: $OUT_DIR"
