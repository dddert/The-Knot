from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class EntityDTO(BaseModel):
    id: str
    type: str = "Entity"
    name: str | None = None
    canonical_name: str | None = None
    language: str | None = None
    aliases: list[str] = Field(default_factory=list)
    page: int | None = None
    confidence: float | None = None
    description: str | None = None


class NumericValueDTO(BaseModel):
    id: str
    parameter: str | None = None
    display_name: str | None = None
    value: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    comparator: str | None = None
    unit_original: str | None = None
    unit_normalized: str | None = None
    context: str | None = None
    source_text: str | None = None
    page: int | None = None
    confidence: float | None = None


class SourceDTO(BaseModel):
    id: str | None = None
    document_id: str | None = None
    title: str | None = None
    filename: str | None = None
    source_type: str | None = None
    access_level: str | None = None
    year: int | None = None
    country: str | None = None
    organization: str | None = None
    authors: list[str] = Field(default_factory=list)
    page: int | None = None
    quote: str | None = None


class FactDTO(BaseModel):
    id: str
    claim_text: str | None = None
    fact_type: str | None = None
    geo_scope: str | None = None
    country: str | None = None
    year: int | None = None
    confidence: float | None = None
    verification_level: str | None = None
    status: str | None = None
    updated_at: str | None = None
    entities: list[EntityDTO] = Field(default_factory=list)
    numeric_values: list[NumericValueDTO] = Field(default_factory=list)
    source: SourceDTO | None = None

    # UI compatibility fields, kept intentionally flat for Streamlit tables.
    source_id: str | None = None
    source_title: str | None = None
    source_page: int | None = None
    source_quote: str | None = None


class GraphNodeDTO(BaseModel):
    id: str
    label: str
    labels: list[str] = Field(default_factory=list)
    title: str | None = None
    status: str | None = None
    confidence: float | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdgeDTO(BaseModel):
    id: str
    source: str | None = None
    target: str | None = None
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphDTO(BaseModel):
    nodes: list[GraphNodeDTO] = Field(default_factory=list)
    edges: list[GraphEdgeDTO] = Field(default_factory=list)


class SearchDebugDTO(BaseModel):
    cypher: str
    params: dict[str, Any]


class SearchResultDTO(BaseModel):
    facts: list[FactDTO]
    sources: list[SourceDTO]
    graph: GraphDTO | None = None
    debug: SearchDebugDTO | None = None


class SearchResponseDTO(BaseModel):
    query: str
    query_plan: dict[str, Any]
    facts: list[FactDTO]
    sources: list[SourceDTO]
    answer: dict[str, Any]
    retrieved_evidence: list[dict[str, Any]] = Field(default_factory=list)
    graph: GraphDTO | None = None
    debug: SearchDebugDTO | None = None
