from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from tqdm import tqdm


load_dotenv()


YANDEX_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


ENTITY_TYPES = [
    "Material",
    "Process",
    "Equipment",
    "Property",
    "Experiment",
    "Publication",
    "Patent",
    "Report",
    "Expert",
    "Laboratory",
    "Facility",
    "TechnologySolution",
    "Geography",
    "EconomicIndicator",
    "EnvironmentalIndicator",
]


RELATION_TYPES = [
    "USES_MATERIAL",
    "APPLIES_TO",
    "OPERATES_AT_CONDITION",
    "PRODUCES_OUTPUT",
    "DESCRIBED_IN",
    "VALIDATED_BY",
    "CONTRADICTS",
    "EXPERT_IN",
    "HAS_ECONOMIC_INDICATOR",
    "HAS_ENVIRONMENTAL_LIMITATION",
]


FACT_TYPES = [
    "technology_applicability",
    "process_condition",
    "experimental_result",
    "economic_indicator",
    "environmental_limit",
    "recommendation",
    "contradiction",
    "expert_competence",
    "publication_metadata",
]


GEO_SCOPES = [
    "domestic",
    "foreign",
    "global",
    "unknown",
]


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[WARN] Bad JSON at line {line_number}: {e}")
                continue

            if isinstance(item, dict):
                yield item


def write_jsonl(path: Path, item: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha1(
        value.encode("utf-8", errors="ignore")
    ).hexdigest()[:length]


def make_id(prefix: str, *parts: Any) -> str:
    joined = ":".join(str(p) for p in parts)
    return f"{prefix}_{stable_hash(joined, 18)}"


def clamp_float(value: Any, default: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    try:
        number = float(value)
    except Exception:
        return default

    if number <= 0:
        return default

    return max(min_value, min(number, max_value))


def normalize_text_for_match(text: str) -> str:
    text = str(text or "").lower()
    text = text.replace("ё", "е")
    text = text.replace(",", ".")
    text = re.sub(r"\s+", "", text)
    return text


def compact_numeric_values(
    values: List[Dict[str, Any]],
    max_items: int = 12,
) -> List[Dict[str, Any]]:
    result = []

    for v in values[:max_items]:
        result.append({
            "id": v.get("id"),
            "parameter": v.get("parameter"),
            "value": v.get("value"),
            "value_min": v.get("value_min"),
            "value_max": v.get("value_max"),
            "comparator": v.get("comparator"),
            "unit": v.get("unit_normalized") or v.get("unit"),
            "source_text": v.get("source_text"),
        })

    return result


def build_prompt(
    chunk: Dict[str, Any],
    max_entities: int,
    max_relations: int,
    max_facts: int,
    max_numeric_values: int,
) -> str:
    numeric_values = compact_numeric_values(
        chunk.get("numeric_values") or [],
        max_items=max_numeric_values,
    )

    metadata = {
        "chunk_id": chunk.get("chunk_id"),
        "document_id": chunk.get("document_id"),
        "filename": chunk.get("filename"),
        "source_type": chunk.get("source_type"),
        "domain": chunk.get("llm_domain"),
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "slide_start": chunk.get("slide_start"),
        "slide_end": chunk.get("slide_end"),
        "sheet_name": chunk.get("sheet_name"),
        "row_start": chunk.get("row_start"),
        "row_end": chunk.get("row_end"),
    }

    text = chunk.get("text") or ""

    return f"""
    Ты извлекаешь проверяемые знания из научно-технического текста горно-металлургической тематики.

    Главная цель:
    Преобразовать фрагмент текста в структурированные сущности, связи и факты для графа знаний R&D.

    Правила:
    1. Не выдумывай. Используй только данный текст.
    2. Каждый факт должен иметь короткую цитату из текста.
    3. Если факт не подтверждается текстом — не добавляй его.
    4. Числа используй только если они реально относятся к факту.
    5. Не извлекай библиографический шум, если в чанке есть технологические факты.
    6. Верни только валидный JSON без markdown.
    7. Не добавляй комментарии вне JSON.

    Как выбирать типы сущностей:
    - Material: руда, шлак, штейн, концентрат, кек, металл, раствор, электролит, реагент.
    - Process: флотация, плавка, выщелачивание, электроэкстракция, конвертирование, охлаждение, измельчение.
    - Equipment: печь, мельница, конвертер, автоклав, гидроциклон, электролизёр.
    - Facility: завод, комбинат, рудник, цех, лаборатория, промышленная площадка.
    - TechnologySolution: технологическая схема, вариант переработки, способ получения продукта.
    - Property: содержание, извлечение, температура, давление, скорость, производительность, сортность.
    - Geography: страна, регион, город, месторождение.
    - EconomicIndicator: CAPEX, OPEX, стоимость, экономический эффект.
    - EnvironmentalIndicator: выбросы, стоки, SO2, экологический риск.

    Как выбирать типы связей:
    - USES_MATERIAL: процесс использует материал.
    - APPLIES_TO: технология применима к материалу/условиям.
    - OPERATES_AT_CONDITION: процесс работает при температуре, давлении, pH, скорости и т.д.
    - PRODUCES_OUTPUT: процесс производит концентрат, катод, кек, шлак и т.д.
    - DESCRIBED_IN: факт/технология описаны в публикации или отчёте.
    - VALIDATED_BY: факт подтверждён экспериментом, промышленными испытаниями или лабораторными данными.
    - HAS_ECONOMIC_INDICATOR: технология имеет CAPEX/OPEX/экономический эффект.
    - HAS_ENVIRONMENTAL_LIMITATION: технология имеет экологическое ограничение.

    Ограничения объёма:
    - не более {max_entities} entities;
    - не более {max_relations} relations;
    - не более {max_facts} facts;
    - выбирай только самые технически значимые факты.

    Confidence:
    - 0.95 — факт содержит точную цитату и числовой параметр;
    - 0.85 — факт содержит точную цитату, но без числа;
    - 0.70 — факт явно следует из текста, но формулировка обобщена;
    - ниже 0.60 используй только если есть неоднозначность.
    Никогда не ставь confidence = 0.0, если сущность, связь или факт найден в тексте.

    Типы сущностей:
    {json.dumps(ENTITY_TYPES, ensure_ascii=False)}

    Типы связей:
    {json.dumps(RELATION_TYPES, ensure_ascii=False)}

    Типы фактов:
    {json.dumps(FACT_TYPES, ensure_ascii=False)}

    geo_scope:
    - domestic — российская / отечественная практика;
    - foreign — зарубежная практика;
    - global — общая международная практика;
    - unknown — география не указана.

    Метаданные чанка:
    {json.dumps(metadata, ensure_ascii=False, indent=2)}

    Кандидаты числовых значений, найденные regex-парсером:
    {json.dumps(numeric_values, ensure_ascii=False, indent=2)}

    Текст чанка:
    \"\"\"{text}\"\"\"

    Верни JSON строго в таком формате:
    {{
      "entities": [
        {{
          "type": "<один тип из списка ENTITY_TYPES>",
          "name": "как в тексте",
          "canonical_name": "нормализованное имя на английском, если возможно",
          "aliases": [],
          "evidence_text": "короткая цитата",
          "confidence": 0.85
        }}
      ],
      "relations": [
        {{
          "type": "<один тип из списка RELATION_TYPES>",
          "source_name": "имя сущности-источника",
          "target_name": "имя сущности-цели или id числового значения",
          "evidence_text": "короткая цитата",
          "confidence": 0.85
        }}
      ],
      "facts": [
        {{
          "claim_text": "краткий проверяемый факт",
          "fact_type": "<один тип из списка FACT_TYPES>",
          "subject": "главная сущность факта",
          "objects": [],
          "numeric_value_ids": [],
          "geo_scope": "unknown",
          "country": null,
          "year": null,
          "quote": "цитата из текста",
          "confidence": 0.85
        }}
      ],
      "warnings": []
    }}
    """.strip()


def build_yandex_model_uri(folder_id: str, model: str) -> str:
    model = model.strip()

    if model.startswith("gpt://"):
        return model

    if "/" in model:
        return f"gpt://{folder_id}/{model}"

    return f"gpt://{folder_id}/{model}/latest"


def call_yandex_completion(
    prompt: str,
    api_key: str,
    folder_id: str,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    json_mode: bool = True,
) -> str:
    model_uri = build_yandex_model_uri(folder_id, model)

    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json",
        "x-folder-id": folder_id,
    }

    payload = {
        "modelUri": model_uri,
        "completionOptions": {
            "stream": False,
            "temperature": temperature,
            "maxTokens": str(max_tokens),
        },
        "messages": [
            {
                "role": "system",
                "text": "Ты строгий JSON extractor. Возвращай только валидный JSON-объект."
            },
            {
                "role": "user",
                "text": prompt
            },
        ],
    }

    if json_mode:
        payload["jsonObject"] = True

    response = requests.post(
        YANDEX_COMPLETION_URL,
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Yandex API error {response.status_code}: "
            f"modelUri={model_uri}; body={response.text[:3000]}"
        )

    data = response.json()

    if "alternatives" in data:
        return data["alternatives"][0]["message"]["text"]

    if "result" in data and "alternatives" in data["result"]:
        return data["result"]["alternatives"][0]["message"]["text"]

    raise RuntimeError(
        "Unexpected Yandex response format: "
        + json.dumps(data, ensure_ascii=False)[:3000]
    )


def call_openai_compatible_completion(
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    json_mode: bool = True,
) -> str:
    base_url = base_url.rstrip("/")
    url = f"{base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты строгий JSON extractor. "
                    "Возвращай только валидный JSON-объект. "
                    "Не используй markdown."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
    }

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenAI-compatible API error {response.status_code}: "
            f"url={url}; model={model}; body={response.text[:3000]}"
        )

    data = response.json()

    try:
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(
            "Unexpected OpenAI-compatible response format: "
            + json.dumps(data, ensure_ascii=False)[:3000]
        ) from e


def call_llm(
    provider: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    json_mode: bool,
) -> Tuple[str, str]:
    provider = provider.lower().strip()

    if provider == "yandex":
        api_key = os.getenv("YANDEX_API_KEY")
        folder_id = os.getenv("YANDEX_FOLDER_ID")
        model = os.getenv("YANDEX_MODEL", "yandexgpt-lite")

        if not api_key:
            raise RuntimeError("YANDEX_API_KEY env/.env variable is required")

        if not folder_id:
            raise RuntimeError("YANDEX_FOLDER_ID env/.env variable is required")

        response_text = call_yandex_completion(
            prompt=prompt,
            api_key=api_key,
            folder_id=folder_id,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            json_mode=json_mode,
        )

        return response_text, model

    if provider in {"openai_compatible", "openai-compatible", "local"}:
        base_url = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
        api_key = os.getenv("OPENAI_COMPATIBLE_API_KEY", "ollama")
        model = os.getenv("OPENAI_COMPATIBLE_MODEL", "gpt-oss:20b")

        response_text = call_openai_compatible_completion(
            prompt=prompt,
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            json_mode=json_mode,
        )

        return response_text, model

    raise ValueError(f"Unsupported provider: {provider}")


def extract_json_from_text(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()

    raw = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")

    if start != -1 and end != -1 and end > start:
        candidate = raw[start:end + 1]

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        try:
            from json_repair import repair_json

            repaired = repair_json(candidate)
            return json.loads(repaired)
        except Exception:
            pass

    try:
        from json_repair import repair_json

        repaired = repair_json(raw)
        return json.loads(repaired)
    except Exception:
        pass

    raise ValueError("Could not parse JSON from LLM response")


def as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value

    if value is None:
        return []

    return []


def normalize_entity(entity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(entity, dict):
        return None

    entity_type = str(entity.get("type") or "").strip()

    if entity_type not in ENTITY_TYPES:
        entity_type = "Material"

    name = str(entity.get("name") or "").strip()

    if not name:
        return None

    canonical_name = str(entity.get("canonical_name") or name).strip()

    aliases = entity.get("aliases")
    if not isinstance(aliases, list):
        aliases = []

    evidence_text = str(entity.get("evidence_text") or "").strip()

    return {
        "type": entity_type,
        "name": name,
        "canonical_name": canonical_name,
        "aliases": aliases[:10],
        "evidence_text": evidence_text,
        "confidence": clamp_float(
            entity.get("confidence"),
            default=0.8 if evidence_text else 0.65,
        ),
    }


def normalize_relation(relation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(relation, dict):
        return None

    relation_type = str(relation.get("type") or "").strip()

    if relation_type not in RELATION_TYPES:
        relation_type = "APPLIES_TO"

    source_name = str(relation.get("source_name") or "").strip()
    target_name = str(relation.get("target_name") or "").strip()

    if not source_name or not target_name:
        return None

    evidence_text = str(relation.get("evidence_text") or "").strip()

    return {
        "type": relation_type,
        "source_name": source_name,
        "target_name": target_name,
        "evidence_text": evidence_text,
        "confidence": clamp_float(
            relation.get("confidence"),
            default=0.75 if evidence_text else 0.6,
        ),
    }


def normalize_fact(fact: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(fact, dict):
        return None

    claim_text = str(fact.get("claim_text") or "").strip()
    quote = str(fact.get("quote") or "").strip()

    if not claim_text:
        return None

    fact_type = str(fact.get("fact_type") or "").strip()

    if fact_type not in FACT_TYPES:
        fact_type = "experimental_result"

    subject = str(fact.get("subject") or "").strip()

    if not subject:
        subject = claim_text[:120]

    objects = fact.get("objects")
    if not isinstance(objects, list):
        objects = []

    numeric_value_ids = fact.get("numeric_value_ids")
    if not isinstance(numeric_value_ids, list):
        numeric_value_ids = []

    geo_scope = str(fact.get("geo_scope") or "unknown").strip()

    if geo_scope not in GEO_SCOPES:
        geo_scope = "unknown"

    country = fact.get("country")
    if country is not None:
        country = str(country).strip() or None

    year = fact.get("year")
    try:
        year = int(year) if year is not None else None
    except Exception:
        year = None

    return {
        "claim_text": claim_text,
        "fact_type": fact_type,
        "subject": subject,
        "objects": objects[:10],
        "numeric_value_ids": numeric_value_ids[:20],
        "geo_scope": geo_scope,
        "country": country,
        "year": year,
        "quote": quote,
        "confidence": clamp_float(
            fact.get("confidence"),
            default=0.9 if quote and numeric_value_ids else 0.85 if quote else 0.7,
        ),
    }


def make_numeric_regex(source_text: str) -> Optional[re.Pattern]:
    """
    Строит regex для точного поиска числового выражения.
    Нужно, чтобы "2%" не матчился внутри "11,2%".
    """
    source_text = str(source_text or "").strip()

    if not source_text:
        return None

    escaped = re.escape(source_text)

    # Разрешаем пробелы между числом и единицей: "98,5%" ~= "98,5 %"
    escaped = escaped.replace(r"\ ", r"\s*")

    # Запятая/точка взаимозаменяемы для чисел
    escaped = escaped.replace(",", r"[,.]")
    escaped = escaped.replace(r"\.", r"[,.]")

    # Не должно быть цифры/знака числа слева и справа
    pattern = rf"(?<![\d,.]){escaped}(?![\d,.])"

    try:
        return re.compile(pattern, flags=re.IGNORECASE)
    except re.error:
        return None


def validate_numeric_links(
    fact: Dict[str, Any],
    chunk_numeric_values: List[Dict[str, Any]],
) -> Dict[str, Any]:
    quote = fact.get("quote") or ""
    claim = fact.get("claim_text") or ""
    searchable = f"{quote} {claim}"

    valid_ids = []

    for value in chunk_numeric_values:
        value_id = value.get("id")
        source_text = value.get("source_text")

        if not value_id or not source_text:
            continue

        pattern = make_numeric_regex(source_text)

        if pattern and pattern.search(searchable):
            valid_ids.append(value_id)

    fact["numeric_value_ids"] = valid_ids
    return fact


def validate_extraction(
    obj: Dict[str, Any],
    max_entities: int,
    max_relations: int,
    max_facts: int,
) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError("Extraction is not a JSON object")

    raw_entities = as_list(obj.get("entities"))
    raw_relations = as_list(obj.get("relations"))
    raw_facts = as_list(obj.get("facts"))
    raw_warnings = as_list(obj.get("warnings"))

    entities = []
    for item in raw_entities:
        normalized = normalize_entity(item)
        if normalized:
            entities.append(normalized)

    relations = []
    for item in raw_relations:
        normalized = normalize_relation(item)
        if normalized:
            relations.append(normalized)

    facts = []
    for item in raw_facts:
        normalized = normalize_fact(item)
        if normalized:
            facts.append(normalized)

    warnings = []
    for item in raw_warnings:
        if isinstance(item, str) and item.strip():
            warnings.append(item.strip())
        elif isinstance(item, dict):
            warnings.append(json.dumps(item, ensure_ascii=False))

    return {
        "entities": entities[:max_entities],
        "relations": relations[:max_relations],
        "facts": facts[:max_facts],
        "warnings": warnings[:20],
    }


def deduplicate_by_id(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []

    for item in items:
        item_id = item.get("id")

        if not item_id:
            result.append(item)
            continue

        if item_id in seen:
            continue

        seen.add(item_id)
        result.append(item)

    return result


def attach_ids(chunk: Dict[str, Any], extraction: Dict[str, Any]) -> Dict[str, Any]:
    chunk_id = chunk.get("chunk_id")
    document_id = chunk.get("document_id")
    chunk_numeric_values = chunk.get("numeric_values") or []

    for entity in extraction["entities"]:
        entity["id"] = make_id(
            "ent",
            document_id,
            chunk_id,
            entity.get("type"),
            entity.get("canonical_name") or entity.get("name"),
        )
        entity["chunk_id"] = chunk_id
        entity["document_id"] = document_id
        entity["page_start"] = chunk.get("page_start")
        entity["page_end"] = chunk.get("page_end")
        entity["slide_start"] = chunk.get("slide_start")
        entity["slide_end"] = chunk.get("slide_end")
        entity["sheet_name"] = chunk.get("sheet_name")
        entity["row_start"] = chunk.get("row_start")
        entity["row_end"] = chunk.get("row_end")

    for relation in extraction["relations"]:
        relation["id"] = make_id(
            "rel",
            document_id,
            chunk_id,
            relation.get("type"),
            relation.get("source_name"),
            relation.get("target_name"),
            relation.get("evidence_text"),
        )
        relation["chunk_id"] = chunk_id
        relation["document_id"] = document_id

    for fact in extraction["facts"]:
        fact = validate_numeric_links(fact, chunk_numeric_values)

        quote = fact.get("quote") or fact.get("claim_text") or ""

        fact["id"] = make_id(
            "fact",
            document_id,
            chunk_id,
            fact.get("fact_type"),
            fact.get("subject"),
            quote,
        )

        fact["chunk_id"] = chunk_id
        fact["document_id"] = document_id

        fact["source"] = {
            "document_id": document_id,
            "chunk_id": chunk_id,
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
            "quote": quote,
        }

        fact["verification_level"] = "source_supported"
        fact["status"] = "auto_extracted"

    extraction["entities"] = deduplicate_by_id(extraction["entities"])
    extraction["relations"] = deduplicate_by_id(extraction["relations"])
    extraction["facts"] = deduplicate_by_id(extraction["facts"])

    return extraction


def should_skip_existing(output_path: Path) -> set[str]:
    if not output_path.exists():
        return set()

    processed = set()

    for item in read_jsonl(output_path):
        chunk_id = item.get("chunk_id")

        if chunk_id:
            processed.add(chunk_id)

    return processed


def load_candidates(
    candidate_chunks_path: Path,
    domain: Optional[str],
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    candidates = list(read_jsonl(candidate_chunks_path))

    if domain:
        candidates = [
            c for c in candidates
            if c.get("llm_domain") == domain
        ]

    if limit:
        candidates = candidates[:limit]

    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract entities, relations and facts from candidate chunks using LLM."
    )

    parser.add_argument(
        "candidate_chunks_path",
        type=str,
        help="Path to llm_candidate_chunks.jsonl",
    )

    parser.add_argument(
        "--provider",
        type=str,
        default=os.getenv("LLM_PROVIDER", "yandex"),
        choices=["yandex", "openai_compatible", "openai-compatible", "local"],
        help="LLM provider: yandex or openai_compatible",
    )

    parser.add_argument(
        "--output",
        type=str,
        default="llm_extractions.jsonl",
        help="Path to output JSONL",
    )

    parser.add_argument(
        "--errors",
        type=str,
        default="llm_extract_errors.jsonl",
        help="Path to errors JSONL",
    )

    parser.add_argument(
        "--domain",
        type=str,
        default=None,
        help="Optional domain filter, e.g. pgm_matte_slag_distribution",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max chunks to process",
    )

    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Sleep between requests",
    )

    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retries per chunk",
    )

    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
    )

    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2200,
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip chunks already present in output",
    )

    parser.add_argument(
        "--no-json-mode",
        action="store_true",
        help="Disable provider JSON mode. Useful if local endpoint does not support response_format/jsonObject.",
    )

    parser.add_argument(
        "--max-entities",
        type=int,
        default=12,
    )

    parser.add_argument(
        "--max-relations",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--max-facts",
        type=int,
        default=8,
    )

    parser.add_argument(
        "--max-numeric-values",
        type=int,
        default=12,
    )

    args = parser.parse_args()

    candidate_chunks_path = Path(args.candidate_chunks_path)
    output_path = Path(args.output)
    errors_path = Path(args.errors)

    processed = should_skip_existing(output_path) if args.resume else set()

    candidates = load_candidates(
        candidate_chunks_path=candidate_chunks_path,
        domain=args.domain,
        limit=args.limit,
    )

    count = 0
    skipped = 0
    success = 0
    failed = 0

    total_entities = 0
    total_relations = 0
    total_facts = 0

    json_mode = not args.no_json_mode

    for chunk in tqdm(candidates, desc="LLM extraction"):
        chunk_id = chunk.get("chunk_id")

        if args.resume and chunk_id in processed:
            skipped += 1
            continue

        prompt = build_prompt(
            chunk=chunk,
            max_entities=args.max_entities,
            max_relations=args.max_relations,
            max_facts=args.max_facts,
            max_numeric_values=args.max_numeric_values,
        )

        last_error = None
        response_text = None
        model_used = None

        for attempt in range(1, args.max_retries + 1):
            try:
                response_text, model_used = call_llm(
                    provider=args.provider,
                    prompt=prompt,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                    timeout=args.timeout,
                    json_mode=json_mode,
                )

                parsed = extract_json_from_text(response_text)

                extraction = validate_extraction(
                    obj=parsed,
                    max_entities=args.max_entities,
                    max_relations=args.max_relations,
                    max_facts=args.max_facts,
                )

                extraction = attach_ids(chunk, extraction)

                item = {
                    "chunk_id": chunk_id,
                    "document_id": chunk.get("document_id"),
                    "filename": chunk.get("filename"),
                    "relative_path": chunk.get("relative_path"),
                    "source_type": chunk.get("source_type"),
                    "content_type": chunk.get("content_type"),
                    "section_type": chunk.get("section_type"),
                    "llm_domain": chunk.get("llm_domain"),
                    "llm_score": chunk.get("llm_score"),
                    "provider": args.provider,
                    "model": model_used,
                    "page_start": chunk.get("page_start"),
                    "page_end": chunk.get("page_end"),
                    "slide_start": chunk.get("slide_start"),
                    "slide_end": chunk.get("slide_end"),
                    "sheet_name": chunk.get("sheet_name"),
                    "row_start": chunk.get("row_start"),
                    "row_end": chunk.get("row_end"),
                    "entities": extraction["entities"],
                    "relations": extraction["relations"],
                    "facts": extraction["facts"],
                    "warnings": extraction["warnings"],
                }

                write_jsonl(output_path, item)

                success += 1
                total_entities += len(extraction["entities"])
                total_relations += len(extraction["relations"])
                total_facts += len(extraction["facts"])
                break

            except Exception as e:
                last_error = str(e)

                if attempt < args.max_retries:
                    time.sleep(1.5 * attempt)
                    continue

                write_jsonl(errors_path, {
                    "chunk_id": chunk_id,
                    "document_id": chunk.get("document_id"),
                    "filename": chunk.get("filename"),
                    "relative_path": chunk.get("relative_path"),
                    "llm_domain": chunk.get("llm_domain"),
                    "provider": args.provider,
                    "model": model_used,
                    "error": last_error,
                    "response_text": response_text[:10000] if response_text else None,
                })

                failed += 1

        count += 1

        if args.sleep > 0:
            time.sleep(args.sleep)

    print("\nDone.")
    print(f"Provider:  {args.provider}")
    print(f"Processed: {count}")
    print(f"Skipped:   {skipped}")
    print(f"Success:   {success}")
    print(f"Failed:    {failed}")
    print(f"Entities:  {total_entities}")
    print(f"Relations: {total_relations}")
    print(f"Facts:     {total_facts}")
    print(f"Output:    {output_path.resolve()}")
    print(f"Errors:    {errors_path.resolve()}")


if __name__ == "__main__":
    main()