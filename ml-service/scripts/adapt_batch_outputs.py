from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open('r', encoding='utf-8') as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f'[WARN] {path.name}:{line_no}: {exc}')
                continue
            if isinstance(item, dict):
                yield item


def stable_id(prefix: str, *parts: Any) -> str:
    raw = ':'.join(str(x) for x in parts)
    return f'{prefix}_{hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:18]}'


def norm(value: str | None) -> str:
    text = (value or '').lower().replace('ё', 'е')
    text = re.sub(r'[^a-zа-я0-9+]+', ' ', text)
    return ' '.join(text.split())


def main() -> None:
    p = argparse.ArgumentParser(description='Adapt chunk-level LLM JSONL to backend ExtractedDocument JSONL.')
    p.add_argument('--extractions', required=True)
    p.add_argument('--chunks', required=True)
    p.add_argument('--numeric-values', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--access-level', default='internal', choices=['public', 'internal', 'confidential'])
    args = p.parse_args()

    extraction_rows = list(read_jsonl(Path(args.extractions)))
    selected_chunk_ids = {str(row.get('chunk_id')) for row in extraction_rows if row.get('chunk_id')}
    chunks = {str(c.get('chunk_id') or c.get('id')): c for c in read_jsonl(Path(args.chunks)) if str(c.get('chunk_id') or c.get('id')) in selected_chunk_ids}

    numeric_by_chunk: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in read_jsonl(Path(args.numeric_values)):
        cid = str(value.get('chunk_id') or '')
        if cid in selected_chunk_ids:
            numeric_by_chunk[cid].append(value)

    rows_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in extraction_rows:
        doc_id = str(row.get('document_id') or '')
        if doc_id:
            rows_by_doc[doc_id].append(row)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with output.open('w', encoding='utf-8') as f:
        for doc_id, rows in rows_by_doc.items():
            doc_chunk_ids = [str(r.get('chunk_id')) for r in rows if r.get('chunk_id')]
            doc_chunks = [chunks[cid] for cid in doc_chunk_ids if cid in chunks]
            first_row = rows[0]
            first_chunk = doc_chunks[0] if doc_chunks else {}

            entity_map: dict[tuple[str, str], dict[str, Any]] = {}
            aliases: dict[str, str] = {}
            for row in rows:
                cid = str(row.get('chunk_id') or '')
                chunk = chunks.get(cid, {})
                for e in row.get('entities') or []:
                    etype = str(e.get('type') or 'Material')
                    canonical = str(e.get('canonical_name') or e.get('name') or '').strip()
                    key = (etype, norm(canonical))
                    if not key[1]:
                        continue
                    eid = stable_id('ent', doc_id, etype, key[1])
                    candidate = {
                        'id': eid,
                        'type': etype,
                        'name': str(e.get('name') or canonical),
                        'canonical_name': canonical,
                        'language': None,
                        'aliases': list(e.get('aliases') or []),
                        'source_chunk_id': cid or None,
                        'page': chunk.get('page_start') if chunk.get('page_start') is not None else row.get('page_start'),
                        'confidence': float(e.get('confidence') or 0.7),
                        'description': e.get('evidence_text'),
                    }
                    if key not in entity_map:
                        entity_map[key] = candidate
                    else:
                        entity_map[key]['confidence'] = max(entity_map[key]['confidence'], candidate['confidence'])
                        entity_map[key]['aliases'] = sorted(set(entity_map[key]['aliases'] + candidate['aliases']))[:20]
                    for name in [candidate['name'], candidate['canonical_name'], *candidate['aliases']]:
                        n = norm(name)
                        if n:
                            aliases.setdefault(n, eid)

            def resolve(name: str | None) -> str | None:
                n = norm(name)
                if not n:
                    return None
                if n in aliases:
                    return aliases[n]
                if len(n) >= 5:
                    matches = {eid for alias, eid in aliases.items() if n in alias or alias in n}
                    if len(matches) == 1:
                        return next(iter(matches))
                return None

            relations = []
            relation_ids = set()
            facts = []
            fact_ids = set()
            warnings = []
            unresolved_relations = 0

            numeric_values = []
            valid_num_ids = set()
            for cid in doc_chunk_ids:
                chunk = chunks.get(cid, {})
                for v in numeric_by_chunk.get(cid, []):
                    vid = str(v.get('id') or '')
                    if not vid or vid in valid_num_ids:
                        continue
                    valid_num_ids.add(vid)
                    numeric_values.append({
                        'id': vid,
                        'parameter': v.get('parameter') or 'numeric_value',
                        'display_name': v.get('display_name') or v.get('parameter'),
                        'value': v.get('value'),
                        'value_min': v.get('value_min'),
                        'value_max': v.get('value_max'),
                        'comparator': v.get('comparator'),
                        'unit_original': v.get('unit_original'),
                        'unit_normalized': v.get('unit_normalized'),
                        'context': v.get('context'),
                        'source_text': v.get('source_text'),
                        'source_chunk_id': cid,
                        'page': chunk.get('page_start'),
                        'confidence': float(v.get('confidence') or 0.6),
                    })

            for row in rows:
                cid = str(row.get('chunk_id') or '')
                chunk = chunks.get(cid, {})
                for rel in row.get('relations') or []:
                    source_id = resolve(rel.get('source_name'))
                    target_id = resolve(rel.get('target_name'))
                    if not source_id or not target_id or source_id == target_id:
                        unresolved_relations += 1
                        continue
                    rid = stable_id('rel', doc_id, rel.get('type'), source_id, target_id, cid)
                    if rid in relation_ids:
                        continue
                    relation_ids.add(rid)
                    relations.append({
                        'id': rid,
                        'type': rel.get('type') or 'RELATED_TO',
                        'source_entity_id': source_id,
                        'target_entity_id': target_id,
                        'source_chunk_id': cid,
                        'evidence_text': rel.get('evidence_text'),
                        'confidence': float(rel.get('confidence') or 0.7),
                    })

                for fact in row.get('facts') or []:
                    fid = str(fact.get('id') or stable_id('fact', doc_id, cid, fact.get('claim_text')))
                    if fid in fact_ids:
                        continue
                    fact_ids.add(fid)
                    geo = fact.get('geo_scope') or 'unknown'
                    if geo == 'global':
                        geo = 'all'
                    if geo not in {'domestic', 'foreign', 'all', 'unknown'}:
                        geo = 'unknown'
                    object_ids = [resolve(str(x)) for x in fact.get('objects') or []]
                    facts.append({
                        'id': fid,
                        'claim_text': fact.get('claim_text') or '',
                        'fact_type': fact.get('fact_type') or 'experimental_result',
                        'subject_entity_id': resolve(fact.get('subject')),
                        'object_entity_ids': sorted({x for x in object_ids if x}),
                        'numeric_value_ids': [x for x in fact.get('numeric_value_ids') or [] if x in valid_num_ids],
                        'source': {
                            'document_id': doc_id,
                            'chunk_id': cid or None,
                            'page': chunk.get('page_start') if chunk else row.get('page_start'),
                            'quote': fact.get('quote'),
                        },
                        'geo_scope': geo,
                        'country': fact.get('country'),
                        'year': fact.get('year'),
                        'confidence': float(fact.get('confidence') or 0.75),
                        'verification_level': fact.get('verification_level') or 'source_supported',
                        'status': fact.get('status') or 'auto_extracted',
                        'updated_at': None,
                    })

            if unresolved_relations:
                warnings.append(f'{unresolved_relations} relations skipped because entity names could not be resolved to IDs.')
            if len(doc_chunks) != len(doc_chunk_ids):
                warnings.append(f'{len(doc_chunk_ids) - len(doc_chunks)} chunks missing from chunks JSONL.')

            filename = first_row.get('filename') or first_chunk.get('filename')
            source_type = first_chunk.get('source_type') or first_row.get('source_type') or 'internal_report'
            extracted = {
                'document_id': doc_id,
                'language': 'ru',
                'status': 'success',
                'source': {
                    'id': f'source_{doc_id}',
                    'document_id': doc_id,
                    'title': filename or doc_id,
                    'filename': filename,
                    'source_type': source_type,
                    'access_level': first_chunk.get('access_level') or args.access_level,
                    'year': first_chunk.get('year'),
                    'country': first_chunk.get('country'),
                    'organization': first_chunk.get('organization'),
                    'authors': first_chunk.get('authors') or [],
                },
                'chunks': [{
                    'id': str(c.get('chunk_id') or c.get('id')),
                    'page': c.get('page_start') if c.get('page_start') is not None else c.get('page'),
                    'text': c.get('text') or '',
                    'embedding_text': c.get('text') or '',
                } for c in doc_chunks],
                'entities': list(entity_map.values()),
                'relations': relations,
                'numeric_values': numeric_values,
                'facts': facts,
                'warnings': warnings,
            }
            f.write(json.dumps(extracted, ensure_ascii=False) + '\n')
            written += 1

    print(f'Documents written: {written}')
    print(f'Output: {output.resolve()}')


if __name__ == '__main__':
    main()
