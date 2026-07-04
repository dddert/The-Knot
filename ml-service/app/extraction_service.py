from __future__ import annotations

import asyncio
import hashlib
import re
from collections import defaultdict
from typing import Any

from app.config import settings
from app.schemas import (
    Chunk, DocumentForExtraction, Entity, ExtractedDocument, Fact, FactSource,
    NumericValue, Relation, Source,
)
from pipeline.extract_numeric_values import find_numeric_values
from pipeline.llm_extractor import (
    attach_ids, build_prompt, call_llm, extract_json_from_text, validate_extraction,
)
from pipeline.make_chunks import normalize_text, split_long_text


def stable_id(prefix: str, *parts: Any) -> str:
    raw = ':'.join(str(x) for x in parts)
    return f'{prefix}_{hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:18]}'


def key_name(value: str | None) -> str:
    text = (value or '').lower().replace('ё', 'е')
    text = re.sub(r'[^a-zа-я0-9+]+', ' ', text)
    return ' '.join(text.split())


def build_chunks(document: DocumentForExtraction) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    index = 0
    for page in document.pages:
        for piece in split_long_text(normalize_text(page.text), settings.extraction_target_chars, settings.extraction_overlap_chars):
            if not piece.strip():
                continue
            chunk_id = stable_id('chunk', document.document_id, index, piece[:200])
            chunks.append({
                'chunk_id': chunk_id,
                'document_id': document.document_id,
                'filename': document.filename,
                'source_type': document.source_type,
                'access_level': document.access_level,
                'page_start': page.page,
                'page_end': page.page,
                'text': piece,
            })
            index += 1
    return chunks


class ExtractionService:
    async def extract(self, document: DocumentForExtraction) -> ExtractedDocument:
        raw_chunks = build_chunks(document)
        warnings: list[str] = []
        if len(raw_chunks) > settings.extraction_max_chunks:
            warnings.append(
                f'Document has {len(raw_chunks)} chunks; only first {settings.extraction_max_chunks} processed by LLM in online mode.'
            )
        llm_chunks = raw_chunks[:settings.extraction_max_chunks]

        all_numeric: list[dict[str, Any]] = []
        numeric_by_chunk: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for chunk in raw_chunks:
            values = find_numeric_values(chunk, include_length_units=False)
            for value in values:
                numeric_by_chunk[chunk['chunk_id']].append(value)
                all_numeric.append(value)

        chunk_extractions: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for chunk in llm_chunks:
            chunk['numeric_values'] = numeric_by_chunk.get(chunk['chunk_id'], [])
            try:
                prompt = build_prompt(
                    chunk,
                    max_entities=settings.extraction_max_entities,
                    max_relations=settings.extraction_max_relations,
                    max_facts=settings.extraction_max_facts,
                    max_numeric_values=settings.extraction_max_numeric_values,
                )
                response_text, _model = await asyncio.to_thread(
                    call_llm,
                    provider=settings.llm_provider,
                    prompt=prompt,
                    temperature=settings.extraction_temperature,
                    max_tokens=settings.extraction_max_tokens,
                    timeout=settings.llm_timeout_seconds,
                    json_mode=True,
                )
                parsed = extract_json_from_text(response_text)
                extraction = validate_extraction(
                    parsed,
                    max_entities=settings.extraction_max_entities,
                    max_relations=settings.extraction_max_relations,
                    max_facts=settings.extraction_max_facts,
                )
                extraction = attach_ids(chunk, extraction)
                chunk_extractions.append((chunk, extraction))
            except Exception as exc:
                warnings.append(f"{chunk['chunk_id']}: {type(exc).__name__}: {str(exc)[:300]}")

        # Document-level entity normalization and ID resolution.
        entities_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        alias_to_id: dict[str, str] = {}
        for chunk, extraction in chunk_extractions:
            for entity in extraction['entities']:
                canonical = entity.get('canonical_name') or entity.get('name') or ''
                ekey = (entity.get('type') or 'Material', key_name(canonical))
                if not ekey[1]:
                    continue
                doc_entity_id = stable_id('ent', document.document_id, *ekey)
                existing = entities_by_key.get(ekey)
                candidate = {
                    'id': doc_entity_id,
                    'type': ekey[0],
                    'name': entity.get('name') or canonical,
                    'canonical_name': canonical,
                    'language': document.language_hint,
                    'aliases': list(entity.get('aliases') or []),
                    'source_chunk_id': chunk['chunk_id'],
                    'page': chunk.get('page_start'),
                    'confidence': float(entity.get('confidence') or 0.7),
                    'description': entity.get('evidence_text'),
                }
                if existing:
                    existing['confidence'] = max(existing['confidence'], candidate['confidence'])
                    existing['aliases'] = sorted(set(existing['aliases'] + candidate['aliases']))[:20]
                else:
                    entities_by_key[ekey] = candidate
                for alias in [candidate['name'], candidate['canonical_name'], *candidate['aliases']]:
                    normalized = key_name(alias)
                    if normalized:
                        alias_to_id.setdefault(normalized, doc_entity_id)

        def resolve(name: str | None) -> str | None:
            normalized = key_name(name)
            if not normalized:
                return None
            if normalized in alias_to_id:
                return alias_to_id[normalized]
            # conservative fuzzy fallback by containment for longer terms
            if len(normalized) >= 5:
                matches = {eid for alias, eid in alias_to_id.items() if normalized in alias or alias in normalized}
                if len(matches) == 1:
                    return next(iter(matches))
            return None

        relations: list[Relation] = []
        facts: list[Fact] = []
        seen_rel: set[str] = set()
        seen_fact: set[str] = set()
        valid_numeric_ids = {v['id'] for v in all_numeric}

        for chunk, extraction in chunk_extractions:
            for rel in extraction['relations']:
                source_id = resolve(rel.get('source_name'))
                target_id = resolve(rel.get('target_name'))
                if not source_id or not target_id or source_id == target_id:
                    continue
                rid = stable_id('rel', document.document_id, rel.get('type'), source_id, target_id, chunk['chunk_id'])
                if rid in seen_rel:
                    continue
                seen_rel.add(rid)
                relations.append(Relation(
                    id=rid,
                    type=rel.get('type') or 'RELATED_TO',
                    source_entity_id=source_id,
                    target_entity_id=target_id,
                    source_chunk_id=chunk['chunk_id'],
                    evidence_text=rel.get('evidence_text'),
                    confidence=float(rel.get('confidence') or 0.7),
                ))

            for fact in extraction['facts']:
                fid = fact.get('id') or stable_id('fact', document.document_id, chunk['chunk_id'], fact.get('claim_text'))
                if fid in seen_fact:
                    continue
                seen_fact.add(fid)
                geo_scope = fact.get('geo_scope') or 'unknown'
                if geo_scope == 'global':
                    geo_scope = 'all'
                if geo_scope not in {'domestic', 'foreign', 'all', 'unknown'}:
                    geo_scope = 'unknown'
                subject_id = resolve(fact.get('subject'))
                object_ids = [resolve(str(x)) for x in fact.get('objects') or []]
                numeric_ids = [x for x in fact.get('numeric_value_ids') or [] if x in valid_numeric_ids]
                facts.append(Fact(
                    id=fid,
                    claim_text=fact.get('claim_text') or '',
                    fact_type=fact.get('fact_type') or 'experimental_result',
                    subject_entity_id=subject_id,
                    object_entity_ids=sorted({x for x in object_ids if x}),
                    numeric_value_ids=sorted(set(numeric_ids)),
                    source=FactSource(
                        document_id=document.document_id,
                        chunk_id=chunk['chunk_id'],
                        page=chunk.get('page_start'),
                        quote=fact.get('quote'),
                    ),
                    geo_scope=geo_scope,
                    country=fact.get('country'),
                    year=fact.get('year') or document.metadata.year,
                    confidence=float(fact.get('confidence') or 0.75),
                    verification_level='source_supported',
                    status='auto_extracted',
                ))

        chunks = [Chunk(id=c['chunk_id'], page=c.get('page_start'), text=c['text'], embedding_text=c['text']) for c in raw_chunks]
        numeric_values = [NumericValue(
            id=v['id'], parameter=v.get('parameter') or 'numeric_value', display_name=v.get('display_name'),
            value=v.get('value'), value_min=v.get('value_min'), value_max=v.get('value_max'),
            comparator=v.get('comparator'), unit_original=v.get('unit_original'), unit_normalized=v.get('unit_normalized'),
            context=v.get('context'), source_text=v.get('source_text'), source_chunk_id=v.get('chunk_id'),
            page=v.get('page_start'), confidence=float(v.get('confidence') or 0.6),
        ) for v in all_numeric]
        source = Source(
            id=f'source_{document.document_id}', document_id=document.document_id,
            title=document.metadata.title or document.filename, filename=document.filename,
            source_type=document.source_type, access_level=document.access_level,
            year=document.metadata.year, country=document.metadata.country,
            organization=document.metadata.organization, authors=document.metadata.authors,
        )
        return ExtractedDocument(
            document_id=document.document_id,
            language=document.language_hint or 'ru',
            status='success' if facts or not self._llm_configured() else 'partial',
            source=source,
            chunks=chunks,
            entities=[Entity.model_validate(v) for v in entities_by_key.values()],
            relations=relations,
            numeric_values=numeric_values,
            facts=facts,
            warnings=warnings,
        )

    @staticmethod
    def _llm_configured() -> bool:
        if settings.llm_provider == 'yandex':
            return bool(settings.yandex_api_key and settings.yandex_folder_id)
        return bool(settings.openai_compatible_base_url)
