from __future__ import annotations
import uuid
from typing import Any
from psycopg.types.json import Jsonb
from app.db.postgres import pg_conn


class AuditService:
    def log(
        self,
        action: str,
        user_id: str = "demo_user",
        role: str = "external_partner",
        target_type: str | None = None,
        target_id: str | None = None,
        query: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        audit_id = f"audit_{uuid.uuid4().hex[:12]}"
        with pg_conn() as conn:
            conn.execute(
                """
                INSERT INTO audit_log (id, user_id, role, action, target_type, target_id, query, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (audit_id, user_id, role, action, target_type, target_id, query, Jsonb(payload or {})),
            )
        return audit_id

    def list_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with pg_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, role, action, target_type, target_id, query, payload, created_at
                FROM audit_log
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "user_id": r[1],
                "role": r[2],
                "action": r[3],
                "target_type": r[4],
                "target_id": r[5],
                "query": r[6],
                "payload": r[7],
                "created_at": r[8].isoformat() if r[8] else None,
            }
            for r in rows
        ]
