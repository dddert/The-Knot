from __future__ import annotations
from typing import Any
from app.schemas.dto import EntityDTO, FactDTO, NumericValueDTO, SourceDTO
from app.services.serialization import clean_props, to_jsonable


def map_entity(props: dict[str, Any]) -> EntityDTO:
    props = clean_props(props)
    return EntityDTO(
        id=props.get("id", ""),
        type=props.get("entity_type") or props.get("type") or props.get("label") or "Entity",
        name=props.get("name"),
        canonical_name=props.get("canonical_name"),
        language=props.get("language"),
        aliases=props.get("aliases") or [],
        page=props.get("page"),
        confidence=props.get("confidence"),
        description=props.get("description"),
    )


def map_numeric(props: dict[str, Any]) -> NumericValueDTO:
    props = clean_props(props)
    return NumericValueDTO(
        id=props.get("id", ""),
        parameter=props.get("parameter"),
        display_name=props.get("display_name"),
        value=props.get("value"),
        value_min=props.get("value_min"),
        value_max=props.get("value_max"),
        comparator=props.get("comparator"),
        unit_original=props.get("unit_original"),
        unit_normalized=props.get("unit_normalized"),
        context=props.get("context"),
        source_text=props.get("source_text"),
        page=props.get("page"),
        confidence=props.get("confidence"),
    )


def map_source(props: dict[str, Any] | None, rel_props: dict[str, Any] | None = None) -> SourceDTO | None:
    if not props:
        return None
    props = clean_props(props)
    rel_props = clean_props(rel_props)
    return SourceDTO(
        id=props.get("id"),
        document_id=props.get("document_id"),
        title=props.get("title"),
        filename=props.get("filename"),
        source_type=props.get("source_type"),
        access_level=props.get("access_level"),
        year=props.get("year"),
        country=props.get("country"),
        organization=props.get("organization"),
        authors=props.get("authors") or [],
        page=rel_props.get("page"),
        quote=rel_props.get("quote"),
    )


def map_fact(
    fact_props: dict[str, Any],
    entities: list[dict[str, Any]] | None = None,
    numeric_values: list[dict[str, Any]] | None = None,
    source_props: dict[str, Any] | None = None,
    source_rel_props: dict[str, Any] | None = None,
) -> FactDTO:
    f = clean_props(fact_props)
    source = map_source(source_props, source_rel_props)
    return FactDTO(
        id=f.get("id", ""),
        claim_text=f.get("claim_text"),
        fact_type=f.get("fact_type"),
        geo_scope=f.get("geo_scope"),
        country=f.get("country"),
        year=f.get("year"),
        confidence=f.get("confidence"),
        verification_level=f.get("verification_level"),
        status=f.get("status"),
        updated_at=to_jsonable(f.get("updated_at")),
        entities=[map_entity(e) for e in (entities or []) if e],
        numeric_values=[map_numeric(n) for n in (numeric_values or []) if n],
        source=source,
        source_id=source.id if source else None,
        source_title=source.title if source else None,
        source_page=source.page if source else None,
        source_quote=source.quote if source else None,
    )
