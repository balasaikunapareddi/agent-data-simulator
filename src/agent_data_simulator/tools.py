"""Read-only tools the evaluated agent is allowed to use.

The estate is served through a read-only interface — schema browsing,
document search, and SQL execution — so any agent can be tested without
seeing the blueprint or the answer key. SQL execution is enforced
read-only at three layers: statement validation, a read-only SQLite
connection, and a SQLite authorizer that denies every write action.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_FORBIDDEN = {
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "vacuum", "attach", "detach", "pragma", "reindex",
    "grant", "revoke",
}

_DENIED_ACTIONS = {
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_INDEX,
    sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER,
    sqlite3.SQLITE_DROP_TEMP_VIEW,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_REINDEX,
}


class ReadOnlyViolation(Exception):
    """Raised when a SQL statement attempts anything but a read."""


def validate_read_only(sql: str) -> str:
    """Validate that `sql` is a single read-only SELECT. Returns cleaned SQL."""
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise ReadOnlyViolation("empty SQL statement")
    if ";" in cleaned:
        raise ReadOnlyViolation("multiple statements are not allowed")

    # Strip string literals and comments before keyword scanning so that
    # e.g. WHERE note='delete later' does not false-positive.
    scrubbed = re.sub(r"'[^']*'", "''", cleaned)
    scrubbed = re.sub(r"--[^\n]*", "", scrubbed)
    scrubbed = re.sub(r"/\*.*?\*/", "", scrubbed, flags=re.DOTALL)

    first_word = re.match(r"[A-Za-z]+", scrubbed)
    if not first_word or first_word.group(0).lower() not in {"select", "with"}:
        raise ReadOnlyViolation("only SELECT statements are allowed")

    words = set(re.findall(r"[A-Za-z]+", scrubbed.lower()))
    bad = words & _FORBIDDEN
    if bad:
        raise ReadOnlyViolation(f"forbidden keyword(s) in SQL: {sorted(bad)}")
    return cleaned


class EstateTools:
    """Read-only interface over one built estate (database + documents)."""

    def __init__(self, db_path: Path, documents: list[dict]):
        self.db_path = Path(db_path)
        self.documents = documents

    # -- schema -----------------------------------------------------------
    def list_tables(self) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def describe_table(self, table: str) -> dict:
        if table not in self.list_tables():
            raise ValueError(f"unknown table: {table}")
        conn = self._connect()
        try:
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            return {
                "table": table,
                "columns": [
                    {"name": c[1], "type": c[2], "not_null": bool(c[3]), "pk": bool(c[5])}
                    for c in cols
                ],
            }
        finally:
            conn.close()

    # -- documents --------------------------------------------------------
    def search_documents(self, query: str, limit: int = 5) -> list[dict]:
        terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2]
        scored = []
        for doc in self.documents:
            title = doc["title"].lower()
            body = doc["body"].lower()
            score = sum(3 * title.count(t) + body.count(t) for t in terms)
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["date"]))
        return [
            {
                "doc_id": d["doc_id"],
                "title": d["title"],
                "kind": d["kind"],
                "author": d["author"],
                "date": d["date"],
                "body": d["body"],
            }
            for _, d in scored[:limit]
        ]

    # -- SQL --------------------------------------------------------------
    def run_sql(self, sql: str, max_rows: int = 100) -> dict:
        cleaned = validate_read_only(sql)
        conn = self._connect()
        try:
            cur = conn.execute(cleaned)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows)
            return {
                "columns": columns,
                "rows": [list(r) for r in rows],
                "truncated": len(rows) == max_rows,
            }
        finally:
            conn.close()

    # -- plumbing ---------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

        def authorizer(action, _p1, _p2, _db, _src):
            return sqlite3.SQLITE_DENY if action in _DENIED_ACTIONS else sqlite3.SQLITE_OK

        conn.set_authorizer(authorizer)
        return conn

    def tool_specs(self) -> list[dict]:
        """Typed tool definitions shown to the agent."""
        return [
            {
                "name": "list_tables",
                "description": "List all tables in the estate database.",
                "parameters": {},
            },
            {
                "name": "describe_table",
                "description": "Show columns and types for one table.",
                "parameters": {"table": "string — table name from list_tables"},
            },
            {
                "name": "search_documents",
                "description": (
                    "Search company documents (memos, chat threads, handbooks) "
                    "by keywords. Returns matching documents with author and date."
                ),
                "parameters": {"query": "string — keywords to search for"},
            },
            {
                "name": "run_sql",
                "description": (
                    "Run one read-only SELECT query against the estate database. "
                    "Writes are rejected."
                ),
                "parameters": {"sql": "string — a single SELECT statement"},
            },
        ]
