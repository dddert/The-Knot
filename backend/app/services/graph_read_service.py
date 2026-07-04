from __future__ import annotations
from typing import Any
from app.db.neo4j import get_neo4j_driver
from app.schemas.dto import GraphDTO, GraphEdgeDTO, GraphNodeDTO
from app.services.serialization import node_to_dict, relationship_to_dict


class GraphReadService:
    def get_node(self, node_id: str, visible_access_levels: list[str] | None = None) -> dict[str, Any] | None:
        visible_access_levels = visible_access_levels or ["public"]
        query = """
        MATCH (n {id: $node_id})
        OPTIONAL MATCH (n)-[*0..3]-(src:Source)
        WITH n, collect(DISTINCT coalesce(src.access_level, 'internal')) AS source_levels
        OPTIONAL MATCH (n)-[rel]-()
        WITH n, source_levels, collect(DISTINCT rel.access_level) AS rel_levels
        WHERE (n:Source AND coalesce(n.access_level, 'internal') IN $visible_access_levels)
           OR any(level IN source_levels WHERE level IN $visible_access_levels)
           OR any(level IN rel_levels WHERE level IN $visible_access_levels)
        RETURN n LIMIT 1
        """
        with get_neo4j_driver().session() as session:
            row = session.run(query, node_id=node_id, visible_access_levels=visible_access_levels).single()
        return node_to_dict(row["n"]) if row else None

    def get_neighbors(self, node_id: str, depth: int = 1, limit: int = 100, mode: str = "compact", visible_access_levels: list[str] | None = None) -> GraphDTO:
        if mode == "none":
            return GraphDTO()
        visible_access_levels = visible_access_levels or ["public"]
        depth = max(1, min(depth, 4))
        query = f"""
        MATCH (center {{id: $node_id}})
        OPTIONAL MATCH (center)-[*0..3]-(visible_s:Source)
        WITH center, collect(DISTINCT coalesce(visible_s.access_level, 'internal')) AS center_levels
        OPTIONAL MATCH (center)-[center_rel]-()
        WITH center, center_levels, collect(DISTINCT center_rel.access_level) AS center_rel_levels
        WHERE (center:Source AND coalesce(center.access_level, 'internal') IN $visible_access_levels)
           OR any(level IN center_levels WHERE level IN $visible_access_levels)
           OR any(level IN center_rel_levels WHERE level IN $visible_access_levels)
        MATCH path=(center)-[*1..{depth}]-(n)
        WHERE all(src IN [x IN nodes(path) WHERE 'Source' IN labels(x)] WHERE coalesce(src.access_level, 'internal') IN $visible_access_levels)
          AND all(f IN [x IN nodes(path) WHERE 'Fact' IN labels(x)] WHERE EXISTS {{ MATCH (f)-[:DESCRIBED_IN]->(fs:Source) WHERE coalesce(fs.access_level, 'internal') IN $visible_access_levels }})
          AND all(rel IN relationships(path) WHERE rel.access_level IS NULL OR rel.access_level IN $visible_access_levels)
        RETURN path
        LIMIT $limit
        """
        return self._paths_to_graph(query, {"node_id": node_id, "limit": limit, "visible_access_levels": visible_access_levels}, mode=mode)

    def get_subgraph(self, fact_ids: list[str] | None = None, limit: int = 50, mode: str = "compact", visible_access_levels: list[str] | None = None) -> GraphDTO:
        if mode == "none":
            return GraphDTO()
        visible_access_levels = visible_access_levels or ["public"]
        params = {"fact_ids": fact_ids or [], "limit": limit, "visible_access_levels": visible_access_levels}
        id_clause = "AND f.id IN $fact_ids" if fact_ids else ""
        query = f"""
        MATCH (f:Fact)-[:DESCRIBED_IN]->(s:Source)
        WHERE coalesce(s.access_level, 'internal') IN $visible_access_levels
        {id_clause}
        OPTIONAL MATCH path=(f)-[r]-(n)
        WHERE all(src IN [x IN nodes(path) WHERE 'Source' IN labels(x)] WHERE coalesce(src.access_level, 'internal') IN $visible_access_levels)
          AND all(rel IN relationships(path) WHERE rel.access_level IS NULL OR rel.access_level IN $visible_access_levels)
        RETURN path
        LIMIT $limit
        """
        return self._paths_to_graph(query, params, mode=mode)

    def get_path(self, source_id: str, target_id: str, max_depth: int = 4, mode: str = "compact", visible_access_levels: list[str] | None = None) -> GraphDTO:
        if mode == "none":
            return GraphDTO()
        visible_access_levels = visible_access_levels or ["public"]
        max_depth = max(1, min(max_depth, 6))
        query = f"""
        MATCH (a {{id: $source_id}}), (b {{id: $target_id}})
        MATCH path = shortestPath((a)-[*..{max_depth}]-(b))
        WHERE all(src IN [x IN nodes(path) WHERE 'Source' IN labels(x)] WHERE coalesce(src.access_level, 'internal') IN $visible_access_levels)
          AND all(f IN [x IN nodes(path) WHERE 'Fact' IN labels(x)] WHERE EXISTS {{ MATCH (f)-[:DESCRIBED_IN]->(fs:Source) WHERE coalesce(fs.access_level, 'internal') IN $visible_access_levels }})
          AND all(rel IN relationships(path) WHERE rel.access_level IS NULL OR rel.access_level IN $visible_access_levels)
        RETURN path
        LIMIT 1
        """
        return self._paths_to_graph(query, {"source_id": source_id, "target_id": target_id, "visible_access_levels": visible_access_levels}, mode=mode)

    def _paths_to_graph(self, query: str, params: dict[str, Any], mode: str = "compact") -> GraphDTO:
        nodes: dict[str, GraphNodeDTO] = {}
        edges: dict[str, GraphEdgeDTO] = {}
        with get_neo4j_driver().session() as session:
            for row in session.run(query, **params):
                path = row.get("path")
                if path is None:
                    continue
                for node in path.nodes:
                    dto = GraphNodeDTO.model_validate(node_to_dict(node))
                    if mode == "compact":
                        dto = self._compact_node(dto)
                    nodes[dto.id] = dto
                for rel in path.relationships:
                    dto = GraphEdgeDTO.model_validate(relationship_to_dict(rel))
                    if mode == "compact":
                        dto.properties = self._compact_edge_props(dto.properties)
                    edges[dto.id] = dto
        return GraphDTO(nodes=list(nodes.values()), edges=list(edges.values()))

    def _compact_node(self, node: GraphNodeDTO) -> GraphNodeDTO:
        props = node.properties or {}
        keep_keys = {
            "id", "name", "canonical_name", "title", "label", "entity_type",
            "claim_text", "fact_type", "geo_scope", "country", "year", "status",
            "confidence", "verification_level", "source_type", "access_level",
            "organization", "parameter", "display_name", "value", "value_min",
            "value_max", "comparator", "unit_normalized", "unit_original", "page",
            "document_id", "filename",
        }
        compact = {k: v for k, v in props.items() if k in keep_keys and v is not None}
        if node.label == "Chunk" and props.get("text"):
            text = str(props.get("text"))
            compact["text_preview"] = text[:240] + ("…" if len(text) > 240 else "")
        if "claim_text" in compact and compact["claim_text"]:
            text = str(compact["claim_text"])
            compact["claim_preview"] = text[:220] + ("…" if len(text) > 220 else "")
        if node.title and len(node.title) > 160:
            node.title = node.title[:157] + "…"
        node.properties = compact
        return node

    def _compact_edge_props(self, props: dict[str, Any]) -> dict[str, Any]:
        keep_keys = {"id", "page", "quote", "confidence", "evidence_text", "source_id", "access_level"}
        compact = {k: v for k, v in (props or {}).items() if k in keep_keys and v is not None}
        if compact.get("quote") and len(str(compact["quote"])) > 180:
            compact["quote"] = str(compact["quote"])[:177] + "…"
        if compact.get("evidence_text") and len(str(compact["evidence_text"])) > 180:
            compact["evidence_text"] = str(compact["evidence_text"])[:177] + "…"
        return compact
