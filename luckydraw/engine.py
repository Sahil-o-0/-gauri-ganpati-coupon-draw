"""Core rules: bills -> customers -> coupons, and the lucky draw.

Rules (confirmed with the client):
  * One coupon per full Rs 21,000 on each bill (invoice total with GST). Bills are not added together.
  * A bill with no customer phone number is held ("waiting_phone") and earns no coupons.
  * Coupons follow the bill: higher amount issues new numbers, lower amount voids the highest ones.
  * Cancelled / deleted bills void their coupons. Coupon numbers are never reused.
  * One prize per customer.
"""
import hashlib
import re
import secrets
from dataclasses import dataclass, field

from . import db


# ---------------------------------------------------------------- phone / city

_PHONE_TIGHT = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)")
_PHONE_LOOSE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9](?:[\s-]?\d){9})(?!\d)")
_LABELS = re.compile(r"(?i)\b(mobile|mob|phone|ph|cell|contact|tel|no)\b\s*[.:\-]*")
_STATES = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh", "goa", "gujarat", "haryana",
    "himachal pradesh", "jharkhand", "karnataka", "kerala", "madhya pradesh", "maharashtra", "manipur",
    "meghalaya", "mizoram", "nagaland", "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal", "delhi", "jammu and kashmir",
    "ladakh", "chandigarh", "puducherry", "india", "mh", "gj", "mp", "up", "rj", "ka", "tn",
}


def normalize_phone(value):
    """Return a clean 10-digit Indian mobile number, or None."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def extract_phone(*texts):
    """First valid 10-digit mobile number found in the given texts (in order)."""
    for text in texts:
        if not text:
            continue
        for pattern in (_PHONE_TIGHT, _PHONE_LOOSE):
            m = pattern.search(text)
            if m:
                phone = normalize_phone(m.group(1))
                if phone:
                    return phone
    return ""


def guess_city(address_lines):
    """Best guess of the city from address lines: last line that is not a state, pin code or phone."""
    cleaned = []
    for line in address_lines:
        line = _PHONE_LOOSE.sub(" ", line or "")
        line = _LABELS.sub(" ", line)
        line = re.sub(r"\b\d{6}\b", " ", line)
        line = line.strip(" ,.-/:;")
        if not line or line.lower() in _STATES:
            continue
        cleaned.append(line)
    if not cleaned:
        return ""
    parts = [p.strip(" .-") for p in cleaned[-1].split(",")]
    parts = [p for p in parts if p and p.lower() not in _STATES]
    city = parts[-1] if parts else cleaned[-1]
    return city.title()[:60]


# ---------------------------------------------------------------- settings

def cfg(conn):
    s = db.all_settings(conn)
    return {
        "per_coupon_paise": int(float(s["amount_per_coupon"]) * 100),
        "per_coupon": int(float(s["amount_per_coupon"])),
        "start": s["campaign_start"],
        "end": s["campaign_end"],
        "digits": int(s["coupon_digits"]),
        "total_prizes": int(s["total_prizes"] or 0),
        "closed": s["closed"] == "1",
        "raw": s,
    }


# ---------------------------------------------------------------- bills

@dataclass
class Voucher:
    guid: str
    voucher_no: str = ""
    date: str = ""            # YYYY-MM-DD
    voucher_type: str = ""
    party_ledger: str = ""
    amount_paise: int = 0
    cancelled: bool = False
    alter_id: int | None = None
    name: str = ""
    phone: str = ""
    city: str = ""
    address: str = ""
    extras: dict = field(default_factory=dict)

    def fingerprint(self):
        raw = "|".join(str(x) for x in (
            self.voucher_no, self.date, self.party_ledger, self.amount_paise, self.cancelled,
            self.name, self.phone, self.city, self.address))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def upsert_customer(conn, phone, name="", city="", force=False):
    row = conn.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()
    t = db.now()
    if row:
        new_name = name if (name and (force or not row["name"])) else row["name"]
        new_city = city if (city and (force or not row["city"])) else row["city"]
        if (new_name, new_city) != (row["name"], row["city"]):
            conn.execute("UPDATE customers SET name=?, city=?, updated_at=? WHERE id=?",
                         (new_name, new_city, t, row["id"]))
        return row["id"]
    cur = conn.execute(
        "INSERT INTO customers(phone, name, city, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (phone, name, city, t, t))
    return cur.lastrowid


def issue_coupons(conn, sale_id, customer_id, n):
    """Give n new coupons the next numbers in one continuous series."""
    nxt = int(db.get_setting(conn, "next_coupon_number"))
    t = db.now()
    for i in range(n):
        conn.execute(
            "INSERT INTO coupons(number, sale_id, customer_id, status, created_at) VALUES (?, ?, ?, 'valid', ?)",
            (nxt + i, sale_id, customer_id, t))
    db.set_setting(conn, "next_coupon_number", nxt + n)


def recompute_sale(conn, sale_id, c=None):
    """Work out status, customer and coupons of one bill from what is stored."""
    c = c or cfg(conn)
    s = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    desired = 0
    if s["tally_deleted"]:
        status, note, reason = "deleted", "Deleted in Tally", "bill deleted in Tally"
    elif s["tally_cancelled"]:
        status, note, reason = "cancelled", "Cancelled in Tally", "bill cancelled in Tally"
    elif not (c["start"] <= (s["voucher_date"] or "") <= c["end"]):
        status, note, reason = "outside_campaign", "Dated outside the campaign", "bill outside campaign dates"
    elif s["amount_paise"] < c["per_coupon_paise"]:
        status, note, reason = "no_coupon", "Below the minimum amount", "bill amount reduced"
    elif not s["phone"]:
        status, note, reason = "waiting_phone", "Phone number needed", "bill amount reduced"
    else:
        status, note, reason = "active", "", "bill amount reduced"
        desired = s["amount_paise"] // c["per_coupon_paise"]

    customer_id = s["customer_id"]
    if s["phone"] and status in ("active", "no_coupon"):
        customer_id = upsert_customer(conn, s["phone"], s["raw_name"], s["raw_city"])

    live = conn.execute(
        "SELECT id, number, status FROM coupons WHERE sale_id = ? AND status IN ('valid', 'won') ORDER BY number",
        (sale_id,)).fetchall()
    have = len(live)
    if desired > have:
        issue_coupons(conn, sale_id, customer_id, desired - have)
    elif desired < have:
        to_void = have - desired
        valid_desc = [r for r in reversed(live) if r["status"] == "valid"]
        for r in valid_desc[:to_void]:
            conn.execute("UPDATE coupons SET status='void', void_reason=? WHERE id=?", (reason, r["id"]))
        if len(valid_desc) < to_void:
            db.audit(conn, "system", "warning",
                     f"Bill {s['voucher_no']} changed but it holds a winning coupon; the winning coupon was kept")
    if customer_id:
        conn.execute("UPDATE coupons SET customer_id=? WHERE sale_id=? AND status='valid' AND customer_id<>?",
                     (customer_id, sale_id, customer_id))
    earned = conn.execute(
        "SELECT COUNT(*) FROM coupons WHERE sale_id=? AND status IN ('valid','won')", (sale_id,)).fetchone()[0]
    conn.execute(
        "UPDATE sales SET status=?, status_note=?, customer_id=?, coupons_earned=?, updated_at=? WHERE id=?",
        (status, note, customer_id, earned, db.now(), sale_id))
    return status, earned


def recompute_all(conn):
    c = cfg(conn)
    ids = [r[0] for r in conn.execute("SELECT id FROM sales ORDER BY id")]
    for sid in ids:
        recompute_sale(conn, sid, c)
    return len(ids)


def upsert_voucher(conn, v, c=None):
    """Save one Tally bill and apply the coupon rules. Returns 'new', 'changed' or 'unchanged'."""
    c = c or cfg(conn)
    fp = v.fingerprint()
    row = conn.execute("SELECT * FROM sales WHERE guid = ?", (v.guid,)).fetchone()
    t = db.now()
    if row and row["fingerprint"] == fp and not row["tally_deleted"]:
        return "unchanged", row["id"]
    if row:
        phone = row["phone"] if row["phone_manual"] else v.phone
        conn.execute(
            """UPDATE sales SET voucher_no=?, voucher_date=?, party_ledger=?, amount_paise=?, alter_id=?,
               fingerprint=?, tally_cancelled=?, tally_deleted=0, raw_name=?, raw_city=?, raw_address=?,
               raw_phone=?, phone=?, updated_at=? WHERE id=?""",
            (v.voucher_no, v.date, v.party_ledger, v.amount_paise, v.alter_id, fp, int(v.cancelled),
             v.name, v.city, v.address, v.phone, phone, t, row["id"]))
        sale_id, result = row["id"], "changed"
    else:
        cur = conn.execute(
            """INSERT INTO sales(guid, voucher_no, voucher_date, party_ledger, amount_paise, alter_id, fingerprint,
               status, tally_cancelled, raw_name, raw_city, raw_address, raw_phone, phone, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting_phone', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (v.guid, v.voucher_no, v.date, v.party_ledger, v.amount_paise, v.alter_id, fp, int(v.cancelled),
             v.name, v.city, v.address, v.phone, v.phone, t, t))
        sale_id, result = cur.lastrowid, "new"
    recompute_sale(conn, sale_id, c)
    return result, sale_id


def find_missing(conn, seen_guids, date_from, date_to):
    """Active bills in the date range that Tally no longer returns (deleted in Tally)."""
    rows = conn.execute(
        "SELECT id, guid FROM sales WHERE tally_deleted = 0 AND voucher_date BETWEEN ? AND ?",
        (date_from, date_to)).fetchall()
    return [r["id"] for r in rows if r["guid"] not in seen_guids]


def mark_deleted(conn, sale_ids, c=None):
    c = c or cfg(conn)
    for sid in sale_ids:
        conn.execute("UPDATE sales SET tally_deleted = 1 WHERE id = ?", (sid,))
        recompute_sale(conn, sid, c)


# ---------------------------------------------------------------- staff edits

def set_sale_phone(conn, sale_id, phone_raw, name="", city="", user=None):
    phone = normalize_phone(phone_raw)
    if not phone:
        raise ValueError("Enter a valid 10-digit mobile number.")
    s = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if not s:
        raise LookupError("Bill not found.")
    if s["customer_id"] and s["phone"] and s["phone"] != phone:
        won = conn.execute(
            "SELECT 1 FROM coupons WHERE sale_id=? AND status='won'", (sale_id,)).fetchone()
        if won:
            raise ValueError("This bill has a winning coupon, so its customer cannot be changed.")
    conn.execute(
        "UPDATE sales SET phone=?, phone_manual=1, raw_name=?, raw_city=? WHERE id=?",
        (phone, name.strip() or s["raw_name"], city.strip() or s["raw_city"], sale_id))
    status, earned = recompute_sale(conn, sale_id)
    cust = conn.execute("SELECT customer_id FROM sales WHERE id=?", (sale_id,)).fetchone()[0]
    if cust and (name.strip() or city.strip()):
        upsert_customer(conn, phone, name.strip(), city.strip(), force=True)
    db.audit(conn, user, "phone entered",
             f"bill {s['voucher_no']} (entry {sale_id}): phone {phone}; coupons now {earned}")
    return status, earned


def edit_customer(conn, customer_id, name=None, city=None, phone=None, user=None):
    cust = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not cust:
        raise LookupError("Customer not found.")
    new_phone = cust["phone"]
    if phone is not None and phone.strip():
        new_phone = normalize_phone(phone)
        if not new_phone:
            raise ValueError("Enter a valid 10-digit mobile number.")
        clash = conn.execute("SELECT name FROM customers WHERE phone=? AND id<>?", (new_phone, customer_id)).fetchone()
        if clash:
            raise ValueError(f"That number already belongs to {clash['name'] or 'another customer'}.")
    conn.execute(
        "UPDATE customers SET name=?, city=?, phone=?, updated_at=? WHERE id=?",
        (cust["name"] if name is None else name.strip(),
         cust["city"] if city is None else city.strip(),
         new_phone, db.now(), customer_id))
    if new_phone != cust["phone"]:
        conn.execute("UPDATE sales SET phone=?, phone_manual=1 WHERE customer_id=?", (new_phone, customer_id))
    db.audit(conn, user, "customer edited", f"customer {customer_id}: {cust['name']} -> {name}, phone {new_phone}")


# ---------------------------------------------------------------- the draw

_ELIGIBLE = """
    FROM coupons c
    WHERE c.status = 'valid'
      AND c.customer_id NOT IN (
          SELECT customer_id FROM winners WHERE status = 'active' OR keep_excluded = 1)
"""


def draw_status(conn, c=None):
    c = c or cfg(conn)
    eligible_coupons = conn.execute("SELECT COUNT(*) " + _ELIGIBLE).fetchone()[0]
    eligible_customers = conn.execute("SELECT COUNT(DISTINCT c.customer_id) " + _ELIGIBLE).fetchone()[0]
    drawn = conn.execute("SELECT COUNT(*) FROM winners WHERE status='active'").fetchone()[0]
    waiting = conn.execute("SELECT COUNT(*) FROM sales WHERE status='waiting_phone'").fetchone()[0]
    total = c["total_prizes"]
    remaining = max(total - drawn, 0) if total else None
    warnings = []
    if waiting:
        warnings.append(f"{waiting} bill(s) are still waiting for a phone number and will not take part.")
    if remaining is not None and eligible_customers < remaining:
        warnings.append(
            f"Only {eligible_customers} eligible customers are left for {remaining} remaining prizes.")
    if not c["closed"]:
        warnings.append("The campaign is still open. Close it first so bills cannot change during the draw.")
    return {
        "eligible_coupons": eligible_coupons, "eligible_customers": eligible_customers,
        "winners_drawn": drawn, "total_prizes": total, "remaining": remaining,
        "waiting_phone": waiting, "closed": c["closed"], "warnings": warnings,
    }


def prize_remaining(conn, prize_id):
    p = conn.execute("SELECT quantity FROM prizes WHERE id=?", (prize_id,)).fetchone()
    if not p:
        raise LookupError("Prize not found.")
    used = conn.execute(
        "SELECT COUNT(*) FROM winners WHERE prize_id=? AND status='active'", (prize_id,)).fetchone()[0]
    return p["quantity"] - used


def winner_rows(conn, where="", params=()):
    sql = """
        SELECT w.id, w.seq, w.status, w.cancel_reason, w.drawn_at, w.drawn_by, w.prize_id,
               p.name AS prize_name, c.number AS coupon_number, c.sale_id,
               cu.id AS customer_id, cu.name, cu.city, cu.phone, s.voucher_no
        FROM winners w
        JOIN coupons c ON c.id = w.coupon_id
        JOIN customers cu ON cu.id = w.customer_id
        JOIN sales s ON s.id = c.sale_id
        LEFT JOIN prizes p ON p.id = w.prize_id
    """ + where + " ORDER BY w.seq"
    return [dict(r) for r in conn.execute(sql, params)]


def draw_winners(conn, user, count=1, prize_id=None):
    """Pick winners at random from valid coupons. One prize per customer. Saved at once."""
    c = cfg(conn)
    count = max(1, int(count))
    drawn = conn.execute("SELECT COUNT(*) FROM winners WHERE status='active'").fetchone()[0]
    if c["total_prizes"]:
        left = c["total_prizes"] - drawn
        if left <= 0:
            raise ValueError(f"All {c['total_prizes']} prizes have already been drawn.")
        count = min(count, left)
    if prize_id:
        left = prize_remaining(conn, prize_id)
        if left <= 0:
            raise ValueError("That prize has no winners left to draw.")
        count = min(count, left)

    rows = conn.execute("SELECT c.id, c.customer_id " + _ELIGIBLE).fetchall()
    pool = [(r["id"], r["customer_id"]) for r in rows]
    if not pool:
        raise ValueError("No eligible coupons are left.")
    rng = secrets.SystemRandom()
    seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM winners").fetchone()[0]
    ids = []
    for _ in range(count):
        if not pool:
            break
        coupon_id, customer_id = rng.choice(pool)
        pool = [p for p in pool if p[1] != customer_id]   # one prize per customer
        seq += 1
        cur = conn.execute(
            "INSERT INTO winners(seq, prize_id, coupon_id, customer_id, drawn_at, drawn_by) VALUES (?, ?, ?, ?, ?, ?)",
            (seq, prize_id, coupon_id, customer_id, db.now(), user))
        conn.execute("UPDATE coupons SET status='won' WHERE id=?", (coupon_id,))
        ids.append(cur.lastrowid)
    db.audit(conn, user, "draw", f"{len(ids)} winner(s) drawn" + (f" for prize {prize_id}" if prize_id else ""))
    marks = ",".join("?" * len(ids))
    return winner_rows(conn, f"WHERE w.id IN ({marks})", ids)


def cancel_winner(conn, winner_id, reason, put_back, user):
    if not (reason or "").strip():
        raise ValueError("A reason is required to cancel a winner.")
    w = conn.execute("SELECT * FROM winners WHERE id=? AND status='active'", (winner_id,)).fetchone()
    if not w:
        raise LookupError("Winner not found.")
    conn.execute(
        "UPDATE winners SET status='cancelled', cancel_reason=?, keep_excluded=? WHERE id=?",
        (reason.strip(), 0 if put_back else 1, winner_id))
    if put_back:
        conn.execute("UPDATE coupons SET status='valid' WHERE id=?", (w["coupon_id"],))
    else:
        conn.execute("UPDATE coupons SET status='void', void_reason='winner cancelled' WHERE id=?", (w["coupon_id"],))
    db.audit(conn, user, "winner cancelled", f"winner {winner_id}: {reason.strip()} (customer put back: {bool(put_back)})")


def assign_prize(conn, winner_id, prize_id, user):
    if prize_id and not conn.execute("SELECT 1 FROM prizes WHERE id=?", (prize_id,)).fetchone():
        raise LookupError("Prize not found.")
    conn.execute("UPDATE winners SET prize_id=? WHERE id=?", (prize_id or None, winner_id))
    db.audit(conn, user, "prize assigned", f"winner {winner_id} -> prize {prize_id}")
