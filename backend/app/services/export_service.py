from __future__ import annotations
import base64
import html
from io import BytesIO
from pathlib import Path
from typing import Any


class ExportService:
    def to_markdown(self, answer: dict[str, Any], facts: list[dict[str, Any]] | None = None) -> str:
        parts = ["# Научный клубок — результат поиска", "", answer.get("summary", "")]
        for section in answer.get("sections", []):
            parts += ["", f"## {section.get('title')}"]
            content = section.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        parts.append("- " + "; ".join(f"{k}: {v}" for k, v in item.items()))
                    else:
                        parts.append(f"- {item}")
            else:
                parts.append(str(content))
        if facts:
            parts += ["", "## Источники и факты"]
            for fact in facts:
                source = fact.get("source_title") or (fact.get("source") or {}).get("title") or "—"
                parts.append(f"- {fact.get('claim_text')} — {source} / confidence={fact.get('confidence')}")
        return "\n".join(parts)

    def to_jsonld(self, answer: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "@context": {
                "sk": "https://example.org/scientific-knot#",
                "claim": "sk:claimText",
                "confidence": "sk:confidence",
                "source": "sk:source",
            },
            "@type": "sk:SearchResult",
            "summary": answer.get("summary"),
            "facts": [
                {
                    "@id": f.get("id"),
                    "@type": "sk:Fact",
                    "claim": f.get("claim_text"),
                    "confidence": f.get("confidence"),
                    "source": f.get("source_title") or (f.get("source") or {}).get("title"),
                }
                for f in facts
            ],
        }

    def to_pdf_base64(self, answer: dict[str, Any], facts: list[dict[str, Any]] | None = None) -> str:
        """Return a simple PDF report encoded as base64.

        Uses ReportLab and attempts to register DejaVu Sans for Cyrillic. The font file
        is referenced locally inside the container and is never exposed to users.
        """
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        font_name = "Helvetica"
        for candidate in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/local/share/fonts/DejaVuSans.ttf",
        ]:
            if Path(candidate).exists():
                pdfmetrics.registerFont(TTFont("DejaVuSans", candidate))
                font_name = "DejaVuSans"
                break

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="SKTitle", parent=styles["Title"], fontName=font_name, fontSize=16, leading=20))
        styles.add(ParagraphStyle(name="SKHeading", parent=styles["Heading2"], fontName=font_name, fontSize=12, leading=16))
        styles.add(ParagraphStyle(name="SKBody", parent=styles["BodyText"], fontName=font_name, fontSize=9, leading=12))
        styles.add(ParagraphStyle(name="SKSmall", parent=styles["BodyText"], fontName=font_name, fontSize=8, leading=10))

        story: list[Any] = []
        story.append(Paragraph("Научный клубок — результат поиска", styles["SKTitle"]))
        story.append(Spacer(1, 10))
        story.append(Paragraph(_safe(answer.get("summary", "")), styles["SKBody"]))
        story.append(Spacer(1, 12))

        for section in answer.get("sections", []):
            story.append(Paragraph(_safe(section.get("title", "Раздел")), styles["SKHeading"]))
            content = section.get("content", [])
            if isinstance(content, list):
                for item in content[:20]:
                    if isinstance(item, dict):
                        line = "; ".join(f"{k}: {v}" for k, v in item.items())
                    else:
                        line = str(item)
                    story.append(Paragraph("• " + _safe(line), styles["SKSmall"]))
            else:
                story.append(Paragraph(_safe(str(content)), styles["SKSmall"]))
            story.append(Spacer(1, 8))

        facts = facts or []
        if facts:
            story.append(Paragraph("Факты и источники", styles["SKHeading"]))
            rows = [["Fact", "Confidence", "Source"]]
            for fact in facts[:25]:
                source = fact.get("source_title") or (fact.get("source") or {}).get("title") or "—"
                rows.append([
                    Paragraph(_safe(str(fact.get("claim_text") or "—"))[:900], styles["SKSmall"]),
                    str(fact.get("confidence") or "—"),
                    Paragraph(_safe(str(source))[:300], styles["SKSmall"]),
                ])
            table = Table(rows, colWidths=[300, 70, 140])
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]))
            story.append(table)

        doc.build(story)
        return base64.b64encode(buffer.getvalue()).decode("ascii")


def _safe(value: str) -> str:
    return html.escape(value or "").replace("\n", "<br/>")
