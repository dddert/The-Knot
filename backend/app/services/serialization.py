from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Convert Neo4j/Python driver values into plain JSON-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    # neo4j.time.DateTime/Date/Time expose iso_format in neo4j 5.x
    iso_format = getattr(value, "iso_format", None)
    if callable(iso_format):
        try:
            return iso_format()
        except Exception:
            pass
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_jsonable(v) for v in value]
    # Neo4j spatial or temporal fallback
    if hasattr(value, "__dict__") and value.__class__.__module__.startswith("neo4j"):
        return str(value)
    return value


def clean_props(props: Mapping[str, Any] | None) -> dict[str, Any]:
    if not props:
        return {}
    return {str(k): to_jsonable(v) for k, v in dict(props).items()}


def node_to_dict(node: Any) -> dict[str, Any]:
    props = clean_props(dict(node))
    labels = list(getattr(node, "labels", []) or [])
    label = labels[0] if labels else props.get("label", "Node")
    node_id = props.get("id") or str(getattr(node, "element_id", ""))
    title = (
        props.get("claim_text")
        or props.get("canonical_name")
        or props.get("title")
        or props.get("name")
        or node_id
    )
    return {
        "id": node_id,
        "label": label,
        "labels": labels,
        "title": title,
        "status": props.get("status"),
        "confidence": props.get("confidence"),
        "properties": props,
    }


def relationship_to_dict(rel: Any) -> dict[str, Any]:
    props = clean_props(dict(rel))
    return {
        "id": props.get("id") or str(getattr(rel, "element_id", "")),
        "source": rel.start_node.get("id") if getattr(rel, "start_node", None) is not None else None,
        "target": rel.end_node.get("id") if getattr(rel, "end_node", None) is not None else None,
        "label": getattr(rel, "type", props.get("type", "RELATED_TO")),
        "properties": props,
    }
