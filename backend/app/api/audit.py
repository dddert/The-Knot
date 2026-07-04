from fastapi import APIRouter, Depends, Query
from app.api.deps import AuditContext, require_roles
from app.services.audit_service import AuditService

router = APIRouter(prefix="/api/audit", tags=["audit"])
audit = AuditService()


@router.get("")
def list_audit(limit: int = Query(default=100, ge=1, le=500), ctx: AuditContext = Depends(require_roles("admin", "manager"))):
    return {"items": audit.list_logs(limit=limit)}
