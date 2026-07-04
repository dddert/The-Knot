from __future__ import annotations
import re
from typing import Any
from app.db.neo4j import get_neo4j_driver
from app.schemas.contracts import ExtractedDocument

ALLOWED_LABELS = {
    "Material",
    "Process",
    "Equipment",
    "Property",
    "Experiment",
    "Publication",
    "Patent",
    "Report",
    "Expert",
    "Laboratory",
    "Facility",
    "TechnologySolution",
    "Geography",
    "EconomicIndicator",
    "EnvironmentalIndicator",
}


def safe_label(label: str) -> str:
    if label not in ALLOWED_LABELS:
        return "Entity"
    return label


def safe_rel_type(rel_type: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", rel_type.upper())
    return cleaned[:64] or "RELATED_TO"


class GraphService:
    def init_schema(self) -> dict[str, Any]:
        constraints = [
            "CREATE CONSTRAINT material_id IF NOT EXISTS FOR (n:Material) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT process_id IF NOT EXISTS FOR (n:Process) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT equipment_id IF NOT EXISTS FOR (n:Equipment) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT source_id IF NOT EXISTS FOR (n:Source) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT fact_id IF NOT EXISTS FOR (n:Fact) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT parameter_id IF NOT EXISTS FOR (n:ParameterValue) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT expert_id IF NOT EXISTS FOR (n:Expert) REQUIRE n.id IS UNIQUE",
            "CREATE INDEX fact_confidence IF NOT EXISTS FOR (n:Fact) ON (n.confidence)",
            "CREATE INDEX fact_year IF NOT EXISTS FOR (n:Fact) ON (n.year)",
            "CREATE INDEX fact_status IF NOT EXISTS FOR (n:Fact) ON (n.status)",
            "CREATE INDEX fact_geo_scope IF NOT EXISTS FOR (n:Fact) ON (n.geo_scope)",
            "CREATE INDEX source_year IF NOT EXISTS FOR (n:Source) ON (n.year)",
            "CREATE INDEX param_name IF NOT EXISTS FOR (n:ParameterValue) ON (n.parameter)",
            "CREATE INDEX param_min IF NOT EXISTS FOR (n:ParameterValue) ON (n.value_min)",
            "CREATE INDEX param_max IF NOT EXISTS FOR (n:ParameterValue) ON (n.value_max)",
            "CREATE INDEX process_canonical IF NOT EXISTS FOR (n:Process) ON (n.canonical_name)",
            "CREATE INDEX material_canonical IF NOT EXISTS FOR (n:Material) ON (n.canonical_name)",
            "CREATE INDEX equipment_canonical IF NOT EXISTS FOR (n:Equipment) ON (n.canonical_name)",
            "CREATE INDEX technology_canonical IF NOT EXISTS FOR (n:TechnologySolution) ON (n.canonical_name)",
            "CREATE INDEX fact_type IF NOT EXISTS FOR (n:Fact) ON (n.fact_type)",
            "CREATE INDEX source_access_level IF NOT EXISTS FOR (n:Source) ON (n.access_level)",
        ]
        fulltext_status = "created"
        fulltext_error = None
        with get_neo4j_driver().session() as session:
            for query in constraints:
                session.run(query)
            try:
                session.run(
                    """
                    CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS
                    FOR (n:Material|Process|Equipment|TechnologySolution|Expert|Source)
                    ON EACH [n.name, n.canonical_name, n.description, n.title]
                    """
                )
                session.run(
                    """
                    CREATE FULLTEXT INDEX fact_fulltext IF NOT EXISTS
                    FOR (f:Fact)
                    ON EACH [f.claim_text, f.fact_type]
                    """
                )
            except Exception as exc:
                # Fulltext syntax may vary between minor editions. Core demo remains functional.
                fulltext_status = "failed"
                fulltext_error = f"{type(exc).__name__}: {exc}"
        result = {"status": "ok", "constraints_and_indexes": len(constraints), "fulltext_indexes": fulltext_status}
        if fulltext_error:
            result["fulltext_error"] = fulltext_error
        return result

    def clear_demo_graph(self) -> dict[str, Any]:
        with get_neo4j_driver().session() as session:
            result = session.run("MATCH (n {demo: true}) WITH collect(n) AS nodes, count(n) AS deleted FOREACH (n IN nodes | DETACH DELETE n) RETURN deleted").single()
        return {"status": "ok", "deleted": result["deleted"] if result else 0}

    def import_extracted_document(self, extracted: ExtractedDocument, demo: bool = False) -> dict[str, Any]:
        self.init_schema()
        doc_id = extracted.document_id
        source_payload = extracted.source.model_dump(exclude_none=True) if extracted.source else {}
        source_id = source_payload.get("id") or f"source_{doc_id}"

        with get_neo4j_driver().session() as session:
            session.execute_write(self._upsert_source, source_id, doc_id, source_payload, demo)

            for chunk in extracted.chunks:
                session.execute_write(
                    self._upsert_chunk,
                    {
                        "id": chunk.id,
                        "page": chunk.page,
                        "text": chunk.text,
                        "embedding_text": chunk.embedding_text or chunk.text,
                        "document_id": doc_id,
                    },
                    source_id,
                    demo,
                )

            for entity in extracted.entities:
                payload = entity.model_dump()
                label = safe_label(entity.type)
                session.execute_write(self._upsert_entity, label, payload, demo)
                if entity.source_chunk_id:
                    session.execute_write(self._link_entity_chunk, entity.id, entity.source_chunk_id)

            for value in extracted.numeric_values:
                session.execute_write(self._upsert_numeric, value.model_dump(), demo)
                if value.source_chunk_id:
                    session.execute_write(self._link_numeric_chunk, value.id, value.source_chunk_id)

            for fact in extracted.facts:
                session.execute_write(self._upsert_fact, fact.model_dump(), source_id, demo)
                related_ids = []
                if fact.subject_entity_id:
                    related_ids.append(fact.subject_entity_id)
                related_ids += fact.object_entity_ids
                for entity_id in sorted(set([x for x in related_ids if x])):
                    session.execute_write(self._link_fact_entity, fact.id, entity_id)
                for num_id in fact.numeric_value_ids:
                    session.execute_write(self._link_fact_numeric, fact.id, num_id)
                if fact.source.chunk_id:
                    session.execute_write(self._link_fact_chunk, fact.id, fact.source.chunk_id)

            for relation in extracted.relations:
                rel_type = safe_rel_type(relation.type)
                session.execute_write(self._upsert_entity_relation, relation.model_dump(), rel_type, source_id, source_payload.get("access_level", "internal"), demo)

        return {
            "status": "imported",
            "document_id": doc_id,
            "source_id": source_id,
            "chunks_count": len(extracted.chunks),
            "entities_count": len(extracted.entities),
            "relations_count": len(extracted.relations),
            "numeric_values_count": len(extracted.numeric_values),
            "facts_count": len(extracted.facts),
        }

    @staticmethod
    def _upsert_source(tx, source_id: str, document_id: str, payload: dict[str, Any], demo: bool):
        tx.run(
            """
            MERGE (s:Source {id: $id})
            SET s.document_id = $document_id,
                s.title = $title,
                s.filename = $filename,
                s.source_type = $source_type,
                s.access_level = $access_level,
                s.year = $year,
                s.country = $country,
                s.organization = $organization,
                s.authors = $authors,
                s.demo = $demo,
                s.updated_at = datetime()
            """,
            id=source_id,
            document_id=document_id,
            title=payload.get("title") or payload.get("filename") or document_id,
            filename=payload.get("filename"),
            source_type=payload.get("source_type", "internal_report"),
            access_level=payload.get("access_level", "internal"),
            year=payload.get("year"),
            country=payload.get("country"),
            organization=payload.get("organization"),
            authors=payload.get("authors", []),
            demo=demo,
        )

    @staticmethod
    def _upsert_chunk(tx, payload: dict[str, Any], source_id: str, demo: bool):
        tx.run(
            """
            MERGE (c:Chunk {id: $id})
            SET c.page = $page,
                c.text = $text,
                c.embedding_text = $embedding_text,
                c.document_id = $document_id,
                c.demo = $demo
            WITH c
            MATCH (s:Source {id: $source_id})
            MERGE (c)-[:PART_OF]->(s)
            """,
            **payload,
            source_id=source_id,
            demo=demo,
        )

    @staticmethod
    def _upsert_entity(tx, label: str, payload: dict[str, Any], demo: bool):
        tx.run(
            f"""
            MERGE (e:{label} {{id: $id}})
            SET e.name = $name,
                e.canonical_name = $canonical_name,
                e.language = $language,
                e.aliases = $aliases,
                e.page = $page,
                e.confidence = $confidence,
                e.description = $description,
                e.entity_type = $type,
                e.demo = $demo,
                e.updated_at = datetime()
            """,
            **payload,
            demo=demo,
        )

    @staticmethod
    def _link_entity_chunk(tx, entity_id: str, chunk_id: str):
        tx.run(
            """
            MATCH (e {id: $entity_id})
            MATCH (c:Chunk {id: $chunk_id})
            MERGE (e)-[:MENTIONED_IN]->(c)
            """,
            entity_id=entity_id,
            chunk_id=chunk_id,
        )

    @staticmethod
    def _upsert_numeric(tx, payload: dict[str, Any], demo: bool):
        tx.run(
            """
            MERGE (p:ParameterValue {id: $id})
            SET p.parameter = $parameter,
                p.display_name = $display_name,
                p.value = $value,
                p.value_min = $value_min,
                p.value_max = $value_max,
                p.comparator = $comparator,
                p.unit_original = $unit_original,
                p.unit_normalized = $unit_normalized,
                p.context = $context,
                p.source_text = $source_text,
                p.page = $page,
                p.confidence = $confidence,
                p.demo = $demo,
                p.updated_at = datetime()
            """,
            **payload,
            demo=demo,
        )

    @staticmethod
    def _link_numeric_chunk(tx, numeric_id: str, chunk_id: str):
        tx.run(
            """
            MATCH (p:ParameterValue {id: $numeric_id})
            MATCH (c:Chunk {id: $chunk_id})
            MERGE (p)-[:EXTRACTED_FROM]->(c)
            """,
            numeric_id=numeric_id,
            chunk_id=chunk_id,
        )

    @staticmethod
    def _upsert_fact(tx, payload: dict[str, Any], source_id: str, demo: bool):
        source = payload.pop("source")
        tx.run(
            """
            MERGE (f:Fact {id: $id})
            SET f.claim_text = $claim_text,
                f.fact_type = $fact_type,
                f.geo_scope = $geo_scope,
                f.country = $country,
                f.year = $year,
                f.confidence = $confidence,
                f.verification_level = $verification_level,
                f.status = $status,
                f.demo = $demo,
                f.updated_at = coalesce($updated_at, toString(datetime()))
            WITH f
            MATCH (s:Source {id: $source_id})
            MERGE (f)-[r:DESCRIBED_IN]->(s)
            SET r.page = $page,
                r.quote = $quote
            """,
            **payload,
            source_id=source_id,
            page=source.get("page"),
            quote=source.get("quote"),
            demo=demo,
        )

    @staticmethod
    def _link_fact_entity(tx, fact_id: str, entity_id: str):
        tx.run(
            """
            MATCH (f:Fact {id: $fact_id})
            MATCH (e {id: $entity_id})
            MERGE (f)-[:ABOUT]->(e)
            """,
            fact_id=fact_id,
            entity_id=entity_id,
        )

    @staticmethod
    def _link_fact_numeric(tx, fact_id: str, numeric_id: str):
        tx.run(
            """
            MATCH (f:Fact {id: $fact_id})
            MATCH (p:ParameterValue {id: $numeric_id})
            MERGE (f)-[:HAS_PARAMETER]->(p)
            """,
            fact_id=fact_id,
            numeric_id=numeric_id,
        )

    @staticmethod
    def _link_fact_chunk(tx, fact_id: str, chunk_id: str):
        tx.run(
            """
            MATCH (f:Fact {id: $fact_id})
            MATCH (c:Chunk {id: $chunk_id})
            MERGE (f)-[:SUPPORTED_BY]->(c)
            """,
            fact_id=fact_id,
            chunk_id=chunk_id,
        )

    @staticmethod
    def _upsert_entity_relation(tx, payload: dict[str, Any], rel_type: str, source_id: str, access_level: str, demo: bool):
        tx.run(
            f"""
            MATCH (a {{id: $source_entity_id}})
            MATCH (b {{id: $target_entity_id}})
            MERGE (a)-[r:{rel_type}]->(b)
            SET r.id = $id,
                r.evidence_text = $evidence_text,
                r.confidence = $confidence,
                r.source_chunk_id = $source_chunk_id,
                r.source_id = $source_id,
                r.access_level = $access_level,
                r.demo = $demo,
                r.updated_at = datetime()
            """,
            **payload,
            source_id=source_id,
            access_level=access_level,
            demo=demo,
        )

