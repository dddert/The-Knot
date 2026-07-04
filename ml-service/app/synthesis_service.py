from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.llm_client import LLMClient
from app.schemas import FinalAnswer, FinalAnswerSection


logger = logging.getLogger("uvicorn.error")


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _count_evidence_refs(value: Any) -> int:
    """Count distinct [E#] references in nested answer content."""
    refs = set(re.findall(r"\[E(\d+)\]", str(value or "")))
    return len(refs)


def _geo_filter_status(query_plan: Any) -> str:
    """Describe the current retrieval guarantee honestly.

    The current chunk index has year/source-type metadata but does not have
    reliable country metadata, so domestic/foreign scope cannot be enforced
    strictly at retrieval time.
    """
    if not isinstance(query_plan, dict):
        return "not_requested"

    scope = query_plan.get("geo_scope")
    if scope in {"domestic", "foreign"}:
        return "requested_but_not_strictly_verified"

    return "not_requested"


def _task_mode(
    query: str,
    query_plan: Any,
) -> str:
    q = str(query or "").casefold()

    fact_type = (
        query_plan.get("fact_type")
        if isinstance(query_plan, dict)
        else None
    )
    intent = (
        query_plan.get("intent")
        if isinstance(query_plan, dict)
        else None
    )

    if (
        fact_type == "contradiction"
        or any(
            token in q
            for token in (
                "противореч",
                "расхожден",
                "несогласован",
                "conflicting",
                "contradict",
            )
        )
    ):
        return "contradiction_analysis"

    if any(
        token in q
        for token in (
            "пробел",
            "недостаточно исслед",
            "knowledge gap",
            "research gap",
            "underexplored",
        )
    ):
        return "knowledge_gap_analysis"

    if (
        intent == "publication_search"
        or any(
            token in q
            for token in (
                "патент",
                "patent",
                "изобретен",
            )
        )
    ):
        return "patent_search"

    if intent == "expert_search":
        return "expert_search"

    return "general"


def _task_specific_requirements(
    task_mode: str,
) -> str:
    if task_mode == "contradiction_analysis":
        return """
Специальный режим CONTRADICTION:
- не называй данные противоречивыми только потому, что значения различаются;
- противоречие требует минимум двух evidence с несовместимыми выводами
  при сопоставимых материале, процессе и условиях;
- различия из-за температуры, реагента, времени, минералогии или иной методики
  обозначай как condition-dependent difference, а не contradiction;
- если подтвержденного противоречия нет, прямо напиши:
  "Подтвержденных противоречий в найденных evidence не выявлено";
- при наличии противоречия перечисли обе стороны отдельно с [E#].
"""

    if task_mode == "knowledge_gap_analysis":
        return """
Специальный режим KNOWLEDGE GAP:
- прямо перечисли пробелы знаний/исследований, а не только проблемы эксплуатации;
- отделяй:
  1) явно указанные источниками ограничения/неизученные вопросы;
  2) осторожно выведенные пробелы из неполноты найденных evidence;
- выводимый пробел помечай как "инференция по найденному корпусу";
- не превращай рекомендацию (например, утеплить штабель) в research gap;
- хороший gap описывает, каких сравнительных данных, режимов, масштабов,
  климатических сценариев или воспроизводимых экспериментов не хватает.
"""

    if task_mode == "patent_search":
        return """
Специальный режим PATENT:
- сначала ищи и перечисляй точные patent identifiers/номера, заявителей и названия,
  только если они явно есть в evidence;
- не называй общую технологию "патентом";
- институты и организации сами по себе не являются ответом на запрос о патентах;
- если точных патентных идентификаторов в evidence нет, прямо напиши:
  "В переданных evidence не найдено идентифицируемых номеров патентов";
- после этого можно отдельно перечислить технологические решения из evidence,
  явно назвав их "технологическими решениями, не подтвержденными как патенты".
"""

    return ""


ORG_LEGAL_FORMS = (
    "ООО",
    "АО",
    "ОАО",
    "ПАО",
    "ФГУП",
    "ФГБУ",
    "ФИЦ",
    "НИЦ",
)

ORG_PATTERNS = [
    # Legal form + quoted or compact unquoted organization name.
    re.compile(
        r"\b(?:ООО|АО|ОАО|ПАО|ФГУП|ФГБУ|ФИЦ|НИЦ)\s+"
        r"(?:"
        r"«[^»\n]{2,90}»"
        r"|\"[^\"\n]{2,90}\""
        r"|[A-ZА-ЯЁ0-9][A-ZА-ЯЁA-Za-zА-Яа-яЁё0-9._-]{2,50}"
        r")",
        flags=re.I,
    ),

    # Named institute. The first name token must look like a proper name
    # or an acronym; this prevents prose such as
    # "институт отлично справился..." from becoming an entity.
    re.compile(
        r"\b(?:Институт|институт)\s+"
        r"(?:"
        r"«[^»\n]{2,80}»"
        r"|\"[^\"\n]{2,80}\""
        r"|[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]{2,35}"
        r"(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]{2,35}){0,4}"
        r"|[A-ZА-ЯЁ]{3,20}"
        r")",
    ),

    # Named laboratory only. Generic prose after "лаборатория" is rejected:
    # "лаборатория отлично справилась..." will not match.
    re.compile(
        r"\b(?:Лаборатория|лаборатория)\s+"
        r"(?:"
        r"«[^»\n]{2,80}»"
        r"|\"[^\"\n]{2,80}\""
        r"|[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]{2,35}"
        r"(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]{2,35}){0,4}"
        r"|[A-ZА-ЯЁ]{3,20}"
        r")",
    ),

    re.compile(
        r"\b(?:Исследовательский\s+центр|Научный\s+центр)\s+"
        r"(?:"
        r"«[^»\n]{2,80}»"
        r"|\"[^\"\n]{2,80}\""
        r"|[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]{2,35}"
        r"(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]{2,35}){0,4}"
        r"|[A-ZА-ЯЁ]{3,20}"
        r")",
    ),
]

ENTITY_REJECT_MARKERS = {
    "заключение по",
    "исследования по",
    "рисунок ",
    "таблица ",
    "источник ",
    "ист.:",
    "ключевые слова",
    "студенты",
    "отлично справ",
    "создана на базе",
    "создано на базе",
    "для подготовки",
}

EXPERT_ACTIVITY_HINTS = (
    "исслед",
    "разработ",
    "заним",
    "провод",
    "выполн",
    "испыт",
    "изуч",
    "технолог",
    "проект",
    "автор",
    "сотрудник",
    "специалист",
)


def _clean_entity_display(name: str) -> str:
    value = str(name or "")

    value = value.replace("\u00ad", "")
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" ,.;:")

    if value.count("«") == 0 and value.endswith("»"):
        value = value[:-1].rstrip()

    return value


def _entity_key(name: str) -> str:
    """OCR-tolerant dedupe key."""
    value = _clean_entity_display(name).casefold()

    value = re.sub(
        r"^\s*(?:ооо|ао|оао|пао|фгуп|фгбу|фиц)\s+",
        "",
        value,
    )

    return re.sub(
        r"[^a-zа-яё0-9]+",
        "",
        value,
        flags=re.I,
    )


def _display_quality(name: str) -> tuple[int, int, int, int]:
    cleaned = _clean_entity_display(name)

    quoted_part = cleaned
    quote_match = re.search(r"«([^»]+)»", cleaned)
    if quote_match:
        quoted_part = quote_match.group(1)

    tokens = re.findall(
        r"[A-Za-zА-Яа-яЁё]+",
        quoted_part,
    )

    suspicious_short_tokens = sum(
        1
        for token in tokens
        if 1 < len(token) <= 4
        and token.casefold() not in {
            "ниц",
            "фиц",
            "фгуп",
            "фгбу",
            "оао",
            "ооо",
            "пао",
            "ао",
        }
    )

    internal_space_penalty = max(0, len(tokens) - 5)
    unmatched_quotes = abs(cleaned.count("«") - cleaned.count("»"))
    length_penalty = max(0, len(cleaned) - 90)

    return (
        unmatched_quotes,
        internal_space_penalty,
        suspicious_short_tokens,
        length_penalty,
    )


def _is_plausible_entity_name(name: str) -> bool:
    cleaned = _clean_entity_display(name)
    lowered = cleaned.casefold()

    if not cleaned or len(cleaned) < 4 or len(cleaned) > 110:
        return False

    if any(marker in lowered for marker in ENTITY_REJECT_MARKERS):
        return False

    legal_form_hits = re.findall(
        r"\b(?:ООО|АО|ОАО|ПАО|ФГУП|ФГБУ|ФИЦ|НИЦ)\b",
        cleaned,
        flags=re.I,
    )
    if len(legal_form_hits) > 1:
        return False

    if re.search(
        r"\)\s+и\s+(?:ООО|АО|ОАО|ПАО|ФГУП|ФГБУ|ФИЦ|НИЦ)\b",
        cleaned,
        flags=re.I,
    ):
        return False

    # Generic laboratory prose is not a named entity.
    if re.match(r"(?i)^лаборатория\s+", cleaned):
        tail = re.sub(
            r"(?i)^лаборатория\s+",
            "",
            cleaned,
        ).strip()

        named = bool(
            re.match(r"^[«\"]", tail)
            or re.match(r"^[A-ZА-ЯЁ]{3,20}\b", tail)
            or re.match(
                r"^[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]{2,35}\b",
                tail,
            )
        )

        if not named:
            return False

    return bool(_entity_key(cleaned))


def _expert_kind(name: str) -> str:
    lowered = name.lower()

    if "лаборатор" in lowered:
        return "laboratory"

    if any(
        token in lowered
        for token in (
            "институт",
            "внии",
            "цнигри",
            "гипроникель",
            "иргиредмет",
        )
    ):
        return "institute"

    if any(
        token in lowered
        for token in (
            "ниц",
            "научный центр",
            "исследовательский центр",
        )
    ):
        return "research_center"

    return "organization"


def _query_context_terms(
    query_plan: Any,
    query: str,
) -> list[str]:
    """Build robust process/topic stems for expert relevance grounding."""
    values: list[str] = []

    if isinstance(query_plan, dict):
        for field in (
            "processes",
            "materials",
            "equipment",
            "properties",
        ):
            field_values = query_plan.get(field) or []
            if isinstance(field_values, list):
                values.extend(
                    str(value)
                    for value in field_values
                    if str(value).strip()
                )

    # The raw query is a weak fallback only when QueryPlan is sparse.
    if not values and query:
        values.append(query)

    stopwords = {
        "какие", "какой", "какая", "эксперты", "эксперт",
        "лаборатории", "лаборатория", "занимаются",
        "найди", "найти", "покажи", "процессы",
        "what", "which", "experts", "laboratories",
    }

    terms: list[str] = []

    for value in values:
        for token in re.findall(
            r"[A-Za-zА-Яа-яЁё]{5,}",
            value.casefold(),
        ):
            if token in stopwords:
                continue

            # Crude but robust corpus stem:
            # автоклавным / автоклавного -> автоклав
            # выщелачиванием / выщелачивания -> выщелач
            stem_len = 8 if len(token) >= 10 else 6
            stem = token[:stem_len]

            if stem not in terms:
                terms.append(stem)

    return terms[:12]


def _entity_has_topic_context(
    excerpt: str,
    start: int,
    end: int,
    context_terms: list[str],
) -> tuple[bool, int]:
    """Require process/topic terms near the entity mention."""
    if not context_terms:
        return True, 0

    window_start = max(0, start - 650)
    window_end = min(len(excerpt), end + 650)
    window = excerpt[window_start:window_end].casefold()

    matched_terms = [
        term
        for term in context_terms
        if term in window
    ]

    if not matched_terms:
        return False, 0

    # Strong process grounding: one long technical stem is sufficient.
    strong_hits = sum(
        1
        for term in matched_terms
        if len(term) >= 7
    )

    return True, max(1, strong_hits)


def _entity_has_activity_context(
    excerpt: str,
    start: int,
    end: int,
) -> bool:
    window = excerpt[
        max(0, start - 420):
        min(len(excerpt), end + 420)
    ].casefold()

    return any(
        hint in window
        for hint in EXPERT_ACTIVITY_HINTS
    )


def _extract_expert_candidates(
    evidence: list[dict[str, Any]],
    *,
    query_plan: Any = None,
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Extract OCR-deduped entities grounded in the expert-search topic."""
    by_key: dict[str, dict[str, Any]] = {}

    context_terms = _query_context_terms(
        query_plan,
        query,
    )

    for item in evidence:
        evidence_id = str(item.get("evidence_id") or "")
        excerpt = str(item.get("excerpt") or "")

        for pattern in ORG_PATTERNS:
            for match in pattern.finditer(excerpt):
                name = _clean_entity_display(
                    match.group(0)
                )

                if not _is_plausible_entity_name(name):
                    continue

                topic_ok, topic_hits = _entity_has_topic_context(
                    excerpt,
                    match.start(),
                    match.end(),
                    context_terms,
                )

                if not topic_ok:
                    continue

                # For generic commercial organizations, require an activity cue
                # near the entity in addition to topical proximity.
                kind = _expert_kind(name)

                if (
                    kind == "organization"
                    and not _entity_has_activity_context(
                        excerpt,
                        match.start(),
                        match.end(),
                    )
                ):
                    continue

                key = _entity_key(name)
                if not key:
                    continue

                if key not in by_key:
                    by_key[key] = {
                        "name": name,
                        "kind": kind,
                        "evidence_ids": [],
                        "topic_hits": topic_hits,
                    }
                else:
                    current_name = str(
                        by_key[key].get("name") or ""
                    )

                    if _display_quality(name) < _display_quality(current_name):
                        by_key[key]["name"] = name
                        by_key[key]["kind"] = kind

                    by_key[key]["topic_hits"] = max(
                        int(by_key[key].get("topic_hits") or 0),
                        topic_hits,
                    )

                if (
                    evidence_id
                    and evidence_id not in by_key[key]["evidence_ids"]
                ):
                    by_key[key]["evidence_ids"].append(
                        evidence_id
                    )

    candidates = list(by_key.values())

    candidates.sort(
        key=lambda item: (
            -int(item.get("topic_hits") or 0),
            -len(item.get("evidence_ids") or []),
            _display_quality(str(item.get("name") or "")),
            str(item.get("name") or "").casefold(),
        )
    )

    return candidates[:limit]


def _safe_confidence(
    value: Any,
    *,
    default: float,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default

    return max(0.0, min(1.0, result))


def _normalize_related_experts(
    items: Any,
    *,
    query_plan: Any,
    expert_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidate_by_key = {
        _entity_key(str(candidate.get("name") or "")): candidate
        for candidate in expert_candidates
        if _entity_key(str(candidate.get("name") or ""))
    }

    normalized_by_key: dict[str, dict[str, Any]] = {}

    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue

            raw_name = _clean_entity_display(
                str(item.get("name") or "")
            )

            if not _is_plausible_entity_name(raw_name):
                continue

            key = _entity_key(raw_name)
            if not key:
                continue

            candidate = candidate_by_key.get(key)

            if expert_candidates and candidate is None:
                continue

            name = (
                str(candidate.get("name"))
                if candidate
                else raw_name
            )

            evidence_ids = item.get("evidence_ids") or []
            if not isinstance(evidence_ids, list):
                evidence_ids = []

            if candidate:
                evidence_ids = list(
                    dict.fromkeys(
                        [
                            *candidate.get("evidence_ids", []),
                            *[
                                str(value)
                                for value in evidence_ids
                                if str(value).strip()
                            ],
                        ]
                    )
                )

            normalized_evidence_ids = [
                str(value)
                for value in evidence_ids
                if str(value).strip()
            ]

            expert_confidence = _safe_confidence(
                item.get("confidence"),
                default=0.6,
            )

            # A literal zero is inconsistent with a candidate that is
            # explicitly grounded by one or more evidence IDs. Keep the
            # floor conservative: this is evidence linkage confidence, not
            # scientific certainty about the person's entire expertise.
            if (
                expert_confidence == 0.0
                and normalized_evidence_ids
            ):
                expert_confidence = (
                    0.65
                    if len(set(normalized_evidence_ids)) >= 2
                    else 0.55
                )

            normalized_by_key[key] = {
                "name": name,
                "kind": str(
                    item.get("kind")
                    or (candidate or {}).get("kind")
                    or _expert_kind(name)
                ),
                "affiliation": item.get("affiliation"),
                "location": item.get("location"),
                "evidence_ids": normalized_evidence_ids,
                "confidence": expert_confidence,
            }

    intent = (
        query_plan.get("intent")
        if isinstance(query_plan, dict)
        else None
    )

    if (
        intent == "expert_search"
        and not normalized_by_key
        and expert_candidates
    ):
        for candidate in expert_candidates[:10]:
            name = str(candidate.get("name") or "")
            key = _entity_key(name)

            if not key or key in normalized_by_key:
                continue

            normalized_by_key[key] = {
                "name": name,
                "kind": candidate.get("kind") or _expert_kind(name),
                "affiliation": None,
                "location": None,
                "evidence_ids": list(
                    candidate.get("evidence_ids") or []
                ),
                "confidence": 0.55,
            }

    return list(normalized_by_key.values())




def _source_title_value(
    source: Any,
) -> str | None:
    """Return a readable source title from dict/string/other payloads."""
    if isinstance(source, dict):
        value = (
            source.get("title")
            or source.get("filename")
            or source.get("document")
            or source.get("document_id")
            or source.get("id")
        )
        return str(value).strip() if value else None

    if isinstance(source, str):
        value = source.strip()
        return value or None

    if source is None:
        return None

    value = str(source).strip()
    return value or None


def _source_page_value(
    source: Any,
) -> Any:
    if not isinstance(source, dict):
        return None

    return (
        source.get("page")
        or source.get("page_start")
        or source.get("source_page")
    )


def _normalize_source_catalog_item(
    source: Any,
) -> dict[str, Any]:
    """Normalize backend source catalog entries that may be dicts or strings."""
    if isinstance(source, dict):
        return {
            "document_id": source.get("document_id"),
            "title": _clip(
                _source_title_value(source),
                300,
            ),
            "source_type": source.get("source_type"),
            "page": _source_page_value(source),
        }

    return {
        "document_id": None,
        "title": _clip(
            _source_title_value(source),
            300,
        ),
        "source_type": None,
        "page": None,
    }



def _compact_facts(
    facts: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    for index, fact in enumerate(facts[:limit], start=1):
        source = fact.get("source")

        result.append({
            "fact_id": fact.get("id") or fact.get("fact_id") or f"F{index}",
            "claim": _clip(
                fact.get("claim_text")
                or fact.get("claim")
                or fact.get("text"),
                900,
            ),
            "fact_type": fact.get("fact_type"),
            "confidence": fact.get("confidence"),
            "source": (
                fact.get("source_title")
                or _source_title_value(source)
            ),
            "page": (
                fact.get("source_page")
                or _source_page_value(source)
            ),
        })

    return result


def _compact_evidence(
    evidence: list[dict[str, Any]],
    *,
    limit: int,
    excerpt_chars: int,
    per_document: int = 2,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    document_counts: dict[str, int] = {}
    seen_chunks: set[str] = set()

    for item in evidence:
        chunk_id = str(item.get("chunk_id") or "")

        if chunk_id and chunk_id in seen_chunks:
            continue

        document_key = str(
            item.get("document_id")
            or item.get("filename")
            or chunk_id
            or len(result)
        )

        if document_counts.get(document_key, 0) >= per_document:
            continue

        evidence_id = f"E{len(result) + 1}"

        result.append({
            "evidence_id": evidence_id,
            "chunk_id": item.get("chunk_id"),
            "document_id": item.get("document_id"),
            "document": (
                item.get("filename")
                or item.get("document_id")
                or "unknown"
            ),
            "source_type": item.get("source_type"),
            "page": item.get("page_start"),
            "score": round(float(item.get("score") or 0.0), 6),
            "excerpt": _clip(item.get("text"), excerpt_chars),
        })

        if chunk_id:
            seen_chunks.add(chunk_id)

        document_counts[document_key] = (
            document_counts.get(document_key, 0) + 1
        )

        if len(result) >= limit:
            break

    return result


PATENT_ID_PATTERNS = [
    re.compile(
        r"\bпатент(?:е|а|у|ом|ы|ов)?\s+"
        r"(?:(?:США|РФ|России|US|RU|EP|WO|CN|JP)\s*)?"
        r"(?:№|N|No\.?)?\s*"
        r"(?P<id>\d{5,})\b",
        flags=re.I,
    ),
    re.compile(
        r"\b(?P<id>(?:WO|EP|US|RU|SU|CN|JP)"
        r"[/\-\s]?\d{4,}(?:[/\-]\d{3,})+)\b",
        flags=re.I,
    ),
]

PATENT_SIGNAL_RE = re.compile(
    r"\b(?:патент\w*|запатент\w*|изобретен\w*|patent\w*)\b",
    flags=re.I,
)


def _extract_patent_mentions(
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in evidence:
        evidence_id = str(item.get("evidence_id") or "")
        excerpt = str(item.get("excerpt") or "")

        for pattern in PATENT_ID_PATTERNS:
            for match in pattern.finditer(excerpt):
                raw = match.group(0).strip(" ,.;:")
                key = re.sub(
                    r"\s+",
                    " ",
                    raw.casefold(),
                )

                if key in seen:
                    continue
                seen.add(key)

                mentions.append({
                    "identifier": raw,
                    "evidence_id": evidence_id,
                    "document": item.get("document"),
                })

    return mentions


def _compact_evidence_for_task(
    evidence: list[dict[str, Any]],
    *,
    task_mode: str,
    limit: int,
    excerpt_chars: int,
    per_document: int = 2,
) -> list[dict[str, Any]]:
    """Select evidence with task-aware coverage while preserving top-ranked hits."""
    if task_mode != "patent_search":
        return _compact_evidence(
            evidence,
            limit=limit,
            excerpt_chars=excerpt_chars,
            per_document=per_document,
        )

    exact_patent: list[dict[str, Any]] = []
    patent_signal: list[dict[str, Any]] = []
    regular: list[dict[str, Any]] = []

    for item in evidence:
        text = str(item.get("text") or "")

        has_exact = any(
            pattern.search(text)
            for pattern in PATENT_ID_PATTERNS
        )

        if has_exact:
            exact_patent.append(item)
        elif PATENT_SIGNAL_RE.search(text):
            patent_signal.append(item)
        else:
            regular.append(item)

    # Ensure exact identifiers reach synthesis even when they rank below top-14.
    # Keep high-ranked general context too.
    ordered: list[dict[str, Any]] = []
    seen_chunks: set[str] = set()

    def add(items: list[dict[str, Any]], max_items: int | None = None) -> None:
        added = 0
        for item in items:
            chunk_key = str(
                item.get("chunk_id")
                or item.get("document_id")
                or id(item)
            )
            if chunk_key in seen_chunks:
                continue

            ordered.append(item)
            seen_chunks.add(chunk_key)
            added += 1

            if max_items is not None and added >= max_items:
                break

    add(exact_patent, max_items=max(4, limit // 3))
    add(patent_signal, max_items=max(3, limit // 4))
    add(list(evidence))

    return _compact_evidence(
        ordered,
        limit=limit,
        excerpt_chars=excerpt_chars,
        per_document=per_document,
    )


def _apply_task_guards(
    normalized: dict[str, Any],
    *,
    task_mode: str,
    patent_mentions: list[dict[str, Any]],
) -> dict[str, Any]:
    if task_mode == "contradiction_analysis":
        summary = str(normalized.get("summary") or "").strip()

        # A confirmed contradiction requires at least two separately cited
        # evidence items. Different values or outcomes under different
        # conditions are not contradictions by themselves.
        citation_count = _count_evidence_refs({
            "summary": normalized.get("summary"),
            "sections": normalized.get("sections"),
        })

        if citation_count < 2:
            required = (
                "Подтвержденных противоречий в найденных evidence не выявлено."
            )

            if "подтвержденных противореч" not in summary.casefold():
                if summary:
                    summary = (
                        required
                        + " Найденные различия следует трактовать как "
                        "условно-зависимые наблюдения: "
                        + summary
                    )
                else:
                    summary = required

            normalized["summary"] = _clip(summary, 6000)

            try:
                current_confidence = float(
                    normalized.get("confidence", 0.0)
                )
            except (TypeError, ValueError):
                current_confidence = 0.0

            # One cited observation is useful evidence, but insufficient to
            # establish a contradiction.
            normalized["confidence"] = min(
                max(0.0, current_confidence),
                0.35 if citation_count == 1 else 0.20,
            )

        return normalized

    if task_mode != "patent_search":
        return normalized

    summary = str(normalized.get("summary") or "").strip()

    if patent_mentions:
        cited = []
        for item in patent_mentions[:4]:
            identifier = str(item.get("identifier") or "").strip()
            evidence_id = str(item.get("evidence_id") or "").strip()

            if not identifier:
                continue

            suffix = f" [{evidence_id}]" if evidence_id else ""
            cited.append(f"{identifier}{suffix}")

        # Deterministically surface explicit identifiers if the LLM omitted them.
        if cited and not any(
            str(item.get("identifier") or "") in summary
            for item in patent_mentions
        ):
            prefix = (
                "В переданных evidence найдены идентифицируемые патентные "
                f"упоминания: {'; '.join(cited)}. "
            )
            summary = prefix + summary
    else:
        required = (
            "В переданных evidence не найдено идентифицируемых номеров патентов."
        )
        if "не найдено идентифицируемых" not in summary.casefold():
            summary = required + " " + summary

    normalized["summary"] = _clip(summary, 6000)
    return normalized


def _available_source_count(
    facts: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> int:
    sources: set[str] = set()

    for fact in facts:
        source = fact.get("source")
        value = (
            fact.get("source_title")
            or _source_title_value(source)
        )
        if value:
            sources.add(str(value))

    for item in evidence:
        value = (
            item.get("document")
            or item.get("filename")
            or item.get("document_id")
        )
        if value:
            sources.add(str(value))

    return len(sources)


def _sanitize_sections(
    value: Any,
) -> list[dict[str, Any]]:
    """Drop/repair malformed LLM section objects before Pydantic validation."""
    if not isinstance(value, list):
        return []

    cleaned: list[dict[str, Any]] = []

    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue

        title = str(item.get("title") or "").strip()
        section_type = str(item.get("type") or "text").strip() or "text"
        content = item.get("content")

        # Empty objects such as {} are common model noise and should not
        # invalidate an otherwise good answer.
        if not title and content in (None, "", [], {}):
            continue

        if not title:
            title = f"Раздел {index}"

        cleaned.append({
            "title": title,
            "type": section_type,
            "content": content if content is not None else "",
        })

    return cleaned


def _normalize_answer_payload(
    data: dict[str, Any],
    *,
    query: str,
    available_sources: int,
    geography_status: str,
    query_plan: Any,
    expert_candidates: list[dict[str, Any]],
    task_mode: str,
    patent_mentions: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = dict(data)

    normalized["summary"] = _clip(
        normalized.get("summary")
        or "Недостаточно данных для связного вывода.",
        6000,
    )

    try:
        confidence = float(normalized.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    confidence = max(0.0, min(1.0, confidence))

    # A literal zero is inconsistent with a non-empty, evidence-cited answer.
    # Apply only a conservative floor; this is a deterministic presentation
    # guard, not a claim of scientific certainty.
    citation_count = _count_evidence_refs({
        "summary": normalized.get("summary"),
        "sections": normalized.get("sections"),
    })

    if confidence == 0.0:
        if (
            available_sources >= 2
            and citation_count >= 2
        ):
            confidence = 0.45
        elif (
            available_sources >= 1
            and citation_count >= 1
        ):
            # One directly cited source supports a non-zero but deliberately
            # lower confidence than a multi-source answer.
            confidence = 0.35

    # Unverified geography must cap confidence because the requested
    # domestic/foreign scope is not strictly enforced by current metadata.
    if geography_status == "requested_but_not_strictly_verified":
        confidence = min(confidence, 0.65)

    normalized["confidence"] = confidence

    try:
        source_count = int(
            normalized.get("source_count", available_sources)
        )
    except (TypeError, ValueError):
        source_count = available_sources

    normalized["source_count"] = max(
        0,
        min(source_count, available_sources),
    )

    normalized["sections"] = _sanitize_sections(
        normalized.get("sections")
    )

    recommendations = normalized.get("recommendations")
    if not isinstance(recommendations, list):
        recommendations = []

    normalized["recommendations"] = [
        _clip(item, 1000)
        for item in recommendations
        if str(item or "").strip()
    ]

    normalized["related_experts"] = _normalize_related_experts(
        normalized.get("related_experts"),
        query_plan=query_plan,
        expert_candidates=expert_candidates,
    )

    # Expert answers often carry grounding in related_experts[].evidence_ids
    # rather than inline [E#] citations in the prose. Count those links before
    # accepting a literal answer confidence of zero.
    intent = (
        query_plan.get("intent")
        if isinstance(query_plan, dict)
        else None
    )

    if (
        intent == "expert_search"
        and float(normalized.get("confidence") or 0.0) == 0.0
        and normalized["related_experts"]
    ):
        grounded_expert_refs = {
            str(evidence_id).strip()
            for item in normalized["related_experts"]
            if isinstance(item, dict)
            for evidence_id in (item.get("evidence_ids") or [])
            if str(evidence_id).strip()
        }

        if grounded_expert_refs:
            normalized["confidence"] = (
                0.55
                if len(grounded_expert_refs) >= 2
                and available_sources >= 2
                else 0.45
            )

    export_payload = normalized.get("export_payload")
    if not isinstance(export_payload, dict):
        export_payload = {}

    export_payload.setdefault("query", query)
    normalized["export_payload"] = export_payload

    normalized = _apply_task_guards(
        normalized,
        task_mode=task_mode,
        patent_mentions=patent_mentions,
    )

    return normalized


class SynthesisService:
    def __init__(self) -> None:
        self.llm = LLMClient()

    async def synthesize(
        self,
        payload: dict[str, Any],
    ) -> FinalAnswer:
        facts = payload.get("facts") or []
        sources = payload.get("sources") or []
        evidence = payload.get("retrieved_evidence") or []
        query = str(payload.get("query") or "")

        if self.llm.available:
            system = (
                "Ты научно-технический аналитик. "
                "Синтезируй ответ ТОЛЬКО из переданных facts и evidence. "
                "Не добавляй сведения из памяти и не выдумывай. "
                "Для каждого существенного вывода указывай ссылки на evidence_id "
                "в формате [E1], [E2] и, если известна, страницу документа. "
                "Если источники противоречат друг другу или данных недостаточно, "
                "скажи это явно. "
                "Никогда не утверждай, что найденные документы относятся к конкретной "
                "стране только потому, что пользователь запросил domestic/foreign scope. "
                "Географию можно утверждать лишь при явном подтверждении во входных данных. "
                "Не превращай отдельные экспериментальные точки в диапазоны или монотонные "
                "закономерности. Например, из '93,3% при 220 °C' нельзя делать вывод "
                "'при 220 °C и выше >90%' без прямого подтверждения. "
                "Не интерполируй и не экстраполируй численные значения. "
                "Верни только один JSON-объект без markdown."
            )

            attempts = [
                {
                    "fact_limit": 20,
                    "evidence_limit": 14,
                    "excerpt_chars": 1100,
                    "max_tokens": 2200,
                },
                {
                    "fact_limit": 10,
                    "evidence_limit": 8,
                    "excerpt_chars": 700,
                    "max_tokens": 1600,
                },
            ]

            for attempt_no, cfg in enumerate(attempts, start=1):
                compact_facts = _compact_facts(
                    facts,
                    limit=cfg["fact_limit"],
                )

                query_plan = payload.get("query_plan")
                geography_status = _geo_filter_status(query_plan)
                task_mode = _task_mode(
                    query,
                    query_plan,
                )
                task_requirements = _task_specific_requirements(
                    task_mode,
                )

                compact_evidence = _compact_evidence_for_task(
                    evidence,
                    task_mode=task_mode,
                    limit=cfg["evidence_limit"],
                    excerpt_chars=cfg["excerpt_chars"],
                    per_document=2,
                )

                compact_sources = [
                    _normalize_source_catalog_item(source)
                    for source in sources[:12]
                ]

                expert_candidates = _extract_expert_candidates(
                    compact_evidence,
                    query_plan=query_plan,
                    query=query,
                )

                patent_mentions = _extract_patent_mentions(
                    compact_evidence,
                )

                compact = {
                    "query": query,
                    "query_plan": query_plan,
                    "task_mode": task_mode,
                    "retrieval_guarantees": {
                        "geography_filter": geography_status,
                        "year_filter": "strict_if_requested",
                        "source_type_filter": "strict_if_requested",
                    },
                    "facts": compact_facts,
                    "evidence": compact_evidence,
                    "expert_entity_candidates": expert_candidates,
                    "patent_mentions": patent_mentions,
                    "source_catalog": compact_sources,
                }

                compact_json = json.dumps(
                    compact,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )

                user = f"""Данные:
{compact_json}

Верни JSON строго такой формы:
{{
  "summary": "краткий связный ответ на запрос пользователя с ссылками [E1], [E2]",
  "confidence": 0.0,
  "source_count": 0,
  "sections": [
    {{
      "title": "Название раздела",
      "type": "bullets|table|text",
      "content": []
    }}
  ],
  "recommendations": [],
  "related_experts": [
    {{
      "name": "Явно названный эксперт, лаборатория или организация",
      "kind": "expert|laboratory|institute|research_center|organization",
      "affiliation": null,
      "location": null,
      "evidence_ids": ["E1"],
      "confidence": 0.0
    }}
  ],
  "export_payload": {{}}
}}

Требования:
{task_requirements}
- summary должен прямо отвечать на исходный query;
- используй только переданные facts/evidence;
- существенные выводы снабжай [E#];
- проверь retrieval_guarantees.geography_filter;
- если geography_filter=requested_but_not_strictly_verified, НЕ пиши
  "в российских источниках", "в зарубежных источниках" и аналогичные
  географические утверждения без прямого подтверждения в evidence;
- в таком случае используй формулировку "в найденных фрагментах корпуса";
- для численных результатов сохраняй точные условия из evidence;
- не заменяй отдельную точку диапазоном: "93,3% при 220 °C" нельзя
  перефразировать как "при 220 °C и выше более 90%";
- не делай вывод о монотонности между разрозненными экспериментальными точками;
- если разные точки дают разные результаты, перечисли их отдельно;
- если query содержит числовые пороги, в основном ответе сначала перечисляй
  только evidence, удовлетворяющие этим порогам; не прошедшие точки можно
  приводить лишь отдельно как контраст;
- confidence оценивай консервативно;
- confidence=0 допустим только если содержательный ответ по evidence невозможен;
- source_count не превышает число реально использованных уникальных документов;
- sections должны содержать полезный научно-технический обзор, а не копию входных данных;
- если query_plan.intent=expert_search, обязательно заполни related_experts;
- related_experts может содержать не только людей, но и явно названные лаборатории,
  институты, исследовательские центры и организации;
- используй только названия, явно присутствующие в evidence или
  expert_entity_candidates;
- для каждого элемента related_experts укажи evidence_ids;
- не придумывай affiliation/location, если они не подтверждены.
"""

                try:
                    logger.info(
                        "LLM synthesis attempt=%d payload_chars=%d "
                        "facts=%d evidence=%d",
                        attempt_no,
                        len(compact_json),
                        len(compact_facts),
                        len(compact_evidence),
                    )

                    data = await self.llm.complete_json(
                        system=system,
                        user=user,
                        max_tokens=cfg["max_tokens"],
                    )

                    if not isinstance(data, dict):
                        raise ValueError(
                            "LLM synthesis result must be a JSON object"
                        )

                    available_sources = _available_source_count(
                        compact_facts,
                        compact_evidence,
                    )

                    normalized = _normalize_answer_payload(
                        data,
                        query=query,
                        available_sources=available_sources,
                        geography_status=geography_status,
                        query_plan=query_plan,
                        expert_candidates=expert_candidates,
                        task_mode=task_mode,
                        patent_mentions=patent_mentions,
                    )

                    answer = FinalAnswer.model_validate(
                        normalized
                    )

                    logger.info(
                        "LLM synthesis success attempt=%d "
                        "confidence=%.3f source_count=%d sections=%d",
                        attempt_no,
                        answer.confidence,
                        answer.source_count,
                        len(answer.sections),
                    )

                    return answer

                except Exception as exc:
                    logger.exception(
                        "LLM synthesis failed attempt=%d/%d "
                        "query=%r error_type=%s error=%s",
                        attempt_no,
                        len(attempts),
                        query,
                        type(exc).__name__,
                        str(exc),
                    )

        # Deterministic expert backfill must survive LLM failure.
        fallback_query_plan = payload.get("query_plan")
        fallback_compact_evidence = _compact_evidence(
            evidence,
            limit=20,
            excerpt_chars=1400,
            per_document=2,
        )
        fallback_expert_candidates = _extract_expert_candidates(
            fallback_compact_evidence,
            query_plan=fallback_query_plan,
            query=query,
            limit=30,
        )
        fallback_related_experts = _normalize_related_experts(
            [],
            query_plan=fallback_query_plan,
            expert_candidates=fallback_expert_candidates,
        )

        rows = []

        for fact in facts[:20]:
            source = fact.get("source")

            rows.append({
                "claim": fact.get("claim_text"),
                "confidence": fact.get("confidence"),
                "source": (
                    fact.get("source_title")
                    or _source_title_value(source)
                ),
                "page": (
                    fact.get("source_page")
                    or _source_page_value(source)
                ),
            })

        compact_evidence = _compact_evidence(
            evidence,
            limit=15,
            excerpt_chars=500,
            per_document=2,
        )

        evidence_rows = [
            {
                "evidence_id": item.get("evidence_id"),
                "document": item.get("document"),
                "page": item.get("page"),
                "score": round(
                    float(item.get("score") or 0),
                    4,
                ),
                "excerpt": item.get("excerpt"),
            }
            for item in compact_evidence
        ]

        unique_sources = _available_source_count(
            facts,
            compact_evidence,
        )

        if (
            isinstance(fallback_query_plan, dict)
            and fallback_query_plan.get("intent") == "expert_search"
            and fallback_related_experts
        ):
            names = ", ".join(
                str(item.get("name"))
                for item in fallback_related_experts[:6]
            )
            summary = (
                "Связный LLM-синтез не был получен. "
                "Из явно названных организаций и лабораторий в найденных "
                f"фрагментах извлечены: {names}."
            )
        else:
            summary = (
                f"Найдено {len(facts)} структурированных фактов и "
                f"{len(evidence)} фрагментов полного корпуса. "
                "Связный LLM-синтез не был получен, поэтому показаны "
                "наиболее релевантные фрагменты без добавления неподтверждённых выводов."
            )

        return FinalAnswer(
            summary=summary,
            confidence=0.75 if facts else 0.55 if evidence else 0.0,
            source_count=unique_sources,
            sections=[
                FinalAnswerSection(
                    title="Структурированные факты",
                    type="table",
                    content=rows,
                ),
                FinalAnswerSection(
                    title="Retrieved evidence",
                    type="table",
                    content=evidence_rows,
                ),
            ],
            recommendations=[
                "Проверить цитаты первоисточников перед принятием технологического решения."
            ],
            related_experts=fallback_related_experts,
            export_payload={
                "query": query,
            },
        )
