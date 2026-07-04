from __future__ import annotations

from typing import Any
from fastapi import FastAPI

from app.config import settings
from app.extraction_service import ExtractionService
from app.query_service import QueryService
from app.retrieval import HybridRetriever
from app.schemas import (
    DocumentForExtraction, ExtractedDocument, FinalAnswer, ParseQueryRequest,
    QueryPlan, RetrieveRequest, RetrieveResponse,
)
from app.synthesis_service import SynthesisService

app = FastAPI(title=settings.app_name, version='0.2.0')
extractor = ExtractionService()
query_service = QueryService()
retriever = HybridRetriever()
synthesizer = SynthesisService()


@app.get('/health')
def health() -> dict[str, Any]:
    return {
        'status': 'ok',
        'app': settings.app_name,
        'llm_provider': settings.llm_provider,
        'dense_enabled': settings.dense_enabled,
        'lexical_enabled': settings.lexical_enabled,
        'retrieval_index_dir': settings.retrieval_index_dir,
    }


@app.post('/ml/extract', response_model=ExtractedDocument)
async def extract(document: DocumentForExtraction) -> ExtractedDocument:
    return await extractor.extract(document)


@app.post('/ml/parse-query', response_model=QueryPlan)
async def parse_query(request: ParseQueryRequest) -> QueryPlan:
    return await query_service.parse(request)


@app.post('/ml/retrieve', response_model=RetrieveResponse)
def retrieve(request: RetrieveRequest) -> RetrieveResponse:
    return retriever.search(request)


@app.post('/ml/index-document')
def index_document(document: DocumentForExtraction) -> dict[str, Any]:
    return retriever.index_document(document)


@app.post('/ml/synthesize-answer', response_model=FinalAnswer)
async def synthesize_answer(payload: dict[str, Any]) -> FinalAnswer:
    return await synthesizer.synthesize(payload)
