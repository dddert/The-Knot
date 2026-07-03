from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tqdm import tqdm


BASE_UNITS = [
    # concentrations
    "мг/дм³", "мг/дм3", "мг / дм³", "мг / дм3",
    "мг/л", "мг / л", "mg/L", "mg/l",
    "г/л", "г / л", "g/L", "g/l",
    "мкг/л", "мкг / л", "µg/l", "μg/l", "ug/l",
    "ppm",

    # metallurgy / assays
    "г/т", "г / т", "g/t",
    "кг/т", "кг / т", "kg/t",

    # productivity / flow
    "т/сут", "т / сут", "т/д", "t/day",
    "т/год", "т / год", "t/y", "t/year",
    "м³/ч", "м3/ч", "м³ / ч", "м3 / ч", "m3/h",
    "м³/сут", "м3/сут", "м³ / сут", "м3 / сут", "m3/day",
    "м/с", "м / с", "m/s",

    # temperature
    "°C", "°С", "град. С", "град С", "degC",

    # chemistry
    "pH",

    # percent
    "%", "проц.", "процентов",

    # economics
    "руб./м³", "руб./м3", "руб/м³", "руб/м3", "RUB/m3",
    "млн руб.", "млн руб", "тыс. руб.", "тыс руб.", "тыс руб",

    # energy
    "кВт·ч", "кВтч", "kWh",

    # pressure
    "МПа", "мПа", "кПа", "Па", "атм", "MPa", "kPa", "Pa", "atm",
]

LENGTH_UNITS = [
    "мм", "см", "м", "км",
    "mm", "cm", "m", "km",
]


NUMBER_PATTERN = r"[-+]?\d+(?:[,.]\d+)?"


def build_unit_pattern(include_length_units: bool = False) -> str:
    units = BASE_UNITS[:]

    if include_length_units:
        units += LENGTH_UNITS

    # Длинные единицы раньше коротких, чтобы "мг/л" не порезалось как "м".
    units = sorted(set(units), key=len, reverse=True)
    escaped = [re.escape(unit) for unit in units]

    # Единица не должна быть частью слова.
    # Это защищает от "4-11 Металлургия" -> "4-11 М".
    return r"(?<![A-Za-zА-Яа-я])(?:" + "|".join(escaped) + r")(?![A-Za-zА-Яа-я])"


def compile_patterns(include_length_units: bool = False):
    unit_pattern = build_unit_pattern(include_length_units)

    range_pattern = re.compile(
        rf"(?P<value_min>{NUMBER_PATTERN})\s*(?:-|до|…|\.\.)\s*"
        rf"(?P<value_max>{NUMBER_PATTERN})\s*(?P<unit>{unit_pattern})",
        re.IGNORECASE,
    )

    comparator_pattern = re.compile(
        rf"(?P<comparator>≤|>=|≥|<=|<|>|не\s+более|не\s+менее|"
        rf"менее|более|до|свыше|от)\s*"
        rf"(?P<value>{NUMBER_PATTERN})\s*(?P<unit>{unit_pattern})",
        re.IGNORECASE,
    )

    value_unit_pattern = re.compile(
        rf"(?P<value>{NUMBER_PATTERN})\s*(?P<unit>{unit_pattern})",
        re.IGNORECASE,
    )

    return range_pattern, comparator_pattern, value_unit_pattern


UNIT_NORMALIZATION = {
    "мг/л": "mg/L",
    "мг / л": "mg/L",
    "мг/дм3": "mg/L",
    "мг/дм³": "mg/L",
    "мг / дм3": "mg/L",
    "мг / дм³": "mg/L",
    "mg/l": "mg/L",
    "mg/L": "mg/L",

    "г/л": "g/L",
    "г / л": "g/L",
    "g/l": "g/L",
    "g/L": "g/L",

    "мкг/л": "ug/L",
    "мкг / л": "ug/L",
    "µg/l": "ug/L",
    "μg/l": "ug/L",
    "ug/l": "ug/L",

    "ppm": "ppm",

    "г/т": "g/t",
    "г / т": "g/t",
    "g/t": "g/t",

    "кг/т": "kg/t",
    "кг / т": "kg/t",
    "kg/t": "kg/t",

    "т/сут": "t/day",
    "т / сут": "t/day",
    "т/д": "t/day",
    "t/day": "t/day",

    "т/год": "t/year",
    "т / год": "t/year",
    "t/y": "t/year",
    "t/year": "t/year",

    "м3/ч": "m3/h",
    "м³/ч": "m3/h",
    "м3 / ч": "m3/h",
    "м³ / ч": "m3/h",
    "m3/h": "m3/h",

    "м3/сут": "m3/day",
    "м³/сут": "m3/day",
    "м3 / сут": "m3/day",
    "м³ / сут": "m3/day",
    "m3/day": "m3/day",

    "м/с": "m/s",
    "м / с": "m/s",
    "m/s": "m/s",

    "°с": "degC",
    "°c": "degC",
    "град. с": "degC",
    "град с": "degC",
    "degc": "degC",

    "%": "%",
    "проц.": "%",
    "процентов": "%",

    "ph": "pH",
    "pH": "pH",

    "руб./м3": "RUB/m3",
    "руб./м³": "RUB/m3",
    "руб/м3": "RUB/m3",
    "руб/м³": "RUB/m3",
    "rub/m3": "RUB/m3",

    "млн руб.": "million_RUB",
    "млн руб": "million_RUB",
    "тыс. руб.": "thousand_RUB",
    "тыс руб.": "thousand_RUB",
    "тыс руб": "thousand_RUB",

    "квт·ч": "kWh",
    "квтч": "kWh",
    "kwh": "kWh",

    "мпа": "MPa",
    "mpa": "MPa",
    "кпа": "kPa",
    "kpa": "kPa",
    "па": "Pa",
    "pa": "Pa",
    "атм": "atm",
    "atm": "atm",

    "мм": "mm",
    "см": "cm",
    "м": "m",
    "км": "km",
    "mm": "mm",
    "cm": "cm",
    "m": "m",
    "km": "km",
}


PARAMETER_RULES = [
    ("sulfate_concentration", [
        r"\bso4\b", r"so₄", r"сульфат", r"сульфаты", r"sulfate",
    ]),
    ("chloride_concentration", [
        r"\bcl\b", r"cl-", r"хлорид", r"хлориды", r"chloride",
    ]),
    ("calcium_concentration", [
        r"\bca\b", r"ca2\+", r"кальций", r"calcium",
    ]),
    ("magnesium_concentration", [
        r"\bmg\b", r"mg2\+", r"магний", r"magnesium",
    ]),
    ("sodium_concentration", [
        r"\bna\b", r"na\+", r"натрий", r"sodium",
    ]),
    ("dry_residue", [
        r"сухой остаток", r"минерализац", r"\btds\b", r"total dissolved solids",
    ]),
    ("temperature", [
        r"температур", r"temperature", r"нагрев", r"охлажден",
    ]),
    ("flow_velocity", [
        r"скорость потока", r"скорость циркуляц", r"velocity", r"flow velocity",
    ]),
    ("flow_rate", [
        r"расход", r"подач", r"циркуляц", r"flow rate",
    ]),
    ("productivity", [
        r"производительност", r"capacity", r"throughput", r"мощност",
    ]),
    ("extraction_recovery", [
        r"извлечен", r"recovery", r"выход металла",
    ]),
    ("content", [
        r"содержание", r"массовая доля", r"концентрац", r"grade", r"content",
    ]),
    ("capex", [
        r"\bcapex\b", r"капитальн", r"капзатрат", r"инвестиц",
    ]),
    ("opex", [
        r"\bopex\b", r"операционн", r"эксплуатационн",
    ]),
    ("economic_effect", [
        r"экономический эффект", r"\bnpv\b", r"\birr\b", r"окупаемост",
    ]),
    ("pressure", [
        r"давлен", r"pressure",
    ]),
    ("ph", [
        r"\bph\b", r"водородный показатель",
    ]),
    ("share_percent", [
        r"доля", r"процент", r"распределен", r"ratio", r"селективност",
    ]),
    ("dimension", [
        r"длина", r"ширина", r"высота", r"толщина", r"диаметр", r"глубина",
        r"линия", r"расширение", r"distance", r"length", r"width", r"height",
    ]),
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


def stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def parse_number(value: str) -> Optional[float]:
    try:
        return float(value.replace(",", "."))
    except Exception:
        return None


def normalize_unit(unit: str) -> str:
    cleaned = " ".join(unit.strip().split())
    key = cleaned.lower()
    return UNIT_NORMALIZATION.get(key, UNIT_NORMALIZATION.get(cleaned, cleaned))


def normalize_comparator(comparator: str) -> str:
    comp = " ".join(comparator.lower().strip().split())

    mapping = {
        "≤": "<=",
        "<=": "<=",
        "не более": "<=",
        "менее": "<",
        "<": "<",

        "≥": ">=",
        ">=": ">=",
        "не менее": ">=",
        "более": ">",
        "свыше": ">",

        "до": "<=",
        "от": ">=",
    }

    return mapping.get(comp, comp)


def get_context(text: str, start: int, end: int, window: int = 180) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    return text[left:right].replace("\n", " ").strip()


def infer_parameter(context: str, unit_normalized: str) -> Tuple[str, float]:
    ctx = context.lower()

    best_parameter = "numeric_value"
    best_score = 0.0

    for parameter, patterns in PARAMETER_RULES:
        score = 0.0

        for pattern in patterns:
            if re.search(pattern, ctx, flags=re.IGNORECASE):
                score += 1.0

        if score > best_score:
            best_score = score
            best_parameter = parameter

    if best_score == 0:
        if unit_normalized in {"mg/L", "g/L", "ug/L", "ppm"}:
            best_parameter = "concentration"
            best_score = 0.6
        elif unit_normalized == "degC":
            best_parameter = "temperature"
            best_score = 0.7
        elif unit_normalized == "m/s":
            best_parameter = "flow_velocity"
            best_score = 0.7
        elif unit_normalized in {"m3/h", "m3/day"}:
            best_parameter = "flow_rate"
            best_score = 0.6
        elif unit_normalized in {"t/day", "t/year"}:
            best_parameter = "productivity"
            best_score = 0.6
        elif unit_normalized == "%":
            best_parameter = "share_percent"
            best_score = 0.5
        elif unit_normalized in {"RUB/m3", "million_RUB", "thousand_RUB"}:
            best_parameter = "economic_indicator"
            best_score = 0.7
        elif unit_normalized in {"MPa", "kPa", "Pa", "atm"}:
            best_parameter = "pressure"
            best_score = 0.7
        elif unit_normalized in {"mm", "cm", "m", "km"}:
            best_parameter = "dimension"
            best_score = 0.5
        elif unit_normalized == "pH":
            best_parameter = "ph"
            best_score = 0.8
        elif unit_normalized in {"g/t", "kg/t"}:
            best_parameter = "content"
            best_score = 0.6

    return best_parameter, min(best_score / 2.0, 1.0)


def is_likely_noise(
    source_text: str,
    context: str,
    unit_normalized: str,
    parameter: str,
) -> bool:
    ctx = context.lower()
    src = source_text.strip()

    # Защита от "Модуль 4-11 Металлургия"
    if re.search(r"модул[ьяе]\s+\d+\s*-\s*\d+", ctx):
        return True

    # Защита от номеров страниц/разделов рядом с процентами и единицами
    if re.fullmatch(r"\d+\s*[-–]\s*\d+\s*[A-Za-zА-Яа-я]?", src):
        return True

    # Не берем даты как технологические параметры.
    if re.fullmatch(r"(19|20)\d{2}\s*г\.?", src.lower()):
        return True

    # Одиночный pH должен иметь число в диапазоне 0-14, проверяется отдельно ниже.
    return False


def confidence_score(
    parameter_score: float,
    unit_normalized: str,
    has_context: bool,
    comparator: str,
    parameter: str,
) -> float:
    score = 0.45

    if unit_normalized:
        score += 0.2

    if has_context:
        score += 0.1

    if parameter != "numeric_value":
        score += 0.15

    if parameter_score > 0:
        score += min(parameter_score, 0.15)

    if comparator in {"range", "<=", ">=", "<", ">", "="}:
        score += 0.05

    return round(min(score, 0.98), 3)


def make_numeric_id(chunk_id: str, source_text: str, start: int, end: int) -> str:
    return "num_" + stable_hash(f"{chunk_id}:{start}:{end}:{source_text}", 18)


def find_numeric_values(
    chunk: Dict[str, Any],
    include_length_units: bool = False,
) -> List[Dict[str, Any]]:
    text = chunk.get("text") or ""

    range_pattern, comparator_pattern, value_unit_pattern = compile_patterns(
        include_length_units=include_length_units
    )

    results = []
    occupied_spans = []

    def is_overlapping(start: int, end: int) -> bool:
        for s, e in occupied_spans:
            if start < e and end > s:
                return True
        return False

    def add_result(
        match: re.Match,
        value: Optional[float],
        value_min: Optional[float],
        value_max: Optional[float],
        comparator: str,
        unit: str,
    ) -> None:
        start, end = match.span()

        if is_overlapping(start, end):
            return

        source_text = match.group(0).strip()
        context = get_context(text, start, end)
        unit_normalized = normalize_unit(unit)

        parameter, parameter_score = infer_parameter(context, unit_normalized)

        if is_likely_noise(source_text, context, unit_normalized, parameter):
            return

        if unit_normalized == "pH":
            val = value if value is not None else value_min
            if val is not None and not (0 <= val <= 14):
                return

        numeric_id = make_numeric_id(chunk["chunk_id"], source_text, start, end)

        result = {
            "id": numeric_id,
            "chunk_id": chunk.get("chunk_id"),
            "document_id": chunk.get("document_id"),
            "filename": chunk.get("filename"),
            "source_path": chunk.get("source_path"),
            "relative_path": chunk.get("relative_path"),
            "source_type": chunk.get("source_type"),

            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "slide_start": chunk.get("slide_start"),
            "slide_end": chunk.get("slide_end"),
            "sheet_name": chunk.get("sheet_name"),
            "row_start": chunk.get("row_start"),
            "row_end": chunk.get("row_end"),

            "parameter": parameter,
            "display_name": parameter,
            "value": value,
            "value_min": value_min,
            "value_max": value_max,
            "comparator": comparator,

            "unit_original": unit.strip(),
            "unit_normalized": unit_normalized,

            "source_text": source_text,
            "context": context,
            "char_start": start,
            "char_end": end,
            "confidence": confidence_score(
                parameter_score=parameter_score,
                unit_normalized=unit_normalized,
                has_context=bool(context),
                comparator=comparator,
                parameter=parameter,
            ),
        }

        results.append(result)
        occupied_spans.append((start, end))

    # 1. Диапазоны: 200-300 мг/л
    for match in range_pattern.finditer(text):
        value_min = parse_number(match.group("value_min"))
        value_max = parse_number(match.group("value_max"))
        unit = match.group("unit")

        if value_min is None or value_max is None:
            continue

        if value_min > value_max:
            value_min, value_max = value_max, value_min

        add_result(
            match=match,
            value=None,
            value_min=value_min,
            value_max=value_max,
            comparator="range",
            unit=unit,
        )

    # 2. Ограничения: ≤300 мг/л, не более 1000 мг/дм³
    for match in comparator_pattern.finditer(text):
        value = parse_number(match.group("value"))
        unit = match.group("unit")
        comparator = normalize_comparator(match.group("comparator"))

        if value is None:
            continue

        add_result(
            match=match,
            value=value,
            value_min=None,
            value_max=None,
            comparator=comparator,
            unit=unit,
        )

    # 3. Простые значения: 51 %, 12 млн руб.
    for match in value_unit_pattern.finditer(text):
        value = parse_number(match.group("value"))
        unit = match.group("unit")

        if value is None:
            continue

        add_result(
            match=match,
            value=value,
            value_min=None,
            value_max=None,
            comparator="=",
            unit=unit,
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract numeric values and units from chunks.jsonl."
    )

    parser.add_argument(
        "chunks_path",
        type=str,
        help="Path to chunks.jsonl",
    )

    parser.add_argument(
        "--output",
        type=str,
        default="numeric_values_v2.jsonl",
        help="Path to output numeric_values_v2.jsonl",
    )

    parser.add_argument(
        "--stats",
        type=str,
        default="numeric_stats_v2.json",
        help="Path to output numeric_stats_v2.json",
    )

    parser.add_argument(
        "--chunks-with-numbers",
        type=str,
        default=None,
        help="Optional path to save chunks that contain numeric values",
    )

    parser.add_argument(
        "--include-length-units",
        action="store_true",
        help="Also extract mm/cm/m/km. Disabled by default to reduce noise.",
    )

    args = parser.parse_args()

    chunks_path = Path(args.chunks_path)
    output_path = Path(args.output)
    stats_path = Path(args.stats)
    chunks_with_numbers_path = Path(args.chunks_with_numbers) if args.chunks_with_numbers else None

    if output_path.exists():
        output_path.unlink()

    if chunks_with_numbers_path and chunks_with_numbers_path.exists():
        chunks_with_numbers_path.unlink()

    stats = {
        "chunks_total": 0,
        "chunks_with_numeric_values": 0,
        "numeric_values_total": 0,
        "by_parameter": {},
        "by_unit": {},
        "by_source_type": {},
        "include_length_units": args.include_length_units,
    }

    for chunk in tqdm(read_jsonl(chunks_path), desc="Extracting numeric values"):
        stats["chunks_total"] += 1

        values = find_numeric_values(
            chunk=chunk,
            include_length_units=args.include_length_units,
        )

        if not values:
            continue

        stats["chunks_with_numeric_values"] += 1

        if chunks_with_numbers_path:
            write_jsonl(chunks_with_numbers_path, {
                "chunk_id": chunk.get("chunk_id"),
                "document_id": chunk.get("document_id"),
                "filename": chunk.get("filename"),
                "source_type": chunk.get("source_type"),
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "text": chunk.get("text"),
                "numeric_value_ids": [v["id"] for v in values],
            })

        for value in values:
            write_jsonl(output_path, value)

            stats["numeric_values_total"] += 1

            parameter = value.get("parameter") or "unknown"
            unit = value.get("unit_normalized") or "unknown"
            source_type = value.get("source_type") or "unknown"

            stats["by_parameter"][parameter] = stats["by_parameter"].get(parameter, 0) + 1
            stats["by_unit"][unit] = stats["by_unit"].get(unit, 0) + 1
            stats["by_source_type"][source_type] = stats["by_source_type"].get(source_type, 0) + 1

    with stats_path.open("w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print("\nDone.")
    print(f"Numeric values: {output_path.resolve()}")
    print(f"Stats:          {stats_path.resolve()}")

    if chunks_with_numbers_path:
        print(f"Chunks filtered: {chunks_with_numbers_path.resolve()}")

    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()