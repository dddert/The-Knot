#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                yield item


def collect_chunk_ids(results_dir: Path, top_per_query: int) -> set[str]:
    chunk_ids: set[str] = set()

    for path in sorted(results_dir.glob("*.full.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        evidence = payload.get("retrieved_evidence") or []
        if not isinstance(evidence, list):
            continue

        for item in evidence[:top_per_query]:
            if not isinstance(item, dict):
                continue
            chunk_id = item.get("chunk_id")
            if chunk_id:
                chunk_ids.add(str(chunk_id))

    return chunk_ids


def main() -> None:
    p = argparse.ArgumentParser(
        description="Select high-value chunks from saved regression/demo search results."
    )
    p.add_argument("chunks_jsonl")
    p.add_argument("results_dir")
    p.add_argument("--output", default="selected_demo_chunks.jsonl")
    p.add_argument("--top-per-query", type=int, default=30)
    args = p.parse_args()

    chunks_path = Path(args.chunks_jsonl)
    results_dir = Path(args.results_dir)
    output_path = Path(args.output)

    wanted = collect_chunk_ids(
        results_dir,
        args.top_per_query,
    )

    selected: list[dict[str, Any]] = []

    for item in iter_jsonl(chunks_path):
        chunk_id = item.get("chunk_id") or item.get("id")
        if chunk_id and str(chunk_id) in wanted:
            selected.append(item)

    with output_path.open("w", encoding="utf-8") as out:
        for item in selected:
            out.write(
                json.dumps(item, ensure_ascii=False)
                + "\n"
            )

    print(
        json.dumps(
            {
                "wanted_chunk_ids": len(wanted),
                "selected_chunks": len(selected),
                "output": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
