"""Web app: JSON API + the screens in luckydraw/static."""
import hmac
import io
import json
import os
import re
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, Response as RawResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import auth, config, db, engine, export, sync, tally

service = sync.SyncService()


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    service.load_state()
    if os.environ.get("LUCKYDRAW_NO_BACKGROUND") != "1":
        service.start()
    yield
    service.stop_event.set()
    service.wake.set()


app = FastAPI(title="Gauri Ganpati Lucky Draw", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(LookupError)
async def lookup_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.middleware("http")
async def no_cache(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/static", "/api")) or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


# ------------------------------------------------------------------ models

class LoginIn(BaseModel):
    username: str
    password: str


class PhoneIn(BaseModel):
    phone: str
    name: str = ""
    city: str = ""


class CustomerIn(BaseModel):
    name: str | None = None
    city: str | None = None
    phone: str | None = None


class PrizeIn(BaseModel):
    name: str
    quantity: int = 1


class DrawIn(BaseModel):
    count: int = 1
    prize_id: int | None = None


class CancelIn(BaseModel):
    reason: str
    put_back: bool = False


class AssignIn(BaseModel):
    prize_id: int | None = None


class SyncIn(BaseModel):
    mode: str = "quick"


class CloseIn(BaseModel):
    force: bool = False


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "staff"


class UserEdit(BaseModel):
    password: str | None = None
    role: str | None = None
    active: bool | None = None


# ------------------------------------------------------------------ login

@app.get("/api/me")
def me(request: Request):
    with db.read() as conn:
        has_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
        s = db.all_settings(conn)
    user = auth.current_user(request)
    return {
        "setup_needed": not has_users, "user": user,
        "config": {"coupon_digits": int(s["coupon_digits"]), "campaign_name": s["campaign_name"],
                   "per_coupon": int(float(s["amount_per_coupon"])), "closed": s["closed"] == "1"},
    }


def _is_https(request):
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _set_session_cookie(response, request, token):
    response.set_cookie("ld_session", token, httponly=True, samesite="lax", max_age=12 * 3600,
                        secure=_is_https(request), path=config.BASE_PATH or "/")


def _check_password(pw):
    if len(pw) < 6:
        raise ValueError("Password must be at least 6 characters.")


@app.post("/api/setup")
def setup(body: LoginIn, request: Request, response: Response):
    with db.tx() as conn:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            raise HTTPException(400, "Setup is already done.")
        _check_password(body.password)
        uid = conn.execute("INSERT INTO users(username, password_hash, role) VALUES (?, ?, 'admin')",
                           (body.username.strip(), auth.hash_password(body.password))).lastrowid
        token = auth.create_session(conn, uid)
        db.audit(conn, body.username, "setup", "first admin created")
    _set_session_cookie(response, request, token)
    return {"ok": True}


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    auth.check_rate_limit(ip)
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ? AND active = 1", (body.username.strip(),)).fetchone()
        if not row or not auth.verify_password(body.password, row["password_hash"]):
            auth.record_failure(ip)
            raise HTTPException(401, "Wrong username or password.")
        token = auth.create_session(conn, row["id"])
    _set_session_cookie(response, request, token)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("ld_session")
    if token:
        with db.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    response.delete_cookie("ld_session", path=config.BASE_PATH or "/")
    return {"ok": True}


# ------------------------------------------------------------------ dashboard

@app.get("/api/dashboard")
def dashboard(user=Depends(auth.require_user)):
    with db.read() as conn:
        c = engine.cfg(conn)
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM sales GROUP BY status")}
        amount = conn.execute(
            "SELECT COALESCE(SUM(amount_paise),0) FROM sales WHERE status IN ('active','no_coupon','waiting_phone')").fetchone()[0]
        today = date.today().isoformat()
        out = {
            "bills": counts,
            "bills_live": sum(counts.get(k, 0) for k in ("active", "no_coupon", "waiting_phone")),
            "customers": conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
            "coupons_issued": conn.execute("SELECT COUNT(*) FROM coupons WHERE status IN ('valid','won')").fetchone()[0],
            "coupons_today": conn.execute(
                "SELECT COUNT(*) FROM coupons WHERE status IN ('valid','won') AND created_at LIKE ?", (today + "%",)).fetchone()[0],
            "amount_rupees": amount / 100,
            "waiting_phone": counts.get("waiting_phone", 0),
            "winners": conn.execute("SELECT COUNT(*) FROM winners WHERE status='active'").fetchone()[0],
            "total_prizes": c["total_prizes"],
            "campaign": {"name": c["raw"]["campaign_name"], "start": c["start"], "end": c["end"],
                         "per_coupon": c["per_coupon"], "closed": c["closed"]},
            "backup": {"at": c["raw"]["last_backup_at"], "note": c["raw"]["last_backup_note"]},
            "sync_enabled": c["raw"]["sync_enabled"] == "1",
        }
    st = service.status()
    if c["closed"]:
        light = "closed"
    elif st["source"] == "connector" and not st["connector_online"]:
        light = "red" if st["connector_last_seen"] else "amber"
    elif st["consecutive_failures"] >= 3:
        light = "red"
    elif st["last"]["ok"] is False:
        light = "amber"
    elif st["last"]["ok"]:
        light = "green"
    else:
        light = "amber"
    out["tally"] = {**st, "light": light}
    return out


# ------------------------------------------------------------------ bills

def _coupon_numbers(conn, sale_ids):
    if not sale_ids:
        return {}
    marks = ",".join("?" * len(sale_ids))
    out = {}
    for r in conn.execute(
            f"SELECT sale_id, number, status FROM coupons WHERE sale_id IN ({marks}) AND status IN ('valid','won') ORDER BY number",
            sale_ids):
        out.setdefault(r["sale_id"], []).append({"number": r["number"], "status": r["status"]})
    return out


@app.get("/api/sales")
def sales(status: str = "all", q: str = "", limit: int = 100, offset: int = 0, user=Depends(auth.require_user)):
    where, params = [], []
    if status != "all":
        where.append("s.status = ?")
        params.append(status)
    q = q.strip()
    if q:
        like = f"%{q}%"
        where.append("(s.voucher_no LIKE ? OR s.party_ledger LIKE ? OR s.raw_name LIKE ? OR s.phone LIKE ? OR cu.name LIKE ? OR CAST(s.id AS TEXT) = ?)")
        params += [like, like, like, like, like, q]
    w = ("WHERE " + " AND ".join(where)) if where else ""
    with db.read() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM sales s LEFT JOIN customers cu ON cu.id=s.customer_id {w}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT s.id, s.voucher_no, s.voucher_date, s.party_ledger, s.amount_paise, s.status, s.status_note,
                       s.coupons_earned, s.phone, s.raw_name, s.raw_city, s.raw_address, s.customer_id,
                       cu.name AS customer_name, cu.city AS customer_city
                FROM sales s LEFT JOIN customers cu ON cu.id = s.customer_id {w}
                ORDER BY s.id DESC LIMIT ? OFFSET ?""", params + [min(limit, 500), offset]).fetchall()
        nums = _coupon_numbers(conn, [r["id"] for r in rows])
        items = []
        for r in rows:
            d = dict(r)
            d["coupons"] = nums.get(r["id"], [])
            items.append(d)
    return {"total": total, "items": items}


@app.post("/api/sales/{sale_id}/phone")
def sale_phone(sale_id: int, body: PhoneIn, user=Depends(auth.require_user)):
    with db.tx() as conn:
        if engine.cfg(conn)["closed"]:
            raise ValueError("The campaign is closed, so coupons cannot change. An admin can reopen it.")
        status, earned = engine.set_sale_phone(conn, sale_id, body.phone, body.name, body.city, user["username"])
    return {"status": status, "coupons": earned}


@app.get("/api/sales/{sale_id}/slip")
def slip(sale_id: int, user=Depends(auth.require_user)):
    with db.read() as conn:
        s = conn.execute(
            """SELECT s.*, cu.name AS customer_name, cu.city AS customer_city, cu.phone AS customer_phone
               FROM sales s LEFT JOIN customers cu ON cu.id=s.customer_id WHERE s.id=?""", (sale_id,)).fetchone()
        if not s:
            raise LookupError("Bill not found.")
        nums = _coupon_numbers(conn, [sale_id]).get(sale_id, [])
        c = engine.cfg(conn)
    return {
        "campaign": c["raw"]["campaign_name"], "entry_no": s["id"], "bill_no": s["voucher_no"], "date": s["voucher_date"],
        "amount": s["amount_paise"] / 100, "name": s["customer_name"] or s["raw_name"], "city": s["customer_city"] or s["raw_city"],
        "phone": s["customer_phone"] or s["phone"], "coupons": [n["number"] for n in nums], "digits": c["digits"],
        "status": s["status"],
    }


# ------------------------------------------------------------------ customers

@app.get("/api/customers")
def customers(q: str = "", limit: int = 100, offset: int = 0, user=Depends(auth.require_user)):
    q = q.strip()
    like = f"%{q}%"
    cond = "WHERE (? = '' OR cu.name LIKE ? OR cu.phone LIKE ? OR cu.city LIKE ?)"
    with db.read() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM customers cu {cond}", (q, like, like, like)).fetchone()[0]
        rows = conn.execute(
            f"""SELECT cu.id, cu.name, cu.phone, cu.city,
                 (SELECT COUNT(*) FROM sales s WHERE s.customer_id=cu.id AND s.status IN ('active','no_coupon')) AS bills,
                 (SELECT COALESCE(SUM(amount_paise),0) FROM sales s WHERE s.customer_id=cu.id AND s.status IN ('active','no_coupon')) AS spend_paise,
                 (SELECT COUNT(*) FROM coupons k WHERE k.customer_id=cu.id AND k.status IN ('valid','won')) AS coupons,
                 (SELECT COUNT(*) FROM winners w WHERE w.customer_id=cu.id AND w.status='active') AS won
                FROM customers cu {cond} ORDER BY cu.name COLLATE NOCASE LIMIT ? OFFSET ?""",
            (q, like, like, like, min(limit, 500), offset)).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}


@app.get("/api/customers/{cid}")
def customer(cid: int, user=Depends(auth.require_user)):
    with db.read() as conn:
        cu = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        if not cu:
            raise LookupError("Customer not found.")
        sales_rows = conn.execute(
            "SELECT id, voucher_no, voucher_date, amount_paise, status, status_note, coupons_earned FROM sales WHERE customer_id=? ORDER BY id DESC",
            (cid,)).fetchall()
        nums = _coupon_numbers(conn, [r["id"] for r in sales_rows])
        sales_list = []
        for r in sales_rows:
            d = dict(r)
            d["coupons"] = nums.get(r["id"], [])
            sales_list.append(d)
        coupons = conn.execute(
            "SELECT number, status, void_reason, sale_id FROM coupons WHERE customer_id=? ORDER BY number", (cid,)).fetchall()
        wins = engine.winner_rows(conn, "WHERE w.customer_id = ?", (cid,))
    return {"customer": dict(cu), "sales": sales_list, "coupons": [dict(r) for r in coupons], "winners": wins}


@app.put("/api/customers/{cid}")
def edit_customer(cid: int, body: CustomerIn, user=Depends(auth.require_user)):
    with db.tx() as conn:
        engine.edit_customer(conn, cid, body.name, body.city, body.phone, user["username"])
    return {"ok": True}


@app.get("/api/search")
def search(q: str, user=Depends(auth.require_user)):
    q = q.strip()
    if not q:
        return {"customers": [], "coupon": None, "sales": []}
    like = f"%{q}%"
    out = {"coupon": None}
    with db.read() as conn:
        digits = re.sub(r"\D", "", q)
        if digits and len(digits) <= 7 and digits == q.replace(" ", ""):
            out["coupon"] = _coupon_detail(conn, int(digits))
        out["customers"] = [dict(r) for r in conn.execute(
            "SELECT id, name, phone, city FROM customers WHERE name LIKE ? OR phone LIKE ? OR city LIKE ? "
            "ORDER BY name COLLATE NOCASE LIMIT 15", (like, like, like))]
        out["sales"] = [dict(r) for r in conn.execute(
            "SELECT id, voucher_no, voucher_date, amount_paise, status, customer_id FROM sales "
            "WHERE voucher_no LIKE ? OR CAST(id AS TEXT) = ? ORDER BY id DESC LIMIT 10", (like, q))]
    return out


def _coupon_detail(conn, number):
    r = conn.execute(
        """SELECT k.number, k.status, k.void_reason, k.sale_id, k.customer_id, s.voucher_no, s.voucher_date, s.amount_paise,
                  cu.name, cu.city, cu.phone
           FROM coupons k JOIN sales s ON s.id=k.sale_id JOIN customers cu ON cu.id=k.customer_id WHERE k.number=?""",
        (number,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["winner"] = (engine.winner_rows(conn, "WHERE c.number = ? AND w.status = 'active'", (number,)) or [None])[0]
    return d


@app.get("/api/coupons/{number}")
def coupon(number: int, user=Depends(auth.require_user)):
    with db.read() as conn:
        d = _coupon_detail(conn, number)
    if not d:
        raise LookupError("No coupon with that number.")
    return d


# ------------------------------------------------------------------ sync / tally

@app.post("/api/sync")
def sync_now(body: SyncIn, user=Depends(auth.require_user)):
    if body.mode == "full" and user["role"] != "admin":
        raise HTTPException(403, "Only an admin can run a full check.")
    with db.read() as conn:
        if engine.cfg(conn)["closed"]:
            raise ValueError("The campaign is closed, so syncing is off.")
    service.trigger("full" if body.mode == "full" else "quick", user["username"])
    return {"started": True}


@app.get("/api/sync/log")
def sync_log(user=Depends(auth.require_user)):
    with db.read() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 100")]


@app.post("/api/tally/test")
def tally_test(user=Depends(auth.require_admin)):
    with db.read() as conn:
        s = db.all_settings(conn)
    if s["tally_source"] == "connector":
        raise ValueError("This software reads Tally through the connector. Use Tally setup > Check Tally now.")
    today = date.today()
    body = tally.build_request((today - timedelta(days=30)).isoformat(), today.isoformat(), s["tally_company"])
    data = tally.fetch(s["tally_host"], s["tally_port"], body, timeout=60)
    everything = tally.parse_vouchers(data, allowed_types=())
    sales_v = tally.parse_vouchers(data, s["voucher_types"].split(","), [t.strip() for t in s["extra_scan_tags"].split(",")])
    return {
        "ok": True, "vouchers": len(everything), "sales": len(sales_v),
        "with_phone": sum(1 for v in sales_v if v.phone),
        "types": sorted({v.voucher_type for v in everything}),
        "sample": [{"no": v.voucher_no, "date": v.date, "amount": v.amount_paise / 100, "name": v.name,
                    "phone": v.phone, "city": v.city} for v in sales_v[:5]],
    }


# ------------------------------------------------------------------ connector (Tally PC -> this server)

def _connector_auth(request: Request):
    ip = request.client.host if request.client else "?"
    auth.check_rate_limit(ip)
    key = request.headers.get("x-connector-key", "")
    with db.read() as conn:
        real = db.get_setting(conn, "connector_key")
    if not real or not hmac.compare_digest(key.encode("utf-8"), real.encode("utf-8")):
        auth.record_failure(ip)
        raise HTTPException(401, "Wrong connector key.")
    return ip


class PollIn(BaseModel):
    version: str = ""
    computer: str = ""


class ConnErrIn(BaseModel):
    message: str = ""


@app.post("/api/connector/poll")
def connector_poll(body: PollIn, request: Request):
    ip = _connector_auth(request)
    service.touch_connector({"version": body.version[:20], "computer": body.computer[:60], "ip": ip})
    return {"job": service.next_job(), "wait": sync.POLL_WAIT}


@app.post("/api/connector/result/{job_id}")
async def connector_result(job_id: str, request: Request):
    _connector_auth(request)
    data = await request.body()
    result = await run_in_threadpool(service.accept_result, job_id, data)
    return {"ok": result.get("ok", False), "message": result.get("message", "")}


@app.post("/api/connector/error/{job_id}")
def connector_error(job_id: str, body: ConnErrIn, request: Request):
    _connector_auth(request)
    service.accept_error(job_id, body.message)
    return {"ok": True}


def _public_url(request: Request):
    return config.PUBLIC_URL or (str(request.base_url).rstrip("/") + config.BASE_PATH)


@app.get("/api/connector/status")
def connector_status(request: Request, user=Depends(auth.require_admin)):
    with db.read() as conn:
        s = db.all_settings(conn)
    st = service.status()
    try:
        info = json.loads(s["connector_info"] or "{}")
    except ValueError:
        info = {}
    try:
        probe = json.loads(s["last_probe"] or "null")
    except ValueError:
        probe = None
    return {"source": s["tally_source"], "online": st["connector_online"], "last_seen": st["connector_last_seen"],
            "info": info, "probe": probe, "probe_pending": service.probe_pending or bool(service.job and service.job["kind"] == "probe"),
            "key_tail": s["connector_key"][-4:], "public_url": _public_url(request),
            "has_probe_file": (config.DATA_DIR / "probe-latest.xml").exists()}


@app.post("/api/connector/probe")
def connector_probe(user=Depends(auth.require_admin)):
    service.request_probe()
    return {"ok": True}


@app.get("/api/connector/probe.xml")
def connector_probe_xml(user=Depends(auth.require_admin)):
    path = config.DATA_DIR / "probe-latest.xml"
    if not path.exists():
        raise LookupError("No Tally reply has been saved yet. Press Check Tally now first.")
    return FileResponse(path, media_type="application/xml", filename="tally_probe_output.xml")


@app.post("/api/connector/regenerate-key")
def connector_regenerate(user=Depends(auth.require_admin)):
    import secrets
    with db.tx() as conn:
        db.set_setting(conn, "connector_key", secrets.token_urlsafe(24))
        db.audit(conn, user["username"], "connector key replaced", "the old connector stops working until the new one is installed")
    return {"ok": True}


@app.get("/api/connector/download")
def connector_download(request: Request, user=Depends(auth.require_admin)):
    with db.read() as conn:
        s = db.all_settings(conn)
    ini = ("# Settings for the Tally connector. Do not share this file: it holds the key.\n"
           f"server={_public_url(request)}\n"
           f"key={s['connector_key']}\n"
           "tally=http://localhost:9000\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(config.CONNECTOR_DIR.iterdir()):
            if f.is_file():
                text = f.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
                z.writestr(f"LuckyDraw-Connector/{f.name}", text)
        z.writestr("LuckyDraw-Connector/connector.ini", ini.replace("\n", "\r\n"))
    with db.tx() as conn:
        db.audit(conn, user["username"], "connector downloaded", "")
    return RawResponse(buf.getvalue(), media_type="application/zip",
                       headers={"Content-Disposition": 'attachment; filename="LuckyDraw-Connector.zip"'})


# ------------------------------------------------------------------ settings

EDITABLE = ["tally_source", "tally_host", "tally_port", "tally_company", "voucher_types", "extra_scan_tags", "amount_per_coupon",
            "campaign_name", "campaign_start", "campaign_end", "sync_enabled", "sync_interval_seconds",
            "quick_window_days", "full_check_minutes", "total_prizes", "coupon_digits", "backup_dir",
            "backup_interval_minutes"]


@app.get("/api/settings")
def get_settings(user=Depends(auth.require_admin)):
    with db.read() as conn:
        s = db.all_settings(conn)
    return {k: s[k] for k in EDITABLE}


@app.put("/api/settings")
def put_settings(body: dict, user=Depends(auth.require_admin)):
    with db.tx() as conn:
        old = db.all_settings(conn)
        new = {k: str(body[k]).strip() for k in EDITABLE if k in body}
        for k in ("amount_per_coupon", "sync_interval_seconds", "quick_window_days", "full_check_minutes",
                  "total_prizes", "coupon_digits", "backup_interval_minutes", "tally_port"):
            if k in new and not re.fullmatch(r"\d+(\.\d+)?", new[k]):
                raise ValueError(f"{k.replace('_', ' ')} must be a number.")
        for k in ("campaign_start", "campaign_end"):
            if k in new:
                datetime.strptime(new[k], "%Y-%m-%d")
        if "amount_per_coupon" in new and float(new["amount_per_coupon"]) <= 0:
            raise ValueError("Amount per coupon must be more than zero.")
        if "tally_source" in new and new["tally_source"] not in ("direct", "connector"):
            raise ValueError("Tally source must be direct or connector.")
        rules_changed = any(k in new and new[k] != old[k] for k in ("amount_per_coupon", "campaign_start", "campaign_end"))
        if rules_changed and conn.execute("SELECT COUNT(*) FROM winners").fetchone()[0]:
            raise ValueError("The coupon amount and campaign dates cannot change after winners have been drawn.")
        for k, v in new.items():
            db.set_setting(conn, k, v)
        recomputed = engine.recompute_all(conn) if rules_changed else 0
        db.audit(conn, user["username"], "settings changed",
                 ", ".join(f"{k}={v}" for k, v in new.items() if old.get(k) != v) or "no change")
    return {"ok": True, "bills_rechecked": recomputed}


@app.post("/api/backup/run")
def backup_run(user=Depends(auth.require_admin)):
    ok, note = sync.do_backup(user["username"])
    return {"ok": ok, "note": note}


# ------------------------------------------------------------------ campaign

@app.post("/api/campaign/close")
def campaign_close(body: CloseIn, user=Depends(auth.require_admin)):
    result = {"ok": True, "message": "No final check was needed."}
    deadline = time.time() + 110
    while True:
        result = service.run_sync("full", user["username"])
        if result.get("message") != "A sync is already running." or time.time() > deadline:
            break
        time.sleep(2)
    if not result.get("ok") and not body.force:
        raise ValueError("The final check with Tally failed: " + result.get("message", "") +
                         " Fix it, or close anyway if you are sure all bills are in.")
    with db.tx() as conn:
        db.set_setting(conn, "closed", "1")
        db.audit(conn, user["username"], "campaign closed", result.get("message", ""))
    sync.do_backup(user["username"])
    return {"ok": True, "final_check": result}


@app.post("/api/campaign/reopen")
def campaign_reopen(user=Depends(auth.require_admin)):
    with db.tx() as conn:
        db.set_setting(conn, "closed", "0")
        db.audit(conn, user["username"], "campaign reopened", "")
    return {"ok": True}


# ------------------------------------------------------------------ prizes / draw / winners

@app.get("/api/prizes")
def prizes(user=Depends(auth.require_user)):
    with db.read() as conn:
        rows = conn.execute(
            """SELECT p.id, p.name, p.quantity, p.sort_order,
                      (SELECT COUNT(*) FROM winners w WHERE w.prize_id=p.id AND w.status='active') AS drawn
               FROM prizes p ORDER BY p.sort_order, p.id""").fetchall()
        c = engine.cfg(conn)
        unnamed = conn.execute("SELECT COUNT(*) FROM winners WHERE prize_id IS NULL AND status='active'").fetchone()[0]
    items = [{**dict(r), "remaining": r["quantity"] - r["drawn"]} for r in rows]
    listed = sum(i["quantity"] for i in items)
    return {"items": items, "total_prizes": c["total_prizes"], "listed": listed,
            "not_yet_named": max(c["total_prizes"] - listed, 0) if c["total_prizes"] else 0,
            "winners_without_prize": unnamed}


@app.post("/api/prizes")
def prize_add(body: PrizeIn, user=Depends(auth.require_admin)):
    if not body.name.strip() or body.quantity < 1:
        raise ValueError("Give the prize a name and a quantity of at least 1.")
    with db.tx() as conn:
        order = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM prizes").fetchone()[0]
        pid = conn.execute("INSERT INTO prizes(name, quantity, sort_order) VALUES (?, ?, ?)",
                           (body.name.strip(), body.quantity, order)).lastrowid
        db.audit(conn, user["username"], "prize added", f"{body.name} x {body.quantity}")
    return {"id": pid}


@app.put("/api/prizes/{pid}")
def prize_edit(pid: int, body: PrizeIn, user=Depends(auth.require_admin)):
    if not body.name.strip() or body.quantity < 1:
        raise ValueError("Give the prize a name and a quantity of at least 1.")
    with db.tx() as conn:
        drawn = conn.execute("SELECT COUNT(*) FROM winners WHERE prize_id=? AND status='active'", (pid,)).fetchone()[0]
        if body.quantity < drawn:
            raise ValueError(f"{drawn} winners are already drawn for this prize, so the quantity cannot be lower.")
        conn.execute("UPDATE prizes SET name=?, quantity=? WHERE id=?", (body.name.strip(), body.quantity, pid))
        db.audit(conn, user["username"], "prize edited", f"{pid}: {body.name} x {body.quantity}")
    return {"ok": True}


@app.delete("/api/prizes/{pid}")
def prize_delete(pid: int, user=Depends(auth.require_admin)):
    with db.tx() as conn:
        if conn.execute("SELECT 1 FROM winners WHERE prize_id=?", (pid,)).fetchone():
            raise ValueError("Winners are already linked to this prize, so it cannot be deleted.")
        conn.execute("DELETE FROM prizes WHERE id=?", (pid,))
        db.audit(conn, user["username"], "prize deleted", str(pid))
    return {"ok": True}


@app.get("/api/draw/status")
def draw_status(user=Depends(auth.require_user)):
    with db.read() as conn:
        return engine.draw_status(conn)


@app.post("/api/draw")
def draw(body: DrawIn, user=Depends(auth.require_admin)):
    with db.tx() as conn:
        winners = engine.draw_winners(conn, user["username"], min(max(body.count, 1), 25), body.prize_id)
    return {"winners": winners}


@app.get("/api/winners")
def winners(user=Depends(auth.require_user)):
    with db.read() as conn:
        return engine.winner_rows(conn)


@app.post("/api/winners/{wid}/cancel")
def winner_cancel(wid: int, body: CancelIn, user=Depends(auth.require_admin)):
    with db.tx() as conn:
        engine.cancel_winner(conn, wid, body.reason, body.put_back, user["username"])
    return {"ok": True}


@app.put("/api/winners/{wid}/prize")
def winner_prize(wid: int, body: AssignIn, user=Depends(auth.require_admin)):
    with db.tx() as conn:
        engine.assign_prize(conn, wid, body.prize_id, user["username"])
    return {"ok": True}


# ------------------------------------------------------------------ users / audit / export

@app.get("/api/users")
def users(user=Depends(auth.require_admin)):
    with db.read() as conn:
        return [dict(r) for r in conn.execute("SELECT id, username, role, active FROM users ORDER BY id")]


@app.post("/api/users")
def user_add(body: UserIn, user=Depends(auth.require_admin)):
    if body.role not in ("admin", "staff") or not body.username.strip():
        raise ValueError("Give a username and a role of admin or staff.")
    _check_password(body.password)
    with db.tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (body.username.strip(),)).fetchone():
            raise ValueError("That username already exists.")
        conn.execute("INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)",
                     (body.username.strip(), auth.hash_password(body.password), body.role))
        db.audit(conn, user["username"], "user added", f"{body.username} ({body.role})")
    return {"ok": True}


@app.put("/api/users/{uid}")
def user_edit(uid: int, body: UserEdit, user=Depends(auth.require_admin)):
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise LookupError("User not found.")
        role = body.role or row["role"]
        active = row["active"] if body.active is None else int(body.active)
        if role not in ("admin", "staff"):
            raise ValueError("Role must be admin or staff.")
        admins_left = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND id<>?", (uid,)).fetchone()[0]
        if admins_left == 0 and (role != "admin" or not active):
            raise ValueError("There must be at least one active admin.")
        if body.password:
            _check_password(body.password)
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(body.password), uid))
        conn.execute("UPDATE users SET role=?, active=? WHERE id=?", (role, active, uid))
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        db.audit(conn, user["username"], "user edited", f"{row['username']}: role={role}, active={active}")
    return {"ok": True}


@app.get("/api/audit")
def audit_log(user=Depends(auth.require_admin)):
    with db.read() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300")]


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.get("/api/export/all.xlsx")
def export_all(user=Depends(auth.require_admin)):
    with db.read() as conn:
        data = export.build_workbook(conn)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="lucky-draw-all-{stamp}.xlsx"'})


@app.get("/api/export/winners.xlsx")
def export_winners(user=Depends(auth.require_user)):
    with db.read() as conn:
        data = export.build_workbook(conn, only_winners=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="lucky-draw-winners-{stamp}.xlsx"'})
