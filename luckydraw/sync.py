"""Background jobs: fetch from Tally on a timer, and the hourly backup."""
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, db, engine, tally

JOB_TIMEOUT = 300       # seconds before a job the connector never answered is given up
POLL_WAIT = 10          # how often the connector asks for work (seconds)


class SyncService:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.pending_mode = None
        self.running = False
        self.consecutive_failures = 0
        self.last = {"at": None, "ok": None, "message": "Not synced yet", "kind": ""}
        self.last_full_at = None
        self.last_ok_at = None
        self.last_backup_check = 0.0
        # connector mode
        self.jlock = threading.Lock()
        self.job = None
        self.last_job_at = 0.0
        self.probe_pending = False
        self.last_seen = None

    def load_state(self):
        """Called once at start-up, after the database exists."""
        with db.read() as conn:
            seen = db.get_setting(conn, "connector_last_seen")
        if seen:
            try:
                self.last_seen = datetime.fromisoformat(seen)
            except ValueError:
                pass

    # ----------------------------------------------------------- one sync run
    def _plan(self, mode, c):
        """Date range to read for a quick or full check."""
        s = c["raw"]
        today = date.today().isoformat()
        if mode == "full":
            return c["start"], max(c["end"], today)
        back = (date.today() - timedelta(days=int(s["quick_window_days"]))).isoformat()
        d_from, d_to = max(c["start"], back), today
        return (d_to if d_from > d_to else d_from), d_to

    def _ingest(self, mode, d_from, d_to, user, get_data):
        """Read Tally's reply (get_data(settings) returns the raw bytes), apply it, log it. Never raises."""
        try:
            with db.read() as conn:
                c = engine.cfg(conn)
            s = c["raw"]
            data = get_data(s)
            vouchers = tally.parse_vouchers(
                data, s["voucher_types"].split(","), [t.strip() for t in s["extra_scan_tags"].split(",")])
            result = self._apply(vouchers, mode, d_from, d_to, c)
            self.consecutive_failures = 0
            self.last_ok_at = datetime.now()
            if mode == "full":
                self.last_full_at = datetime.now()
            self._log(mode, True, result, user)
            return {"ok": True, **result}
        except Exception as e:  # report, never crash the loop
            self.consecutive_failures += 1
            self._log(mode, False, {"message": str(e)}, user)
            return {"ok": False, "message": str(e)}

    def run_sync(self, mode="quick", user=None):
        """mode: 'quick' (recent days), 'full' (whole campaign, also finds deleted bills)."""
        with db.read() as conn:
            c = engine.cfg(conn)
        if c["closed"] or c["raw"]["sync_enabled"] != "1":
            return {"ok": False, "message": "Syncing is switched off (campaign closed or sync disabled)."}
        if c["raw"]["tally_source"] == "connector":
            return self._run_via_connector(mode, user, c)
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "message": "A sync is already running."}
        self.running = True
        try:
            d_from, d_to = self._plan(mode, c)
            body = tally.build_request(d_from, d_to, c["raw"]["tally_company"])
            return self._ingest(mode, d_from, d_to, user, lambda s: tally.fetch(s["tally_host"], s["tally_port"], body))
        finally:
            self.running = False
            self.lock.release()

    def _apply(self, vouchers, mode, d_from, d_to, c):
        new = changed = 0
        note = ""
        with db.tx() as conn:
            seen = set()
            for v in vouchers:
                seen.add(v.guid)
                res, _ = engine.upsert_voucher(conn, v, c)
                if res == "new":
                    new += 1
                elif res == "changed":
                    changed += 1
            if mode == "full":
                missing = engine.find_missing(conn, seen, d_from, d_to)
                in_db = conn.execute(
                    "SELECT COUNT(*) FROM sales WHERE tally_deleted=0 AND voucher_date BETWEEN ? AND ?",
                    (d_from, d_to)).fetchone()[0]
                # Safety: if Tally suddenly returns (almost) nothing, it is probably the wrong company
                # or a Tally problem, not hundreds of deleted bills. Do not void anything then.
                if missing and (not vouchers or (len(missing) > 20 and len(missing) > in_db * 0.5)):
                    note = f" Deleted-bill check skipped: {len(missing)} bills missing, which looks like a Tally problem."
                    db.audit(conn, "system", "warning", note.strip())
                elif missing:
                    engine.mark_deleted(conn, missing, c)
                    changed += len(missing)
                    db.audit(conn, "system", "bills deleted in Tally", f"{len(missing)} bill(s) no longer in Tally; coupons voided")
        msg = f"{len(vouchers)} bills read, {new} new, {changed} changed.{note}"
        return {"fetched": len(vouchers), "new": new, "changed": changed, "message": msg}

    def _log(self, kind, ok, result, user):
        msg = result.get("message", "")
        self.last = {"at": db.now(), "ok": ok, "message": msg, "kind": kind}
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO sync_log(at, kind, ok, fetched, new_sales, changed_sales, message) VALUES (?,?,?,?,?,?,?)",
                (db.now(), kind + (" (manual)" if user else ""), int(ok), result.get("fetched", 0),
                 result.get("new", 0), result.get("changed", 0), msg))
            conn.execute("DELETE FROM sync_log WHERE id < (SELECT MAX(id) - 500 FROM sync_log)")

    def trigger(self, mode="quick", user=None):
        """Ask the background thread to sync now."""
        self.pending_mode = (mode, user)
        self.wake.set()

    # ----------------------------------------------------------- loops
    def loop(self):
        time.sleep(3)
        while not self.stop_event.is_set():
            try:
                job, self.pending_mode = self.pending_mode, None
                with db.read() as conn:
                    s = db.all_settings(conn)
                if s["tally_source"] == "connector":
                    pass  # the connector asks for work itself (see next_job)
                elif job:
                    self.run_sync(job[0], job[1])
                elif s["sync_enabled"] == "1" and s["closed"] != "1":
                    due_full = (self.last_full_at is None or
                                datetime.now() - self.last_full_at >= timedelta(minutes=int(s["full_check_minutes"])))
                    self.run_sync("full" if due_full else "quick")
                self.maybe_backup(s)
                interval = max(20, int(s["sync_interval_seconds"]))
            except Exception:
                interval = 60
            self.wake.wait(interval)
            self.wake.clear()

    def maybe_backup(self, s):
        every = max(1, int(s["backup_interval_minutes"])) * 60
        if time.time() - self.last_backup_check >= every:
            self.last_backup_check = time.time()
            do_backup()

    def start(self):
        threading.Thread(target=self.loop, daemon=True, name="tally-sync").start()

    def connector_online(self):
        return bool(self.last_seen and (datetime.now() - self.last_seen).total_seconds() < max(45, POLL_WAIT * 4))

    def status(self):
        with db.read() as conn:
            source = db.get_setting(conn, "tally_source")
        return {
            "source": source, "connector_online": self.connector_online(),
            "connector_last_seen": self.last_seen.isoformat(timespec="seconds") if self.last_seen else None,
            "running": self.running or bool(self.job), "last": self.last, "consecutive_failures": self.consecutive_failures,
            "last_ok_at": self.last_ok_at.isoformat(timespec="seconds") if self.last_ok_at else None,
            "last_full_at": self.last_full_at.isoformat(timespec="seconds") if self.last_full_at else None,
        }


    # ----------------------------------------------------------- connector mode
    # The connector on the Tally PC asks "any work?" every few seconds (poll). When a check is due it is
    # handed the exact XML request to send to Tally, and posts Tally's raw reply back (result).

    def _make_job(self, kind, user, c):
        today = date.today()
        if kind == "probe":
            d_from, d_to = (today - timedelta(days=30)).isoformat(), today.isoformat()
        else:
            d_from, d_to = self._plan(kind, c)
        return {"id": secrets.token_hex(8), "kind": kind, "from": d_from, "to": d_to, "user": user,
                "company": c["raw"]["tally_company"], "issued_at": time.time(), "handed": False,
                "event": threading.Event(), "out": {}}

    def _payload(self, j):
        j["handed"] = True
        j["issued_at"] = time.time()
        return {"id": j["id"], "kind": j["kind"],
                "request_xml": tally.build_request(j["from"], j["to"], j["company"]).decode("utf-8")}

    def _finish(self, j, result):
        j["out"] = result
        j["event"].set()
        self.job = None

    def touch_connector(self, info):
        self.last_seen = datetime.now()
        with db.tx() as conn:
            db.set_setting(conn, "connector_last_seen", self.last_seen.isoformat(timespec="seconds"))
            db.set_setting(conn, "connector_info", json.dumps(info))

    def next_job(self):
        """Called when the connector polls. Returns a job payload or None."""
        with self.jlock:
            with db.read() as conn:
                c = engine.cfg(conn)
            s = c["raw"]
            j = self.job
            if j and time.time() - j["issued_at"] > JOB_TIMEOUT:
                self.consecutive_failures += 1
                msg = "The connector did not send Tally's reply in time."
                self._log(j["kind"], False, {"message": msg}, j["user"])
                self._finish(j, {"ok": False, "message": msg})
                j = None
            if j:
                return None if j["handed"] else self._payload(j)
            if self.probe_pending:
                self.probe_pending = False
                self.job = self._make_job("probe", None, c)
                return self._payload(self.job)
            if c["closed"] or s["sync_enabled"] != "1":
                return None
            kind = user = None
            if self.pending_mode:
                (kind, user), self.pending_mode = self.pending_mode, None
            elif time.time() - self.last_job_at >= max(20, int(s["sync_interval_seconds"])):
                due_full = (self.last_full_at is None or
                            datetime.now() - self.last_full_at >= timedelta(minutes=int(s["full_check_minutes"])))
                kind = "full" if due_full else "quick"
            if not kind:
                return None
            self.last_job_at = time.time()
            self.job = self._make_job(kind, user, c)
            return self._payload(self.job)

    def _run_via_connector(self, mode, user, c):
        """Used by 'Close campaign': ask the connector for a check and wait for the answer."""
        with self.jlock:
            if self.job:
                return {"ok": False, "message": "A sync is already running."}
            j = self.job = self._make_job(mode, user, c)
            self.last_job_at = time.time()
        if not self.connector_online():
            with self.jlock:
                self._finish(j, {"ok": False, "message": "The Tally connector is not running on the Tally PC."})
            return j["out"]
        if not j["event"].wait(100):
            return {"ok": False, "message": "The Tally connector did not answer. Is it running on the Tally PC, with Tally open?"}
        return j["out"]

    def accept_result(self, job_id, data):
        with self.jlock:
            j = self.job
            if not j or j["id"] != job_id:
                raise LookupError("This job is no longer wanted (it timed out or was already answered).")
            j["issued_at"] = time.time()  # keep it alive while we process
        if j["kind"] == "probe":
            result = self._probe_done(data)
            with self.jlock:
                self._finish(j, result)
            return result
        with self.lock:
            self.running = True
            try:
                result = self._ingest(j["kind"], j["from"], j["to"], j["user"], lambda s: data)
            finally:
                self.running = False
        with self.jlock:
            self._finish(j, result)
        return result

    def accept_error(self, job_id, message):
        with self.jlock:
            j = self.job
            if not j or j["id"] != job_id:
                raise LookupError("This job is no longer wanted.")
            message = (message or "The connector could not read Tally.")[:500]
            if j["kind"] == "probe":
                self._save_probe({"at": db.now(), "ok": False, "message": message})
            else:
                self.consecutive_failures += 1
                self._log(j["kind"], False, {"message": message}, j["user"])
            self._finish(j, {"ok": False, "message": message})

    def request_probe(self):
        self.probe_pending = True

    def _save_probe(self, info):
        with db.tx() as conn:
            db.set_setting(conn, "last_probe", json.dumps(info))

    def _probe_done(self, data):
        """Check what Tally sent: how many vouchers, which types, and what phone/city we could read."""
        (config.DATA_DIR / "probe-latest.xml").write_bytes(data[:20_000_000])
        info = {"at": db.now(), "bytes": len(data)}
        try:
            with db.read() as conn:
                s = db.all_settings(conn)
            everything = tally.parse_vouchers(data, allowed_types=())
            sales = tally.parse_vouchers(data, s["voucher_types"].split(","),
                                         [t.strip() for t in s["extra_scan_tags"].split(",")])
            info.update({
                "ok": True, "vouchers": len(everything), "sales": len(sales),
                "with_phone": sum(1 for v in sales if v.phone),
                "types": sorted({v.voucher_type for v in everything}),
                "sample": [{"no": v.voucher_no, "date": v.date, "amount": v.amount_paise / 100, "name": v.name,
                            "phone": v.phone, "city": v.city, "address": v.address} for v in sales[:8]]})
            info["message"] = f"{len(everything)} vouchers, {len(sales)} Sales bills, {info['with_phone']} with a phone number."
        except Exception as e:
            info.update({"ok": False, "message": str(e)})
        self._save_probe(info)
        return {"ok": info["ok"], "message": info["message"]}


# ---------------------------------------------------------------- backup

def do_backup(user="system"):
    """Copy the database to the backup folder, replacing the previous backup.
    The new copy is checked first, so a damaged copy never replaces a good one."""
    with db.read() as conn:
        folder = Path(db.get_setting(conn, "backup_dir"))
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp, final = folder / "luckydraw-backup.tmp", folder / "luckydraw-backup.db"
        if tmp.exists():
            tmp.unlink()
        src = db.connect()
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            check = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if check != "ok":
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Backup copy failed the health check ({check}); old backup kept.")
        os.replace(tmp, final)
        note = f"OK: {final}"
        ok = True
    except Exception as e:
        note, ok = f"FAILED: {e}", False
    with db.tx() as conn:
        db.set_setting(conn, "last_backup_at", db.now())
        db.set_setting(conn, "last_backup_note", note)
    return ok, note
