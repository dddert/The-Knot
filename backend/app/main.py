from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.db.neo4j import close_neo4j_driver, get_neo4j_driver
from app.db.postgres import init_postgres, pg_conn
from app.services.graph_service import GraphService
from app.api import documents, graph, search, facts, dashboard, audit, export, compare


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_postgres()
    with get_neo4j_driver().session() as session:
        session.run("RETURN 1")
    GraphService().init_schema()
    yield
    close_neo4j_driver()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(graph.router)
app.include_router(search.router)
app.include_router(facts.router)
app.include_router(dashboard.router)
app.include_router(audit.router)
app.include_router(export.router)
app.include_router(compare.router)


@app.get("/health")
def health():
    dependencies = {"neo4j": "unknown", "postgres": "unknown"}
    try:
        with get_neo4j_driver().session() as session:
            session.run("RETURN 1").single()
        dependencies["neo4j"] = "ok"
    except Exception:
        dependencies["neo4j"] = "error"
    try:
        with pg_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        dependencies["postgres"] = "ok"
    except Exception:
        dependencies["postgres"] = "error"
    return {
        "status": "ok" if all(v == "ok" for v in dependencies.values()) else "degraded",
        "app": settings.app_name,
        "use_mock_ml": settings.use_mock_ml,
        "dependencies": dependencies,
    }
