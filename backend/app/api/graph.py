from fastapi import APIRouter, HTTPException, Query, Depends
from app.api.deps import AuditContext, require_roles
from app.services.graph_service import GraphService
from app.services.graph_read_service import GraphReadService
from app.services.audit_service import AuditService

router = APIRouter(prefix="/api/graph", tags=["graph"])
graph_admin = GraphService()
graph_read = GraphReadService()
audit = AuditService()

INTERNAL_ROLES = ("researcher", "analyst", "manager", "admin")


@router.post("/init-schema")
def init_schema(ctx: AuditContext = Depends(require_roles("admin"))):
    result = graph_admin.init_schema()
    audit.log("graph_schema_initialized", user_id=ctx.user_id, role=ctx.role, target_type="graph", payload=result)
    return result


@router.delete("/clear-demo")
def clear_demo_graph(ctx: AuditContext = Depends(require_roles("admin"))):
    result = graph_admin.clear_demo_graph()
    audit.log("graph_cleared", user_id=ctx.user_id, role=ctx.role, target_type="graph", payload=result)
    return result


@router.get("/node/{node_id}")
def node(node_id: str, ctx: AuditContext = Depends(require_roles(*INTERNAL_ROLES))):
    result = graph_read.get_node(node_id, visible_access_levels=ctx.visible_access_levels)
    if not result:
        raise HTTPException(status_code=404, detail="Node not found")
    audit.log("graph_node_viewed", user_id=ctx.user_id, role=ctx.role, target_type="graph_node", target_id=node_id)
    return result


@router.get("/neighbors/{node_id}")
def neighbors(
    node_id: str,
    depth: int = Query(default=1, ge=1, le=4),
    limit: int = Query(default=100, ge=1, le=500),
    mode: str = Query(default="compact", pattern="^(compact|full|none)$"),
    ctx: AuditContext = Depends(require_roles(*INTERNAL_ROLES)),
):
    result = graph_read.get_neighbors(node_id=node_id, depth=depth, limit=limit, mode=mode, visible_access_levels=ctx.visible_access_levels)
    audit.log("graph_neighbors_viewed", user_id=ctx.user_id, role=ctx.role, target_type="graph_node", target_id=node_id, payload={"depth": depth, "limit": limit, "mode": mode})
    return result.model_dump(exclude_none=True)


@router.get("/path")
def path(
    source_id: str,
    target_id: str,
    max_depth: int = Query(default=4, ge=1, le=6),
    mode: str = Query(default="compact", pattern="^(compact|full|none)$"),
    ctx: AuditContext = Depends(require_roles(*INTERNAL_ROLES)),
):
    result = graph_read.get_path(source_id=source_id, target_id=target_id, max_depth=max_depth, mode=mode, visible_access_levels=ctx.visible_access_levels)
    audit.log("graph_path_viewed", user_id=ctx.user_id, role=ctx.role, target_type="graph", payload={"source_id": source_id, "target_id": target_id, "max_depth": max_depth, "mode": mode})
    return result.model_dump(exclude_none=True)


@router.get("/subgraph")
def subgraph(
    fact_ids: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    mode: str = Query(default="compact", pattern="^(compact|full|none)$"),
    ctx: AuditContext = Depends(require_roles(*INTERNAL_ROLES)),
):
    ids = [x.strip() for x in fact_ids.split(",") if x.strip()] if fact_ids else []
    if not ids:
        raise HTTPException(status_code=400, detail="fact_ids is required for subgraph endpoint; use search response graph for exploratory views")
    result = graph_read.get_subgraph(ids, limit=limit, mode=mode, visible_access_levels=ctx.visible_access_levels)
    audit.log("subgraph_viewed", user_id=ctx.user_id, role=ctx.role, target_type="graph", payload={"fact_ids": ids, "limit": limit, "mode": mode})
    return result.model_dump(exclude_none=True)
