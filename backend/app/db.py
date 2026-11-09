"""SQLite persistence for Afterword. Plain sqlite3, JSON columns, zero setup.

Two worlds share one database in this demo:
  * the family side (cases, findings, notices sent), and
  * the merchant side (merchants, their customer records, notices received, pending PayPal actions).
In production these are separate systems that only exchange signed notices.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

DB_PATH = os.getenv("AFTERWORD_DB", os.path.join(os.path.dirname(__file__), "..", "afterword.db"))
_lock = threading.RLock()

SCHEMA = """
-- ---------------------------------------------------------------- family side
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    claimant_name TEXT, claimant_email TEXT, relationship TEXT, language TEXT,
    deceased_name TEXT, deceased_email TEXT, date_of_birth TEXT, date_of_death TEXT,
    place_of_death TEXT, registration_number TEXT, issuing_authority TEXT, country TEXT,
    certificate_sha256 TEXT, certificate_phash TEXT, certificate_language TEXT, certificate_deleted_at TEXT,
    network TEXT,             -- JSON: cross-case fraud graph result
    extraction TEXT,          -- JSON: raw fields + engine
    verification TEXT,        -- JSON: {score, checks:[{check,passed,detail}]}
    status TEXT,              -- DRAFT | VERIFIED | NOTIFIED | CLOSED | OBJECTED
    letter TEXT,              -- final closure letter (family's language)
    objection_token TEXT,     -- proof-of-life: secret link sent to the deceased's own address
    objection_deadline TEXT,  -- irreversible actions wait until this time
    objected_at TEXT, objection_note TEXT,
    redteam INTEGER DEFAULT 0,  -- created by the "Try to break it" lab; ignored by the fraud graph for real cases
    session TEXT DEFAULT '',  -- demo world of one visitor (hosted demo); '' = the shared/real world
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY, case_id TEXT, kind TEXT, to_addr TEXT, subject TEXT, body TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY, case_id TEXT,
    merchant_name TEXT, merchant_email TEXT, merchant_id TEXT,
    kind TEXT,                -- SUBSCRIPTION | INVOICE | ONE_OFF | PERSONAL
    recurring INTEGER, cadence TEXT, typical_amount REAL, currency TEXT,
    references_json TEXT,     -- JSON {subscription_ids, transaction_ids, invoice_ids}
    charges_after_death TEXT, -- JSON [{date, amount, transaction_id}]
    owed_back REAL, reason TEXT, selected INTEGER,
    period_days REAL, next_charge TEXT, yearly_cost REAL, price_note TEXT,
    history TEXT,             -- JSON [{date, amount, after_death}] for the timeline
    notice_id TEXT, status TEXT  -- FOUND | NOTIFIED | RECEIVED | CLOSED | NOT_ON_NETWORK
);
-- ---------------------------------------------------------------- merchant side
CREATE TABLE IF NOT EXISTS merchants (
    id TEXT PRIMARY KEY, name TEXT, category TEXT, support_email TEXT,
    paypal_email TEXT, auto_close INTEGER, auto_refund_cap REAL, accent TEXT
);
CREATE TABLE IF NOT EXISTS merchant_customers (
    id TEXT PRIMARY KEY, merchant_id TEXT, customer_name TEXT, email TEXT,
    paypal_subscription_id TEXT, plan_name TEXT, amount REAL, currency TEXT, status TEXT,
    emails_suppressed INTEGER DEFAULT 0,
    session TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS merchant_invoices (
    id TEXT PRIMARY KEY, merchant_id TEXT, email TEXT, paypal_invoice_id TEXT,
    description TEXT, amount REAL, currency TEXT, status TEXT,
    session TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notices (
    id TEXT PRIMARY KEY, case_id TEXT, merchant_id TEXT,
    payload TEXT,             -- JSON: the signed notice body
    signature TEXT,
    status TEXT,              -- SENT | RECEIVED | IN_REVIEW | ACTIONED | CLOSED | REJECTED
    review TEXT,              -- JSON: merchant-side checks + matches
    outcome TEXT,             -- JSON: {cancelled:[], refunded:[{id, amount}], invoices_cancelled:[], refunded_total}
    reply TEXT,               -- merchant's letter to the family
    receipt TEXT, receipt_sig TEXT,  -- closure receipt signed with the merchant's own Ed25519 key
    confirmation TEXT,        -- JSON: hub's independent re-read of PayPal after closing
    received_at TEXT, closed_at TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY, notice_id TEXT, merchant_id TEXT,
    tool TEXT, params TEXT, rationale TEXT, policy_checks TEXT,
    status TEXT,              -- PENDING | EXECUTING | EXECUTED | REJECTED | FAILED | EXPIRED
    proposed_by TEXT, decided_by TEXT, result TEXT,
    params_hash TEXT, expires_at TEXT, amount REAL,
    phase TEXT,               -- now (reversible) | after_window (irreversible)
    not_before TEXT,          -- the gateway refuses to run it earlier (objection window)
    preapproved_by TEXT,      -- a person approved it early; it runs when the window passes
    attempts INTEGER,         -- PayPal attempts so far (transient failures are retried with the same Request-Id)
    created_at TEXT, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
-- ---------------------------------------------------------------- shared audit trail
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, actor TEXT, action TEXT, ref TEXT, detail TEXT,
    prev_hash TEXT,
    hash TEXT                 -- sha256 chain: editing any row breaks every later hash
);
CREATE TRIGGER IF NOT EXISTS audit_append_only_u BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_append_only_d BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""

JSON_COLS = {"history", "network", "receipt", "confirmation", "extraction", "verification", "references_json", "charges_after_death", "payload",
             "review", "outcome", "params", "policy_checks", "detail"}
TABLES = ("cases", "findings", "merchants", "merchant_customers", "merchant_invoices", "notices", "pending_actions", "outbox")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def later(days: int = 0, hours: int = 0, seconds: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours, seconds=seconds)).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


_conn: "_Locked | None" = None


class _Rows:
    """Query results read out while the lock was held, so no thread ever reads a cursor another thread moved."""
    def __init__(self, rows: list):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _Locked:
    """One SQLite connection shared by the web workers and the background ticker. Every call holds the
    process-wide lock: without it, one thread's COMMIT can land between another's check and COMMIT
    ("cannot commit - no transaction is active"), which shows up on slower machines such as Windows."""
    def __init__(self, raw: sqlite3.Connection):
        self.raw = raw

    def execute(self, sql: str, params: tuple | list = ()) -> _Rows:
        with _lock:
            return _Rows(self.raw.execute(sql, params).fetchall())

    def executescript(self, script: str) -> None:
        with _lock:
            self.raw.executescript(script)

    def commit(self) -> None:
        with _lock:
            self.raw.commit()


def conn() -> _Locked:
    global _conn
    with _lock:
        if _conn is None:
            raw = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
            raw.row_factory = sqlite3.Row
            raw.executescript(SCHEMA)
            _migrate(raw)
            raw.commit()
            _conn = _Locked(raw)
    return _conn


def _migrate(c: sqlite3.Connection) -> None:
    """Adds columns introduced after a database file was first created (CREATE TABLE IF NOT EXISTS won't)."""
    for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \(\n(.*?)\n\);", SCHEMA, re.S):
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for line in body.split("\n"):
            for part in re.sub(r"--.*", "", line).split(","):
                bits = part.split()
                if len(bits) >= 2 and bits[0].isidentifier() and bits[0].upper() not in ("PRIMARY", "FOREIGN") and bits[0] not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {bits[0]} {' '.join(bits[1:]).replace('PRIMARY KEY', '')}")
                    have.add(bits[0])


def reset() -> None:
    with _lock:
        c = conn()
        for t in TABLES:
            c.execute(f"DELETE FROM {t}")
        c.execute("DROP TABLE IF EXISTS audit_log")  # demo reset only; triggers go with the table
        for t in ("mock_subscriptions", "mock_captures", "mock_invoices", "mock_refunds"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
        c.executescript(SCHEMA)
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
    return {k: (json.dumps(v, default=str) if k in JSON_COLS and v is not None and not isinstance(v, str) else v)
            for k, v in values.items()}


def insert(table: str, values: dict) -> dict:
    enc = _encode(values)
    with _lock:
        conn().execute(f"INSERT INTO {table} ({', '.join(enc)}) VALUES ({', '.join('?' for _ in enc)})", list(enc.values()))
        conn().commit()
    return values


def update(table: str, row_id: Any, values: dict) -> None:
    enc = _encode(values)
    with _lock:
        conn().execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in enc)} WHERE id = ?", [*enc.values(), row_id])
        conn().commit()


def claim(table: str, row_id: Any, expect_status: str, values: dict) -> bool:
    """Compare-and-set on status: True only for the one caller that moved the row out of expect_status."""
    enc = _encode(values)
    with _lock:
        cur = conn().raw.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in enc)} WHERE id = ? AND status = ?",
                                 [*enc.values(), row_id, expect_status])
        n = cur.rowcount
        conn().commit()
    return n == 1


def get(table: str, row_id: Any) -> dict | None:
    with _lock:
        return _decode(conn().execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone())


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        return [_decode(r) for r in conn().execute(sql, params).fetchall()]


def kv_get(k: str) -> str | None:
    r = query("SELECT v FROM kv WHERE k=?", (k,))
    return r[0]["v"] if r else None


def kv_set(k: str, v: str) -> None:
    with _lock:
        conn().execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (k, v))
        conn().commit()


# ---------------------------------------------------------------- hash-chained audit
GENESIS = "0" * 64


def _row_hash(prev: str, ts: str, actor: str, action: str, ref: str | None, detail: str | None) -> str:
    return hashlib.sha256(json.dumps([prev, ts, actor, action, ref, detail], separators=(",", ":")).encode()).hexdigest()


def audit(actor: str, action: str, ref: str | None = None, detail: Any = None) -> None:
    with _lock:
        last = conn().execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev = last["hash"] if last and last["hash"] else GENESIS
        ts = now()
        d = json.dumps(detail, default=str, sort_keys=True) if detail is not None else None
        conn().execute(
            "INSERT INTO audit_log (ts, actor, action, ref, detail, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, actor, action, ref, d, prev, _row_hash(prev, ts, actor, action, ref, d)),
        )
        conn().commit()


def verify_audit_chain() -> dict:
    """Recompute every hash. Any edited, inserted or deleted row breaks the chain from that point."""
    prev, n = GENESIS, 0
    with _lock:
        rows = conn().execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    for r in rows:
        expected = _row_hash(prev, r["ts"], r["actor"], r["action"], r["ref"], r["detail"])
        if r["prev_hash"] != prev or r["hash"] != expected:
            return {"ok": False, "rows_checked": n, "broken_at_id": r["id"], "head": prev}
        prev, n = r["hash"], n + 1
    return {"ok": True, "rows_checked": n, "head": prev}
