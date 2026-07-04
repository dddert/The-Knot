from fastapi import APIRouter, HTTPException, Query, Depends
from app.api.deps import AuditContext, audit_context, require_roles
from app.schemas.contracts import FactUpdateRequest
from app.services.audit_service import AuditService
from app.services.fact_service import FactService
from app.services.fact_version_service import FactVersionService

router = APIRouter(prefix="/api/facts", tags=["facts"])
facts = FactService()
versions = FactVersionService()
audit = AuditService()


@router.get("")
def list_facts(
    status: str | None = Query(default=None),
    geo_scope: str | None = Query(default=None),
    confidence_min: float = Query(default=0.0, ge=0, le=1),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: AuditContext = Depends(audit_context),
):
    items = facts.list_facts(
        status=status,
        geo_scope=geo_scope,
        confidence_min=confidence_min,
        limit=limit,
        visible_access_levels=ctx.visible_access_levels,
    )
    return {"items": [item.model_dump(exclude_none=True) for item in items]}


@router.get("/{fact_id}")
def get_fact(fact_id: str, ctx: AuditContext = Depends(audit_context)):
    fact = facts.get_fact(fact_id, visible_access_levels=ctx.visible_access_levels)
    if not fact:
        raise HTTPException(status_code=404, detail="Fact not found")
    audit.log("fact_viewed", user_id=ctx.user_id, role=ctx.role, target_type="fact", target_id=fact_id)
    return fact.model_dump(exclude_none=True)


@router.patch("/{fact_id}")
def update_fact(
    fact_id: str,
    request: FactUpdateRequest,
    ctx: AuditContext = Depends(require_roles("admin", "analyst")),
):
    # RBAC source of truth is query/session context, never request body.
    updates = request.model_dump(exclude_none=True)
    updates.pop("comment", None)
    updated, previous, version = facts.update_with_version(
        fact_id=fact_id,
        updates=updates,
        comment=request.comment,
        updated_by=ctx.user_id,
        visible_access_levels=ctx.visible_access_levels,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Fact not found")
    audit.log(
        "fact_updated",
        user_id=ctx.user_id,
        role=ctx.role,
        target_type="fact",
        target_id=fact_id,
        payload={"version": version, "updates": updates},
    )
    return {"fact": updated.model_dump(exclude_none=True), "version": version}


@router.get("/{fact_id}/versions")
def fact_versions(
    fact_id: str,
    ctx: AuditContext = Depends(require_roles("admin", "analyst", "manager")),
):
    fact = facts.get_fact(fact_id, visible_access_levels=ctx.visible_access_levels)
    if not fact:
        raise HTTPException(status_code=404, detail="Fact not found")
    audit.log("fact_versions_viewed", user_id=ctx.user_id, role=ctx.role, target_type="fact", target_id=fact_id)
    return {"items": versions.list_versions(fact_id)}
