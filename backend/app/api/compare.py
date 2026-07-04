from __future__ import annotations
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ConfigDict
from app.api.deps import AuditContext, audit_context
from app.schemas.contracts import GeoScope, NumericConstraint, QueryPlan
from app.services.audit_service import AuditService
from app.services.compare_service import CompareService

router = APIRouter(prefix="/api/compare", tags=["compare"])
compare_service = CompareService()
audit = AuditService()


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    process: str | None = None
    processes: list[str] = Field(default_factory=list)
    material: str | None = None
    materials: list[str] = Field(default_factory=list)
    geo_scope: GeoScope = "all"
    countries: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    confidence_min: float = Field(default=0.0, ge=0, le=1)
    numeric_constraints: list[NumericConstraint] = Field(default_factory=list)
    group_by: str = "geo_scope"
    limit: int = Field(default=100, ge=1, le=500)


def _as_list(single: str | None, many: list[str]) -> list[str]:
    values = list(many or [])
    if single:
        values.append(single)
    return [v for v in values if v]


@router.post("")
def compare(request: CompareRequest, ctx: AuditContext = Depends(audit_context)):
    plan = QueryPlan(
        intent="compare_practices",
        processes=_as_list(request.process, request.processes),
        materials=_as_list(request.material, request.materials),
        geo_scope=request.geo_scope,
        countries=request.countries,
        year_from=request.year_from,
        year_to=request.year_to,
        confidence_min=request.confidence_min,
        numeric_constraints=request.numeric_constraints,
        comparison_mode=True,
        group_by=[request.group_by],
    )
    result = compare_service.compare(plan, group_by=request.group_by, limit=request.limit, role=ctx.role)
    audit.log(
        "comparison_executed",
        user_id=ctx.user_id,
        role=ctx.role,
        target_type="compare",
        query=f"group_by={request.group_by}; processes={plan.processes}; materials={plan.materials}",
        payload={"total_facts": result["total_facts"], "group_by": request.group_by},
    )
    return result
