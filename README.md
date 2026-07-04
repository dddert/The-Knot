# Scientific Knot demo-hardened MVP

GraphRAG-oriented MVP для хакатона: FastAPI + Neo4j + PostgreSQL + React/Vite + nginx. Сейчас включён `USE_MOCK_ML=true`, чтобы backend/frontend работали без отдельного ML/NLP сервиса.

Честная формулировка: это **backend/frontend demo-hardened MVP**, а не production security и не полноценный semantic GraphRAG. Реальный ML должен подключаться через готовые контракты `/ml/extract`, `/ml/parse-query`, `/ml/synthesize-answer`.

## Запуск

```bash
docker compose up --build
```

Frontend теперь переписан со Streamlit на React/Vite. В Docker он собирается в static bundle и отдаётся через nginx. Nginx также проксирует `/api/*` и `/health` в backend, поэтому браузеру достаточно открыть `localhost:3000`. Старый Streamlit сохранён только как legacy-папка `frontend_streamlit_legacy/` и не участвует в docker-compose.

Сервисы:

- React Frontend: http://localhost:3000
- Backend OpenAPI: http://localhost:8000/docs
- Neo4j Browser: http://localhost:17474, `neo4j/password`
- PostgreSQL: `localhost:55432`, db `scientific_knot`, user `sk_user`, password `sk_password`

Внутри docker-compose backend подключается к `neo4j:7687` и `postgres:5432`; внешние порты нужны только для локального демо.

## Быстрый демо-сценарий

1. Открыть http://localhost:3000.
2. В sidebar выбрать `Role = admin`.
3. Нажать `Init Neo4j schema`.
4. Нажать `Import mock`.
5. Перейти на `Search`, выполнить демо-запрос про обессоливание или католит.
6. Показать QueryPlan, facts, source/page/quote, numeric values и graph.
7. Перейти на `Graph`, показать `Fact → Source / ParameterValue / Material / Process / Chunk`.
8. Перейти на `Fact Editor`, под ролью `analyst/admin` изменить статус на `expert_verified`.
9. Перейти на `Export`, скачать Markdown / JSON-LD / PDF.
10. Перейти на `Audit` под ролью `admin/manager`, показать audit log.


## React frontend

Новый frontend находится в `frontend/`:

```text
frontend/
  Dockerfile          # Node build stage + nginx runtime
  nginx.conf          # static hosting + reverse proxy to backend
  package.json        # React/Vite/TypeScript
  src/
    App.tsx           # SPA pages: Overview, Import, Search, Graph, Compare, Fact Editor, Dashboard, Export, Audit
    components/       # Layout, DataTable, GraphView, UI primitives
    lib/              # API client, demo role context, types
    styles/app.css    # polished glass-style UI
```

Локальная разработка без Docker:

```bash
cd frontend
npm install
npm run dev
```

Vite dev server проксирует `/api` и `/health` на `localhost:8000`. Production build проверяется командой:

```bash
cd frontend
npm run build
```

## Demo role tokens

Backend больше не доверяет внутренней роли только из query param. Для внутренних ролей нужен заголовок `X-Demo-Role-Token`.

| Role | Demo token |
|---|---|
| `external_partner` | `partner-token` |
| `researcher` | `researcher-token` |
| `analyst` | `analyst-token` |
| `manager` | `manager-token` |
| `admin` | `admin-token` |

React UI отправляет token автоматически через `X-Demo-Role-Token`. Для curl:

```bash
curl -X POST "http://localhost:8000/api/graph/init-schema?user_id=alex&role=admin" \
  -H "X-Demo-Role-Token: admin-token"
```

Без token запрос с `role=admin/manager/analyst/researcher` вернёт `401`. Запрос без роли считается `external_partner`.

Production-замена: JWT/OIDC, серверная identity-модель, CORS allowlist, закрытые DB-порты, секреты через Vault/CI secrets.

## Access model в MVP

| Role | Public | Internal | Confidential |
|---|---:|---:|---:|
| external_partner | да | нет | нет |
| researcher | да | да | нет |
| analyst | да | да | нет |
| manager | да | да | да |
| admin | да | да | да |

Эта модель применяется в search/list/compare/dashboard и в read-only graph API. Graph endpoints также закрыты от `external_partner`.

## Контракты

- `contracts/extracted_document.schema.json`
- `contracts/query_plan.schema.json`
- `contracts/final_answer.schema.json`
- `mock/mock_extracted_document.json`

`mock/mock_extracted_document.json` валидируется против `contracts/extracted_document.schema.json`. `ExtractedDocument.source` теперь описан явно, а не `dict[str, Any]`. Пользовательский search input валидируется через `SearchFilters` Pydantic-модель вместо свободного `dict[str, Any]`.

## Mock ML mode

Пока `USE_MOCK_ML=true`, backend читает `mock/mock_extracted_document.json` и использует mock query parser / mock answer synthesizer.

Для загруженных файлов backend делает best-effort text extraction: TXT/MD/CSV/PDF/DOCX/XLSX. В mock mode содержимое файла всё ещё не превращается в реальные факты, но mock extraction теперь **namespaced по `document_id`**: `source`, `chunks`, `entities`, `numeric_values`, `facts` получают уникальные ID для конкретного документа и не перетирают друг друга.

Важно: обработка загруженного документа больше не имеет mock fallback. `POST /api/documents/{document_id}/process` вернёт `404 Document not found or access denied`, если документа нет или роль его не видит. Синтетический mock запускается только явным endpoint `POST /api/documents/process-mock`.

## Retrieval status

Реализовано:

- graph-structured retrieval по `QueryPlan`;
- строгий `SearchFilters` input contract;
- numeric filters с null-safe range logic и базовой поддержкой операторов `between`, `<`, `<=`, `>`, `>=`, `=`;
- Neo4j full-text fallback по `Fact.claim_text` / `fact_type`.

Не заявлять как готовое:

- production semantic search;
- vector index / embeddings / reranking;
- полноценный NLP по произвольным документам.

Правильная формулировка: semantic retrieval предусмотрен архитектурно и подключается через ML/embeddings слой.


## Health endpoint

`GET /health` не раскрывает URI/пароли, но показывает статус зависимостей без секретов:

```json
{
  "status": "ok",
  "app": "Scientific Knot",
  "use_mock_ml": true,
  "dependencies": {"neo4j": "ok", "postgres": "ok"}
}
```

## Основные endpoints

```http
GET    /health
POST   /api/graph/init-schema
DELETE /api/graph/clear-demo
POST   /api/documents/upload
GET    /api/documents
POST   /api/documents/{document_id}/process
POST   /api/documents/process-mock
POST   /api/search
POST   /api/compare
GET    /api/graph/node/{node_id}
GET    /api/graph/neighbors/{node_id}
GET    /api/graph/path
GET    /api/graph/subgraph
GET    /api/facts
GET    /api/facts/{fact_id}
PATCH  /api/facts/{fact_id}
GET    /api/facts/{fact_id}/versions
GET    /api/dashboard/coverage
GET    /api/audit
POST   /api/export/markdown
POST   /api/export/jsonld
POST   /api/export/pdf
```

## Sensitive endpoint guards

| Operation | Allowed roles |
|---|---|
| `POST /api/graph/init-schema` | `admin` |
| `DELETE /api/graph/clear-demo` | `admin` |
| `PATCH /api/facts/{fact_id}` | `admin`, `analyst` |
| `POST /api/export/*` | `admin`, `analyst`, `manager` |
| `GET /api/audit` | `admin`, `manager` |
| `GET /api/facts/{fact_id}/versions` | `admin`, `analyst`, `manager` |
| graph read endpoints | `researcher`, `analyst`, `manager`, `admin`; `/api/graph/subgraph` требует `fact_ids` |
| dashboard | `researcher`, `analyst`, `manager`, `admin` |
| `POST /api/documents/{document_id}/process` | `admin`, `manager`, `analyst`, `researcher` если документ видим роли |

## Проверка numeric filter

```bash
curl -X POST "http://localhost:8000/api/search?user_id=alex&role=analyst" \
  -H "X-Demo-Role-Token: analyst-token" \
  -H "Content-Type: application/json" \
  -d '{
    "query":"Какие методы обессоливания воды подходят?",
    "filters":{
      "geo_scope":"all",
      "confidence_min":0.0,
      "numeric_parameter":"dry_residue",
      "numeric_max":1000,
      "numeric_unit":"mg/L"
    },
    "graph_mode":"compact"
  }'
```

## Проверка PDF export

```bash
curl -X POST "http://localhost:8000/api/export/pdf?user_id=alex&role=analyst" \
  -H "X-Demo-Role-Token: analyst-token" \
  -H "Content-Type: application/json" \
  -d '{"answer":{"summary":"Demo","sections":[],"confidence":0.8},"facts":[]}' \
| jq -r '.content_base64' | base64 -d > result.pdf
```

## Smoke test

После запуска контейнеров:

```bash
./scripts/smoke_test.sh
```

Требует `jq` на хосте.

## Замена mock ML на реальный ML service

В `docker-compose.yml` поменять:

```yaml
USE_MOCK_ML: "false"
ML_SERVICE_URL: http://ml-service:9000
```

И добавить сервис `ml-service`, который соответствует контрактам.

## Demo-polish notes после React-патча

- Search UI теперь показывает явные фильтры `process`, `material`, `country`, `year_from`, `year_to`, `status`, `fact_type`, `geo_scope`, `confidence_min` и numeric constraints.
- Search results явно показывают `source_quote`, чтобы на демо было видно, что факт подтверждён цитатой, страницей и источником.
- Export UI теперь отправляет `fact_ids`, а backend заново читает факты из Neo4j с учётом роли и `access_level`. Legacy payload `facts` оставлен только для совместимости.
- React сохраняет `lastSearch` и текущую страницу в `sessionStorage`, поэтому случайный refresh не ломает demo-flow.
- Compare имеет переключатель `table/cards`: table удобнее для сравнения CAPEX/OPEX, cards удобнее на узких экранах.
- `package-lock.json` включён в проект; frontend Dockerfile использует `npm ci` для воспроизводимой сборки.

## Recovery path при старых Docker volumes

Если после нескольких версий проекта появляются странные ошибки миграций, индексов или старых данных, используйте чистый запуск:

```bash
docker compose down -v
docker compose up --build
```

Обычный перезапуск без удаления volumes:

```bash
docker compose down
docker compose up --build
```

## Честная формулировка для защиты

Не говорим, что в mock mode система реально извлекает факты из любого загруженного PDF. Говорим:

> Backend/frontend реализуют платформенный слой: upload, best-effort text extraction, строгие контракты, graph import, Neo4j retrieval, numeric filters, access levels, audit, versions, React UI и export. В текущей сборке ML/NLP заменён mock service, поэтому факты синтетические, но проходят через тот же `ExtractedDocument` contract, что и будущий ML-сервис.


## Integrated universal ML service (added)

В `ml-service/` добавлен реальный сервис без четырёх hardcoded-доменов:

- `/ml/extract` — адаптер текущего chunk/numeric/LLM pipeline к `ExtractedDocument`;
- `/ml/parse-query` — generic QueryPlan для произвольного запроса;
- `/ml/retrieve` — full-corpus hybrid retrieval (FAISS dense + SQLite FTS5 lexical + RRF, optional reranker);
- `/ml/synthesize-answer` — grounded synthesis по Neo4j facts и retrieved chunks.

Backend `SearchService` теперь вызывает `/ml/retrieve` и передаёт evidence в synthesis. Поиск не ограничен четырьмя демо-темами.

Важно: сначала построить индекс по полному `chunks.jsonl`, см. `ml-service/README.md`.
