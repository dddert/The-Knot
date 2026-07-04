from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import httpx
from app.core.config import settings
from app.schemas.contracts import DocumentForExtraction, ExtractedDocument, FinalAnswer, FinalAnswerSection, NumericConstraint, QueryPlan, RetrieveRequest, RetrieveResponse, SearchFilters


class MLClient:
    def __init__(self):
        self.base_url = settings.ml_service_url.rstrip("/")
        self.use_mock = settings.use_mock_ml

    async def extract(self, document: DocumentForExtraction) -> ExtractedDocument:
        if self.use_mock:
            data = json.loads(Path(settings.mock_extracted_document_path).read_text(encoding="utf-8"))
            data = self._document_specific_mock(data, document)
            return ExtractedDocument.model_validate(data)

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{self.base_url}/ml/extract", json=document.model_dump())
            response.raise_for_status()
            return ExtractedDocument.model_validate(response.json())


    async def index_document(self, document: DocumentForExtraction) -> dict[str, Any]:
        """Persist uploaded document chunks in the ML retrieval overlay.

        This is intentionally separate from LLM extraction: a PDF becomes
        searchable immediately after upload, while expensive graph enrichment
        can be triggered later via /documents/{id}/process.
        """
        if self.use_mock:
            return {
                "document_id": document.document_id,
                "indexed_chunks": 0,
                "status": "mock_skipped",
            }

        timeout = httpx.Timeout(
            connect=10.0,
            read=300.0,
            write=60.0,
            pool=30.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.base_url}/ml/index-document",
                json=document.model_dump(),
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("ML index-document returned non-object JSON")
            return data


    def _document_specific_mock(self, data: dict[str, Any], document: DocumentForExtraction) -> dict[str, Any]:
        """Make mock extraction deterministic per uploaded document.

        In mock mode the facts are still synthetic, but IDs and source metadata are tied to
        the selected document so multiple uploads do not overwrite the same graph nodes.
        """
        doc_id = document.document_id
        source = data.setdefault("source", {})
        source["id"] = f"source_{doc_id}"
        source["document_id"] = doc_id
        source["filename"] = document.filename
        source["title"] = document.metadata.title or source.get("title") or document.filename
        source["source_type"] = document.source_type or source.get("source_type") or "internal_report"
        source["access_level"] = document.access_level or source.get("access_level") or "internal"
        source["year"] = document.metadata.year or source.get("year")
        source["country"] = document.metadata.country or source.get("country")
        source["organization"] = document.metadata.organization or source.get("organization")
        source["authors"] = document.metadata.authors or source.get("authors") or []
        data["document_id"] = doc_id

        # Keep historical demo IDs for the canonical mock document used in smoke tests.
        if doc_id == "doc_mock_001":
            return data

        def suffixed(value: str | None) -> str | None:
            return f"{value}_{doc_id}" if value else value

        chunk_map: dict[str, str] = {}
        entity_map: dict[str, str] = {}
        numeric_map: dict[str, str] = {}

        for chunk in data.get("chunks", []):
            old = chunk.get("id")
            new = suffixed(old)
            if old and new:
                chunk_map[old] = new
                chunk["id"] = new

        for entity in data.get("entities", []):
            old = entity.get("id")
            new = suffixed(old)
            if old and new:
                entity_map[old] = new
                entity["id"] = new
            if entity.get("source_chunk_id") in chunk_map:
                entity["source_chunk_id"] = chunk_map[entity["source_chunk_id"]]

        for numeric in data.get("numeric_values", []):
            old = numeric.get("id")
            new = suffixed(old)
            if old and new:
                numeric_map[old] = new
                numeric["id"] = new
            if numeric.get("source_chunk_id") in chunk_map:
                numeric["source_chunk_id"] = chunk_map[numeric["source_chunk_id"]]

        for relation in data.get("relations", []):
            relation["id"] = suffixed(relation.get("id"))
            if relation.get("source_entity_id") in entity_map:
                relation["source_entity_id"] = entity_map[relation["source_entity_id"]]
            if relation.get("target_entity_id") in entity_map:
                relation["target_entity_id"] = entity_map[relation["target_entity_id"]]
            if relation.get("source_chunk_id") in chunk_map:
                relation["source_chunk_id"] = chunk_map[relation["source_chunk_id"]]

        for fact in data.get("facts", []):
            fact["id"] = suffixed(fact.get("id"))
            if fact.get("subject_entity_id") in entity_map:
                fact["subject_entity_id"] = entity_map[fact["subject_entity_id"]]
            fact["object_entity_ids"] = [entity_map.get(x, x) for x in fact.get("object_entity_ids", [])]
            fact["numeric_value_ids"] = [numeric_map.get(x, x) for x in fact.get("numeric_value_ids", [])]
            fact_source = fact.setdefault("source", {})
            fact_source["document_id"] = doc_id
            if fact_source.get("chunk_id") in chunk_map:
                fact_source["chunk_id"] = chunk_map[fact_source["chunk_id"]]
        data.setdefault("warnings", []).append(
            "Mock extraction used synthetic facts, but IDs/source metadata were namespaced to the uploaded document."
        )
        return data

    async def parse_query(self, query: str, filters: SearchFilters | dict[str, Any] | None = None) -> QueryPlan:
        filters_model = filters if isinstance(filters, SearchFilters) else SearchFilters.model_validate(filters or {})
        if self.use_mock:
            return self._mock_parse_query(query, filters_model)

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.base_url}/ml/parse-query", json={"query": query, "filters": filters_model.model_dump(exclude_none=True)})
            response.raise_for_status()
            return QueryPlan.model_validate(response.json())

    async def retrieve(
        self,
        query: str,
        plan: QueryPlan,
        *,
        top_k: int = 30,
        visible_access_levels: list[str] | None = None,
    ) -> RetrieveResponse:
        if self.use_mock:
            return RetrieveResponse(query=query, expanded_query=query, hits=[], warnings=["Mock ML mode: full-corpus retrieval disabled."])

        payload = RetrieveRequest(
            query=query,
            query_plan=plan,
            top_k=top_k,
            visible_access_levels=visible_access_levels or ["public"],
        )
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(f"{self.base_url}/ml/retrieve", json=payload.model_dump())
                response.raise_for_status()
                return RetrieveResponse.model_validate(response.json())
        except Exception as exc:
            return RetrieveResponse(
                query=query,
                expanded_query=query,
                hits=[],
                warnings=[f"Retrieval unavailable: {type(exc).__name__}: {str(exc)[:300]}"],
            )

    async def synthesize_answer(self, payload: dict[str, Any]) -> FinalAnswer:
        if self.use_mock:
            facts = payload.get("facts", [])
            sources = payload.get("sources", [])
            fact_rows = []
            numeric_rows = []
            geo_counts: dict[str, int] = {}
            status_counts: dict[str, int] = {}
            source_counts: dict[str, int] = {}
            for f in facts[:20]:
                geo = f.get("geo_scope") or "unknown"
                status = f.get("status") or "unknown"
                source_title = f.get("source_title") or "—"
                geo_counts[geo] = geo_counts.get(geo, 0) + 1
                status_counts[status] = status_counts.get(status, 0) + 1
                source_counts[source_title] = source_counts.get(source_title, 0) + 1
                fact_rows.append({
                    "claim": f.get("claim_text"),
                    "confidence": f.get("confidence"),
                    "status": status,
                    "geo_scope": geo,
                    "source": source_title,
                })
                for n in f.get("numeric_values", [])[:6]:
                    if n.get("value_min") is not None and n.get("value_max") is not None:
                        value = f"{n.get('value_min')}–{n.get('value_max')}"
                    elif n.get("value_max") is not None:
                        value = f"≤{n.get('value_max')}"
                    elif n.get("value_min") is not None:
                        value = f"≥{n.get('value_min')}"
                    else:
                        value = n.get("value")
                    numeric_rows.append({
                        "fact_id": f.get("id"),
                        "parameter": n.get("display_name") or n.get("parameter"),
                        "value": value,
                        "unit": n.get("unit_normalized") or n.get("unit_original"),
                        "confidence": n.get("confidence"),
                    })
            summary = (
                f"Найдено {len(facts)} релевантных фактов и {len(sources)} источников. "
                "Ответ сформирован в mock-режиме: backend показывает структуру вывода, а ML-синтез подключается отдельно."
            )
            gaps = [
                "Mock-синтез не делает полноценный научный консенсус; это responsibility ML-сервиса.",
                "Graph/full-text retrieval уже применяет источники, confidence, access level и числовые ограничения.",
            ]
            if not facts:
                gaps.insert(0, "Нет видимых фактов для текущей роли/фильтров; проверьте access level и импорт mock-данных.")
            recommendations = [
                "Проверить auto_extracted факты через ручной редактор.",
                "Для production подключить /ml/synthesize-answer с консенсусом, противоречиями и gap analysis.",
            ]
            return FinalAnswer(
                summary=summary,
                confidence=round(sum([f.get("confidence", 0) for f in facts]) / max(len(facts), 1), 2),
                source_count=len(sources),
                sections=[
                    FinalAnswerSection(title="Найденные факты", type="table", content=fact_rows),
                    FinalAnswerSection(title="Числовые условия", type="table", content=numeric_rows[:30]),
                    FinalAnswerSection(title="География", type="table", content=[{"geo_scope": k, "facts": v} for k, v in sorted(geo_counts.items())]),
                    FinalAnswerSection(title="Статусы верификации", type="table", content=[{"status": k, "facts": v} for k, v in sorted(status_counts.items())]),
                    FinalAnswerSection(title="Источники", type="table", content=[{"source": k, "facts": v} for k, v in sorted(source_counts.items())]),
                    FinalAnswerSection(title="Пробелы и ограничения", type="bullets", content=gaps),
                ],
                recommendations=recommendations,
                related_experts=[],
                export_payload={"markdown": summary, "json_ld": {"@type": "SearchResult"}},
            )

        # Synthesis can legitimately outlive retrieval/extraction timeouts:
        # the ML service may wait on an external LLM and still complete
        # successfully. Keep this below the frontend/nginx 300s budget.
        synthesis_timeout = httpx.Timeout(
            connect=10.0,
            read=240.0,
            write=30.0,
            pool=30.0,
        )

        async with httpx.AsyncClient(
            timeout=synthesis_timeout
        ) as client:
            response = await client.post(
                f"{self.base_url}/ml/synthesize-answer",
                json=payload,
            )
            response.raise_for_status()
            return FinalAnswer.model_validate(
                response.json()
            )

    def _mock_parse_query(self, query: str, filters: SearchFilters) -> QueryPlan:
        q = query.lower()
        plan = QueryPlan()
        plan.year_from = filters.year_from
        plan.year_to = filters.year_to
        plan.confidence_min = filters.confidence_min
        plan.geo_scope = filters.geo_scope
        if filters.country:
            plan.countries = [filters.country]
        if filters.status:
            plan.status = filters.status
        if filters.fact_type:
            plan.fact_type = filters.fact_type
        if filters.verification_level:
            plan.verification_level = filters.verification_level
        if filters.source_type:
            plan.source_type = filters.source_type

        if "католит" in q or "electrowinning" in q or "электроэкстрак" in q:
            plan.intent = "technology_review"
            plan.processes = ["nickel electrowinning"]
            plan.materials = ["nickel", "catholyte"]
            plan.comparison_mode = "миров" in q or "зарубеж" in q or "срав" in q
        elif "обессол" in q or "сульфат" in q or "хлорид" in q:
            plan.intent = "recommend_technology"
            plan.processes = ["water desalination"]
            plan.materials = ["sulfates", "chlorides", "calcium", "magnesium", "sodium"]
        elif "au" in q or "ag" in q or "мпг" in q or "шла" in q:
            plan.intent = "find_experiments_and_publications"
            plan.processes = ["matte slag distribution"]
            plan.materials = ["gold", "silver", "platinum group metals", "copper matte", "nickel matte", "slag"]
            plan.year_from = 2021
            plan.year_to = 2026
        elif "шахт" in q or "закач" in q:
            plan.intent = "compare_technologies"
            plan.processes = ["mine water deep injection"]
            plan.materials = ["mine water"]
            plan.comparison_mode = True

        if filters.process:
            plan.processes = [filters.process]
        if filters.material:
            plan.materials = [filters.material]
        if filters.numeric_parameter:
            plan.numeric_constraints = [
                NumericConstraint(
                    parameter=filters.numeric_parameter,
                    operator=filters.numeric_operator or "between",
                    value_min=filters.numeric_min,
                    value_max=filters.numeric_max,
                    unit=filters.numeric_unit or None,
                )
            ]
        return plan
