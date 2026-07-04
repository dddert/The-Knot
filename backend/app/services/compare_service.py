from __future__ import annotations
from collections import defaultdict
from typing import Any
from app.schemas.contracts import QueryPlan
from app.core.access import visible_access_levels
from app.schemas.dto import FactDTO
from app.services.cypher_builder import CypherBuilder
from app.services.fact_mapper import map_fact
from app.db.neo4j import get_neo4j_driver


class CompareService:
    """Build comparison tables over facts, usually domestic vs foreign practice."""

    def __init__(self):
        self.cypher = CypherBuilder()

    def compare(
        self,
        plan: QueryPlan,
        group_by: str = "geo_scope",
        limit: int = 100,
        role: str = "analyst",
    ) -> dict[str, Any]:
        cypher, params = self.cypher.build_fact_search(plan, limit=limit, visible_access_levels=visible_access_levels(role))
        facts: list[FactDTO] = []
        with get_neo4j_driver().session() as session:
            for row in session.run(cypher, **params):
                facts.append(map_fact(
                    dict(row["f"]),
                    entities=[dict(e) for e in row["entities"] if e],
                    numeric_values=[dict(v) for v in row["params"] if v],
                    source_props=dict(row["s"]) if row["s"] else None,
                    source_rel_props=dict(row["ds"]) if row["ds"] else None,
                ))

        groups: dict[str, list[FactDTO]] = defaultdict(list)
        for fact in facts:
            key = getattr(fact, group_by, None) or "unknown"
            groups[str(key)].append(fact)

        return {
            "group_by": group_by,
            "total_facts": len(facts),
            "groups": [
                {
                    "key": key,
                    "count": len(items),
                    "avg_confidence": round(sum((f.confidence or 0) for f in items) / max(len(items), 1), 3),
                    "facts": [f.model_dump(exclude_none=True) for f in items],
                }
                for key, items in sorted(groups.items(), key=lambda kv: kv[0])
            ],
            "table": self._comparison_rows(facts, group_by=group_by),
        }

    def _comparison_rows(self, facts: list[FactDTO], group_by: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for fact in facts:
            numeric = []
            for n in fact.numeric_values:
                if n.value_min is not None and n.value_max is not None:
                    value = f"{n.value_min:g}–{n.value_max:g}"
                elif n.value is not None:
                    value = f"{n.value:g}"
                elif n.value_max is not None:
                    value = f"≤{n.value_max:g}"
                elif n.value_min is not None:
                    value = f"≥{n.value_min:g}"
                else:
                    value = ""
                numeric.append(f"{n.display_name or n.parameter}: {value} {n.unit_normalized or ''}".strip())

            methods = [
                e.name or e.canonical_name
                for e in fact.entities
                if e.type in {"TechnologySolution", "Equipment", "Process"}
            ]
            technology = ", ".join([m for m in methods if m])[:240] or "—"
            numeric_text = "; ".join(numeric) or "—"
            rows.append({
                "Технология / процесс": technology,
                "Практика": getattr(fact, group_by, None) or "unknown",
                "География": fact.geo_scope,
                "Страна": fact.country,
                "Эффективность": "—",
                "CAPEX": _find_numeric(fact, "capex"),
                "OPEX": _find_numeric(fact, "opex"),
                "Условия применимости": numeric_text,
                "Экологические ограничения": "см. claim / source",
                "Вывод": fact.claim_text,
                "Источник": fact.source_title,
                "Стр.": fact.source_page,
                "Год": fact.year,
                "Confidence": fact.confidence,
                "Статус": fact.status,
                "fact_id": fact.id,
                # Legacy keys for API compatibility.
                "group": getattr(fact, group_by, None) or "unknown",
                "technology_or_process": technology,
                "claim": fact.claim_text,
                "numeric_parameters": numeric_text,
                "geo_scope": fact.geo_scope,
                "country": fact.country,
                "year": fact.year,
                "confidence": fact.confidence,
                "status": fact.status,
                "source": fact.source_title,
                "page": fact.source_page,
            })
        return rows


def _find_numeric(fact: FactDTO, parameter: str) -> str:
    for n in fact.numeric_values:
        if (n.parameter or "").lower() == parameter.lower():
            if n.value_min is not None and n.value_max is not None:
                value = f"{n.value_min:g}–{n.value_max:g}"
            elif n.value is not None:
                value = f"{n.value:g}"
            elif n.value_max is not None:
                value = f"≤{n.value_max:g}"
            elif n.value_min is not None:
                value = f"≥{n.value_min:g}"
            else:
                value = "—"
            return f"{value} {n.unit_normalized or ''}".strip()
    return "—"
