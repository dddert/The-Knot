from __future__ import annotations
from fastapi import Header, HTTPException, Query
from pydantic import BaseModel
from app.core.access import ALL_ROLES, visible_access_levels

# Demo-only token map. Production should replace this with JWT/OIDC identity.
DEMO_ROLE_TOKENS = {
    "partner-token": "external_partner",
    "researcher-token": "researcher",
    "analyst-token": "analyst",
    "manager-token": "manager",
    "admin-token": "admin",
}


class AuditContext(BaseModel):
    user_id: str = "demo_user"
    role: str = "external_partner"

    @property
    def visible_access_levels(self) -> list[str]:
        return visible_access_levels(self.role)


def _normalize_role(role: str) -> str:
    if role not in ALL_ROLES:
        raise HTTPException(status_code=400, detail=f"Unknown role: {role}")
    return role


def _resolve_role(role_query: str, demo_role_token: str | None) -> str:
    """Resolve demo role from a token, not from a self-asserted query param.

    Query-param role is kept only as UI/audit context for external_partner. Any internal
    role requires the matching X-Demo-Role-Token header.
    """
    if demo_role_token:
        if demo_role_token not in DEMO_ROLE_TOKENS:
            raise HTTPException(status_code=401, detail="Invalid X-Demo-Role-Token")
        return DEMO_ROLE_TOKENS[demo_role_token]
    role_query = _normalize_role(role_query)
    if role_query != "external_partner":
        raise HTTPException(status_code=401, detail="Internal demo roles require X-Demo-Role-Token")
    return "external_partner"


def audit_context(
    user_id: str = Query(default="demo_user", description="Current demo user id"),
    role: str = Query(default="external_partner", description="Requested demo role; internal roles require token"),
    x_demo_role_token: str | None = Header(default=None, alias="X-Demo-Role-Token"),
) -> AuditContext:
    return AuditContext(user_id=user_id, role=_resolve_role(role, x_demo_role_token))


def ensure_role(role: str, allowed_roles: set[str] | tuple[str, ...] | list[str]) -> None:
    role = _normalize_role(role)
    allowed = set(allowed_roles)
    if role not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient role: '{role}'. Allowed roles: {', '.join(sorted(allowed))}",
        )


def require_roles(*allowed_roles: str):
    def checker(
        user_id: str = Query(default="demo_user", description="Current demo user id"),
        role: str = Query(default="external_partner", description="Requested demo role; internal roles require token"),
        x_demo_role_token: str | None = Header(default=None, alias="X-Demo-Role-Token"),
    ) -> AuditContext:
        ctx = AuditContext(user_id=user_id, role=_resolve_role(role, x_demo_role_token))
        ensure_role(ctx.role, allowed_roles)
        return ctx
    return checker
