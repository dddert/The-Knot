from __future__ import annotations
import uuid
from typing import Any
from psycopg.types.json import Jsonb
from app.db.postgres import pg_conn


class FactVersionService:
    def next_version(self, fact_id: str) -> int:
        with pg_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM fact_versions WHERE fact_id = %s",
                (fact_id,),
            ).fetchone()
        return int(row[0])

    def create_version(
        self,
        fact_id: str,
        previous_payload: dict[str, Any],
        new_payload: dict[str, Any],
        comment: str | None,
        updated_by: str,
    ) -> dict[str, Any]:
        version = self.next_version(fact_id)
        version_id = f"fv_{uuid.uuid4().hex[:12]}"
        with pg_conn() as conn:
            conn.execute(
                """
                INSERT INTO fact_versions (id, fact_id, version, previous_payload, new_payload, comment, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (version_id, fact_id, version, Jsonb(previous_payload), Jsonb(new_payload), comment, updated_by),
            )
        return {"id": version_id, "fact_id": fact_id, "version": version}

    def list_versions(self, fact_id: str) -> list[dict[str, Any]]:
        with pg_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, fact_id, version, previous_payload, new_payload, comment, updated_by, created_at
                FROM fact_versions
                WHERE fact_id = %s
                ORDER BY version DESC
                """,
                (fact_id,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "fact_id": r[1],
                "version": r[2],
                "previous_payload": r[3],
                "new_payload": r[4],
                "comment": r[5],
                "updated_by": r[6],
                "created_at": r[7].isoformat() if r[7] else None,
            }
            for r in rows
        ]
