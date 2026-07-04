from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from typing import Any
from app.api.deps import AuditContext, require_roles
from app.services.audit_service import AuditService
from app.services.export_service import ExportService
from app.services.fact_service import FactService

router = APIRouter(prefix="/api/export", tags=["export"])
exporter = ExportService()
facts_service = FactService()
audit = AuditService()


class ExportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: dict[str, Any] = Field(default_factory=dict)
    fact_ids: list[str] = Field(default_factory=list)
    # Backward-compatible demo fallback. The React UI now sends fact_ids so the
    # backend re-fetches facts with access checks before export.
    facts: list[dict[str, Any]] = Field(default_factory=list)


def _server_side_facts(payload: ExportPayload, ctx: AuditContext) -> tuple[list[dict[str, Any]], bool]:
    if payload.fact_ids:
        facts = facts_service.get_facts_by_ids(payload.fact_ids, visible_access_levels=ctx.visible_access_levels)
        missing = set(payload.fact_ids) - {f.id for f in facts}
        if missing:
            # Do not disclose whether IDs exist or are simply not visible.
            raise HTTPException(status_code=404, detail="Some facts were not found or access was denied")
        return [f.model_dump(exclude_none=True) for f in facts], True
    return payload.facts, False


def _answer(payload: ExportPayload, facts: list[dict[str, Any]]) -> dict[str, Any]:
    if payload.answer:
        return payload.answer
    return {
        "summary": f"Экспортировано {len(facts)} фактов из графа знаний.",
        "confidence": round(sum(float(f.get("confidence") or 0) for f in facts) / max(len(facts), 1), 2),
        "sections": [],
    }


@router.post("/markdown")
def export_markdown(
    payload: ExportPayload,
    ctx: AuditContext = Depends(require_roles("admin", "analyst", "manager")),
):
    server_facts, server_side = _server_side_facts(payload, ctx)
    markdown = exporter.to_markdown(_answer(payload, server_facts), server_facts)
    audit.log(
        "export_created",
        user_id=ctx.user_id,
        role=ctx.role,
        target_type="export",
        payload={"format": "markdown", "facts_count": len(server_facts), "server_side_facts": server_side},
    )
    return {"format": "markdown", "content": markdown, "server_side_facts": server_side}


@router.post("/jsonld")
def export_jsonld(
    payload: ExportPayload,
    ctx: AuditContext = Depends(require_roles("admin", "analyst", "manager")),
):
    server_facts, server_side = _server_side_facts(payload, ctx)
    jsonld = exporter.to_jsonld(_answer(payload, server_facts), server_facts)
    audit.log(
        "export_created",
        user_id=ctx.user_id,
        role=ctx.role,
        target_type="export",
        payload={"format": "jsonld", "facts_count": len(server_facts), "server_side_facts": server_side},
    )
    return {"format": "jsonld", "content": jsonld, "server_side_facts": server_side}


@router.post("/pdf")
def export_pdf(
    payload: ExportPayload,
    ctx: AuditContext = Depends(require_roles("admin", "analyst", "manager")),
):
    server_facts, server_side = _server_side_facts(payload, ctx)
    content_base64 = exporter.to_pdf_base64(_answer(payload, server_facts), server_facts)
    audit.log(
        "export_created",
        user_id=ctx.user_id,
        role=ctx.role,
        target_type="export",
        payload={"format": "pdf", "facts_count": len(server_facts), "server_side_facts": server_side},
    )
    return {
        "format": "pdf",
        "filename": "scientific_knot_result.pdf",
        "content_base64": content_base64,
        "encoding": "base64",
        "server_side_facts": server_side,
    }
