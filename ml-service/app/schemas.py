from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


EntityType = Literal[
    'Material', 'Process', 'Equipment', 'Property', 'Experiment',
    'Publication', 'Patent', 'Report', 'Expert', 'Laboratory', 'Facility',
    'TechnologySolution', 'Geography', 'EconomicIndicator',
    'EnvironmentalIndicator',
]
FactStatus = Literal['auto_extracted', 'source_supported', 'expert_verified', 'contradicted', 'deprecated']
GeoScope = Literal['domestic', 'foreign', 'all', 'unknown']
AccessLevel = Literal['public', 'internal', 'confidential']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PageText(StrictModel):
    page: int
    text: str


class SourceMetadata(StrictModel):
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    country: str | None = None
    organization: str | None = None


class Source(StrictModel):
    id: str | None = None
    document_id: str | None = None
    title: str | None = None
    filename: str | None = None
    source_type: str = 'internal_report'
    access_level: AccessLevel = 'internal'
    year: int | None = None
    country: str | None = None
    organization: str | None = None
    authors: list[str] = Field(default_factory=list)


class DocumentForExtraction(StrictModel):
    document_id: str
    filename: str
    document_type: str = 'report'
    language_hint: str | None = None
    source_type: str = 'internal_report'
    access_level: AccessLevel = 'internal'
    metadata: SourceMetadata = Field(default_factory=SourceMetadata)
    pages: list[PageText]


class Chunk(StrictModel):
    id: str
    page: int | None = None
    text: str
    embedding_text: str | None = None


class Entity(StrictModel):
    id: str
    type: EntityType
    name: str
    canonical_name: str
    language: str | None = None
    aliases: list[str] = Field(default_factory=list)
    source_chunk_id: str | None = None
    page: int | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    description: str | None = None


class Relation(StrictModel):
    id: str
    type: str
    source_entity_id: str
    target_entity_id: str
    source_chunk_id: str | None = None
    evidence_text: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)


class NumericValue(StrictModel):
    id: str
    parameter: str
    display_name: str | None = None
    value: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    comparator: str | None = None
    unit_original: str | None = None
    unit_normalized: str | None = None
    context: str | None = None
    source_text: str | None = None
    source_chunk_id: str | None = None
    page: int | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)


class FactSource(StrictModel):
    document_id: str
    chunk_id: str | None = None
    page: int | None = None
    quote: str | None = None


class Fact(StrictModel):
    id: str
    claim_text: str
    fact_type: str
    subject_entity_id: str | None = None
    object_entity_ids: list[str] = Field(default_factory=list)
    numeric_value_ids: list[str] = Field(default_factory=list)
    source: FactSource
    geo_scope: GeoScope = 'unknown'
    country: str | None = None
    year: int | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    verification_level: str = 'source_supported'
    status: FactStatus = 'auto_extracted'
    updated_at: str | None = None


class ExtractedDocument(StrictModel):
    document_id: str
    language: str = 'ru'
    status: str = 'success'
    source: Source = Field(default_factory=Source)
    chunks: list[Chunk] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    numeric_values: list[NumericValue] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class NumericConstraint(StrictModel):
    parameter: str
    operator: Literal['between', '<', '<=', '>', '>=', '='] = 'between'
    value: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    unit: str | None = None


class QueryPlan(StrictModel):
    intent: str = 'technology_review'
    materials: list[str] = Field(default_factory=list)
    processes: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    properties: list[str] = Field(default_factory=list)
    geo_scope: GeoScope = 'all'
    countries: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    confidence_min: float = Field(default=0.0, ge=0, le=1)
    status: FactStatus | None = None
    fact_type: str | None = None
    verification_level: str | None = None
    source_type: str | None = None
    numeric_constraints: list[NumericConstraint] = Field(default_factory=list)
    comparison_mode: bool = False
    group_by: list[str] = Field(default_factory=list)


class SearchFilters(StrictModel):
    process: str | None = None
    material: str | None = None
    country: str | None = None
    geo_scope: GeoScope = 'all'
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    confidence_min: float = Field(default=0.0, ge=0, le=1)
    status: FactStatus | None = None
    fact_type: str | None = None
    verification_level: str | None = None
    source_type: str | None = None
    numeric_parameter: str | None = None
    numeric_operator: Literal['between', '<', '<=', '>', '>=', '='] = 'between'
    numeric_min: float | None = None
    numeric_max: float | None = None
    numeric_unit: str | None = None


class ParseQueryRequest(StrictModel):
    query: str
    filters: SearchFilters = Field(default_factory=SearchFilters)


class RetrieveRequest(StrictModel):
    query: str
    query_plan: QueryPlan | None = None
    top_k: int = Field(default=30, ge=1, le=200)
    visible_access_levels: list[AccessLevel] = Field(default_factory=lambda: ['public'])


class RetrievedChunk(StrictModel):
    chunk_id: str
    document_id: str | None = None
    score: float
    dense_score: float | None = None
    lexical_score: float | None = None
    reranker_score: float | None = None
    filename: str | None = None
    source_type: str | None = None
    access_level: AccessLevel = 'internal'
    page_start: int | None = None
    page_end: int | None = None
    text: str


class RetrieveResponse(StrictModel):
    query: str
    expanded_query: str
    hits: list[RetrievedChunk] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FinalAnswerSection(StrictModel):
    title: str
    type: str = 'bullets'
    content: Any = Field(default_factory=list)


class FinalAnswer(StrictModel):
    summary: str
    confidence: float = Field(ge=0, le=1)
    source_count: int
    sections: list[FinalAnswerSection]
    recommendations: list[str] = Field(default_factory=list)
    related_experts: list[dict[str, Any]] = Field(default_factory=list)
    export_payload: dict[str, Any] = Field(default_factory=dict)
