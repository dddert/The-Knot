from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from tqdm import tqdm


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open('r', encoding='utf-8') as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f'[WARN] bad JSON line {line_no}: {exc}')
                continue
            text = str(item.get('text') or '').strip()
            chunk_id = item.get('chunk_id') or item.get('id')
            if text and chunk_id:
                yield item


def read_raw_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open('r', encoding='utf-8') as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f'[WARN] bad JSON line {line_no}: {exc}')
                continue
            if isinstance(item, dict):
                yield item


def count_items(path: Path) -> int:
    return sum(1 for _ in read_jsonl(path))


def _coerce_year(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        year = int(value)
        return year if 1900 <= year <= 2100 else None

    text = str(value).strip()
    if not text:
        return None

    match = re.search(r"(?<!\d)(19\d{2}|20\d{2}|2100)(?!\d)", text)
    if not match:
        return None

    year = int(match.group(1))
    return year if 1900 <= year <= 2100 else None


def extract_year(item: dict[str, Any]) -> int | None:
    """Best-effort publication/document year extraction.

    Priority:
    1) explicit top-level year fields;
    2) document/extra metadata;
    3) filename.
    """
    direct_keys = (
        "year",
        "publication_year",
        "document_year",
        "source_year",
    )
    for key in direct_keys:
        year = _coerce_year(item.get(key))
        if year is not None:
            return year

    metadata_candidates = [
        item.get("document_metadata"),
        item.get("extra_metadata"),
        item.get("metadata"),
    ]

    metadata_keys = (
        "year",
        "publication_year",
        "document_year",
        "date",
        "publication_date",
        "created",
        "creation_date",
        "modified",
        "modification_date",
        "CreationDate",
        "ModDate",
    )

    for metadata in metadata_candidates:
        if not isinstance(metadata, dict):
            continue

        for key in metadata_keys:
            year = _coerce_year(metadata.get(key))
            if year is not None:
                return year

    return _coerce_year(item.get("filename"))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('chunks_path')
    p.add_argument('--index-dir', default='retrieval-index')
    p.add_argument('--model', default='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
    p.add_argument('--batch-size', type=int, default=64)
    p.add_argument('--device', default='auto', help='auto|cpu|cuda|mps')
    p.add_argument('--max-seq-length', type=int, default=768)
    p.add_argument('--query-prefix', default='')
    p.add_argument('--passage-prefix', default='')
    p.add_argument('--no-dense', action='store_true')
    p.add_argument('--numeric-values-path', default=None, help='Optional numeric_values.jsonl for numeric constraints')
    args = p.parse_args()

    chunks_path = Path(args.chunks_path)
    out = Path(args.index_dir)
    out.mkdir(parents=True, exist_ok=True)
    total = count_items(chunks_path)
    if total == 0:
        raise RuntimeError('No valid chunks found')
    print(f'Valid chunks: {total}')

    db_path = out / 'lexical.sqlite3'
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, document_id UNINDEXED, filename UNINDEXED, source_type UNINDEXED, access_level UNINDEXED, page_start UNINDEXED, page_end UNINDEXED, text, tokenize='unicode61')")
    conn.execute("CREATE TABLE numeric_values (chunk_id TEXT NOT NULL, parameter TEXT, value REAL, value_min REAL, value_max REAL, comparator TEXT, unit TEXT)")
    conn.execute("CREATE INDEX idx_numeric_parameter ON numeric_values(parameter)")
    conn.execute("CREATE INDEX idx_numeric_chunk ON numeric_values(chunk_id)")

    metadata_path = out / 'metadata.jsonl'
    encoder = None
    embeddings = None
    dim = None
    if not args.no_dense:
        from sentence_transformers import SentenceTransformer
        device = None if args.device == 'auto' else args.device
        encoder = SentenceTransformer(args.model, device=device)
        if args.max_seq_length and args.max_seq_length > 0:
            encoder.max_seq_length = int(args.max_seq_length)
        print(
            f'Embedding model: {args.model} | '
            f'device={encoder.device} | '
            f'max_seq_length={encoder.max_seq_length} | '
            f'batch_size={args.batch_size}'
        )

    pending_meta: list[dict[str, Any]] = []
    pending_texts: list[str] = []
    position = 0

    def flush() -> None:
        nonlocal position, embeddings, dim
        if not pending_meta:
            return
        conn.executemany(
            'INSERT INTO chunks_fts(rowid, chunk_id, document_id, filename, source_type, access_level, page_start, page_end, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [(
                position - len(pending_meta) + i + 1,
                m['chunk_id'], m.get('document_id'), m.get('filename'), m.get('source_type'),
                m.get('access_level') or 'internal', m.get('page_start'), m.get('page_end'), m['text'],
            ) for i, m in enumerate(pending_meta)],
        )
        if encoder is not None:
            vectors = encoder.encode(
                [args.passage_prefix + text for text in pending_texts],
                batch_size=args.batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            ).astype('float32')
            if embeddings is None:
                dim = int(vectors.shape[1])
                embeddings = np.memmap(out / 'embeddings.f32', dtype='float32', mode='w+', shape=(total, dim))
            embeddings[position - len(pending_meta):position] = vectors
        pending_meta.clear()
        pending_texts.clear()

    with metadata_path.open('w', encoding='utf-8') as meta_file:
        for item in tqdm(read_jsonl(chunks_path), total=total, desc='Indexing chunks'):
            meta = {
                'chunk_id': item.get('chunk_id') or item.get('id'),
                'document_id': item.get('document_id'),
                'filename': item.get('filename'),
                'source_type': item.get('source_type'),
                'access_level': item.get('access_level') or 'internal',
                'year': extract_year(item),
                'page_start': item.get('page_start') if item.get('page_start') is not None else item.get('page'),
                'page_end': item.get('page_end') if item.get('page_end') is not None else item.get('page'),
                'text': str(item.get('text') or ''),
            }
            meta_file.write(json.dumps(meta, ensure_ascii=False) + '\n')
            pending_meta.append(meta)
            pending_texts.append(meta['text'])
            position += 1
            if len(pending_meta) >= args.batch_size:
                flush()
        flush()
    if args.numeric_values_path:
        numeric_path = Path(args.numeric_values_path)
        batch = []
        for value in tqdm(read_raw_jsonl(numeric_path), desc='Indexing numeric values'):
            chunk_id = value.get('chunk_id')
            if not chunk_id:
                continue
            batch.append((
                chunk_id, value.get('parameter'), value.get('value'), value.get('value_min'),
                value.get('value_max'), value.get('comparator'), value.get('unit_normalized') or value.get('unit'),
            ))
            if len(batch) >= 5000:
                conn.executemany('INSERT INTO numeric_values(chunk_id, parameter, value, value_min, value_max, comparator, unit) VALUES (?, ?, ?, ?, ?, ?, ?)', batch)
                batch.clear()
        if batch:
            conn.executemany('INSERT INTO numeric_values(chunk_id, parameter, value, value_min, value_max, comparator, unit) VALUES (?, ?, ?, ?, ?, ?, ?)', batch)

    conn.commit()
    conn.close()

    if embeddings is not None and dim is not None:
        embeddings.flush()
        import faiss
        index = faiss.IndexFlatIP(dim)
        mm = np.memmap(out / 'embeddings.f32', dtype='float32', mode='r', shape=(total, dim))
        add_batch = 10000
        for start in tqdm(range(0, total, add_batch), desc='Building FAISS'):
            index.add(np.asarray(mm[start:start + add_batch], dtype='float32'))
        faiss.write_index(index, str(out / 'dense.faiss'))

    config = {
        'count': total,
        'embedding_model': None if args.no_dense else args.model,
        'dimension': dim,
        'max_seq_length': None if args.no_dense else args.max_seq_length,
        'query_prefix': args.query_prefix,
        'passage_prefix': args.passage_prefix,
        'dense': not args.no_dense,
        'lexical': True,
        'numeric': bool(args.numeric_values_path),
    }
    (out / 'index_config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
