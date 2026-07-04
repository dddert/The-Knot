from fastapi import APIRouter, Depends
from app.api.deps import AuditContext, audit_context
from app.schemas.contracts import SearchRequest
from app.services.audit_service import AuditService
from app.services.search_service import SearchService

router = APIRouter(prefix="/api/search", tags=["search"])
search_service = SearchService()
audit = AuditService()


@router.post("")
async def search(request: SearchRequest, ctx: AuditContext = Depends(audit_context)):
    result = await search_service.search(request, role=ctx.role)
    audit.log(
        "search_executed",
        user_id=ctx.user_id,
        role=ctx.role,
        target_type="search",
        query=request.query,
        payload={
            "query_plan": result.query_plan,
            "facts_count": len(result.facts),
            "sources_count": len(result.sources),
        },
    )
    return result.model_dump(exclude_none=True)
