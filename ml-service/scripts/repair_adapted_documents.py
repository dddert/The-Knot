#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def infer_year(source: dict[str, Any]) -> int | None:
    for key in ("year", "publication_year", "date"):
        value = source.get(key)
        if isinstance(value, int) and 1900 <= value <= 2100:
            return value
        if isinstance(value, str):
            match = YEAR_RE.search(value)
            if match:
                return int(match.group(1))

    for key in ("filename", "title"):
        value = source.get(key)
        if isinstance(value, str):
            match = YEAR_RE.search(value)
            if match:
                return int(match.group(1))

    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    docs = 0
    years_filled = 0
    missing_year = 0

    graph_totals = {
        "chunks": 0,
        "entities": 0,
        "relations": 0,
        "facts": 0,
        "numeric_values": 0,
    }

    with input_path.open("r", encoding="utf-8") as src, output_path.open(
        "w", encoding="utf-8"
    ) as dst:
        for line in src:
            if not line.strip():
                continue

            doc = json.loads(line)
            docs += 1

            source = doc.get("source")
            if not isinstance(source, dict):
                source = {}
                doc["source"] = source

            if source.get("year") is None:
                year = infer_year(source)
                if year is not None:
                    source["year"] = year
                    years_filled += 1

            if source.get("year") is None:
                missing_year += 1

            for key in graph_totals:
                value = doc.get(key)
                if isinstance(value, list):
                    graph_totals[key] += len(value)

            dst.write(json.dumps(doc, ensure_ascii=False) + "\n")

    print(f"Documents:       {docs}")
    print(f"Years filled:    {years_filled}")
    print(f"Years missing:   {missing_year}")
    for key, value in graph_totals.items():
        print(f"{key:16} {value}")
    print(f"Output:          {output_path}")


if __name__ == "__main__":
    main()
