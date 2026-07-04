from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

from app.config import settings
from app.schemas import DocumentForExtraction, QueryPlan, RetrieveRequest, RetrieveResponse, RetrievedChunk


logger = logging.getLogger('uvicorn.error')


LEXICAL_STOPWORDS = {
    "и", "или", "а", "но", "в", "во", "на", "по", "из", "для", "при",
    "с", "со", "к", "ко", "от", "до", "за", "под", "над", "между",
    "как", "какие", "какой", "какая", "какое", "которые", "описаны",
    "найди", "найти", "покажи", "показать",
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "from",
}


def _norm_tokens(text: str) -> list[str]:
    tokens = re.findall(
        r'[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_+\-.]{1,}',
        text.lower(),
    )

    seen: list[str] = []
    for token in tokens:
        if token in LEXICAL_STOPWORDS:
            continue
        if token not in seen:
            seen.append(token)

    return seen[:24]


def _norm_token_query(text: str) -> str:
    tokens = _norm_tokens(text)
    return ' OR '.join(
        f'"{token.replace(chr(34), "")}"'
        for token in tokens
    )


def expand_query(query: str, plan: QueryPlan | None) -> str:
    if not plan:
        return query

    extras: list[str] = []

    # Countries are intentionally excluded from lexical expansion.
    # Geographic scope is not reliable metadata in the current chunk index.
    for values in (
        plan.materials,
        plan.processes,
        plan.equipment,
        plan.properties,
    ):
        extras.extend(
            str(v).strip()
            for v in values
            if str(v).strip()
        )

    q = query.casefold()

    # Generic task-mode expansion. These are morphology/intent variants,
    # not topic/domain hardcoding.
    if (
        getattr(plan, "fact_type", None) == "contradiction"
        or any(
            token in q
            for token in (
                "противореч",
                "расхожден",
                "несогласован",
                "contradict",
                "conflicting",
            )
        )
    ):
        extras.extend([
            "противоречие",
            "противоречивые данные",
            "расхождение",
            "различие результатов",
            "несогласованность",
            "conflicting results",
            "contradiction",
        ])

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
        extras.extend([
            "недостаточно исследовано",
            "требует дальнейших исследований",
            "не изучено",
            "ограниченность данных",
            "нерешенные вопросы",
            "research gap",
        ])

    if (
        getattr(plan, "intent", None) == "publication_search"
        or any(
            token in q
            for token in (
                "патент",
                "patent",
                "изобретен",
            )
        )
    ):
        extras.extend([
            "патент",
            "патенты",
            "патента",
            "патенте",
            "патентный",
            "изобретение",
            "изобретения",
            "заявка",
            "способ",
            "patent",
        ])

    unique: list[str] = []
    seen: set[str] = set()

    for item in extras:
        item = item.strip()
        key = item.casefold()

        if not item or key in seen:
            continue

        seen.add(key)
        unique.append(item)

    return query if not unique else query + '\n' + ' ; '.join(unique[:36])


MATERIAL_ALIASES: dict[str, tuple[str, ...]] = {
    "ni": ("ni", "никел", "nickel"),
    "cu": ("cu", "мед", "copper"),
    "co": ("co", "кобальт", "cobalt"),
    "zn": ("zn", "цинк", "zinc"),
    "fe": ("fe", "желез", "iron"),
    "au": ("au", "золот", "gold"),
    "ag": ("ag", "серебр", "silver"),
    "pt": ("pt", "платин", "platinum"),
    "pd": ("pd", "паллад", "palladium"),
    "rh": ("rh", "роди", "rhodium"),
    "al": ("al", "алюмин", "aluminium", "aluminum"),
    "mg": ("mg", "магни", "magnesium"),
    "ca": ("ca", "кальц", "calcium"),
    "na": ("na", "натри", "sodium"),
    "li": ("li", "лити", "lithium"),
    "mn": ("mn", "марган", "manganese"),
    "cr": ("cr", "хром", "chromium"),
    "pb": ("pb", "свин", "lead"),
    "sn": ("sn", "олов", "tin"),
    "ti": ("ti", "титан", "titanium"),
    "mo": ("mo", "молибден", "molybdenum"),
    "w": ("w", "вольфрам", "tungsten"),
}

PARAMETER_CONTEXT_HINTS: dict[str, tuple[str, ...]] = {
    "extraction_recovery": (
        "извлеч",
        "recovery",
        "yield",
        "выход",
        "степен",
        "η",
    ),
    "temperature": (
        "температур",
        "temperature",
        "t=",
        "t =",
        "нагрев",
    ),
    "pressure": (
        "давлен",
        "pressure",
        "p=",
        "p =",
    ),
    "flow_velocity": (
        "скорост",
        "velocity",
    ),
    "flow_rate": (
        "расход",
        "flow rate",
    ),
    "ph": (
        "ph",
        "водородн",
    ),
    "sulfate_concentration": (
        "сульфат",
        "sulfate",
        "so4",
    ),
    "chloride_concentration": (
        "хлорид",
        "chloride",
        "cl-",
    ),
    "calcium_concentration": (
        "кальц",
        "calcium",
    ),
    "magnesium_concentration": (
        "магни",
        "magnesium",
    ),
    "sodium_concentration": (
        "натри",
        "sodium",
    ),
    "dry_residue": (
        "сухой остаток",
        "tds",
        "минерализац",
    ),
}

UNIT_VALUE_PATTERNS: dict[str, str] = {
    "%": r"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*%",
    "degC": (
        r"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*"
        r"(?:°\s*[cсCС]|deg\s*c)"
    ),
    "MPa": (
        r"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*"
        r"(?:mpa|мпа)"
    ),
    "kPa": (
        r"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*"
        r"(?:kpa|кпа)"
    ),
    "mg/L": (
        r"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*"
        r"(?:mg/l|мг/л|мг/дм3|мг/дм³)"
    ),
    "g/L": (
        r"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*"
        r"(?:g/l|г/л)"
    ),
    "g/t": (
        r"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*"
        r"(?:g/t|г/т)"
    ),
    "m/s": (
        r"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*"
        r"(?:m/s|м/с)"
    ),
    "m3/h": (
        r"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*"
        r"(?:m3/h|м3/ч|м³/ч)"
    ),
}


def _material_patterns(materials: list[str]) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []

    for raw in materials:
        material = str(raw or "").strip()
        if not material:
            continue

        aliases = MATERIAL_ALIASES.get(
            material.lower(),
            (material,),
        )

        for alias in aliases:
            alias = alias.strip()
            if not alias:
                continue

            # Short Latin chemical symbols need strict token boundaries.
            if re.fullmatch(r"[A-Za-z]{1,3}", alias):
                pattern = (
                    rf"(?<![A-Za-zА-Яа-яЁё0-9])"
                    rf"{re.escape(alias)}"
                    rf"(?![A-Za-zА-Яа-яЁё0-9])"
                )
            else:
                # Russian aliases are often stems to cover inflection:
                # никел -> никель, никеля, никелевый.
                pattern = re.escape(alias)

            patterns.append(
                re.compile(pattern, flags=re.I)
            )

    return patterns


def _compare_numeric(
    value: float,
    constraint: Any,
) -> bool:
    operator = constraint.operator or "between"

    required_min = (
        constraint.value_min
        if constraint.value_min is not None
        else constraint.value
    )
    required_max = (
        constraint.value_max
        if constraint.value_max is not None
        else constraint.value
    )

    if operator == "between":
        if required_min is not None and value < required_min:
            return False
        if required_max is not None and value > required_max:
            return False
        return True

    target = (
        required_max
        if operator in {"<", "<="}
        else required_min
    )

    if target is None:
        return True

    if operator == "<":
        return value < target
    if operator == "<=":
        return value <= target
    if operator == ">":
        return value > target
    if operator == ">=":
        return value >= target
    if operator == "=":
        return abs(value - target) <= 1e-9

    return False


def _all_material_occurrences(
    text: str,
) -> list[tuple[int, str]]:
    """Return positions of known material mentions with canonical labels."""
    occurrences: list[tuple[int, str]] = []

    for canonical, aliases in MATERIAL_ALIASES.items():
        for alias in aliases:
            alias = alias.strip()
            if not alias:
                continue

            if re.fullmatch(r"[A-Za-z]{1,3}", alias):
                pattern = re.compile(
                    rf"(?<![A-Za-zА-Яа-яЁё0-9])"
                    rf"{re.escape(alias)}"
                    rf"(?![A-Za-zА-Яа-яЁё0-9])",
                    flags=re.I,
                )
            else:
                pattern = re.compile(
                    re.escape(alias),
                    flags=re.I,
                )

            occurrences.extend(
                (match.start(), canonical)
                for match in pattern.finditer(text)
            )

    return occurrences


def _requested_material_occurrences(
    text: str,
    materials: list[str],
) -> list[tuple[int, str]]:
    occurrences: list[tuple[int, str]] = []

    for raw in materials:
        material = str(raw or "").strip()
        if not material:
            continue

        canonical = material.lower()
        aliases = MATERIAL_ALIASES.get(
            canonical,
            (material,),
        )

        for alias in aliases:
            alias = alias.strip()
            if not alias:
                continue

            if re.fullmatch(r"[A-Za-z]{1,3}", alias):
                pattern = re.compile(
                    rf"(?<![A-Za-zА-Яа-яЁё0-9])"
                    rf"{re.escape(alias)}"
                    rf"(?![A-Za-zА-Яа-яЁё0-9])",
                    flags=re.I,
                )
            else:
                pattern = re.compile(
                    re.escape(alias),
                    flags=re.I,
                )

            occurrences.extend(
                (match.start(), canonical)
                for match in pattern.finditer(text)
            )

    return occurrences


def _constraint_matches(
    text: str,
    constraint: Any,
) -> list[tuple[int, int, float]]:
    """Find textual numeric values that satisfy one constraint."""
    unit = str(constraint.unit or "")
    value_pattern = UNIT_VALUE_PATTERNS.get(unit)

    if not value_pattern:
        # Unsupported unit: cannot perform local textual validation.
        return []

    matches: list[tuple[int, int, float]] = []

    for match in re.finditer(
        value_pattern,
        text,
        flags=re.I,
    ):
        try:
            value = float(
                match.group("value").replace(",", ".")
            )
        except (TypeError, ValueError):
            continue

        if _compare_numeric(value, constraint):
            matches.append(
                (match.start(), match.end(), value)
            )

    return matches


def _has_parameter_context(
    text: str,
    start: int,
    end: int,
    constraint: Any,
) -> bool:
    hints = PARAMETER_CONTEXT_HINTS.get(
        str(constraint.parameter or ""),
        (),
    )

    # Engineering units themselves are strong enough for these parameters.
    if str(constraint.parameter or "") in {
        "temperature",
        "pressure",
        "flow_velocity",
        "flow_rate",
        "ph",
    }:
        return True

    if not hints:
        return True

    context = text[
        max(0, start - 180):
        min(len(text), end + 120)
    ].lower()

    return any(
        hint in context
        for hint in hints
    )


def _requested_material_is_nearest(
    numeric_pos: int,
    requested_canonical: str,
    all_materials: list[tuple[int, str]],
    *,
    max_distance: int,
) -> bool:
    nearby = [
        (abs(numeric_pos - pos), canonical)
        for pos, canonical in all_materials
        if abs(numeric_pos - pos) <= max_distance
    ]

    if not nearby:
        return False

    nearby.sort(
        key=lambda item: item[0]
    )

    nearest_distance, nearest_material = nearby[0]

    if nearest_material == requested_canonical:
        return True

    # Allow a near-tie only when the requested material is essentially
    # co-located with the closest mention (e.g. compact analytical tables).
    requested_distances = [
        distance
        for distance, canonical in nearby
        if canonical == requested_canonical
    ]

    return bool(
        requested_distances
        and min(requested_distances) <= nearest_distance + 18
    )


def _passes_contextual_numeric_guard(
    meta: dict[str, Any],
    plan: QueryPlan | None,
) -> bool:
    """Require all numeric constraints in one local passage around one material.

    This is intentionally stronger than chunk-level intersection:
    - all constraints must be satisfied near the same requested-material anchor;
    - all matched values must lie in one compact passage;
    - recovery percentages must be locally associated with the requested
      material rather than a closer Cu/Co/Zn/etc. mention.
    """
    if (
        plan is None
        or not plan.numeric_constraints
        or not plan.materials
    ):
        return True

    chunk_text = str(meta.get("text") or "")
    if not chunk_text:
        return False

    requested_occurrences = _requested_material_occurrences(
        chunk_text,
        list(plan.materials),
    )

    if not requested_occurrences:
        return False

    all_materials = _all_material_occurrences(
        chunk_text
    )

    constraint_matches: list[
        tuple[Any, list[tuple[int, int, float]]]
    ] = []

    for constraint in plan.numeric_constraints:
        matches = _constraint_matches(
            chunk_text,
            constraint,
        )

        # If a unit is unsupported by the textual guard, fall back to the
        # existing SQLite numeric eligibility filter for that constraint.
        if (
            not matches
            and str(constraint.unit or "")
            not in UNIT_VALUE_PATTERNS
        ):
            continue

        if not matches:
            return False

        constraint_matches.append(
            (constraint, matches)
        )

    if not constraint_matches:
        return True

    anchor_radius = 360
    max_numeric_span = 520

    for anchor_pos, requested_canonical in requested_occurrences:
        selected_positions: list[int] = []
        all_constraints_pass = True

        for constraint, matches in constraint_matches:
            eligible: list[tuple[int, int, float]] = []

            for start, end, value in matches:
                numeric_pos = start

                if abs(numeric_pos - anchor_pos) > anchor_radius:
                    continue

                if not _has_parameter_context(
                    chunk_text,
                    start,
                    end,
                    constraint,
                ):
                    continue

                # For extraction/recovery percentages, reject cases such as:
                # "Co 96.4%, Cu 93.3%, Zn 92.2%" merely because Ni is
                # mentioned elsewhere in the same chunk.
                if (
                    str(constraint.parameter or "")
                    == "extraction_recovery"
                    and not _requested_material_is_nearest(
                        numeric_pos,
                        requested_canonical,
                        all_materials,
                        max_distance=220,
                    )
                ):
                    continue

                eligible.append(
                    (start, end, value)
                )

            if not eligible:
                all_constraints_pass = False
                break

            # Pick the value nearest to the shared material anchor.
            best = min(
                eligible,
                key=lambda item: abs(item[0] - anchor_pos),
            )

            selected_positions.append(
                best[0]
            )

        if not all_constraints_pass:
            continue

        local_positions = selected_positions + [anchor_pos]

        if (
            max(local_positions) - min(local_positions)
            <= max_numeric_span
        ):
            return True

    return False



def _coerce_meta_year(value: Any) -> int | None:
    try:
        if value is None:
            return None
        year = int(value)
        return year if 1900 <= year <= 2100 else None
    except (TypeError, ValueError):
        return None


def _passes_plan_filters(
    meta: dict[str, Any],
    plan: QueryPlan | None,
) -> bool:
    if plan is None:
        return True

    year = _coerce_meta_year(meta.get("year"))

    # Year constraints are strict: unknown-year documents do not satisfy
    # an explicit temporal filter.
    if plan.year_from is not None:
        if year is None or year < plan.year_from:
            return False

    if plan.year_to is not None:
        if year is None or year > plan.year_to:
            return False

    if plan.source_type:
        source_type = str(meta.get("source_type") or "").strip().lower()
        if source_type != str(plan.source_type).strip().lower():
            return False

    return True



class HFSequenceReranker:
    """Reliable sequence-classification reranker with a CrossEncoder-like API."""

    def __init__(self, model_name: str) -> None:
        import torch
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        self.torch = torch
        self.model_name = model_name
        self.max_length = int(
            os.getenv("RERANKER_MAX_LENGTH", "512")
        )
        self.batch_size = int(
            os.getenv("RERANKER_BATCH_SIZE", "8")
        )

        if torch.cuda.is_available():
            self.device = torch.device("cuda")
        else:
            self.device = torch.device("cpu")

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name
        )
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model_name
        )
        self.model.to(self.device)
        self.model.eval()

        logger.info(
            "Reranker loaded model=%s device=%s max_length=%s batch_size=%s",
            model_name,
            self.device,
            self.max_length,
            self.batch_size,
        )

    def predict(
        self,
        pairs: list[tuple[str, str]],
    ) -> np.ndarray:
        if not pairs:
            return np.asarray([], dtype="float32")

        scores: list[float] = []

        for start in range(
            0,
            len(pairs),
            self.batch_size,
        ):
            batch = pairs[
                start:start + self.batch_size
            ]

            inputs = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )

            inputs = {
                key: value.to(self.device)
                for key, value in inputs.items()
            }

            with self.torch.no_grad():
                logits = self.model(
                    **inputs,
                    return_dict=True,
                ).logits

            values = (
                logits.view(-1)
                .detach()
                .float()
                .cpu()
                .numpy()
            )
            scores.extend(
                float(value)
                for value in values
            )

        return np.asarray(
            scores,
            dtype="float32",
        )


class HybridRetriever:
    def __init__(self) -> None:
        self.index_dir = Path(settings.retrieval_index_dir)
        self.uploaded_dir = self.index_dir / "uploaded-documents"
        self._loaded = False
        self._lock = Lock()

        self.base_metadata: list[dict[str, Any]] = []
        self.base_count = 0
        self.uploaded_metadata: list[dict[str, Any]] = []
        self.uploaded_embeddings: np.ndarray | None = None
        self.metadata: list[dict[str, Any]] = []

        self.faiss_index = None
        self.encoder = None
        self.reranker = None
        self.config: dict[str, Any] = {}
        self.chunk_to_index: dict[str, int] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            config_path = self.index_dir / 'index_config.json'
            if config_path.exists():
                self.config = json.loads(config_path.read_text(encoding='utf-8'))
            metadata_path = self.index_dir / 'metadata.jsonl'
            if metadata_path.exists():
                with metadata_path.open('r', encoding='utf-8') as f:
                    self.base_metadata = [
                        json.loads(line)
                        for line in f
                        if line.strip()
                    ]
            self.base_count = len(self.base_metadata)
            self.metadata = list(self.base_metadata)

            dense_path = self.index_dir / 'dense.faiss'
            if settings.dense_enabled and dense_path.exists():
                try:
                    import faiss
                    self.faiss_index = faiss.read_index(str(dense_path))
                    from sentence_transformers import SentenceTransformer
                    model_name = self.config.get('embedding_model') or settings.embedding_model
                    self.encoder = SentenceTransformer(model_name)
                    logger.info(
                        "Dense retrieval loaded index=%s model=%s count=%s",
                        dense_path,
                        model_name,
                        getattr(self.faiss_index, 'ntotal', None),
                    )
                except Exception as exc:
                    logger.exception(
                        "Dense retrieval load failed: %s",
                        exc,
                    )
                    self.faiss_index = None
                    self.encoder = None

            if settings.reranker_model:
                try:
                    self.reranker = HFSequenceReranker(
                        settings.reranker_model
                    )
                except Exception as exc:
                    logger.exception(
                        "Reranker load failed model=%s: %s",
                        settings.reranker_model,
                        exc,
                    )
                    self.reranker = None
            self._reload_uploaded_locked()
            self._loaded = True

    @staticmethod
    def _uploaded_document_key(document_id: str) -> str:
        digest = hashlib.sha1(
            document_id.encode("utf-8", errors="ignore")
        ).hexdigest()[:20]
        return f"doc_{digest}"

    def _reload_uploaded_locked(self) -> None:
        """Reload persistent user-uploaded chunks into the in-memory overlay."""
        self.uploaded_dir.mkdir(parents=True, exist_ok=True)

        uploaded_meta: list[dict[str, Any]] = []
        vector_parts: list[np.ndarray] = []

        for meta_path in sorted(self.uploaded_dir.glob("*.json")):
            try:
                payload = json.loads(
                    meta_path.read_text(encoding="utf-8")
                )
                chunks = payload.get("chunks") or []
                chunks = [
                    item
                    for item in chunks
                    if isinstance(item, dict)
                    and str(item.get("chunk_id") or "").strip()
                    and str(item.get("text") or "").strip()
                ]
                if not chunks:
                    continue

                vectors: np.ndarray | None = None
                vector_path = meta_path.with_suffix(".npy")

                if self.encoder is not None:
                    if vector_path.exists():
                        try:
                            loaded = np.load(
                                vector_path,
                                allow_pickle=False,
                            )
                            if (
                                loaded.ndim == 2
                                and loaded.shape[0] == len(chunks)
                            ):
                                vectors = np.asarray(
                                    loaded,
                                    dtype="float32",
                                )
                        except Exception:
                            vectors = None

                    if vectors is None:
                        prefix = self.config.get(
                            "passage_prefix",
                            settings.embedding_passage_prefix,
                        )
                        vectors = self.encoder.encode(
                            [
                                prefix + str(item.get("text") or "")
                                for item in chunks
                            ],
                            normalize_embeddings=True,
                            convert_to_numpy=True,
                            show_progress_bar=False,
                        ).astype("float32")
                        with vector_path.open("wb") as fh:
                            np.save(fh, vectors)

                uploaded_meta.extend(chunks)
                if self.encoder is not None:
                    assert vectors is not None
                    vector_parts.append(vectors)

            except Exception as exc:
                logger.exception(
                    "Uploaded retrieval document load failed path=%s: %s",
                    meta_path,
                    exc,
                )

        self.uploaded_metadata = uploaded_meta
        self.base_count = len(self.base_metadata)
        self.metadata = (
            list(self.base_metadata)
            + list(self.uploaded_metadata)
        )

        self.chunk_to_index = {
            str(item.get("chunk_id")): idx
            for idx, item in enumerate(self.metadata)
            if item.get("chunk_id")
        }

        if vector_parts:
            self.uploaded_embeddings = np.vstack(
                vector_parts
            ).astype("float32")
        else:
            self.uploaded_embeddings = None

        logger.info(
            "Uploaded retrieval overlay loaded documents=%s chunks=%s dense=%s",
            len(list(self.uploaded_dir.glob("*.json"))),
            len(self.uploaded_metadata),
            self.uploaded_embeddings is not None,
        )

    @staticmethod
    def _compact_numeric_value(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "parameter": value.get("parameter"),
            "value": value.get("value"),
            "value_min": value.get("value_min"),
            "value_max": value.get("value_max"),
            "comparator": value.get("comparator"),
            "unit": (
                value.get("unit_normalized")
                or value.get("unit")
                or value.get("unit_original")
            ),
        }

    def index_document(
        self,
        document: DocumentForExtraction,
    ) -> dict[str, Any]:
        """Upsert a user document into a persistent retrieval overlay."""
        self._load()

        from app.extraction_service import build_chunks
        from pipeline.extract_numeric_values import find_numeric_values

        raw_chunks = build_chunks(document)
        if not raw_chunks:
            raise ValueError(
                "Document contains no extractable text chunks"
            )

        chunks: list[dict[str, Any]] = []
        for chunk in raw_chunks:
            numeric_values = find_numeric_values(
                chunk,
                include_length_units=False,
            )
            chunks.append({
                "chunk_id": chunk["chunk_id"],
                "document_id": document.document_id,
                "filename": document.filename,
                "source_type": document.source_type,
                "access_level": document.access_level,
                "year": document.metadata.year,
                "country": document.metadata.country,
                "organization": document.metadata.organization,
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "text": chunk.get("text") or "",
                "numeric_values": [
                    self._compact_numeric_value(value)
                    for value in numeric_values
                ],
                "uploaded": True,
            })

        vectors: np.ndarray | None = None
        if self.encoder is not None:
            prefix = self.config.get(
                "passage_prefix",
                settings.embedding_passage_prefix,
            )
            vectors = self.encoder.encode(
                [
                    prefix + str(item.get("text") or "")
                    for item in chunks
                ],
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            ).astype("float32")

        self.uploaded_dir.mkdir(
            parents=True,
            exist_ok=True,
        )
        key = self._uploaded_document_key(
            document.document_id
        )
        meta_path = self.uploaded_dir / f"{key}.json"
        vector_path = self.uploaded_dir / f"{key}.npy"

        payload = {
            "document_id": document.document_id,
            "filename": document.filename,
            "chunks": chunks,
        }

        meta_tmp = meta_path.with_suffix(".json.tmp")
        meta_tmp.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(meta_tmp, meta_path)

        if vectors is not None:
            vector_tmp = vector_path.with_suffix(".npy.tmp")
            with vector_tmp.open("wb") as fh:
                np.save(fh, vectors)
            os.replace(vector_tmp, vector_path)
        elif vector_path.exists():
            vector_path.unlink()

        with self._lock:
            self._reload_uploaded_locked()

        return {
            "status": "indexed",
            "document_id": document.document_id,
            "indexed_chunks": len(chunks),
            "dense_indexed": vectors is not None,
            "persistent_path": str(meta_path),
        }

    def _dense(self, query: str, k: int) -> dict[int, float]:
        if self.encoder is None:
            return {}

        prefix = self.config.get(
            "query_prefix",
            settings.embedding_query_prefix,
        )
        vector = self.encoder.encode(
            [prefix + query],
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype("float32")

        result: dict[int, float] = {}

        if self.faiss_index is not None:
            scores, ids = self.faiss_index.search(
                vector,
                min(k, max(1, int(self.faiss_index.ntotal))),
            )
            for idx, score in zip(ids[0], scores[0]):
                if idx >= 0:
                    result[int(idx)] = float(score)

        if (
            self.uploaded_embeddings is not None
            and len(self.uploaded_embeddings) > 0
        ):
            similarities = (
                self.uploaded_embeddings @ vector[0]
            )
            take = min(k, len(similarities))
            if take > 0:
                top = np.argpartition(
                    similarities,
                    -take,
                )[-take:]
                top = top[
                    np.argsort(
                        similarities[top]
                    )[::-1]
                ]
                for local_idx in top:
                    result[
                        self.base_count + int(local_idx)
                    ] = float(similarities[local_idx])

        return result

    def _lexical(self, query: str, k: int) -> dict[int, float]:
        result: dict[int, float] = {}

        db_path = self.index_dir / "lexical.sqlite3"
        if settings.lexical_enabled and db_path.exists():
            fts_query = _norm_token_query(query)
            if fts_query:
                try:
                    with sqlite3.connect(str(db_path)) as conn:
                        rows = conn.execute(
                            "SELECT rowid, bm25(chunks_fts) AS rank "
                            "FROM chunks_fts "
                            "WHERE chunks_fts MATCH ? "
                            "ORDER BY rank LIMIT ?",
                            (fts_query, k),
                        ).fetchall()
                    result.update({
                        int(rowid) - 1: float(-rank)
                        for rowid, rank in rows
                        if rowid > 0
                    })
                except sqlite3.Error:
                    pass

        # Uploaded documents are expected to be a small overlay, therefore a
        # deterministic in-memory lexical scan is cheaper and safer than
        # mutating the 682 MB base FTS artifact at runtime.
        tokens = _norm_tokens(query)
        if tokens and self.uploaded_metadata:
            scored: list[tuple[int, float]] = []
            query_cf = query.casefold().strip()

            for local_idx, meta in enumerate(
                self.uploaded_metadata
            ):
                text = str(meta.get("text") or "")
                text_cf = text.casefold()
                score = 0.0

                for token in tokens:
                    count = text_cf.count(token)
                    if count:
                        score += 1.0 + min(count, 8) * 0.15

                if query_cf and query_cf in text_cf:
                    score += 4.0

                if score > 0:
                    scored.append((
                        self.base_count + local_idx,
                        score,
                    ))

            for idx, score in sorted(
                scored,
                key=lambda item: item[1],
                reverse=True,
            )[:k]:
                result[idx] = max(
                    result.get(idx, float("-inf")),
                    float(score),
                )

        return result

    @staticmethod
    def _constraint_sql(constraint, idx: int) -> tuple[str, list[Any]]:
        clauses = ['parameter = ?']
        params: list[Any] = [constraint.parameter]
        if constraint.unit:
            clauses.append('unit = ?')
            params.append(constraint.unit)
        operator = constraint.operator or 'between'
        required_min = constraint.value_min if constraint.value_min is not None else constraint.value
        required_max = constraint.value_max if constraint.value_max is not None else constraint.value
        if operator == 'between':
            if required_min is not None:
                clauses.append('coalesce(value_max, value, 1.0e18) >= ?')
                params.append(required_min)
            if required_max is not None:
                clauses.append('coalesce(value_min, value, -1.0e18) <= ?')
                params.append(required_max)
        elif operator in {'<=', '<'}:
            limit = required_max if required_max is not None else required_min
            if limit is not None:
                clauses.append(f'coalesce(value_max, value, 1.0e18) {operator} ?')
                params.append(limit)
        elif operator in {'>=', '>'}:
            limit = required_min if required_min is not None else required_max
            if limit is not None:
                clauses.append(f'coalesce(value_min, value, -1.0e18) {operator} ?')
                params.append(limit)
        elif operator == '=':
            target = constraint.value if constraint.value is not None else required_min if required_min is not None else required_max
            if target is not None:
                clauses.append('coalesce(value_min, value, -1.0e18) <= ?')
                clauses.append('coalesce(value_max, value, 1.0e18) >= ?')
                params.extend([target, target])
        return ' AND '.join(clauses), params

    @staticmethod
    def _uploaded_numeric_value_matches(
        value: dict[str, Any],
        constraint: Any,
    ) -> bool:
        if str(value.get("parameter") or "") != str(
            constraint.parameter
        ):
            return False

        if constraint.unit:
            unit = str(value.get("unit") or "")
            if unit != str(constraint.unit):
                return False

        candidates = [
            value.get("value"),
            value.get("value_min"),
            value.get("value_max"),
        ]
        candidates = [
            float(item)
            for item in candidates
            if item is not None
        ]
        return any(
            _compare_numeric(item, constraint)
            for item in candidates
        )

    def _numeric_matches(
        self,
        plan: QueryPlan | None,
    ) -> set[int] | None:
        constraints = (
            list(plan.numeric_constraints)
            if plan
            else []
        )
        if not constraints:
            return None

        matched_sets: list[set[int]] = []

        db_path = self.index_dir / "lexical.sqlite3"
        if db_path.exists():
            try:
                with sqlite3.connect(str(db_path)) as conn:
                    for idx, constraint in enumerate(
                        constraints
                    ):
                        where, params = self._constraint_sql(
                            constraint,
                            idx,
                        )
                        rows = conn.execute(
                            f"SELECT DISTINCT chunk_id "
                            f"FROM numeric_values WHERE {where} "
                            f"LIMIT 20000",
                            params,
                        ).fetchall()
                        matched_sets.append({
                            self.chunk_to_index[cid]
                            for (cid,) in rows
                            if cid in self.chunk_to_index
                        })
            except sqlite3.Error:
                matched_sets = [
                    set()
                    for _ in constraints
                ]
        else:
            matched_sets = [
                set()
                for _ in constraints
            ]

        # Merge numeric eligibility from the persistent uploaded overlay.
        for constraint_idx, constraint in enumerate(
            constraints
        ):
            uploaded_matches: set[int] = set()

            for local_idx, meta in enumerate(
                self.uploaded_metadata
            ):
                values = meta.get("numeric_values") or []
                if any(
                    isinstance(value, dict)
                    and self._uploaded_numeric_value_matches(
                        value,
                        constraint,
                    )
                    for value in values
                ):
                    uploaded_matches.add(
                        self.base_count + local_idx
                    )

            matched_sets[
                constraint_idx
            ].update(uploaded_matches)

        if not matched_sets:
            return None

        result = matched_sets[0]
        for values in matched_sets[1:]:
            result = result.intersection(values)
        return result

    def search(self, req: RetrieveRequest) -> RetrieveResponse:
        self._load()
        expanded = expand_query(req.query, req.query_plan)
        warnings: list[str] = []
        if not self.metadata:
            return RetrieveResponse(query=req.query, expanded_query=expanded, hits=[], warnings=['Retrieval index is not built.'])

        has_strict_filters = bool(
            req.query_plan
            and (
                req.query_plan.year_from is not None
                or req.query_plan.year_to is not None
                or req.query_plan.source_type
                or req.query_plan.numeric_constraints
            )
        )
        filter_multiplier = 10 if has_strict_filters else 1

        dense = self._dense(
            expanded,
            max(settings.retrieval_dense_k, req.top_k * filter_multiplier),
        )

        lexical_k = max(
            settings.retrieval_lexical_k,
            req.top_k * filter_multiplier,
        )

        # Original user wording is the precision branch.
        lexical_original = self._lexical(
            req.query,
            lexical_k,
        )

        # QueryPlan expansion is a recall branch. It is fused separately,
        # so noisy expansion cannot fully replace the original ranking.
        lexical_expanded = {}
        if expanded.strip() != req.query.strip():
            lexical_expanded = self._lexical(
                expanded,
                lexical_k,
            )

        numeric_matches = self._numeric_matches(req.query_plan)

        if not dense:
            warnings.append('Dense retrieval unavailable; using lexical branch only.')
        if not lexical_original and not lexical_expanded:
            warnings.append('Lexical retrieval unavailable or returned no hits.')
        if req.query_plan and req.query_plan.geo_scope not in {'all', 'unknown'}:
            warnings.append(
                'Geography is used for query expansion only; strict geographic filtering '
                'is unavailable because the current chunk index has no reliable country metadata.'
            )

        rrf: dict[int, float] = {}

        # Precision branch gets a modestly higher weight.
        ranked_branches = (
            (dense, 1.0),
            (lexical_original, 1.35),
            (lexical_expanded, 0.75),
        )

        for scores, weight in ranked_branches:
            ranked = sorted(
                scores.items(),
                key=lambda x: x[1],
                reverse=True,
            )
            for rank, (idx, _) in enumerate(ranked, start=1):
                rrf[idx] = (
                    rrf.get(idx, 0.0)
                    + weight / (settings.retrieval_rrf_k + rank)
                )
        if numeric_matches is not None:
            # IMPORTANT:
            # Numeric matches are a strict eligibility filter, not a ranking
            # branch. Ranking them by sorted internal chunk index would inject
            # arbitrary order into RRF and promote unrelated chunks.
            if not numeric_matches:
                warnings.append('Numeric constraints matched no indexed chunks.')
            else:
                warnings.append(
                    f'Numeric constraints matched {len(numeric_matches)} indexed chunks; '
                    'topical ranking is determined by lexical/dense relevance.'
                )

                if (
                    req.query_plan
                    and req.query_plan.materials
                ):
                    warnings.append(
                        'Contextual numeric guard is active: all numeric constraints must be '
                        'satisfied in one local passage around the same requested-material anchor.'
                    )

                if (
                    req.query_plan
                    and len(req.query_plan.numeric_constraints) > 1
                ):
                    warnings.append(
                        'Multiple numeric constraints are enforced in one compact material-anchored passage; '
                        'this is stronger than chunk-level intersection but still does not prove '
                        'a shared Experiment node.'
                    )

        if not rrf:
            if numeric_matches is not None and numeric_matches:
                warnings.append(
                    'Numeric matches exist, but no topical lexical/dense candidates were found; '
                    'returning no hits rather than arbitrary numeric-only matches.'
                )
            return RetrieveResponse(
                query=req.query,
                expanded_query=expanded,
                hits=[],
                warnings=warnings,
            )

        allowed = set(req.visible_access_levels)
        candidates = []

        # Avoid letting one long document occupy the whole top-k.
        max_chunks_per_document = 2
        document_counts: dict[str, int] = {}

        for idx, score in sorted(
            rrf.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            if not (0 <= idx < len(self.metadata)):
                continue
            if numeric_matches is not None and idx not in numeric_matches:
                continue

            meta = self.metadata[idx]
            level = meta.get('access_level') or 'internal'

            if level not in allowed:
                continue
            if not _passes_plan_filters(meta, req.query_plan):
                continue
            if not _passes_contextual_numeric_guard(
                meta,
                req.query_plan,
            ):
                continue

            document_key = str(
                meta.get('document_id')
                or meta.get('filename')
                or meta.get('chunk_id')
                or idx
            )

            if document_counts.get(document_key, 0) >= max_chunks_per_document:
                continue

            candidates.append((idx, score))
            document_counts[document_key] = (
                document_counts.get(document_key, 0) + 1
            )

            if len(candidates) >= max(
                req.top_k,
                settings.retrieval_rerank_pool,
            ):
                break

        rerank_scores: dict[int, float] = {}
        final_scores: dict[int, float] = {}

        if self.reranker and candidates:
            # Keep the original fused retrieval rank as a guardrail.
            # A cross-encoder is powerful but can over-promote OCR-noisy chunks
            # when used as the sole final score.
            retrieval_rank = {
                idx: rank
                for rank, (idx, _) in enumerate(candidates, start=1)
            }

            pairs = [
                (
                    req.query,
                    self.metadata[idx].get('text') or '',
                )
                for idx, _ in candidates
            ]

            try:
                values = self.reranker.predict(pairs)

                rerank_scores = {
                    idx: float(value)
                    for (idx, _), value
                    in zip(candidates, values)
                }

                reranked = sorted(
                    candidates,
                    key=lambda x: rerank_scores.get(
                        x[0],
                        float('-inf'),
                    ),
                    reverse=True,
                )

                rerank_rank = {
                    idx: rank
                    for rank, (idx, _)
                    in enumerate(reranked, start=1)
                }

                # Rank fusion is robust to arbitrary cross-encoder logit scales.
                # Reranker dominates, retrieval remains a precision guardrail.
                retrieval_weight = float(
                    os.getenv(
                        "RERANK_RETRIEVAL_WEIGHT",
                        "0.35",
                    )
                )
                reranker_weight = float(
                    os.getenv(
                        "RERANK_MODEL_WEIGHT",
                        "0.65",
                    )
                )
                fusion_k = float(
                    os.getenv(
                        "RERANK_FUSION_K",
                        "60",
                    )
                )

                for idx, fused_score in candidates:
                    final_scores[idx] = (
                        retrieval_weight
                        / (
                            fusion_k
                            + retrieval_rank[idx]
                        )
                        + reranker_weight
                        / (
                            fusion_k
                            + rerank_rank[idx]
                        )
                    )

                candidates = sorted(
                    candidates,
                    key=lambda x: final_scores.get(
                        x[0],
                        float('-inf'),
                    ),
                    reverse=True,
                )

            except Exception as exc:
                logger.exception(
                    "Reranker inference failed: %s",
                    exc,
                )
                warnings.append(
                    f'Reranker failed: {type(exc).__name__}'
                )

        hits: list[RetrievedChunk] = []
        for idx, fused_score in candidates[:req.top_k]:
            meta = self.metadata[idx]
            hits.append(RetrievedChunk(
                chunk_id=str(meta.get('chunk_id') or meta.get('id') or idx),
                document_id=meta.get('document_id'),
                score=float(
                    final_scores.get(
                        idx,
                        fused_score,
                    )
                ),
                dense_score=dense.get(idx),
                lexical_score=max(
                    lexical_original.get(idx, float("-inf")),
                    lexical_expanded.get(idx, float("-inf")),
                )
                if (
                    idx in lexical_original
                    or idx in lexical_expanded
                )
                else None,
                reranker_score=rerank_scores.get(idx),
                filename=meta.get('filename'),
                source_type=meta.get('source_type'),
                access_level=meta.get('access_level') or 'internal',
                page_start=meta.get('page_start') or meta.get('page'),
                page_end=meta.get('page_end') or meta.get('page'),
                text=meta.get('text') or '',
            ))
        return RetrieveResponse(query=req.query, expanded_query=expanded, hits=hits, warnings=warnings)
