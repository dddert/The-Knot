from __future__ import annotations
from app.db.neo4j import get_neo4j_driver
from app.services.serialization import to_jsonable


class DashboardService:
    def coverage(self, visible_access_levels: list[str] | None = None) -> dict:
        visible_access_levels = visible_access_levels or ["public"]
        with get_neo4j_driver().session() as session:
            counts_row = session.run(
                """
                MATCH (f:Fact)-[:DESCRIBED_IN]->(s:Source)
                WHERE coalesce(s.access_level, 'internal') IN $visible_access_levels
                WITH collect(DISTINCT f) AS visible_facts
                UNWIND visible_facts AS f
                OPTIONAL MATCH (f)-[:ABOUT]->(e)
                OPTIONAL MATCH (f)-[:HAS_PARAMETER]->(p:ParameterValue)
                OPTIONAL MATCH (f)-[:SUPPORTED_BY]->(c:Chunk)
                OPTIONAL MATCH (f)-[:DESCRIBED_IN]->(src:Source)
                WITH visible_facts,
                     collect(DISTINCT e) AS entities,
                     collect(DISTINCT p) AS params,
                     collect(DISTINCT c) AS chunks,
                     collect(DISTINCT src) AS sources
                RETURN [
                  {label: 'Fact', count: size(visible_facts)},
                  {label: 'Source', count: size(sources)},
                  {label: 'Chunk', count: size(chunks)},
                  {label: 'ParameterValue', count: size(params)},
                  {label: 'Entity', count: size(entities)}
                ] AS counts
                """,
                visible_access_levels=visible_access_levels,
            ).single()
            counts = counts_row["counts"] if counts_row else []
            facts_by_process = session.run(
                """
                MATCH (f:Fact)-[:DESCRIBED_IN]->(s:Source)
                WHERE coalesce(s.access_level, 'internal') IN $visible_access_levels
                MATCH (f)-[:ABOUT]->(p:Process)
                RETURN p.canonical_name AS process, count(DISTINCT f) AS facts
                ORDER BY facts DESC
                LIMIT 20
                """,
                visible_access_levels=visible_access_levels,
            ).data()
            facts_by_geo = session.run(
                """
                MATCH (f:Fact)-[:DESCRIBED_IN]->(s:Source)
                WHERE coalesce(s.access_level, 'internal') IN $visible_access_levels
                RETURN coalesce(f.geo_scope, 'unknown') AS geo_scope, count(f) AS facts
                ORDER BY facts DESC
                """,
                visible_access_levels=visible_access_levels,
            ).data()
            facts_by_status = session.run(
                """
                MATCH (f:Fact)-[:DESCRIBED_IN]->(s:Source)
                WHERE coalesce(s.access_level, 'internal') IN $visible_access_levels
                RETURN coalesce(f.status, 'unknown') AS status, count(f) AS facts
                ORDER BY facts DESC
                """,
                visible_access_levels=visible_access_levels,
            ).data()
            weak_topics = session.run(
                """
                MATCH (p:Process)
                OPTIONAL MATCH (p)<-[:ABOUT]-(f:Fact)-[:DESCRIBED_IN]->(s:Source)
                WHERE f IS NULL OR coalesce(s.access_level, 'internal') IN $visible_access_levels
                WITH p.canonical_name AS process, count(DISTINCT f) AS facts
                WHERE facts <= 1
                RETURN process, facts
                ORDER BY facts ASC, process ASC
                LIMIT 10
                """,
                visible_access_levels=visible_access_levels,
            ).data()
            contradictions = session.run(
                """
                MATCH (f:Fact {status: 'contradicted'})-[:DESCRIBED_IN]->(s:Source)
                WHERE coalesce(s.access_level, 'internal') IN $visible_access_levels
                RETURN count(f) AS count
                """,
                visible_access_levels=visible_access_levels,
            ).single()["count"]
        return {
            "counts": to_jsonable(counts),
            "facts_by_process": to_jsonable(facts_by_process),
            "facts_by_geo": to_jsonable(facts_by_geo),
            "facts_by_status": to_jsonable(facts_by_status),
            "weak_topics": to_jsonable(weak_topics),
            "contradictions_count": contradictions,
            "visible_access_levels": visible_access_levels,
        }
