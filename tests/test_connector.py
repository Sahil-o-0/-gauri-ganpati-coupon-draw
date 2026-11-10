"""The Tally PC connector talks to the server over HTTP: poll -> job -> Tally reply -> bills appear."""
import json
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from datetime import date
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "connector"))
import connector  # noqa: E402
import mock_tally  # noqa: E402

from luckydraw import db  # noqa: E402
from luckydraw.main import app, service  # noqa: E402
from test_flow import bill as _bill  # noqa: E402


def bill(i, amount, **kw):
    return _bill(i, amount, date=date.today().isoformat(), **kw)   # quick checks only look at the last few days


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def world(tmp_path, monkeypatch):
    data = tmp_path / "bills.json"
    monkeypatch.setattr(mock_tally, "DATA", data)
    tally_srv = ThreadingHTTPServer(("127.0.0.1", 0), mock_tally.Handler)
    threading.Thread(target=tally_srv.serve_forever, daemon=True).start()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    service.consecutive_failures = 0
    service.job = None
    service.last_seen = None
    service.last_full_at = None
    service.last_job_at = 0.0
    with TestClient(app) as c:
        assert c.post("/api/setup", json={"username": "admin", "password": "secret1"}).status_code == 200
        assert c.put("/api/settings", json={"tally_source": "connector", "campaign_start": "2020-01-01", "campaign_end": "2099-12-31"}).status_code == 200
        key = db.get_setting(db.connect(), "connector_key")
        cfg = {"server": f"http://127.0.0.1:{port}", "key": key, "tally": f"http://127.0.0.1:{tally_srv.server_address[1]}"}
        yield c, cfg, data
    server.should_exit = True
    tally_srv.shutdown()


def test_connector_end_to_end(world):
    c, cfg, data = world
    data.write_text(json.dumps([bill(1, 63000), bill(2, 21000), bill(3, 70000, phone=False)]))

    assert c.get("/api/dashboard").json()["tally"]["light"] == "amber"      # never connected
    connector.run_once(cfg)                                                  # first poll: a due check is handed out and answered
    d = c.get("/api/dashboard").json()
    assert d["tally"]["connector_online"] and d["tally"]["light"] == "green"
    assert d["bills"]["active"] == 2 and d["bills"]["waiting_phone"] == 1 and d["coupons_issued"] == 4

    # wrong key is refused
    bad = dict(cfg, key="nope")
    assert connector.run_once(bad) == 15

    # "Sync now" from the website is picked up on the next poll
    data.write_text(json.dumps([bill(1, 63000), bill(2, 21000), bill(3, 70000, phone=False), bill(4, 42000)]))
    c.post("/api/sync", json={"mode": "quick"})
    connector.run_once(cfg)
    assert c.get("/api/dashboard").json()["bills"]["active"] == 3

    # probe: shows what was read, saves the raw reply for download
    assert c.post("/api/connector/probe").status_code == 200
    connector.run_once(cfg)
    st = c.get("/api/connector/status").json()
    assert st["online"] and st["probe"]["ok"] and st["probe"]["sales"] == 4 and st["probe"]["with_phone"] == 3
    assert c.get("/api/connector/probe.xml").status_code == 200

    # connector zip has the config filled in
    z = c.get("/api/connector/download")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    import io, zipfile
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "LuckyDraw-Connector/connector.ini" in names and "LuckyDraw-Connector/connector.bat" in names
    ini = zipfile.ZipFile(io.BytesIO(z.content)).read("LuckyDraw-Connector/connector.ini").decode()
    assert f"key={cfg['key']}" in ini


def test_connector_reports_tally_down(world):
    c, cfg, data = world
    cfg = dict(cfg, tally="http://127.0.0.1:9")                              # nothing listens there
    connector.run_once(cfg)
    d = c.get("/api/dashboard").json()
    assert d["tally"]["last"]["ok"] is False and "Cannot read from Tally" in d["tally"]["last"]["message"]


def test_connector_close_campaign_waits_for_connector(world):
    c, cfg, data = world
    data.write_text(json.dumps([bill(1, 21000)]))
    stop = threading.Event()
    connector.run_once(cfg)                      # the connector is already running on the Tally PC

    def run():
        while not stop.is_set():
            connector.run_once(cfg)
            time.sleep(0.3)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    r = c.post("/api/campaign/close", json={"force": False})
    stop.set()
    assert r.status_code == 200, r.text
    assert c.get("/api/dashboard").json()["campaign"]["closed"] is True


def test_connector_key_required(world):
    c, cfg, _ = world
    assert c.post("/api/connector/poll", json={}).status_code == 401
    assert c.get("/api/connector/status").status_code == 200   # admin session
