from __future__ import annotations
import re
from typing import Any
from app.schemas.contracts import QueryPlan


_SOFT_STOPWORDS = {
    "для", "при", "или", "как", "это", "the", "and", "with", "from",
    "процесс", "процессы", "технология", "технологии", "метод", "методы",
}


def _soft_term_stem(token: str) -> str:
    token = token.casefold().strip()
    if re.fullmatch(r"[a-z]{1,3}\d*", token):
        return token

    endings = (
        "иями", "ями", "ами", "анием", "ением", "нием", "ием",
        "ание", "ение", "ние", "ого", "ему", "ому", "ыми", "ими",
        "ий", "ый", "ая", "ое", "ые", "ую", "юю", "ым", "им",
        "ом", "ем", "ах", "ях", "ов", "ев", "ей",
    )
    for ending in endings:
        if token.endswith(ending) and len(token) - len(ending) >= 5:
            return token[:-len(ending)]
    return token


def _soft_terms(values: list[str]) -> list[str]:
    terms: list[str] = []
    for value in values:
        for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", value or ""):
            lowered = token.casefold()
            if lowered in _SOFT_STOPWORDS:
                continue
            stem = _soft_term_stem(lowered)
            if len(stem) >= 3 and stem not in terms:
                terms.append(stem)
    return terms[:24]



class CypherBuilder:
    """Builds safe parameterized Cypher queries from ML QueryPlan."""

    def build_fact_search(
        self,
        plan: QueryPlan,
        limit: int = 50,
        visible_access_levels: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        visible_access_levels = visible_access_levels or ["public"]
        params: dict[str, Any] = {
            "processes": plan.processes,
            "materials": plan.materials,
            "equipment": plan.equipment,
            "year_from": plan.year_from,
            "year_to": plan.year_to,
            "confidence_min": plan.confidence_min,
            "geo_scope": plan.geo_scope,
            "countries": plan.countries,
            "visible_access_levels": visible_access_levels,
            "limit": limit,
        }

        clauses = ["f.confidence >= $confidence_min"]
        clauses.append("coalesce(s.access_level, 'internal') IN $visible_access_levels")
        if plan.year_from is not None:
            clauses.append("f.year >= $year_from")
        if plan.year_to is not None:
            clauses.append("f.year <= $year_to")
        if plan.geo_scope and plan.geo_scope != "all":
            clauses.append("f.geo_scope = $geo_scope")
        if plan.countries:
            clauses.append("f.country IN $countries")
        if plan.status:
            clauses.append("f.status = $status")
            params["status"] = plan.status
        if plan.fact_type:
            clauses.append("f.fact_type = $fact_type")
            params["fact_type"] = plan.fact_type
        if plan.verification_level:
            clauses.append("f.verification_level = $verification_level")
            params["verification_level"] = plan.verification_level
        if plan.source_type:
            clauses.append("s.source_type = $source_type")
            params["source_type"] = plan.source_type
        if plan.processes:
            clauses.append("EXISTS { MATCH (f)-[:ABOUT]->(p:Process) WHERE p.canonical_name IN $processes }")
        if plan.materials:
            clauses.append("EXISTS { MATCH (f)-[:ABOUT]->(m:Material) WHERE m.canonical_name IN $materials }")
        if plan.equipment:
            clauses.append("EXISTS { MATCH (f)-[:ABOUT]->(eq:Equipment) WHERE eq.canonical_name IN $equipment }")

        match_parts = ["MATCH (f:Fact)", "MATCH (f)-[ds:DESCRIBED_IN]->(s:Source)"]
        numeric_clauses: list[str] = []
        for idx, constraint in enumerate(plan.numeric_constraints or []):
            alias = f"nv{idx}"
            match_parts.append(f"MATCH (f)-[:HAS_PARAMETER]->({alias}:ParameterValue)")
            params[f"num_parameter_{idx}"] = constraint.parameter
            params[f"num_unit_{idx}"] = constraint.unit
            required_min = constraint.value_min if constraint.value_min is not None else constraint.value
            required_max = constraint.value_max if constraint.value_max is not None else constraint.value
            params[f"required_min_{idx}"] = required_min
            params[f"required_max_{idx}"] = required_max

            numeric_clauses.append(f"{alias}.parameter = $num_parameter_{idx}")
            if constraint.unit:
                numeric_clauses.append(f"{alias}.unit_normalized = $num_unit_{idx}")
            operator = constraint.operator or "between"
            # Demo semantics:
            # - between: interval overlap between requested and fact ranges
            # - <=/<: fact upper bound should satisfy requested maximum
            # - >=/>: fact lower bound should satisfy requested minimum
            # - =: requested value should lie inside the fact range
            if operator == "between":
                if required_min is not None:
                    numeric_clauses.append(f"coalesce({alias}.value_max, {alias}.value, 1.0e18) >= $required_min_{idx}")
                if required_max is not None:
                    numeric_clauses.append(f"coalesce({alias}.value_min, {alias}.value, -1.0e18) <= $required_max_{idx}")
            elif operator in {"<=", "<"}:
                if required_max is None and required_min is not None:
                    params[f"required_max_{idx}"] = required_min
                    required_max = required_min
                if required_max is not None:
                    op = "<" if operator == "<" else "<="
                    numeric_clauses.append(f"coalesce({alias}.value_max, {alias}.value, 1.0e18) {op} $required_max_{idx}")
            elif operator in {">=", ">"}:
                if required_min is None and required_max is not None:
                    params[f"required_min_{idx}"] = required_max
                    required_min = required_max
                if required_min is not None:
                    op = ">" if operator == ">" else ">="
                    numeric_clauses.append(f"coalesce({alias}.value_min, {alias}.value, -1.0e18) {op} $required_min_{idx}")
            elif operator == "=":
                required_value = constraint.value if constraint.value is not None else required_min if required_min is not None else required_max
                params[f"required_value_{idx}"] = required_value
                if required_value is not None:
                    numeric_clauses.append(f"coalesce({alias}.value_min, {alias}.value, -1.0e18) <= $required_value_{idx}")
                    numeric_clauses.append(f"coalesce({alias}.value_max, {alias}.value, 1.0e18) >= $required_value_{idx}")

        where = " AND ".join(clauses + numeric_clauses) if clauses or numeric_clauses else "true"
        cypher = f"""
        {' '.join(match_parts)}
        WHERE {where}
        OPTIONAL MATCH (f)-[:ABOUT]->(e)
        OPTIONAL MATCH (f)-[:HAS_PARAMETER]->(v:ParameterValue)
        RETURN f, collect(DISTINCT e) AS entities, collect(DISTINCT v) AS params, s, ds
        ORDER BY f.confidence DESC, f.year DESC
        LIMIT $limit
        """
        return cypher, params

    def build_fact_search_by_chunks(
        self,
        retrieved_chunks: list[dict[str, Any]],
        plan: QueryPlan,
        explicit_filters: dict[str, Any] | None = None,
        limit: int = 50,
        visible_access_levels: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Hybrid GraphRAG bridge with explicit-hard / inferred-soft semantics.

        - Provenance bridge is always hard: facts must be SUPPORTED_BY retrieved chunks.
        - API/UI filters explicitly supplied by the user are hard constraints.
        - QueryPlan fields inferred from natural language are ranking hints only.
        - Process/material/equipment are stem-like soft entity boosts.
        - Numeric eligibility remains enforced by ML retrieval until graph
          ParameterValue coverage is complete.
        """
        visible_access_levels = visible_access_levels or ["public"]
        explicit_filters = explicit_filters or {}

        deduped: dict[str, float] = {}
        for item in retrieved_chunks:
            chunk_id = str(item.get("chunk_id") or "").strip()
            if not chunk_id:
                continue
            score = float(item.get("score") or 0.0)
            previous = deduped.get(chunk_id)
            if previous is None or score > previous:
                deduped[chunk_id] = score

        entity_terms = _soft_terms(
            list(plan.processes or [])
            + list(plan.materials or [])
            + list(plan.equipment or [])
        )

        # Explicit UI process/material values are still treated as semantic
        # entity hints rather than exact canonical-name filters because Russian
        # morphology and OCR make exact equality brittle.
        explicit_entity_terms = _soft_terms([
            str(explicit_filters.get("process") or ""),
            str(explicit_filters.get("material") or ""),
        ])

        params: dict[str, Any] = {
            "retrieved_chunks": [
                {"chunk_id": chunk_id, "score": score}
                for chunk_id, score in deduped.items()
            ],
            "confidence_min": float(
                explicit_filters.get(
                    "confidence_min",
                    plan.confidence_min,
                ) or 0.0
            ),
            "entity_terms": entity_terms,
            "explicit_entity_terms": explicit_entity_terms,
            "visible_access_levels": visible_access_levels,
            "limit": limit,

            # Inferred QueryPlan hints: soft only.
            "plan_year_from": plan.year_from,
            "plan_year_to": plan.year_to,
            "plan_geo_scope": (
                plan.geo_scope
                if plan.geo_scope and plan.geo_scope != "all"
                else None
            ),
            "plan_countries": list(plan.countries or []),
            "plan_status": plan.status,
            "plan_fact_type": plan.fact_type,
            "plan_verification_level": plan.verification_level,
            "plan_source_type": plan.source_type,
        }

        clauses = [
            "f.confidence >= $confidence_min",
            "coalesce(s.access_level, 'internal') IN $visible_access_levels",
        ]

        # Hard constraints come only from explicit API/UI filters.
        if explicit_filters.get("year_from") is not None:
            params["hard_year_from"] = explicit_filters["year_from"]
            clauses.append(
                "coalesce(f.year, s.year) >= $hard_year_from"
            )
        if explicit_filters.get("year_to") is not None:
            params["hard_year_to"] = explicit_filters["year_to"]
            clauses.append(
                "coalesce(f.year, s.year) <= $hard_year_to"
            )

        hard_geo_scope = explicit_filters.get("geo_scope")
        if hard_geo_scope and hard_geo_scope != "all":
            params["hard_geo_scope"] = hard_geo_scope
            clauses.append("f.geo_scope = $hard_geo_scope")

        if explicit_filters.get("country"):
            params["hard_country"] = explicit_filters["country"]
            clauses.append(
                "coalesce(f.country, s.country) = $hard_country"
            )
        if explicit_filters.get("status"):
            params["hard_status"] = explicit_filters["status"]
            clauses.append("f.status = $hard_status")
        if explicit_filters.get("fact_type"):
            params["hard_fact_type"] = explicit_filters["fact_type"]
            clauses.append("f.fact_type = $hard_fact_type")
        if explicit_filters.get("verification_level"):
            params["hard_verification_level"] = explicit_filters[
                "verification_level"
            ]
            clauses.append(
                "f.verification_level = $hard_verification_level"
            )
        if explicit_filters.get("source_type"):
            params["hard_source_type"] = explicit_filters["source_type"]
            clauses.append("s.source_type = $hard_source_type")

        where = " AND ".join(clauses)

        cypher = f"""
        UNWIND $retrieved_chunks AS rc
        MATCH (c:Chunk {{id: rc.chunk_id}})
        MATCH (f:Fact)-[:SUPPORTED_BY]->(c)
        MATCH (f)-[ds:DESCRIBED_IN]->(s:Source)
        WHERE {where}
        WITH f, s, ds,
             max(toFloat(coalesce(rc.score, 0.0))) AS retrieval_score

        OPTIONAL MATCH (f)-[:ABOUT]->(e)
        WITH f, s, ds, retrieval_score,
             collect(DISTINCT e) AS entities

        WITH f, s, ds, retrieval_score, entities,

             CASE
               WHEN size($entity_terms) = 0 THEN 0
               ELSE reduce(
                 matches = 0,
                 entity IN entities |
                   matches + CASE
                     WHEN any(
                       term IN $entity_terms
                       WHERE toLower(
                         coalesce(entity.canonical_name, entity.name, '')
                       ) CONTAINS term
                     )
                     THEN 1 ELSE 0
                   END
               )
             END AS entity_match_score,

             CASE
               WHEN size($explicit_entity_terms) = 0 THEN 0
               ELSE reduce(
                 matches = 0,
                 entity IN entities |
                   matches + CASE
                     WHEN any(
                       term IN $explicit_entity_terms
                       WHERE toLower(
                         coalesce(entity.canonical_name, entity.name, '')
                       ) CONTAINS term
                     )
                     THEN 1 ELSE 0
                   END
               )
             END AS explicit_entity_match_score,

             CASE
               WHEN $plan_year_from IS NULL
                AND $plan_year_to IS NULL THEN 0
               WHEN coalesce(f.year, s.year) IS NULL THEN 0
               WHEN ($plan_year_from IS NULL
                     OR coalesce(f.year, s.year) >= $plan_year_from)
                AND ($plan_year_to IS NULL
                     OR coalesce(f.year, s.year) <= $plan_year_to)
               THEN 1 ELSE 0
             END
             +
             CASE
               WHEN $plan_geo_scope IS NULL THEN 0
               WHEN f.geo_scope = $plan_geo_scope THEN 1
               ELSE 0
             END
             +
             CASE
               WHEN size($plan_countries) = 0 THEN 0
               WHEN coalesce(f.country, s.country) IN $plan_countries
               THEN 1 ELSE 0
             END
             +
             CASE
               WHEN $plan_status IS NULL THEN 0
               WHEN f.status = $plan_status THEN 1
               ELSE 0
             END
             +
             CASE
               WHEN $plan_fact_type IS NULL THEN 0
               WHEN f.fact_type = $plan_fact_type THEN 1
               ELSE 0
             END
             +
             CASE
               WHEN $plan_verification_level IS NULL THEN 0
               WHEN f.verification_level = $plan_verification_level
               THEN 1 ELSE 0
             END
             +
             CASE
               WHEN $plan_source_type IS NULL THEN 0
               WHEN s.source_type = $plan_source_type THEN 1
               ELSE 0
             END AS plan_match_score

        OPTIONAL MATCH (f)-[:HAS_PARAMETER]->(v:ParameterValue)
        RETURN f,
               entities,
               collect(DISTINCT v) AS params,
               s,
               ds,
               retrieval_score,
               entity_match_score,
               explicit_entity_match_score,
               plan_match_score
        ORDER BY explicit_entity_match_score DESC,
                 retrieval_score DESC,
                 plan_match_score DESC,
                 entity_match_score DESC,
                 f.confidence DESC,
                 coalesce(f.year, s.year) DESC
        LIMIT $limit
        """
        return cypher, params

    def build_text_fallback(
        self,
        query: str,
        limit: int = 50,
        visible_access_levels: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        visible_access_levels = visible_access_levels or ["public"]
        tokens = [t for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", query.lower()) if len(t) >= 4]
        tokens = tokens[:8]
        fulltext_query = " OR ".join(tokens) if tokens else "__empty__"
        params: dict[str, Any] = {
            "fulltext_query": fulltext_query,
            "visible_access_levels": visible_access_levels,
            "limit": limit,
        }
        cypher = """
        CALL db.index.fulltext.queryNodes("fact_fulltext", $fulltext_query)
        YIELD node AS f, score
        MATCH (f)-[ds:DESCRIBED_IN]->(s:Source)
        WHERE coalesce(s.access_level, 'internal') IN $visible_access_levels
        OPTIONAL MATCH (f)-[:ABOUT]->(e)
        OPTIONAL MATCH (f)-[:HAS_PARAMETER]->(v:ParameterValue)
        RETURN f, collect(DISTINCT e) AS entities, collect(DISTINCT v) AS params, s, ds
        ORDER BY score DESC, f.confidence DESC, f.year DESC
        LIMIT $limit
        """
        return cypher, params
