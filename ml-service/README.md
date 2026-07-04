# Scientific Knot ML service

Реальный ML-сервис под готовые backend contracts:

- `POST /ml/extract` — document -> `ExtractedDocument`;
- `POST /ml/parse-query` — arbitrary query -> generic `QueryPlan`;
- `POST /ml/retrieve` — hybrid full-corpus retrieval;
- `POST /ml/synthesize-answer` — grounded synthesis from graph facts + retrieved evidence.

## 1. Построить индекс по всем chunks

```bash
cd ml-service
pip install -r requirements.txt
pip install -r requirements-dense.txt
python scripts/build_retrieval_index.py /path/to/chunks.jsonl \
  --index-dir /path/to/retrieval-index \
  --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
  --batch-size 64 \
  --numeric-values-path /path/to/numeric_values_v2.jsonl
```

CPU-only аварийный вариант без dense embeddings:

```bash
python scripts/build_retrieval_index.py /path/to/chunks.jsonl \
  --index-dir /path/to/retrieval-index \
  --no-dense
```

## 2. ENV

```env
LLM_PROVIDER=yandex
YANDEX_API_KEY=...
YANDEX_FOLDER_ID=...
YANDEX_MODEL=yandexgpt-lite
RETRIEVAL_INDEX_DIR=/path/to/retrieval-index
DENSE_ENABLED=true
LEXICAL_ENABLED=true
```

## 3. Запуск

```bash
uvicorn app.main:app --host 0.0.0.0 --port 9000
```

`/ml/retrieve` не использует четыре hardcoded domain buckets. QueryPlan применяется только как expansion/filter context.

## Адаптация уже посчитанных batch JSONL

Текущий `llm_extractor.py` пишет chunk-level записи и relations/facts по именам. Backend ожидает document-level `ExtractedDocument` и ID-ссылки. Конвертация:

```bash
python scripts/adapt_batch_outputs.py \
  --extractions /path/to/llm_extractions_pgm.jsonl \
  --chunks /path/to/chunks.jsonl \
  --numeric-values /path/to/numeric_values_v2.jsonl \
  --output /path/to/extracted_documents_pgm.jsonl
```

Импорт в работающий backend:

```bash
python scripts/import_extracted_jsonl.py /path/to/extracted_documents_pgm.jsonl \
  --backend-url http://localhost:8000 \
  --role admin \
  --token admin-token
```
