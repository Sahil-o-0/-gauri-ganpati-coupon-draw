"""SQLite database: schema, connection helpers and settings."""
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'staff')),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    phone TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY,                -- also the serial entry number
    guid TEXT UNIQUE NOT NULL,             -- Tally voucher GUID: stops duplicates
    voucher_no TEXT,
    voucher_date TEXT,
    party_ledger TEXT,
    amount_paise INTEGER NOT NULL DEFAULT 0,
    alter_id INTEGER,
    fingerprint TEXT,
    status TEXT NOT NULL,                  -- active, no_coupon, waiting_phone, cancelled, deleted, outside_campaign
    status_note TEXT NOT NULL DEFAULT '',
    tally_cancelled INTEGER NOT NULL DEFAULT 0,
    tally_deleted INTEGER NOT NULL DEFAULT 0,
    raw_name TEXT NOT NULL DEFAULT '',
    raw_city TEXT NOT NULL DEFAULT '',
    raw_address TEXT NOT NULL DEFAULT '',
    raw_phone TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',        -- phone used for this sale (manual entry wins)
    phone_manual INTEGER NOT NULL DEFAULT 0,
    customer_id INTEGER REFERENCES customers(id),
    coupons_earned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sales_status ON sales(status);
CREATE INDEX IF NOT EXISTS idx_sales_customer ON sales(customer_id);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(voucher_date);

CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY,
    number INTEGER UNIQUE NOT NULL,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL DEFAULT 'valid',  -- valid, void, won
    void_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_coupons_sale ON coupons(sale_id);
CREATE INDEX IF NOT EXISTS idx_coupons_customer ON coupons(customer_id, status);

CREATE TABLE IF NOT EXISTS prizes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS winners (
    id INTEGER PRIMARY KEY,
    seq INTEGER NOT NULL,
    prize_id INTEGER REFERENCES prizes(id),
    coupon_id INTEGER NOT NULL REFERENCES coupons(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    drawn_at TEXT,
    drawn_by TEXT,
    status TEXT NOT NULL DEFAULT 'active', -- active, cancelled
    cancel_reason TEXT NOT NULL DEFAULT '',
    keep_excluded INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_winners_customer ON winners(customer_id);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY,
    at TEXT,
    kind TEXT,
    ok INTEGER,
    fetched INTEGER DEFAULT 0,
    new_sales INTEGER DEFAULT 0,
    changed_sales INTEGER DEFAULT 0,
    message TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    at TEXT,
    user TEXT,
    action TEXT,
    detail TEXT
);
"""

DEFAULT_SETTINGS = {
    "tally_source": config.SOURCE,
    "connector_key": "",
    "connector_last_seen": "",
    "connector_info": "",
    "last_probe": "",
    "tally_host": "localhost",
    "tally_port": "9000",
    "tally_company": "",
    "voucher_types": "Sales",
    "extra_scan_tags": "",
    "amount_per_coupon": "21000",
    "campaign_name": "Gauri Ganpati Lucky Draw",
    "campaign_start": "2026-11-08",
    "campaign_end": "2027-07-05",
    "sync_enabled": "1",
    "sync_interval_seconds": "90",
    "quick_window_days": "3",
    "full_check_minutes": "60",
    "total_prizes": "142",
    "next_coupon_number": "1",
    "coupon_digits": "3",
    "backup_dir": str(config.DEFAULT_BACKUP_DIR),
    "backup_interval_minutes": "60",
    "closed": "0",
    "last_backup_at": "",
    "last_backup_note": "",
}


def now():
    return datetime.now().isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def tx():
    """One write transaction. Everything inside succeeds or nothing is saved."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


@contextmanager
def read():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        row = conn.execute("SELECT value FROM settings WHERE key = 'connector_key'").fetchone()
        if not row or not row["value"]:
            conn.execute("UPDATE settings SET value = ? WHERE key = 'connector_key'", (secrets.token_urlsafe(24),))
    finally:
        conn.close()


def get_setting(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def all_settings(conn):
    out = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value FROM settings"):
        out[row["key"]] = row["value"]
    return out


def audit(conn, user, action, detail=""):
    conn.execute(
        "INSERT INTO audit_log(at, user, action, detail) VALUES (?, ?, ?, ?)",
        (now(), user or "system", action, detail),
    )
