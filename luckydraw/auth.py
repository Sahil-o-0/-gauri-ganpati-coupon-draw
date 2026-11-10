"""Logins: PBKDF2 password hashes and cookie sessions. Roles: admin, staff."""
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from . import db

ITERATIONS = 200_000
SESSION_HOURS = 12
_failures = {}  # ip -> [timestamps]


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        _, iters, salt, digest = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(test.hex(), digest)
    except (ValueError, TypeError):
        return False


def check_rate_limit(ip):
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < 300]
    _failures[ip] = recent
    if len(recent) >= 8:
        raise HTTPException(429, "Too many wrong passwords. Wait 5 minutes and try again.")


def record_failure(ip):
    _failures.setdefault(ip, []).append(time.time())


def create_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions(token, user_id, created_at) VALUES (?, ?, ?)", (token, user_id, db.now()))
    conn.execute("DELETE FROM sessions WHERE created_at < ?",
                 ((datetime.now() - timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds"),))
    return token


def current_user(request: Request):
    token = request.cookies.get("ld_session")
    if not token:
        return None
    with db.read() as conn:
        row = conn.execute(
            "SELECT u.id, u.username, u.role, u.active, s.created_at FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token = ?", (token,)).fetchone()
    if not row or not row["active"]:
        return None
    if datetime.fromisoformat(row["created_at"]) < datetime.now() - timedelta(hours=SESSION_HOURS):
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def require_user(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Please log in.")
    return user


def require_admin(request: Request):
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "Only an admin can do this.")
    return user
