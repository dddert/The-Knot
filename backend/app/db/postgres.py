from contextlib import contextmanager
import psycopg
from app.core.config import settings


def get_connection() -> psycopg.Connection:
    return psycopg.connect(settings.postgres_dsn)


@contextmanager
def pg_conn():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_postgres() -> None:
    with pg_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
              id TEXT PRIMARY KEY,
              user_id TEXT,
              role TEXT,
              action TEXT NOT NULL,
              target_type TEXT,
              target_id TEXT,
              query TEXT,
              payload JSONB,
              created_at TIMESTAMPTZ DEFAULT now()
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fact_versions (
              id TEXT PRIMARY KEY,
              fact_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              previous_payload JSONB NOT NULL,
              new_payload JSONB NOT NULL,
              comment TEXT,
              updated_by TEXT,
              created_at TIMESTAMPTZ DEFAULT now()
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY,
              filename TEXT NOT NULL,
              content_type TEXT,
              storage_path TEXT,
              access_level TEXT DEFAULT 'internal',
              status TEXT NOT NULL,
              created_at TIMESTAMPTZ DEFAULT now(),
              processed_at TIMESTAMPTZ
            );
            """
        )
        conn.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS access_level TEXT DEFAULT 'internal'")
