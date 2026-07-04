from __future__ import annotations
from typing import Any
from app.core.config import settings
from app.core.access import visible_access_levels
from app.db.neo4j import get_neo4j_driver
from app.schemas.contracts import QueryPlan, SearchRequest
from app.schemas.dto import GraphDTO, SearchDebugDTO, SearchResultDTO, SearchResponseDTO, SourceDTO
from app.services.cypher_builder import CypherBuilder
from app.services.fact_mapper import map_fact, map_source
from app.services.graph_read_service import GraphReadService
from app.services.ml_client import MLClient


class SearchService:
    def __init__(self):
        self.ml = MLClient()
        self.cypher = CypherBuilder()
        self.graph = GraphReadService()

    async def search(self, request: SearchRequest, role: str = "external_partner", limit: int = 50) -> SearchResponseDTO:
        plan = await self.ml.parse_query(request.query, request.filters)

        # Only filters explicitly supplied by the API/UI are allowed to become
        # hard graph constraints. QueryPlan fields inferred from free text are
        # useful semantic hints, but graph metadata is sparse and should not
        # zero out otherwise relevant provenance-linked facts.
        explicit_filters = request.filters.model_dump(
            exclude_none=True,
            exclude_defaults=True,
        )

        # Hybrid retrieval must run before graph lookup. The retrieved chunk IDs
        # are the provenance bridge into Neo4j facts imported from those chunks.
        retrieval = await self.ml.retrieve(
            request.query,
            plan,
            top_k=min(max(limit, 20), 50),
            visible_access_levels=visible_access_levels(role),
        )
        retrieved_evidence = [hit.model_dump() for hit in retrieval.hits]

        # Preserve ML ranking across the provenance bridge. A chunk may appear
        # more than once in merged retrieval branches; keep its best score.
        retrieved_chunk_scores: dict[str, float] = {}
        for hit in retrieval.hits:
            if not hit.chunk_id:
                continue
            score = float(hit.score or 0.0)
            previous = retrieved_chunk_scores.get(hit.chunk_id)
            if previous is None or score > previous:
                retrieved_chunk_scores[hit.chunk_id] = score

        retrieved_chunks = [
            {"chunk_id": chunk_id, "score": score}
            for chunk_id, score in retrieved_chunk_scores.items()
        ]

        graph_result = self.search_facts_by_chunks(
            retrieved_chunks,
            plan=plan,
            explicit_filters=explicit_filters,
            limit=limit,
            graph_mode=request.graph_mode,
            role=role,
        )

        # Backward-compatible fallback for corpora that were not selectively
        # enriched yet or when none of the retrieved chunks exists in Neo4j.
        if not graph_result.facts:
            # Do not erase explicit/structured semantics with a broad text
            # fallback. For strict filters, zero graph facts is a valid answer;
            # full-corpus evidence still reaches synthesis.
            has_strict_plan_filters = any([
                bool(explicit_filters),
                bool(plan.numeric_constraints),
            ])

            graph_result = self.search_facts(
                plan,
                limit=limit,
                graph_mode=request.graph_mode,
                role=role,
                raw_query=None if has_strict_plan_filters else request.query,
            )
            if graph_result.debug:
                graph_result.debug.params["graph_retrieval_mode"] = (
                    "query_plan_fallback"
                )
        elif graph_result.debug:
            graph_result.debug.params["graph_retrieval_mode"] = (
                "retrieved_chunk_bridge"
            )

        # Surface retrieved sources even when the graph has not been pre-enriched yet.
        source_map = {s.id: s for s in graph_result.sources if s.id}
        for hit in retrieval.hits:
            source_id = f"source_{hit.document_id}" if hit.document_id else None
            if source_id and source_id not in source_map:
                source_map[source_id] = SourceDTO(
                    id=source_id,
                    document_id=hit.document_id,
                    title=hit.filename or hit.document_id,
                    filename=hit.filename,
                    source_type=hit.source_type,
                    access_level=hit.access_level,
                    page=hit.page_start,
                    quote=(hit.text[:500] if hit.text else None),
                )

        synthesis_payload = {
            "query": request.query,
            "query_plan": plan.model_dump(),
            "facts": [f.model_dump() for f in graph_result.facts],
            "graph_paths": graph_result.graph.model_dump() if graph_result.graph else None,
            "sources": [s.model_dump() for s in source_map.values()],
            "retrieved_evidence": retrieved_evidence,
            "retrieval_warnings": retrieval.warnings,
        }
        answer = await self.ml.synthesize_answer(synthesis_payload)

        if graph_result.debug:
            graph_result.debug.params["retrieval_hits"] = len(retrieved_evidence)
            graph_result.debug.params["retrieval_warnings"] = retrieval.warnings

        return SearchResponseDTO(
            query=request.query,
            query_plan=plan.model_dump(),
            facts=graph_result.facts,
            sources=list(source_map.values()),
            retrieved_evidence=retrieved_evidence,
            graph=graph_result.graph,
            answer=answer.model_dump(),
            debug=graph_result.debug if settings.expose_debug and role in {"admin", "analyst"} else None,
        )

    def search_facts_by_chunks(
        self,
        retrieved_chunks: list[dict[str, Any]],
        plan: QueryPlan,
        explicit_filters: dict[str, Any] | None = None,
        limit: int = 50,
        graph_mode: str = "compact",
        role: str = "external_partner",
    ) -> SearchResultDTO:
        visible_levels = visible_access_levels(role)

        if not retrieved_chunks:
            return SearchResultDTO(
                facts=[],
                sources=[],
                graph=GraphDTO(),
                debug=SearchDebugDTO(
                    cypher="// no retrieved chunk ids",
                    params={
                        "retrieved_chunks": [],
                        "visible_access_levels": visible_levels,
                        "graph_retrieval_mode": "retrieved_chunk_bridge",
                    },
                ),
            )

        cypher, params = self.cypher.build_fact_search_by_chunks(
            retrieved_chunks,
            plan=plan,
            explicit_filters=explicit_filters or {},
            limit=limit,
            visible_access_levels=visible_levels,
        )
        facts, source_map, fact_ids = self._run_fact_query(cypher, params)

        graph = self.graph.get_subgraph(
            fact_ids=fact_ids[:10],
            limit=100,
            mode=graph_mode,
            visible_access_levels=visible_levels,
        ) if fact_ids and graph_mode != "none" else GraphDTO()

        debug_params = dict(params)
        debug_params["graph_retrieval_mode"] = "retrieved_chunk_bridge"

        return SearchResultDTO(
            facts=facts,
            sources=list(source_map.values()),
            graph=graph,
            debug=SearchDebugDTO(
                cypher=cypher,
                params=debug_params,
            ),
        )

    def search_facts(
        self,
        plan: QueryPlan,
        limit: int = 50,
        graph_mode: str = "compact",
        role: str = "external_partner",
        raw_query: str | None = None,
    ) -> SearchResultDTO:
        visible_levels = visible_access_levels(role)
        cypher, params = self.cypher.build_fact_search(plan, limit=limit, visible_access_levels=visible_levels)
        facts, source_map, fact_ids = self._run_fact_query(cypher, params)

        fallback_used = False
        if not facts and raw_query:
            fallback_used = True
            cypher, params = self.cypher.build_text_fallback(
                raw_query,
                limit=limit,
                visible_access_levels=visible_levels,
            )
            fallback_error = None
            try:
                facts, source_map, fact_ids = self._run_fact_query(cypher, params)
            except Exception as exc:
                # If fulltext index is unavailable in a Neo4j edition/minor version, keep search stable.
                fallback_error = f"{type(exc).__name__}: {exc}"
                facts, source_map, fact_ids = [], {}, []

        graph = self.graph.get_subgraph(
            fact_ids=fact_ids[:10],
            limit=100,
            mode=graph_mode,
            visible_access_levels=visible_levels,
        ) if fact_ids and graph_mode != "none" else GraphDTO()
        debug_params = dict(params)
        debug_params["fallback_used"] = fallback_used
        debug_params["visible_access_levels"] = visible_levels
        if fallback_used and 'fallback_error' in locals() and fallback_error:
            debug_params["fallback_error"] = fallback_error
        return SearchResultDTO(
            facts=facts,
            sources=list(source_map.values()),
            graph=graph,
            debug=SearchDebugDTO(cypher=cypher, params=debug_params),
        )

    def _run_fact_query(self, cypher: str, params: dict[str, Any]) -> tuple[list, dict[str, SourceDTO], list[str]]:
        facts = []
        source_map: dict[str, SourceDTO] = {}
        fact_ids: list[str] = []
        with get_neo4j_driver().session() as session:
            rows = session.run(cypher, **params)
            for row in rows:
                source = dict(row["s"]) if row["s"] else None
                ds = dict(row["ds"]) if row["ds"] else None
                fact = map_fact(
                    dict(row["f"]),
                    entities=[dict(e) for e in row["entities"] if e],
                    numeric_values=[dict(v) for v in row["params"] if v],
                    source_props=source,
                    source_rel_props=ds,
                )
                facts.append(fact)
                fact_ids.append(fact.id)
                source_dto = map_source(source, ds)
                if source_dto and source_dto.id:
                    source_map[source_dto.id] = source_dto
        return facts, source_map, fact_ids
