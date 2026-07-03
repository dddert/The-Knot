from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tqdm import tqdm


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


import re


def normalize_text(text: str) -> str:
    """
    Нормализация текста после PDF/DOCX/PPTX парсинга.
    Цель:
    - убрать переносы внутри фраз;
    - склеить слова вида "медно-\nникелевых";
    - убрать одиночные номера страниц;
    - сохранить абзацы там, где они действительно нужны.
    """
    if not text:
        return ""

    text = text.replace("\x00", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")

    raw_lines = []
    for line in text.split("\n"):
        line = " ".join(line.split()).strip()

        if not line:
            raw_lines.append("")
            continue

        # Убираем одиночные номера страниц: "2", "15", "| 4"
        if re.fullmatch(r"\|?\s*\d{1,4}\s*", line):
            continue

        # Убираем частые декоративные маркеры
        line = line.replace("", "•").strip()

        raw_lines.append(line)

    paragraphs = []
    buffer = ""

    def flush():
        nonlocal buffer
        if buffer.strip():
            paragraphs.append(buffer.strip())
        buffer = ""

    def looks_like_heading(line: str) -> bool:
        if len(line) > 120:
            return False

        letters = [ch for ch in line if ch.isalpha()]
        if not letters:
            return False

        upper_share = sum(ch.isupper() for ch in letters) / max(len(letters), 1)

        # Заголовки часто короткие и в верхнем регистре
        return upper_share > 0.65 and len(line) <= 80

    def starts_new_item(line: str) -> bool:
        return bool(re.match(r"^(\d+[\.\)]|[•\-])\s+", line))

    for line in raw_lines:
        if line == "":
            flush()
            continue

        if not buffer:
            buffer = line
            continue

        # Склейка переносов внутри слов: "медно-" + "никелевых"
        if buffer.endswith("-") and line and line[0].islower():
            buffer = buffer + line
            continue

        # Новый абзац для явных заголовков и пунктов списка
        if looks_like_heading(line) or starts_new_item(line):
            flush()
            buffer = line
            continue

        # Если предыдущая строка заканчивается пунктуацией, вероятно новый смысловой кусок
        if re.search(r"[.!?;:]$", buffer) and len(buffer) > 80 and len(line) > 20:
            flush()
            buffer = line
            continue

        # Обычная склейка строк PDF в одну фразу
        buffer = buffer + " " + line

    flush()

    normalized = "\n".join(paragraphs)

    # Чистим множественные пробелы
    normalized = re.sub(r"[ \t]+", " ", normalized)

    # Чистим слишком много пустых строк
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)

    return normalized.strip()


def stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def make_chunk_id(
    document_id: str,
    chunk_index: int,
    text: str,
) -> str:
    base = f"{document_id}:{chunk_index}:{stable_hash(text, 12)}"
    return f"chunk_{stable_hash(base, 16)}"


def split_long_text(
    text: str,
    target_chars: int,
    overlap_chars: int,
) -> List[str]:
    """
    Делит длинный текст на чанки.
    Старается резать по абзацам, потом по предложениям, потом по символам.
    """
    text = normalize_text(text)

    if not text:
        return []

    if len(text) <= target_chars:
        return [text]

    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks = []
    current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n{paragraph}".strip() if current else paragraph

        if len(candidate) <= target_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(paragraph) <= target_chars:
            current = paragraph
        else:
            chunks.extend(split_very_long_paragraph(paragraph, target_chars, overlap_chars))

    if current:
        chunks.append(current)

    if overlap_chars <= 0 or len(chunks) <= 1:
        return chunks

    return add_overlap(chunks, overlap_chars)


def split_very_long_paragraph(
    text: str,
    target_chars: int,
    overlap_chars: int,
) -> List[str]:
    separators = [". ", "; ", ": ", " "]

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + target_chars, len(text))

        if end < len(text):
            best_cut = -1

            for sep in separators:
                pos = text.rfind(sep, start, end)
                if pos > start + target_chars * 0.5:
                    best_cut = pos + len(sep)
                    break

            if best_cut != -1:
                end = best_cut

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = max(end - overlap_chars, start + 1)

    return chunks


def add_overlap(chunks: List[str], overlap_chars: int) -> List[str]:
    result = []

    for i, chunk in enumerate(chunks):
        if i == 0:
            result.append(chunk)
            continue

        prev_tail = chunks[i - 1][-overlap_chars:].strip()
        if prev_tail:
            result.append(f"{prev_tail}\n{chunk}".strip())
        else:
            result.append(chunk)

    return result


def build_base_chunk(
    document: Dict[str, Any],
    chunk_index: int,
    text: str,
    content_type: str,
    section_type: str,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    slide_start: Optional[int] = None,
    slide_end: Optional[int] = None,
    sheet_name: Optional[str] = None,
    row_start: Optional[int] = None,
    row_end: Optional[int] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    text = normalize_text(text)

    document_id = document["document_id"]
    chunk_id = make_chunk_id(document_id, chunk_index, text)

    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "chunk_index": chunk_index,

        "filename": document.get("filename"),
        "extension": document.get("extension"),
        "source_path": document.get("source_path"),
        "relative_path": document.get("relative_path"),
        "source_type": document.get("source_type"),
        "content_type": content_type,
        "section_type": section_type,

        "page_start": page_start,
        "page_end": page_end,
        "slide_start": slide_start,
        "slide_end": slide_end,
        "sheet_name": sheet_name,
        "row_start": row_start,
        "row_end": row_end,

        "text": text,
        "text_length": len(text),
        "text_hash": stable_hash(text, 24),

        "document_metadata": document.get("parsed", {}).get("metadata", {}),
        "file_stat": document.get("file_stat", {}),
        "extra_metadata": extra_metadata or {},
    }


def chunk_pages_document(
    document: Dict[str, Any],
    target_chars: int,
    overlap_chars: int,
    min_chars: int,
) -> List[Dict[str, Any]]:
    parsed = document.get("parsed", {})
    pages = parsed.get("pages", [])

    chunks = []
    buffer_texts = []
    buffer_page_start = None
    buffer_page_end = None

    chunk_index = 0

    for page in pages:
        page_num = page.get("page")
        page_text = normalize_text(page.get("text", ""))

        if not page_text:
            continue

        if buffer_page_start is None:
            buffer_page_start = page_num

        candidate_text = "\n".join(buffer_texts + [page_text]).strip()

        if len(candidate_text) <= target_chars:
            buffer_texts.append(page_text)
            buffer_page_end = page_num
            continue

        if buffer_texts:
            chunk_text = "\n".join(buffer_texts).strip()

            for part in split_long_text(chunk_text, target_chars, overlap_chars):
                if len(part) >= min_chars:
                    chunks.append(build_base_chunk(
                        document=document,
                        chunk_index=chunk_index,
                        text=part,
                        content_type="pages",
                        section_type="page_text",
                        page_start=buffer_page_start,
                        page_end=buffer_page_end,
                    ))
                    chunk_index += 1

        buffer_texts = [page_text]
        buffer_page_start = page_num
        buffer_page_end = page_num

    if buffer_texts:
        chunk_text = "\n".join(buffer_texts).strip()

        for part in split_long_text(chunk_text, target_chars, overlap_chars):
            if len(part) >= min_chars:
                chunks.append(build_base_chunk(
                    document=document,
                    chunk_index=chunk_index,
                    text=part,
                    content_type="pages",
                    section_type="page_text",
                    page_start=buffer_page_start,
                    page_end=buffer_page_end,
                ))
                chunk_index += 1

    return chunks


def chunk_plain_document(
    document: Dict[str, Any],
    target_chars: int,
    overlap_chars: int,
    min_chars: int,
) -> List[Dict[str, Any]]:
    parsed = document.get("parsed", {})
    paragraphs = parsed.get("paragraphs", [])
    tables = parsed.get("tables", [])

    chunks = []
    chunk_index = 0

    text = "\n".join([normalize_text(p) for p in paragraphs if normalize_text(p)])

    for part in split_long_text(text, target_chars, overlap_chars):
        if len(part) >= min_chars:
            chunks.append(build_base_chunk(
                document=document,
                chunk_index=chunk_index,
                text=part,
                content_type="document",
                section_type="paragraph_text",
            ))
            chunk_index += 1

    for table in tables:
        table_index = table.get("table_index")
        rows = table.get("rows", [])

        row_texts = []
        for i, row in enumerate(rows, start=1):
            cells = [str(cell).strip() for cell in row if str(cell).strip()]
            if cells:
                row_texts.append(f"row_{i}: " + " | ".join(cells))

        table_text = "\n".join(row_texts)

        for part in split_long_text(table_text, target_chars, overlap_chars):
            if len(part) >= min_chars:
                chunks.append(build_base_chunk(
                    document=document,
                    chunk_index=chunk_index,
                    text=part,
                    content_type="document",
                    section_type="table",
                    extra_metadata={
                        "table_index": table_index,
                    },
                ))
                chunk_index += 1

    return chunks


def chunk_slides_document(
    document: Dict[str, Any],
    target_chars: int,
    overlap_chars: int,
    min_chars: int,
) -> List[Dict[str, Any]]:
    parsed = document.get("parsed", {})
    slides = parsed.get("slides", [])

    chunks = []
    buffer_texts = []
    slide_start = None
    slide_end = None

    chunk_index = 0

    for slide in slides:
        slide_num = slide.get("slide")
        slide_text = normalize_text(slide.get("text", ""))

        if not slide_text:
            continue

        if slide_start is None:
            slide_start = slide_num

        candidate_text = "\n".join(buffer_texts + [slide_text]).strip()

        if len(candidate_text) <= target_chars:
            buffer_texts.append(slide_text)
            slide_end = slide_num
            continue

        if buffer_texts:
            chunk_text = "\n".join(buffer_texts).strip()

            for part in split_long_text(chunk_text, target_chars, overlap_chars):
                if len(part) >= min_chars:
                    chunks.append(build_base_chunk(
                        document=document,
                        chunk_index=chunk_index,
                        text=part,
                        content_type="slides",
                        section_type="slide_text",
                        slide_start=slide_start,
                        slide_end=slide_end,
                    ))
                    chunk_index += 1

        buffer_texts = [slide_text]
        slide_start = slide_num
        slide_end = slide_num

    if buffer_texts:
        chunk_text = "\n".join(buffer_texts).strip()

        for part in split_long_text(chunk_text, target_chars, overlap_chars):
            if len(part) >= min_chars:
                chunks.append(build_base_chunk(
                    document=document,
                    chunk_index=chunk_index,
                    text=part,
                    content_type="slides",
                    section_type="slide_text",
                    slide_start=slide_start,
                    slide_end=slide_end,
                ))
                chunk_index += 1

    return chunks


def chunk_spreadsheet_document(
    document: Dict[str, Any],
    target_chars: int,
    overlap_chars: int,
    min_chars: int,
) -> List[Dict[str, Any]]:
    parsed = document.get("parsed", {})
    sheets = parsed.get("sheets", [])

    chunks = []
    chunk_index = 0

    for sheet in sheets:
        sheet_name = sheet.get("sheet_name")
        rows = sheet.get("rows", [])

        buffer_lines = []
        row_start = None
        row_end = None

        for row_idx, row in enumerate(rows, start=1):
            cells = [str(cell).strip() for cell in row if str(cell).strip()]
            if not cells:
                continue

            line = f"row_{row_idx}: " + " | ".join(cells)

            if row_start is None:
                row_start = row_idx

            candidate = "\n".join(buffer_lines + [line]).strip()

            if len(candidate) <= target_chars:
                buffer_lines.append(line)
                row_end = row_idx
                continue

            if buffer_lines:
                chunk_text = "\n".join(buffer_lines).strip()

                for part in split_long_text(chunk_text, target_chars, overlap_chars):
                    if len(part) >= min_chars:
                        chunks.append(build_base_chunk(
                            document=document,
                            chunk_index=chunk_index,
                            text=part,
                            content_type="spreadsheet",
                            section_type="sheet_rows",
                            sheet_name=sheet_name,
                            row_start=row_start,
                            row_end=row_end,
                        ))
                        chunk_index += 1

            buffer_lines = [line]
            row_start = row_idx
            row_end = row_idx

        if buffer_lines:
            chunk_text = "\n".join(buffer_lines).strip()

            for part in split_long_text(chunk_text, target_chars, overlap_chars):
                if len(part) >= min_chars:
                    chunks.append(build_base_chunk(
                        document=document,
                        chunk_index=chunk_index,
                        text=part,
                        content_type="spreadsheet",
                        section_type="sheet_rows",
                        sheet_name=sheet_name,
                        row_start=row_start,
                        row_end=row_end,
                    ))
                    chunk_index += 1

    return chunks


def chunk_document(
    document: Dict[str, Any],
    target_chars: int,
    overlap_chars: int,
    min_chars: int,
) -> List[Dict[str, Any]]:
    parsed = document.get("parsed", {})
    content_type = parsed.get("content_type")

    if content_type == "pages":
        return chunk_pages_document(document, target_chars, overlap_chars, min_chars)

    if content_type == "document":
        return chunk_plain_document(document, target_chars, overlap_chars, min_chars)

    if content_type == "slides":
        return chunk_slides_document(document, target_chars, overlap_chars, min_chars)

    if content_type == "spreadsheet":
        return chunk_spreadsheet_document(document, target_chars, overlap_chars, min_chars)

    return []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert parsed_documents.jsonl into chunks.jsonl."
    )

    parser.add_argument(
        "parsed_documents_path",
        type=str,
        help="Path to parsed_documents.jsonl",
    )

    parser.add_argument(
        "--output",
        type=str,
        default="chunks.jsonl",
        help="Path to output chunks.jsonl",
    )

    parser.add_argument(
        "--stats",
        type=str,
        default="chunks_stats.json",
        help="Path to output chunks_stats.json",
    )

    parser.add_argument(
        "--target-chars",
        type=int,
        default=2500,
        help="Target chunk size in characters",
    )

    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=300,
        help="Overlap size in characters",
    )

    parser.add_argument(
        "--min-chars",
        type=int,
        default=120,
        help="Skip chunks shorter than this value",
    )

    args = parser.parse_args()

    parsed_documents_path = Path(args.parsed_documents_path)
    output_path = Path(args.output)
    stats_path = Path(args.stats)

    if output_path.exists():
        output_path.unlink()

    stats = {
        "documents_total": 0,
        "documents_with_chunks": 0,
        "chunks_total": 0,
        "chunks_by_content_type": {},
        "chunks_by_source_type": {},
        "skipped_documents": 0,
        "empty_documents": 0,
        "target_chars": args.target_chars,
        "overlap_chars": args.overlap_chars,
        "min_chars": args.min_chars,
    }

    for document in tqdm(list(read_jsonl(parsed_documents_path)), desc="Making chunks"):
        stats["documents_total"] += 1

        chunks = chunk_document(
            document=document,
            target_chars=args.target_chars,
            overlap_chars=args.overlap_chars,
            min_chars=args.min_chars,
        )

        if not chunks:
            stats["skipped_documents"] += 1
            if document.get("text_length", 0) == 0:
                stats["empty_documents"] += 1
            continue

        stats["documents_with_chunks"] += 1

        for chunk in chunks:
            write_jsonl(output_path, chunk)

            stats["chunks_total"] += 1

            content_type = chunk.get("content_type") or "unknown"
            source_type = chunk.get("source_type") or "unknown"

            stats["chunks_by_content_type"][content_type] = (
                stats["chunks_by_content_type"].get(content_type, 0) + 1
            )

            stats["chunks_by_source_type"][source_type] = (
                stats["chunks_by_source_type"].get(source_type, 0) + 1
            )

    with stats_path.open("w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print("\nDone.")
    print(f"Chunks: {output_path.resolve()}")
    print(f"Stats:  {stats_path.resolve()}")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()