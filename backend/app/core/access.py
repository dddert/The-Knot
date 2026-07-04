from __future__ import annotations

INTERNAL_ROLES = {"researcher", "analyst", "manager", "admin"}
ALL_ROLES = ["external_partner", "researcher", "analyst", "manager", "admin"]


def visible_access_levels(role: str) -> list[str]:
    """Demo access model. Production should derive role from JWT/OIDC, not query params."""
    if role == "external_partner":
        return ["public"]
    if role in {"researcher", "analyst"}:
        return ["public", "internal"]
    if role in {"manager", "admin"}:
        return ["public", "internal", "confidential"]
    return ["public"]


def can_view_internal(role: str) -> bool:
    return "internal" in visible_access_levels(role)


def can_view_confidential(role: str) -> bool:
    return "confidential" in visible_access_levels(role)
