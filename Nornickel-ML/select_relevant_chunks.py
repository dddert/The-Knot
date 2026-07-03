from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from tqdm import tqdm


DOMAIN_RULES = {
    "water_desalination": {
        "keywords": [
            "обессоливание", "деминерализация", "водоподготовка",
            "шахтные воды", "рудничные воды", "сточные воды",
            "сульфаты", "сульфат", "so4",
            "хлориды", "хлорид", "cl-",
            "кальций", "ca", "магний", "mg", "натрий", "na",
            "сухой остаток", "минерализация",
            "обратный осмос", "ионный обмен", "электродиализ",
            "reverse osmosis", "ion exchange", "electrodialysis",
            "desalination", "demineralization", "tds"
        ],
        "numeric_parameters": [
            "sulfate_concentration",
            "chloride_concentration",
            "calcium_concentration",
            "magnesium_concentration",
            "sodium_concentration",
            "dry_residue",
            "concentration",
            "ph"
        ],
        "weight": 1.2,
    },

    "nickel_electrowinning": {
        "keywords": [
            "электроэкстракция", "электроэкстракции",
            "electrowinning", "electroextraction",
            "католит", "catholyte", "катодный электролит",
            "никель", "nickel", "ni",
            "хлорное выщелачивание", "хлоридное выщелачивание",
            "рафинирование никеля", "электролит",
            "циркуляция", "скорость потока", "расход",
            "ванна электроэкстракции", "диафрагменная ячейка",
            "chlorine leaching", "chloride leaching"
        ],
        "numeric_parameters": [
            "flow_velocity",
            "flow_rate",
            "temperature",
            "concentration",
            "ph",
            "pressure",
            "productivity"
        ],
        "weight": 1.2,
    },

    "pgm_matte_slag_distribution": {
        "keywords": [
            "au", "ag", "pt", "pd", "rh", "ru", "ir",
            "золото", "серебро", "платина", "палладий",
            "мпг", "металлы платиновой группы",
            "pgm", "platinum group metals",
            "драгоценные металлы", "дм",
            "штейн", "шлака", "шлак", "matte", "slag",
            "медный штейн", "никелевый штейн", "медно-никелевый штейн",
            "коэффициент распределения",
            "распределение между штейном и шлаком",
            "distribution coefficient",
            "matte-slag distribution",
            "partition coefficient",
            "partitioning between matte and slag",
            "плавка", "конвертирование", "smelting"
        ],
        "numeric_parameters": [
            "content",
            "share_percent",
            "extraction_recovery",
            "temperature",
            "pressure",
            "concentration"
        ],
        "weight": 1.3,
    },

    "mine_water_injection": {
        "keywords": [
            "шахтные воды", "рудничные воды", "карьерные воды",
            "закачка", "нагнетание", "инъекция",
            "глубокие горизонты", "подземные горизонты",
            "поглощающая скважина", "нагнетательная скважина",
            "водоотлив", "дренажные воды",
            "mine water", "injection", "deep horizon",
            "deep well", "reinjection", "underground injection",
            "технико-экономические показатели",
            "капитальные затраты", "операционные затраты",
            "opex", "capex"
        ],
        "numeric_parameters": [
            "flow_rate",
            "productivity",
            "pressure",
            "capex",
            "opex",
            "economic_indicator",
            "economic_effect",
            "concentration",
            "ph"
        ],
        "weight": 1.1,
    },
}


GENERAL_IMPORTANT_KEYWORDS = [
    "эксперимент", "экспериментальные данные", "лаборатор",
    "опыт", "испытания", "результаты", "показано",
    "рекоменд", "вывод", "ограничение",
    "технология", "техническое решение",
    "параметр", "условия", "режим",
    "патент", "обзор", "публикация",
    "experiment", "experimental", "results",
    "recommendation", "review", "technology", "condition"
]


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[WARN] Bad JSON at line {line_number}: {e}")


def write_jsonl(path: Path, item: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def normalize_for_search(text: str) -> str:
    text = text.lower()
    text = text.replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def keyword_score(text: str, keywords: List[str]) -> Tuple[float, List[str]]:
    text_norm = normalize_for_search(text)

    matched = []
    score = 0.0

    for keyword in keywords:
        kw = normalize_for_search(keyword)

        if not kw:
            continue

        # Короткие химические символы ищем только как отдельные токены,
        # иначе au/ag/pt/ru/ir ловятся внутри обычных английских слов.
        if len(kw) <= 3 and re.fullmatch(r"[a-zа-я0-9+-]+", kw):
            pattern = rf"(?<![a-zа-я0-9]){re.escape(kw)}(?![a-zа-я0-9])"
            found = re.search(pattern, text_norm, flags=re.IGNORECASE) is not None
        else:
            found = kw in text_norm

        if found:
            matched.append(keyword)

            if len(kw) >= 12:
                score += 2.0
            elif len(kw) >= 5:
                score += 1.0
            else:
                score += 0.3

    return score, matched


def load_numeric_values(path: Path, max_values_per_chunk: int = 20) -> Dict[str, List[Dict[str, Any]]]:
    by_chunk = defaultdict(list)

    for value in tqdm(read_jsonl(path), desc="Loading numeric values"):
        chunk_id = value.get("chunk_id")
        if not chunk_id:
            continue

        if len(by_chunk[chunk_id]) >= max_values_per_chunk:
            continue

        compact_value = {
            "id": value.get("id"),
            "parameter": value.get("parameter"),
            "value": value.get("value"),
            "value_min": value.get("value_min"),
            "value_max": value.get("value_max"),
            "comparator": value.get("comparator"),
            "unit_normalized": value.get("unit_normalized"),
            "source_text": value.get("source_text"),
            "confidence": value.get("confidence"),
        }

        by_chunk[chunk_id].append(compact_value)

    return dict(by_chunk)


def numeric_score(
    numeric_values: List[Dict[str, Any]],
    required_parameters: List[str],
) -> Tuple[float, List[str]]:
    if not numeric_values:
        return 0.0, []

    required = set(required_parameters)
    matched_params = []

    score = 0.0

    for value in numeric_values:
        parameter = value.get("parameter")
        unit = value.get("unit_normalized")
        confidence = value.get("confidence") or 0

        if parameter in required:
            matched_params.append(parameter)
            score += 1.5 * float(confidence)

        # Любое нормальное число тоже немного полезно
        if unit in {"mg/L", "g/L", "g/t", "kg/t", "%", "degC", "m/s", "m3/h", "m3/day", "t/day", "t/year", "pH", "RUB/m3", "million_RUB"}:
            score += 0.3

    return score, sorted(set(matched_params))


def score_chunk(
    chunk: Dict[str, Any],
    numeric_values: List[Dict[str, Any]],
) -> Dict[str, Any]:
    text = chunk.get("text") or ""

    domain_scores = {}
    all_matched_keywords = {}
    all_matched_numeric_parameters = {}

    general_score, general_matches = keyword_score(text, GENERAL_IMPORTANT_KEYWORDS)

    best_domain = None
    best_score = 0.0

    for domain, rules in DOMAIN_RULES.items():
        kw_score, matched_keywords = keyword_score(text, rules["keywords"])
        num_score, matched_params = numeric_score(numeric_values, rules["numeric_parameters"])

        score = (kw_score + num_score + general_score * 0.4) * rules.get("weight", 1.0)

        domain_scores[domain] = round(score, 3)
        all_matched_keywords[domain] = matched_keywords[:20]
        all_matched_numeric_parameters[domain] = matched_params

        if score > best_score:
            best_score = score
            best_domain = domain

    # Бонус за документы с более осмысленным типом
    source_type = chunk.get("source_type")
    if source_type in {"article", "review", "journal", "conference_material"}:
        best_score += 0.5

    # Бонус за наличие чисел
    if numeric_values:
        best_score += 0.5

    return {
        "best_domain": best_domain,
        "score": round(best_score, 3),
        "domain_scores": domain_scores,
        "matched_keywords": all_matched_keywords,
        "matched_numeric_parameters": all_matched_numeric_parameters,
        "general_matches": general_matches[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select relevant chunks for LLM extraction."
    )

    parser.add_argument(
        "chunks_path",
        type=str,
        help="Path to chunks.jsonl",
    )

    parser.add_argument(
        "numeric_values_path",
        type=str,
        help="Path to numeric_values_v2.jsonl",
    )

    parser.add_argument(
        "--output",
        type=str,
        default="llm_candidate_chunks.jsonl",
        help="Path to output candidate chunks",
    )

    parser.add_argument(
        "--stats",
        type=str,
        default="llm_candidate_stats.json",
        help="Path to output stats JSON",
    )

    parser.add_argument(
        "--min-score",
        type=float,
        default=3.0,
        help="Minimum score to keep a chunk",
    )

    parser.add_argument(
        "--max-per-domain",
        type=int,
        default=3000,
        help="Maximum chunks per domain",
    )

    parser.add_argument(
        "--max-values-per-chunk",
        type=int,
        default=20,
        help="Maximum numeric values attached to one chunk",
    )

    args = parser.parse_args()

    chunks_path = Path(args.chunks_path)
    numeric_values_path = Path(args.numeric_values_path)
    output_path = Path(args.output)
    stats_path = Path(args.stats)

    if output_path.exists():
        output_path.unlink()

    numeric_by_chunk = load_numeric_values(
        numeric_values_path,
        max_values_per_chunk=args.max_values_per_chunk,
    )

    candidates_by_domain = defaultdict(list)

    stats = {
        "chunks_total": 0,
        "chunks_scored_above_threshold": 0,
        "chunks_selected_total": 0,
        "min_score": args.min_score,
        "max_per_domain": args.max_per_domain,
        "by_domain_before_limit": {},
        "by_domain_after_limit": {},
    }

    for chunk in tqdm(read_jsonl(chunks_path), desc="Scoring chunks"):
        stats["chunks_total"] += 1

        chunk_id = chunk.get("chunk_id")
        numeric_values = numeric_by_chunk.get(chunk_id, [])

        scoring = score_chunk(chunk, numeric_values)

        if scoring["score"] < args.min_score:
            continue

        domain = scoring["best_domain"] or "unknown"

        item = {
            "chunk_id": chunk.get("chunk_id"),
            "document_id": chunk.get("document_id"),
            "filename": chunk.get("filename"),
            "extension": chunk.get("extension"),
            "source_path": chunk.get("source_path"),
            "relative_path": chunk.get("relative_path"),
            "source_type": chunk.get("source_type"),
            "content_type": chunk.get("content_type"),
            "section_type": chunk.get("section_type"),

            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "slide_start": chunk.get("slide_start"),
            "slide_end": chunk.get("slide_end"),
            "sheet_name": chunk.get("sheet_name"),
            "row_start": chunk.get("row_start"),
            "row_end": chunk.get("row_end"),

            "text": chunk.get("text"),
            "text_length": chunk.get("text_length"),
            "text_hash": chunk.get("text_hash"),

            "llm_domain": domain,
            "llm_score": scoring["score"],
            "domain_scores": scoring["domain_scores"],
            "matched_keywords": scoring["matched_keywords"],
            "matched_numeric_parameters": scoring["matched_numeric_parameters"],
            "general_matches": scoring["general_matches"],

            "numeric_values": numeric_values,
        }

        candidates_by_domain[domain].append(item)
        stats["chunks_scored_above_threshold"] += 1

    selected = []

    for domain, items in candidates_by_domain.items():
        stats["by_domain_before_limit"][domain] = len(items)

        items_sorted = sorted(
            items,
            key=lambda x: x["llm_score"],
            reverse=True,
        )

        limited = items_sorted[:args.max_per_domain]
        stats["by_domain_after_limit"][domain] = len(limited)

        selected.extend(limited)

    # Дедупликация на случай пересечений
    seen_chunk_ids = set()
    selected_unique = []

    for item in sorted(selected, key=lambda x: x["llm_score"], reverse=True):
        chunk_id = item["chunk_id"]

        if chunk_id in seen_chunk_ids:
            continue

        seen_chunk_ids.add(chunk_id)
        selected_unique.append(item)

    for item in selected_unique:
        write_jsonl(output_path, item)

    stats["chunks_selected_total"] = len(selected_unique)

    with stats_path.open("w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print("\nDone.")
    print(f"Candidate chunks: {output_path.resolve()}")
    print(f"Stats:            {stats_path.resolve()}")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()