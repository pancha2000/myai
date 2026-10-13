"""Turso (libsql) cloud database layer.

Key points:
* `get_db_client()` is SYNC and returns an async client -> never `await` it.
  (The original main.py did `await get_db_client()`, which raises
  "TypeError: object Client can't be used in 'await' expression".)
* libsql:// URLs are converted to https:// because the websocket transport
  is the usual cause of "WSServerHandshakeError / 505" errors.
"""
import os

import libsql_client
from dotenv import load_dotenv

load_dotenv()

from contextlib import asynccontextmanager  # noqa: E402


def _normalize_url(url: str) -> str:
    url = url.strip()
    for prefix in ("libsql://", "wss://", "ws://"):
        if url.startswith(prefix):
            return "https://" + url[len(prefix):]
    return url


def get_db_client():
    """Return a new libsql async client (call inside a running event loop)."""
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")
    if not url or not token:
        raise ValueError(
            "TURSO_DATABASE_URL / TURSO_AUTH_TOKEN are missing "
            "(.env locally, Environment Variables on Koyeb)."
        )
    return libsql_client.create_client(url=_normalize_url(url), auth_token=token)


@asynccontextmanager
async def db_session():
    """`async with db_session() as db:` -> client that is always closed."""
    client = get_db_client()
    try:
        yield client
    finally:
        await client.close()


def rows_to_dicts(result) -> list:
    cols = list(result.columns)
    return [{c: row[i] for i, c in enumerate(cols)} for row in result.rows]


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS master_plan (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        parent_id INTEGER,
        task_title TEXT NOT NULL,
        task_description TEXT,
        status TEXT DEFAULT 'Pending',
        FOREIGN KEY (project_id) REFERENCES projects(id)
    )""",
    """CREATE TABLE IF NOT EXISTS hints_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        module_name TEXT,
        hint_content TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id)
    )""",
    """CREATE TABLE IF NOT EXISTS knowledge_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query_keyword TEXT UNIQUE,
        clean_data TEXT,
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    # NEW: queue so the cloud dashboard can ask the laptop AI to work
    """CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_type TEXT NOT NULL,
        payload TEXT,
        status TEXT DEFAULT 'pending',
        result TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_plan_project ON master_plan(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_hints_project ON hints_memory(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
]

# Columns added later (safe to run on a DB created by the old version)
MIGRATIONS = [
    "ALTER TABLE knowledge_cache ADD COLUMN sources TEXT",
]


async def init_database():
    async with db_session() as db:
        for stmt in SCHEMA:
            await db.execute(stmt)
        for stmt in MIGRATIONS:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists
    print("Turso database schema ready.")
