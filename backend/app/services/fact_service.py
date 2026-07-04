from __future__ import annotations
from typing import Any
from app.db.neo4j import get_neo4j_driver
from app.schemas.dto import FactDTO
from app.services.fact_mapper import map_fact
from app.services.fact_version_service import FactVersionService


class FactService:
    allowed_update_fields = {"claim_text", "confidence", "status", "verification_level"}

    def get_fact(self, fact_id: str, visible_access_levels: list[str] | None = None) -> FactDTO | None:
        visible_access_levels = visible_access_levels or ["public"]
        with get_neo4j_driver().session() as session:
            row = session.run(
                """
                MATCH (f:Fact {id: $fact_id})
                MATCH (f)-[ds:DESCRIBED_IN]->(s:Source)
                WHERE coalesce(s.access_level, 'internal') IN $visible_access_levels
                OPTIONAL MATCH (f)-[:HAS_PARAMETER]->(v:ParameterValue)
                OPTIONAL MATCH (f)-[:ABOUT]->(e)
                RETURN f, s, ds, collect(DISTINCT v) AS params, collect(DISTINCT e) AS entities
                """,
                fact_id=fact_id,
                visible_access_levels=visible_access_levels,
            ).single()
        if not row:
            return None
        return map_fact(
            dict(row["f"]),
            entities=[dict(e) for e in row["entities"] if e],
            numeric_values=[dict(v) for v in row["params"] if v],
            source_props=dict(row["s"]) if row["s"] else None,
            source_rel_props=dict(row["ds"]) if row["ds"] else None,
        )

    def list_facts(
        self,
        status: str | None = None,
        geo_scope: str | None = None,
        confidence_min: float = 0.0,
        limit: int = 100,
        visible_access_levels: list[str] | None = None,
    ) -> list[FactDTO]:
        clauses = ["f.confidence >= $confidence_min"]
        visible_access_levels = visible_access_levels or ["public"]
        params: dict[str, Any] = {"confidence_min": confidence_min, "limit": limit, "visible_access_levels": visible_access_levels}
        if status:
            clauses.append("f.status = $status")
            params["status"] = status
        if geo_scope and geo_scope != "all":
            clauses.append("f.geo_scope = $geo_scope")
            params["geo_scope"] = geo_scope
        query = f"""
        MATCH (f:Fact)
        MATCH (f)-[ds:DESCRIBED_IN]->(s:Source)
        WHERE {' AND '.join(clauses)}
          AND coalesce(s.access_level, 'internal') IN $visible_access_levels
        OPTIONAL MATCH (f)-[:HAS_PARAMETER]->(v:ParameterValue)
        OPTIONAL MATCH (f)-[:ABOUT]->(e)
        RETURN f, s, ds, collect(DISTINCT v) AS params, collect(DISTINCT e) AS entities
        ORDER BY f.confidence DESC, f.year DESC
        LIMIT $limit
        """
        facts: list[FactDTO] = []
        with get_neo4j_driver().session() as session:
            for row in session.run(query, **params):
                facts.append(map_fact(
                    dict(row["f"]),
                    entities=[dict(e) for e in row["entities"] if e],
                    numeric_values=[dict(v) for v in row["params"] if v],
                    source_props=dict(row["s"]) if row["s"] else None,
                    source_rel_props=dict(row["ds"]) if row["ds"] else None,
                ))
        return facts

    def get_facts_by_ids(self, fact_ids: list[str], visible_access_levels: list[str] | None = None) -> list[FactDTO]:
        visible_access_levels = visible_access_levels or ["public"]
        if not fact_ids:
            return []
        query = """
        MATCH (f:Fact)
        WHERE f.id IN $fact_ids
        MATCH (f)-[ds:DESCRIBED_IN]->(s:Source)
        WHERE coalesce(s.access_level, 'internal') IN $visible_access_levels
        OPTIONAL MATCH (f)-[:HAS_PARAMETER]->(v:ParameterValue)
        OPTIONAL MATCH (f)-[:ABOUT]->(e)
        RETURN f, s, ds, collect(DISTINCT v) AS params, collect(DISTINCT e) AS entities
        """
        result: list[FactDTO] = []
        with get_neo4j_driver().session() as session:
            rows = session.run(query, fact_ids=fact_ids, visible_access_levels=visible_access_levels)
            for row in rows:
                result.append(map_fact(
                    dict(row["f"]),
                    entities=[dict(e) for e in row["entities"] if e],
                    numeric_values=[dict(v) for v in row["params"] if v],
                    source_props=dict(row["s"]) if row["s"] else None,
                    source_rel_props=dict(row["ds"]) if row["ds"] else None,
                ))
        order = {fact_id: i for i, fact_id in enumerate(fact_ids)}
        return sorted(result, key=lambda f: order.get(f.id, 10**9))

    def update_fact(self, fact_id: str, updates: dict[str, Any]) -> FactDTO | None:
        set_parts = []
        params = {"fact_id": fact_id}
        for key, value in updates.items():
            if key in self.allowed_update_fields and value is not None:
                set_parts.append(f"f.{key} = ${key}")
                params[key] = value
        if not set_parts:
            return self.get_fact(fact_id, visible_access_levels=["public", "internal", "confidential"])
        set_parts.append("f.updated_at = toString(datetime())")
        query = f"""
        MATCH (f:Fact {{id: $fact_id}})
        SET {', '.join(set_parts)}
        RETURN f.id AS id
        """
        with get_neo4j_driver().session() as session:
            row = session.run(query, **params).single()
        return self.get_fact(row["id"], visible_access_levels=["public", "internal", "confidential"]) if row else None

    def update_with_version(
        self,
        fact_id: str,
        updates: dict[str, Any],
        comment: str | None,
        updated_by: str,
        visible_access_levels: list[str] | None = None,
    ) -> tuple[FactDTO | None, dict[str, Any] | None, int | None]:
        visible_access_levels = visible_access_levels or ["public"]
        previous = self.get_fact(fact_id, visible_access_levels=visible_access_levels)
        if not previous:
            return None, None, None
        updated = self.update_fact(fact_id, updates)
        if not updated:
            return None, None, None
        version = FactVersionService().create_version(
            fact_id=fact_id,
            previous_payload=previous.model_dump(),
            new_payload=updated.model_dump(),
            comment=comment,
            updated_by=updated_by,
        )
        return updated, previous.model_dump(), version
