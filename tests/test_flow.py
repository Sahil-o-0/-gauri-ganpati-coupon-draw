"""End to end: fake Tally -> sync -> API, the way the shop will use it."""
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import mock_tally  # noqa: E402

from luckydraw import config, db  # noqa: E402
from luckydraw.main import app, service  # noqa: E402


def bill(i, amount, phone=True, date="2026-12-01", cancelled=False, name=None):
    name = name or f"Customer {i}"
    return {"guid": f"G{i}", "number": f"GG/{i:03d}", "date": date, "type": "Sales", "party": name, "buyer": name,
            "address": ["Main Road", "Pune", "Maharashtra", "411001"] + ([f"Mob: 98{i:08d}"] if phone else []),
            "amount": amount, "cancelled": cancelled, "alter_id": i}


@pytest.fixture()
def tally_server(tmp_path, monkeypatch):
    data = tmp_path / "bills.json"
    monkeypatch.setattr(mock_tally, "DATA", data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mock_tally.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], data
    srv.shutdown()


@pytest.fixture()
def client(tally_server):
    port, _ = tally_server
    service.consecutive_failures = 0
    with TestClient(app) as c:
        assert c.post("/api/setup", json={"username": "admin", "password": "secret1"}).status_code == 200
        r = c.put("/api/settings", json={"tally_host": "127.0.0.1", "tally_port": port, "campaign_end": "2027-07-05"})
        assert r.status_code == 200, r.text
        yield c


def test_full_flow(client, tally_server):
    port, data = tally_server
    bills = [bill(1, 63000), bill(2, 21000), bill(3, 20999), bill(4, 70000, phone=False), bill(5, 42000, cancelled=True),
             bill(6, 105000, date="2027-08-01"), bill(7, 21000, name="Same Person"), bill(8, 21000, name="Same Person")]
    bills[7]["address"] = bills[6]["address"]            # same phone as bill 7
    data.write_text(json.dumps(bills))

    r = service.run_sync("full")
    assert r["ok"], r
    assert r["fetched"] == 7 and r["new"] == 7          # bill 6 is after the campaign end: Tally is not even asked for it

    d = client.get("/api/dashboard").json()
    assert d["bills"]["active"] == 4 and d["bills"]["waiting_phone"] == 1 and d["bills"]["no_coupon"] == 1
    assert d["bills"]["cancelled"] == 1 and "outside_campaign" not in d["bills"]
    assert d["coupons_issued"] == 3 + 1 + 1 + 1          # bills 1, 2, 7, 8
    assert d["tally"]["light"] == "green"

    # same data again: nothing changes
    r = service.run_sync("full"); assert r["new"] == 0 and r["changed"] == 0

    # waiting for phone -> enter it -> 3 coupons (70,000)
    waiting = client.get("/api/sales?status=waiting_phone").json()["items"]
    assert [w["voucher_no"] for w in waiting] == ["GG/004"]
    r = client.post(f"/api/sales/{waiting[0]['id']}/phone", json={"phone": "9000011111", "name": "Late Phone", "city": "Satara"})
    assert r.status_code == 200 and r.json()["coupons"] == 3
    assert client.post(f"/api/sales/{waiting[0]['id']}/phone", json={"phone": "123"}).status_code == 400

    # bill 1 edited down in Tally; bill 2 cancelled; bill 8 deleted
    bills[0]["amount"] = 42000; bills[0]["alter_id"] = 100
    bills[1]["cancelled"] = True
    bills.pop(7)
    data.write_text(json.dumps(bills))
    r = service.run_sync("full")
    assert r["changed"] >= 3, r
    d = client.get("/api/dashboard").json()
    assert d["bills"]["deleted"] == 1
    assert d["coupons_issued"] == 2 + 0 + 1 + 3          # bill1=2, bill2 cancelled, bill7=1, bill4=3

    # customers, search, coupon lookup
    cust = client.get("/api/customers?q=Satara").json()
    assert cust["total"] == 1 and cust["items"][0]["coupons"] == 3
    first = client.get("/api/search?q=1").json()
    assert first["coupon"]["number"] == 1 and first["coupon"]["name"] == "Customer 1"
    assert client.get("/api/coupons/9999").status_code == 404
    prof = client.get(f"/api/customers/{cust['items'][0]['id']}").json()
    assert len(prof["coupons"]) == 3 and prof["sales"][0]["voucher_no"] == "GG/004"
    slip = client.get(f"/api/sales/{waiting[0]['id']}/slip").json()
    assert slip["coupons"] and slip["phone"] == "9000011111"

    # prizes can be added later; draw
    assert client.post("/api/prizes", json={"name": "Gold coin", "quantity": 1}).status_code == 200
    pr = client.get("/api/prizes").json()
    assert pr["total_prizes"] == 142 and pr["not_yet_named"] == 141
    ds = client.get("/api/draw/status").json()
    assert ds["eligible_customers"] == 3 and any("still open" in w for w in ds["warnings"])
    w1 = client.post("/api/draw", json={"count": 1, "prize_id": pr["items"][0]["id"]}).json()["winners"]
    assert w1[0]["prize_name"] == "Gold coin"
    w2 = client.post("/api/draw", json={"count": 10}).json()["winners"]
    assert len(w2) == 2                                    # only 3 customers exist, one prize each
    assert len({w["customer_id"] for w in w1 + w2}) == 3
    assert client.post("/api/draw", json={"count": 1}).status_code == 400
    wid = w2[0]["id"]
    assert client.put(f"/api/winners/{wid}/prize", json={"prize_id": pr["items"][0]["id"]}).status_code == 200
    assert client.post(f"/api/winners/{wid}/cancel", json={"reason": ""}).status_code == 400

    # excel
    x = client.get("/api/export/all.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert client.get("/api/export/winners.xlsx").status_code == 200

    # close the campaign: locks it, stops sync, makes a backup
    r = client.post("/api/campaign/close", json={"force": False})
    assert r.status_code == 200, r.text
    assert client.post("/api/sync", json={"mode": "quick"}).status_code == 400
    assert client.get("/api/dashboard").json()["tally"]["light"] == "closed"
    backup = Path(config.DEFAULT_BACKUP_DIR) / "luckydraw-backup.db"
    assert backup.exists() and client.get("/api/dashboard").json()["backup"]["note"].startswith("OK")
    assert client.post("/api/campaign/reopen").status_code == 200


def test_tally_down_is_reported_not_fatal(client, tally_server):
    port, data = tally_server
    client.put("/api/settings", json={"tally_port": 1})
    for _ in range(3):
        r = service.run_sync("quick")
        assert not r["ok"] and "Cannot reach Tally" in r["message"]
    assert client.get("/api/dashboard").json()["tally"]["light"] == "red"
    assert client.get("/api/sync/log").json()[0]["ok"] == 0


def test_wrong_company_does_not_mass_delete(client, tally_server):
    port, data = tally_server
    data.write_text(json.dumps([bill(i, 21000) for i in range(1, 31)]))
    assert service.run_sync("full")["ok"]
    data.write_text(json.dumps([]))                         # Tally suddenly returns nothing
    r = service.run_sync("full")
    assert r["ok"] and "skipped" in r["message"]
    assert client.get("/api/dashboard").json()["bills"].get("deleted", 0) == 0


def test_permissions_and_logins(client):
    assert client.post("/api/users", json={"username": "staff1", "password": "abcdef", "role": "staff"}).status_code == 200
    anon = TestClient(app)
    assert anon.get("/api/dashboard").status_code == 401
    assert anon.post("/api/login", json={"username": "staff1", "password": "wrong"}).status_code == 401
    assert anon.post("/api/login", json={"username": "staff1", "password": "abcdef"}).status_code == 200
    assert anon.get("/api/dashboard").status_code == 200
    for path in ("/api/settings", "/api/users", "/api/audit"):
        assert anon.get(path).status_code == 403
    assert anon.post("/api/draw", json={"count": 1}).status_code == 403
    assert anon.post("/api/campaign/close", json={}).status_code == 403
    assert client.put("/api/users/1", json={"role": "staff"}).status_code == 400      # last admin


def test_settings_change_blocked_after_draw(client, tally_server):
    port, data = tally_server
    data.write_text(json.dumps([bill(i, 21000) for i in range(1, 4)]))
    service.run_sync("full")
    assert client.post("/api/draw", json={"count": 1}).status_code == 200
    r = client.put("/api/settings", json={"amount_per_coupon": 10000})
    assert r.status_code == 400 and "after winners" in r.json()["detail"]


def test_backup_replaces_previous(client):
    from luckydraw import sync
    ok1, _ = sync.do_backup(); ok2, _ = sync.do_backup()
    folder = Path(config.DEFAULT_BACKUP_DIR)
    assert ok1 and ok2 and sorted(p.name for p in folder.iterdir()) == ["luckydraw-backup.db"]
