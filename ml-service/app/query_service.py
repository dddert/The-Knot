from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Any

from app.llm_client import LLMClient
from app.schemas import NumericConstraint, ParseQueryRequest, QueryPlan


logger = logging.getLogger(__name__)

QUERY_LLM_TIMEOUT_SECONDS = float(
    os.getenv("QUERY_LLM_TIMEOUT_SECONDS", "8")
)
QUERY_LLM_MAX_ATTEMPTS = max(
    0,
    int(os.getenv("QUERY_LLM_MAX_ATTEMPTS", "1")),
)
QUERY_DETERMINISTIC_FAST_SCORE = int(
    os.getenv("QUERY_DETERMINISTIC_FAST_SCORE", "4")
)



# ---------------------------------------------------------------------------
# Existing functionality: unit normalization
# ---------------------------------------------------------------------------

UNIT_MAP = {
    "мг/л": "mg/L",
    "мг/дм3": "mg/L",
    "мг/дм³": "mg/L",
    "mg/l": "mg/L",

    "г/л": "g/L",
    "g/l": "g/L",

    "г/т": "g/t",
    "g/t": "g/t",

    "°c": "degC",
    "°с": "degC",
    "degc": "degC",

    "%": "%",

    "м/с": "m/s",
    "m/s": "m/s",

    "м3/ч": "m3/h",
    "м³/ч": "m3/h",

    "mpa": "MPa",
    "мпа": "MPa",

    "kpa": "kPa",
    "кпа": "kPa",

    "ph": "pH",
}


# ---------------------------------------------------------------------------
# Existing functionality: parameter inference
# ---------------------------------------------------------------------------

PARAM_HINTS = [
    (
        "sulfate_concentration",
        ("сульфат", "sulfate", "so4"),
    ),
    (
        "chloride_concentration",
        ("хлорид", "chloride", "cl-"),
    ),
    (
        "calcium_concentration",
        ("кальц", "calcium", "ca2+"),
    ),
    (
        "magnesium_concentration",
        ("магни", "magnesium", "mg2+"),
    ),
    (
        "sodium_concentration",
        ("натри", "sodium", "na+"),
    ),
    (
        "dry_residue",
        ("сухой остаток", "tds", "минерализац"),
    ),
    (
        "temperature",
        ("температур", "temperature"),
    ),
    (
        "flow_velocity",
        (
            "скорость потока",
            "flow velocity",
            "скорость циркуляц",
        ),
    ),
    (
        "flow_rate",
        ("расход", "flow rate"),
    ),
    (
        "extraction_recovery",
        ("извлечен", "recovery"),
    ),
    (
        "pressure",
        ("давлен", "pressure"),
    ),
    (
        "ph",
        ("ph", "водородный показатель"),
    ),
]


# ---------------------------------------------------------------------------
# New: strict taxonomy protection
# ---------------------------------------------------------------------------

ALLOWED_INTENTS = {
    "technology_review",
    "fact_search",
    "experiment_search",
    "comparative_analysis",
    "expert_search",
    "publication_search",
    "numeric_search",
}


ALLOWED_FACT_TYPES = {
    "technology_applicability",
    "process_condition",
    "experimental_result",
    "economic_indicator",
    "environmental_limit",
    "recommendation",
    "contradiction",
    "expert_competence",
    "publication_metadata",
}


ALLOWED_GEO_SCOPES = {
    "domestic",
    "foreign",
    "all",
    "unknown",
}


# ---------------------------------------------------------------------------
# New: chemical formula normalization
# ---------------------------------------------------------------------------

FORMULA_CANONICAL = {
    "so2": "SO2",
    "so3": "SO3",
    "h2s": "H2S",
    "co2": "CO2",
    "nox": "NOx",
    "ni": "Ni",
    "cu": "Cu",
    "au": "Au",
    "ag": "Ag",
    "co": "Co",
    "pt": "Pt",
    "pd": "Pd",
    "rh": "Rh",
    "na2so4": "Na2SO4",
    "cao": "CaO",
    "mgo": "MgO",
}

MATERIAL_HINTS = {
    "Ni": (
        "никел",
        "nickel",
    ),
    "Cu": (
        "мед",
        "copper",
    ),
    "Au": (
        "золот",
        "gold",
    ),
    "Ag": (
        "серебр",
        "silver",
    ),
    "Co": (
        "кобальт",
        "cobalt",
    ),
    "Pt": (
        "платин",
        "platinum",
    ),
}


# ---------------------------------------------------------------------------
# Existing functionality: explicit UI filters
# ---------------------------------------------------------------------------

def _explicit_filters(
    req: ParseQueryRequest,
) -> dict[str, Any]:
    f = req.filters

    constraints: list[dict[str, Any]] = []

    if f.numeric_parameter:
        constraints.append({
            "parameter": f.numeric_parameter,
            "operator": f.numeric_operator,
            "value_min": f.numeric_min,
            "value_max": f.numeric_max,
            "unit": f.numeric_unit,
        })

    return {
        "year_from": f.year_from,
        "year_to": f.year_to,
        "confidence_min": f.confidence_min,
        "geo_scope": f.geo_scope,
        "countries": [f.country] if f.country else [],
        "status": f.status,
        "fact_type": f.fact_type,
        "verification_level": f.verification_level,
        "source_type": f.source_type,
        "materials": [f.material] if f.material else [],
        "processes": [f.process] if f.process else [],
        "numeric_constraints": constraints,
    }


# ---------------------------------------------------------------------------
# Existing functionality: infer numeric parameter
# ---------------------------------------------------------------------------

def _infer_parameter(query: str) -> str:
    q = query.lower()

    for parameter, hints in PARAM_HINTS:
        if any(hint in q for hint in hints):
            return parameter

    return "numeric_value"


def _infer_parameter_near(
    query: str,
    start: int,
    end: int,
    unit: str | None = None,
) -> str:
    """Infer the parameter from nearby semantic cues.

    Cues before a numeric value are preferred over cues that appear after it.
    This matters for phrases such as:
      "извлечение не менее 90% при температуре ниже 100 °C"
    where the word "температуре" is physically closer to 90% than
    "извлечение", but belongs to the next numeric condition.
    """
    q = query.lower()
    normalized_unit = unit or ""

    # Strong unit priors for unambiguous engineering units.
    unit_priors = {
        "degC": "temperature",
        "MPa": "pressure",
        "kPa": "pressure",
        "m/s": "flow_velocity",
        "m3/h": "flow_rate",
        "pH": "ph",
    }

    if normalized_unit in unit_priors:
        return unit_priors[normalized_unit]

    best_parameter = "numeric_value"
    best_score = float("inf")

    for parameter, hints in PARAM_HINTS:
        for hint in hints:
            for cue in re.finditer(re.escape(hint.lower()), q):
                if cue.end() <= start:
                    # Semantic labels usually precede their value.
                    distance = start - cue.end()
                    direction_penalty = 0.0
                    between = q[cue.end():start]
                elif cue.start() >= end:
                    # A cue after the value often belongs to the next clause.
                    distance = cue.start() - end
                    direction_penalty = 35.0
                    between = q[end:cue.start()]
                else:
                    distance = 0.0
                    direction_penalty = 0.0
                    between = ""

                clause_penalty = 0.0
                if re.search(r"[,;:.!?]", between):
                    clause_penalty += 30.0

                # "при температуре", "и температуре" etc. usually starts
                # a new condition after the previous numeric value.
                if cue.start() >= end and re.search(
                    r"\b(?:при|и|а|но)\s*$",
                    between,
                ):
                    clause_penalty += 25.0

                score = distance + direction_penalty + clause_penalty

                # Percentages next to extraction/recovery wording are
                # overwhelmingly likely to be recovery constraints.
                if (
                    normalized_unit == "%"
                    and parameter == "extraction_recovery"
                ):
                    score -= 15.0

                if score < best_score:
                    best_score = score
                    best_parameter = parameter

    if best_score <= 120:
        return best_parameter

    return "numeric_value"


def _extract_numeric_constraints(
    query: str,
) -> list[NumericConstraint]:
    number = r"[-+]?\d+(?:\.\d+)?"

    unit = (
        r"мг/л|мг/дм3|мг/дм³|mg/l|"
        r"г/л|g/l|"
        r"г/т|g/t|"
        r"°c|°с|degc|%|"
        r"м/с|m/s|"
        r"м3/ч|м³/ч|"
        r"mpa|мпа|"
        r"kpa|кпа|ph"
    )

    patterns: list[tuple[str, str | None]] = [
        (
            rf"(?P<op><=|>=|≤|≥|<|>)\s*"
            rf"(?P<value>{number})\s*"
            rf"(?P<unit>{unit})",
            None,
        ),
        (
            rf"(?:не\s+менее|как\s+минимум|минимум)\s*"
            rf"(?P<value>{number})\s*"
            rf"(?P<unit>{unit})",
            ">=",
        ),
        (
            rf"(?:не\s+более|не\s+выше|максимум)\s*"
            rf"(?P<value>{number})\s*"
            rf"(?P<unit>{unit})",
            "<=",
        ),
        (
            rf"(?:ниже|меньше)\s*"
            rf"(?P<value>{number})\s*"
            rf"(?P<unit>{unit})",
            "<",
        ),
        (
            rf"(?:выше|больше)\s*"
            rf"(?P<value>{number})\s*"
            rf"(?P<unit>{unit})",
            ">",
        ),
    ]

    result: list[NumericConstraint] = []
    seen: set[tuple[str, str, float, str]] = set()

    for pattern, fixed_operator in patterns:
        for match in re.finditer(
            pattern,
            query,
            flags=re.I,
        ):
            raw_operator = (
                fixed_operator
                or match.groupdict().get("op")
                or "="
            )

            operator = {
                "≤": "<=",
                "≥": ">=",
            }.get(
                raw_operator,
                raw_operator,
            )

            value = float(
                match.group("value")
            )

            raw_unit = match.group("unit")

            normalized_unit = UNIT_MAP.get(
                raw_unit.lower(),
                raw_unit,
            )

            parameter = _infer_parameter_near(
                query,
                match.start(),
                match.end(),
                normalized_unit,
            )

            key = (
                parameter,
                operator,
                value,
                normalized_unit,
            )

            if key in seen:
                continue

            seen.add(key)

            result.append(
                NumericConstraint(
                    parameter=parameter,
                    operator=operator,
                    value=value,
                    unit=normalized_unit,
                )
            )

    return result


# ---------------------------------------------------------------------------
# Existing fallback parser
# Preserved, not replaced.
# ---------------------------------------------------------------------------

def fallback_parse(
    req: ParseQueryRequest,
) -> QueryPlan:
    explicit = _explicit_filters(req)

    plan = QueryPlan(**explicit)

    q = req.query.lower().replace(",", ".")

    # Existing intent heuristics.
    if any(
        token in q
        for token in [
            "сравни",
            "compare",
            "vs ",
            "против",
        ]
    ):
        plan.comparison_mode = True
        plan.intent = "comparative_analysis"

    elif any(
        token in q
        for token in [
            "эксперимент",
            "опыт",
            "experiment",
        ]
    ):
        plan.intent = "experiment_search"

    elif any(
        token in q
        for token in [
            "эксперт",
            "лаборатор",
            "author",
            "expert",
        ]
    ):
        plan.intent = "expert_search"

    # Numeric parsing from symbolic and natural-language constraints.
    if not plan.numeric_constraints:
        plan.numeric_constraints.extend(
            _extract_numeric_constraints(q)
        )

    return plan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _append_unique(
    values: list[str],
    value: str,
) -> None:
    value = value.strip()

    if not value:
        return

    lowered = {
        existing.lower()
        for existing in values
    }

    if value.lower() not in lowered:
        values.append(value)


# ---------------------------------------------------------------------------
# New: deterministic enrichment.
#
# IMPORTANT:
# This is enrichment on top of old fallback_parse.
# It does not replace numeric parsing or UI filters.
# ---------------------------------------------------------------------------

def enrich_from_query(
    req: ParseQueryRequest,
    plan: QueryPlan,
) -> QueryPlan:
    q = req.query.lower()
    original = req.query

    explicit = _explicit_filters(req)

    # ------------------------------------------------------------------
    # Deterministic task-mode signals.
    #
    # These are generic R&D intents, not demo-domain hardcoding.
    # Reapplied after LLM merge by enrich_from_query(), so an LLM cannot
    # silently downgrade a contradiction/patent/gap request.
    # ------------------------------------------------------------------

    if any(
        token in q
        for token in [
            "противореч",
            "расхожден",
            "несогласован",
            "конфликтующ",
            "contradict",
            "conflicting",
            "inconsisten",
        ]
    ):
        plan.intent = "comparative_analysis"
        plan.comparison_mode = True

        if explicit.get("fact_type") is None:
            plan.fact_type = "contradiction"

    if any(
        token in q
        for token in [
            "пробел",
            "недостаточно исслед",
            "мало исслед",
            "не изучен",
            "неизучен",
            "требует дальнейш",
            "knowledge gap",
            "research gap",
            "underexplored",
        ]
    ):
        plan.intent = "fact_search"

    if any(
        token in q
        for token in [
            "патент",
            "изобретен",
            "patent",
            "патентн",
        ]
    ):
        plan.intent = "publication_search"

        if explicit.get("fact_type") is None:
            plan.fact_type = "publication_metadata"

    # ------------------------------------------------------------------
    # Geography from explicit wording.
    #
    # UI filter still has priority.
    # ------------------------------------------------------------------

    explicit_geo = explicit.get("geo_scope")

    ui_has_specific_geo = (
        explicit_geo is not None
        and explicit_geo != "all"
    )

    if not ui_has_specific_geo:
        if any(
            token in q
            for token in [
                "российск",
                "отечествен",
                "в россии",
                "россия",
            ]
        ):
            plan.geo_scope = "domestic"

        elif any(
            token in q
            for token in [
                "зарубежн",
                "за рубежом",
                "иностранн",
                "foreign",
            ]
        ):
            plan.geo_scope = "foreign"

    # ------------------------------------------------------------------
    # Years from explicit wording.
    #
    # UI year filters still have priority.
    # ------------------------------------------------------------------

    if explicit.get("year_from") is None:
        after_match = re.search(
            r"после\s+(19\d{2}|20\d{2})",
            q,
        )

        if after_match:
            plan.year_from = int(after_match.group(1)) + 1
        else:
            since_match = re.search(
                r"(?:начиная\s+с|с)\s+"
                r"(19\d{2}|20\d{2})",
                q,
            )

            if since_match:
                plan.year_from = int(since_match.group(1))

    if explicit.get("year_to") is None:
        match = re.search(
            r"(?:до|не\s+позднее)\s+"
            r"(19\d{2}|20\d{2})",
            q,
        )

        if match:
            plan.year_to = int(match.group(1))

    # Year range: 2018-2024 / 2018–2024 / 2018 по 2024
    range_match = re.search(
        r"\b(19\d{2}|20\d{2})"
        r"\s*(?:-|–|—|по)\s*"
        r"(19\d{2}|20\d{2})\b",
        q,
    )

    if range_match:
        if explicit.get("year_from") is None:
            plan.year_from = int(
                range_match.group(1)
            )

        if explicit.get("year_to") is None:
            plan.year_to = int(
                range_match.group(2)
            )

    # "за последние 5 лет"
    if explicit.get("year_from") is None:
        recent_match = re.search(
            r"за\s+последн\w*\s+"
            r"(\d+)\s+лет",
            q,
        )

        if recent_match:
            years = int(
                recent_match.group(1)
            )

            plan.year_from = (
                datetime.now().year - years
            )

    # ------------------------------------------------------------------
    # Chemical formulas.
    #
    # Explicit UI material remains authoritative.
    # ------------------------------------------------------------------

    if not explicit["materials"]:
        formula_pattern = (
            r"\b("
            r"SO2|SO3|H2S|CO2|NOx|"
            r"Ni|Cu|Au|Ag|Pt|Pd|Rh|Co|"
            r"Na2SO4|CaO|MgO"
            r")\b"
        )

        for match in re.findall(
            formula_pattern,
            original,
            flags=re.I,
        ):
            canonical = FORMULA_CANONICAL.get(
                match.lower(),
                match,
            )
            _append_unique(
                plan.materials,
                canonical,
            )

        for canonical, hints in MATERIAL_HINTS.items():
            if any(
                hint in q
                for hint in hints
            ):
                _append_unique(
                    plan.materials,
                    canonical,
                )

    # ------------------------------------------------------------------
    # Generic process phrases.
    #
    # This is intentionally lightweight.
    # It is not a four-domain classifier.
    # ------------------------------------------------------------------

    if not explicit["processes"]:
        process_patterns = [
            r"\b(кучн\w*\s+выщелачивани\w*)",
            r"\b(автоклавн\w*\s+выщелачивани\w*)",
            r"\b(подземн\w*\s+выщелачивани\w*)",
            r"\b(флотаци\w*)",
            r"\b(гидрометаллург\w*\s+переработк\w*)",
            r"\b(пирометаллург\w*\s+переработк\w*)",
            r"\b(удалени\w*\s+[A-Za-zА-Яа-я0-9₂₃₄+\-]+)",
        ]

        for pattern in process_patterns:
            match = re.search(
                pattern,
                q,
                flags=re.I,
            )

            if match:
                _append_unique(
                    plan.processes,
                    match.group(1),
                )

    return plan


# ---------------------------------------------------------------------------
# New: information score.
#
# Allows us to detect:
# valid JSON != useful QueryPlan
# ---------------------------------------------------------------------------

def plan_information_score(
    plan: QueryPlan,
) -> int:
    score = 0

    score += len(plan.materials) * 2
    score += len(plan.processes) * 2

    score += len(plan.equipment)
    score += len(plan.properties)

    if plan.geo_scope not in {
        "all",
        "unknown",
    }:
        score += 2

    if plan.year_from is not None:
        score += 2

    if plan.year_to is not None:
        score += 2

    if plan.numeric_constraints:
        score += 3

    if plan.comparison_mode:
        score += 2

    if plan.intent not in {
        "technology_review",
        "fact_search",
    }:
        score += 1

    if plan.fact_type is not None:
        score += 1

    return score


# ---------------------------------------------------------------------------
# New: sanitize LLM output.
#
# Crucial fix:
# Do NOT allow:
#   intent="удаление SO2 ..."
#   fact_type="methods"
#   fact_type="descriptions"
# ---------------------------------------------------------------------------

def sanitize_llm_data(
    data: dict[str, Any],
) -> dict[str, Any]:
    clean = dict(data)

    # LLM JSON is semantically useful but not always schema-perfect.
    # Coerce common single-item forms instead of rejecting the entire plan:
    #
    #   "processes": "autoclave leaching"
    #       -> ["autoclave leaching"]
    #
    #   "numeric_constraints": {...}
    #       -> [{...}]
    #
    # Invalid item types are dropped field-locally; they must not destroy
    # a valid deterministic fallback QueryPlan.
    list_fields = {
        "materials",
        "processes",
        "equipment",
        "properties",
        "countries",
        "group_by",
    }

    for field in list_fields:
        value = clean.get(field)

        if value is None:
            continue

        if isinstance(value, str):
            value = [value]
        elif not isinstance(value, (list, tuple, set)):
            logger.warning(
                "Dropping unsupported LLM list field=%s type=%s",
                field,
                type(value).__name__,
            )
            clean.pop(field, None)
            continue

        normalized: list[str] = []

        for item in value:
            if not isinstance(item, str):
                continue

            item = item.strip()

            if item and item not in normalized:
                normalized.append(item)

        clean[field] = normalized

    numeric_constraints = clean.get(
        "numeric_constraints"
    )

    if isinstance(numeric_constraints, dict):
        clean["numeric_constraints"] = [
            numeric_constraints
        ]
    elif (
        numeric_constraints is not None
        and not isinstance(
            numeric_constraints,
            list,
        )
    ):
        logger.warning(
            "Dropping unsupported LLM numeric_constraints type=%s",
            type(numeric_constraints).__name__,
        )
        clean.pop(
            "numeric_constraints",
            None,
        )

    intent = clean.get("intent")

    if (
        intent is not None
        and intent not in ALLOWED_INTENTS
    ):
        logger.warning(
            "Dropping unsupported LLM intent=%r",
            intent,
        )

        clean.pop("intent", None)

    fact_type = clean.get("fact_type")

    if (
        fact_type is not None
        and fact_type not in ALLOWED_FACT_TYPES
    ):
        logger.warning(
            "Dropping unsupported LLM fact_type=%r",
            fact_type,
        )

        clean.pop("fact_type", None)

    geo_scope = clean.get("geo_scope")

    if (
        geo_scope is not None
        and geo_scope not in ALLOWED_GEO_SCOPES
    ):
        logger.warning(
            "Dropping unsupported LLM geo_scope=%r",
            geo_scope,
        )

        clean.pop("geo_scope", None)

    return clean


# ---------------------------------------------------------------------------
# New: merge LLM output over deterministic plan.
#
# Empty LLM arrays cannot destroy useful deterministic values.
# ---------------------------------------------------------------------------

def merge_llm_plan(
    base: QueryPlan,
    data: dict[str, Any],
) -> dict[str, Any]:
    merged = base.model_dump()

    list_fields = {
        "materials",
        "processes",
        "equipment",
        "properties",
        "countries",
        "group_by",
    }

    for key, value in data.items():
        if key not in merged:
            continue

        if value is None:
            continue

        # Empty LLM lists must not erase
        # deterministic information.
        if isinstance(value, list) and not value:
            continue

        if (
            key in list_fields
            and isinstance(value, list)
        ):
            existing = list(
                merged.get(key) or []
            )

            # Deterministic entity signals are authoritative when present.
            # This prevents schema-valid but semantically bad LLM rewrites
            # such as "autofrettage leaching" from polluting a correctly
            # parsed Russian process phrase.
            if (
                key in {
                    "materials",
                    "processes",
                    "equipment",
                }
                and existing
            ):
                continue

            for item in value:
                if isinstance(item, str):
                    _append_unique(
                        existing,
                        item,
                    )

            merged[key] = existing
            continue

        # Numeric constraints:
        # deterministic parsing wins for the same numeric condition.
        # LLM may add constraints that deterministic parsing did not find,
        # but must not overwrite a correctly parsed value with a wrong
        # parameter label.
        if key == "numeric_constraints":
            if not value:
                continue

            existing = list(merged.get(key) or [])

            def numeric_signature(item: Any) -> tuple[Any, ...]:
                if hasattr(item, "model_dump"):
                    item = item.model_dump()

                if not isinstance(item, dict):
                    return (str(item),)

                return (
                    item.get("operator"),
                    item.get("value"),
                    item.get("value_min"),
                    item.get("value_max"),
                    item.get("unit"),
                )

            existing_signatures = {
                numeric_signature(item)
                for item in existing
            }

            for item in value:
                signature = numeric_signature(item)

                if signature not in existing_signatures:
                    existing.append(item)
                    existing_signatures.add(signature)

            merged[key] = existing
            continue

        merged[key] = value

    return merged


# ---------------------------------------------------------------------------
# Existing behavior preserved:
# explicit UI filters always win.
# ---------------------------------------------------------------------------

def apply_explicit_priority(
    req: ParseQueryRequest,
    merged: dict[str, Any],
) -> dict[str, Any]:
    explicit = _explicit_filters(req)

    scalar_fields = [
        "year_from",
        "year_to",
        "confidence_min",
        "geo_scope",
        "status",
        "fact_type",
        "verification_level",
        "source_type",
    ]

    for key in scalar_fields:
        value = explicit.get(key)

        if value is None:
            continue

        # Default "all" should not erase
        # query-derived geography.
        if (
            key == "geo_scope"
            and value == "all"
        ):
            continue

        merged[key] = value

    if explicit["countries"]:
        merged["countries"] = (
            explicit["countries"]
        )

    if explicit["materials"]:
        merged["materials"] = (
            explicit["materials"]
        )

    if explicit["processes"]:
        merged["processes"] = (
            explicit["processes"]
        )

    if explicit["numeric_constraints"]:
        merged["numeric_constraints"] = (
            explicit["numeric_constraints"]
        )

    return merged


# ---------------------------------------------------------------------------
# Query service
# ---------------------------------------------------------------------------

class QueryService:
    def __init__(self) -> None:
        self.llm = LLMClient()

    async def parse(
        self,
        req: ParseQueryRequest,
    ) -> QueryPlan:
        # ---------------------------------------------------------------
        # Layer 1:
        # preserve old fallback parser completely
        # ---------------------------------------------------------------

        fallback = fallback_parse(req)

        # ---------------------------------------------------------------
        # Layer 2:
        # deterministic generic enrichment
        # ---------------------------------------------------------------

        fallback = enrich_from_query(
            req,
            fallback,
        )

        fallback_score = plan_information_score(
            fallback
        )

        # Strong deterministic plans should never wait for the LLM.
        # Numeric/year/geo/contradiction/patent/expert queries are already
        # handled by deterministic rules and are more reliable this way.
        if fallback_score >= QUERY_DETERMINISTIC_FAST_SCORE:
            logger.info(
                "Returning strong deterministic QueryPlan without LLM. "
                "query=%r score=%d",
                req.query,
                fallback_score,
            )
            return fallback

        if not self.llm.available or QUERY_LLM_MAX_ATTEMPTS <= 0:
            logger.warning(
                "LLM unavailable/disabled; returning deterministic QueryPlan. "
                "query=%r score=%d",
                req.query,
                fallback_score,
            )

            return fallback

        system = (
            "Ты преобразуешь произвольный научно-технический "
            "запрос в строгий QueryPlan для поиска по R&D-корпусу "
            "горно-металлургической отрасли. "
            "Не ограничивайся заранее заданными доменами. "
            "Не придумывай значения enum. "
            "Не выдумывай фильтры. "
            "Верни только JSON."
        )

        user = f"""Запрос пользователя:
{req.query}

Явные UI-фильтры:
{json.dumps(
    req.filters.model_dump(exclude_none=True),
    ensure_ascii=False,
)}

Верни JSON с полями:
intent,
materials,
processes,
equipment,
properties,
geo_scope,
countries,
year_from,
year_to,
confidence_min,
status,
fact_type,
verification_level,
source_type,
numeric_constraints,
comparison_mode,
group_by.

Допустимые intent:
- technology_review
- fact_search
- experiment_search
- comparative_analysis
- expert_search
- publication_search
- numeric_search

Допустимые fact_type:
- technology_applicability
- process_condition
- experimental_result
- economic_indicator
- environmental_limit
- recommendation
- contradiction
- expert_competence
- publication_metadata

Правила:
- если подходящий fact_type неочевиден, верни null;
- НЕ придумывай новые intent;
- НЕ придумывай новые fact_type;
- materials/processes/equipment/properties —
  короткие нормализованные технические термины;
- сохраняй химические обозначения:
  SO2, SO3, H2S, Ni, Cu, Au, Ag, Pt;
- не подменяй неизвестный запрос одним из демо-сценариев;
- явные UI-фильтры имеют приоритет;
- geo_scope только:
  domestic|foreign|all|unknown;
- "российские", "отечественные", "в России"
  означает domestic;
- "после 2018 года"
  означает year_from=2019;
- "с 2018 года" или "начиная с 2018 года"
  означает year_from=2018;
- numeric_constraints:
  parameter, operator, value,
  value_min, value_max, unit;
- operator только:
  between|<|<=|>|>=|=;
- отсутствующие значения:
  null или пустые списки.
"""

        best_plan = fallback
        best_score = fallback_score

        # ---------------------------------------------------------------
        # Layer 3:
        # LLM enrichment with retry only when useful
        # ---------------------------------------------------------------

        for attempt in range(
            1,
            QUERY_LLM_MAX_ATTEMPTS + 1,
        ):
            try:
                raw_data = await asyncio.wait_for(
                    self.llm.complete_json(
                        system=system,
                        user=user,
                        max_tokens=1200,
                    ),
                    timeout=QUERY_LLM_TIMEOUT_SECONDS,
                )

                if not isinstance(
                    raw_data,
                    dict,
                ):
                    raise ValueError(
                        "LLM QueryPlan must be a JSON object, "
                        f"got {type(raw_data).__name__}"
                    )

                logger.info(
                    "Raw LLM QueryPlan. "
                    "attempt=%d query=%r data=%s",
                    attempt,
                    req.query,
                    json.dumps(
                        raw_data,
                        ensure_ascii=False,
                    ),
                )

                # -------------------------------------------------------
                # Protect schema from invented enums
                # -------------------------------------------------------

                data = sanitize_llm_data(
                    raw_data
                )

                # -------------------------------------------------------
                # Merge over deterministic fallback
                # -------------------------------------------------------

                merged = merge_llm_plan(
                    fallback,
                    data,
                )

                # -------------------------------------------------------
                # Explicit UI filters always win
                # -------------------------------------------------------

                merged = apply_explicit_priority(
                    req,
                    merged,
                )

                candidate = QueryPlan.model_validate(
                    merged
                )

                # -------------------------------------------------------
                # Reapply exact deterministic signals.
                #
                # Example:
                # "российские источники"
                # "после 2018 года"
                #
                # LLM should not lose them.
                # -------------------------------------------------------

                candidate = enrich_from_query(
                    req,
                    candidate,
                )

                # And UI wins again.
                candidate_data = (
                    candidate.model_dump()
                )

                candidate_data = (
                    apply_explicit_priority(
                        req,
                        candidate_data,
                    )
                )

                candidate = QueryPlan.model_validate(
                    candidate_data
                )

                score = plan_information_score(
                    candidate
                )

                logger.info(
                    "Parsed QueryPlan. "
                    "attempt=%d score=%d "
                    "intent=%s "
                    "materials=%s "
                    "processes=%s "
                    "geo_scope=%s "
                    "year_from=%s "
                    "year_to=%s "
                    "fact_type=%s",
                    attempt,
                    score,
                    candidate.intent,
                    candidate.materials,
                    candidate.processes,
                    candidate.geo_scope,
                    candidate.year_from,
                    candidate.year_to,
                    candidate.fact_type,
                )

                if score > best_score:
                    best_plan = candidate
                    best_score = score

                # -------------------------------------------------------
                # If already informative, stop.
                # -------------------------------------------------------

                if score >= 4:
                    return candidate

            except Exception as exc:
                logger.exception(
                    "LLM parse-query attempt failed. "
                    "attempt=%d/%d "
                    "query=%r "
                    "error_type=%s "
                    "error=%s",
                    attempt,
                    QUERY_LLM_MAX_ATTEMPTS,
                    req.query,
                    type(exc).__name__,
                    str(exc),
                )

        # ---------------------------------------------------------------
        # Important:
        # return the best deterministic/LLM combination,
        # not an empty generic fallback.
        # ---------------------------------------------------------------

        logger.warning(
            "Returning best available QueryPlan after bounded enrichment. "
            "query=%r score=%d attempts=%d timeout_s=%.1f",
            req.query,
            best_score,
            QUERY_LLM_MAX_ATTEMPTS,
            QUERY_LLM_TIMEOUT_SECONDS,
        )

        return best_plan