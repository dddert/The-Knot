# ML integration status

## Что изменено

1. Добавлен отдельный `ml-service/` с контрактами:
   - `POST /ml/extract`
   - `POST /ml/parse-query`
   - `POST /ml/retrieve`
   - `POST /ml/synthesize-answer`
2. Убрана архитектурная зависимость от 4 hardcoded demo domains.
3. Backend `SearchService` теперь делает full-corpus retrieval и передаёт evidence в synthesis.
4. Retrieval:
   - dense FAISS (optional);
   - SQLite FTS5 lexical;
   - RRF fusion;
   - optional CrossEncoder reranker;
   - numeric constraints через существующие numeric JSONL.
5. Исправлены опасные defaults:
   - `year_from/year_to` теперь пустые;
   - numeric filter во frontend выключен по умолчанию;
   - Compare больше не режет 2020–2026 автоматически.
6. Добавлен `EnvironmentalIndicator` в backend schema и Neo4j labels.
7. Добавлен batch adapter:
   - chunk-level LLM JSONL -> document-level `ExtractedDocument`;
   - relation `source_name/target_name` -> entity IDs;
   - fact subject/objects -> entity IDs.
8. Добавлен admin/analyst endpoint `POST /api/documents/import-extracted`.
9. Исправлен numeric parameter inference по расстоянию до конкретного числа:
   `95% извлечения` больше не становится `temperature` из-за соседнего `90 °C`.

## Проверено

- Python `compileall`: OK.
- Frontend `npm run build`: OK.
- ML FastAPI smoke test: health/parse-query/retrieve/synthesize = 200.
- Lexical retrieval smoke test: OK.
- Numeric constraint retrieval smoke test: `sulfate_concentration <= 300 mg/L` выбрал правильный chunk.
- Batch adapter output валидируется backend `ExtractedDocument` Pydantic schema.

## Первый запуск на реальных данных

### A. Построить full-corpus index

```powershell
cd ml-service
pip install -r requirements.txt
pip install -r requirements-dense.txt

python scripts/build_retrieval_index.py "K:\Nornickel-ML\chunks.jsonl" `
  --index-dir "K:\Nornickel-ML\retrieval-index" `
  --numeric-values-path "K:\Nornickel-ML\numeric_values_v2.jsonl" `
  --model "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2" `
  --batch-size 64
```

Если dense неожиданно тормозит, сразу строим lexical+numeric fallback:

```powershell
python scripts/build_retrieval_index.py "K:\Nornickel-ML\chunks.jsonl" `
  --index-dir "K:\Nornickel-ML\retrieval-index" `
  --numeric-values-path "K:\Nornickel-ML\numeric_values_v2.jsonl" `
  --no-dense
```

### B. Запустить ML service локально

```powershell
$env:RETRIEVAL_INDEX_DIR="K:\Nornickel-ML\retrieval-index"
$env:YANDEX_API_KEY="..."
$env:YANDEX_FOLDER_ID="..."
$env:YANDEX_MODEL="yandexgpt-lite"
$env:LLM_PROVIDER="yandex"

uvicorn app.main:app --host 0.0.0.0 --port 9000
```

### C. Переключить backend

```env
USE_MOCK_ML=false
ML_SERVICE_URL=http://localhost:9000
```

### D. Адаптировать уже готовый PGM batch

```powershell
python scripts/adapt_batch_outputs.py `
  --extractions "K:\Nornickel-ML\llm_extractions_pgm.jsonl" `
  --chunks "K:\Nornickel-ML\chunks.jsonl" `
  --numeric-values "K:\Nornickel-ML\numeric_values_v2.jsonl" `
  --output "K:\Nornickel-ML\extracted_documents_pgm.jsonl"
```

После запуска backend:

```powershell
python scripts/import_extracted_jsonl.py "K:\Nornickel-ML\extracted_documents_pgm.jsonl" `
  --backend-url http://localhost:8000 `
  --role admin `
  --token admin-token
```
