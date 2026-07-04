from fastapi import APIRouter, Depends
from app.api.deps import AuditContext, require_roles
from app.services.audit_service import AuditService
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
dashboard = DashboardService()
audit = AuditService()


@router.get("/coverage")
def coverage(ctx: AuditContext = Depends(require_roles("admin", "manager", "analyst", "researcher"))):
    result = dashboard.coverage(visible_access_levels=ctx.visible_access_levels)
    audit.log("dashboard_opened", user_id=ctx.user_id, role=ctx.role, target_type="dashboard")
    return result
