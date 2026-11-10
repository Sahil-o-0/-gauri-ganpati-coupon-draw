"""Lucky Draw Tally connector (Python version, standard library only).

Runs on the Tally PC. It asks the lucky-draw server every few seconds whether a check is due. If so, it sends the
server's request to Tally (on this PC) and posts Tally's raw reply back to the server. It only ever reads from Tally.
Most shops use connector.bat (PowerShell, nothing to install). This file does the same job for PCs that have Python.
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

VERSION = "1"


def log(msg):
    print(time.strftime("%d-%m-%Y %H:%M:%S") + "  " + msg, flush=True)


def load_config(path):
    cfg = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    cfg["server"] = cfg.get("server", "").rstrip("/")
    cfg.setdefault("tally", "http://localhost:9000")
    if not cfg["server"] or not cfg.get("key"):
        raise SystemExit("connector.ini needs server= and key= lines. Download the connector again from the lucky-draw website.")
    return cfg


def call_server(cfg, path, data, content_type="application/json", timeout=120):
    req = urllib.request.Request(cfg["server"] + path, data=data, method="POST",
                                 headers={"X-Connector-Key": cfg["key"], "Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def ask_tally(cfg, xml_text):
    req = urllib.request.Request(cfg["tally"], data=xml_text.encode("utf-8"),
                                 headers={"Content-Type": "text/xml; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read()


def run_once(cfg):
    """One round: poll, and if there is a job, do it. Returns the number of seconds to wait."""
    body = json.dumps({"version": VERSION, "computer": socket.gethostname()}).encode("utf-8")
    try:
        reply = json.loads(call_server(cfg, "/api/connector/poll", body).decode("utf-8"))
    except Exception as e:
        log("Cannot reach the lucky-draw server: " + str(e))
        return 15
    job = reply.get("job")
    wait = int(reply.get("wait") or 10)
    if not job:
        return wait
    log(f"Job {job['id']} ({job['kind']})")
    try:
        data = ask_tally(cfg, job["request_xml"])
    except Exception as e:
        msg = f"Cannot read from Tally at {cfg['tally']}. Is Tally open with the company loaded and the server on? ({e})"
        log(msg)
        try:
            call_server(cfg, f"/api/connector/error/{job['id']}", json.dumps({"message": msg}).encode("utf-8"))
        except Exception as e2:
            log("Could not report the problem to the server: " + str(e2))
        return wait
    try:
        call_server(cfg, f"/api/connector/result/{job['id']}", data, "application/octet-stream", timeout=300)
        log(f"Sent Tally's reply ({len(data)} bytes) to the server.")
        return 2
    except Exception as e:
        log("Could not send Tally's reply to the server: " + str(e))
        return wait


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = load_config(os.path.join(here, "connector.ini"))
    log(f"Connector started. Server: {cfg['server']}  Tally: {cfg['tally']}")
    while True:
        try:
            wait = run_once(cfg)
        except Exception as e:  # never stop
            log("Unexpected problem: " + str(e))
            wait = 15
        time.sleep(wait)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
