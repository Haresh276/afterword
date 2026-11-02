"""SQLite persistence for ProofPay. Plain sqlite3, JSON columns, zero setup."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.getenv("PROOFPAY_DB", os.path.join(os.path.dirname(__file__), "..", "proofpay.db"))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    id TEXT PRIMARY KEY,
    title TEXT,
    freelancer_name TEXT,
    freelancer_email TEXT,
    client_name TEXT,
    client_email TEXT,
    currency TEXT,
    total_amount REAL,
    raw_text TEXT,
    summary TEXT,
    risk_flags TEXT,               -- JSON list[str]
    engine TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS milestones (
    id TEXT PRIMARY KEY,
    contract_id TEXT,
    seq INTEGER,
    name TEXT,
    amount REAL,
    due_date TEXT,
    acceptance_criteria TEXT,      -- JSON list[str]
    status TEXT,                   -- DRAFT|CONFIRMED|SUBMITTED|VERIFIED|PENDING_APPROVAL|INVOICED|PAID|CANCELLED
    proof_score REAL,
    proof_report TEXT,             -- JSON
    deliverable TEXT,              -- JSON {kind, value, excerpt}
    invoice_id TEXT,
    invoice_url TEXT,
    invoice_status TEXT,
    sent_at TEXT,
    paid_at TEXT,
    reminders_sent INTEGER DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY,
    milestone_id TEXT,
    tool TEXT,
    params TEXT,                   -- JSON
    rationale TEXT,
    policy_checks TEXT,            -- JSON list[{check, passed, detail}]
    status TEXT,                   -- PENDING|APPROVED|REJECTED|EXECUTED|FAILED
    proposed_by TEXT,
    decided_by TEXT,
    result TEXT,
    created_at TEXT,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    actor TEXT,                    -- human|contract_agent|proof_agent|paypal_agent|copilot|system
    action TEXT,
    milestone_id TEXT,
    detail TEXT                    -- JSON
);
"""

JSON_COLS = {"risk_flags", "acceptance_criteria", "proof_report", "deliverable", "params", "policy_checks", "detail"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = connect()
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def reset() -> None:
    global _conn
    with _lock:
        c = conn()
        for t in ("contracts", "milestones", "pending_actions", "audit_log"):
            c.execute(f"DELETE FROM {t}")
        c.commit()


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_COLS & d.keys():
        if d[k]:
            try:
                d[k] = json.loads(d[k])
            except (TypeError, ValueError):
                pass
    return d


def _encode(values: dict) -> dict:
    out = {}
    for k, v in values.items():
        out[k] = json.dumps(v) if k in JSON_COLS and v is not None and not isinstance(v, str) else v
    return out


def insert(table: str, values: dict) -> dict:
    values = _encode(values)
    cols = ", ".join(values)
    qs = ", ".join("?" for _ in values)
    with _lock:
        conn().execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", list(values.values()))
        conn().commit()
    return values


def update(table: str, row_id: Any, values: dict) -> None:
    values = _encode(values)
    sets = ", ".join(f"{k} = ?" for k in values)
    with _lock:
        conn().execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*values.values(), row_id])
        conn().commit()


def get(table: str, row_id: Any) -> dict | None:
    return _decode(conn().execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone())


def query(sql: str, params: tuple = ()) -> list[dict]:
    return [_decode(r) for r in conn().execute(sql, params).fetchall()]


def audit(actor: str, action: str, milestone_id: str | None = None, detail: Any = None) -> None:
    with _lock:
        conn().execute(
            "INSERT INTO audit_log (ts, actor, action, milestone_id, detail) VALUES (?, ?, ?, ?, ?)",
            (now(), actor, action, milestone_id, json.dumps(detail, default=str) if detail is not None else None),
        )
        conn().commit()
