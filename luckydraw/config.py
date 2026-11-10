import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("LUCKYDRAW_DATA", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "luckydraw.db"
DEFAULT_BACKUP_DIR = Path(os.environ.get("LUCKYDRAW_BACKUP", BASE_DIR / "backup"))
STATIC_DIR = Path(__file__).resolve().parent / "static"
HOST = os.environ.get("LUCKYDRAW_HOST", "0.0.0.0")
PORT = int(os.environ.get("LUCKYDRAW_PORT", "8080"))
# "direct": this software runs on the Tally PC and talks to Tally itself.
# "connector": this software runs on a server; a small connector program on the Tally PC sends the bills to it.
SOURCE = os.environ.get("LUCKYDRAW_SOURCE", "direct")
PUBLIC_URL = os.environ.get("LUCKYDRAW_PUBLIC_URL", "").rstrip("/")
# Path the site is served under by the web server in front of it, e.g. "/coupon-draw" (it strips this prefix).
BASE_PATH = os.environ.get("LUCKYDRAW_BASE_PATH", "").rstrip("/")
CONNECTOR_DIR = BASE_DIR / "connector"

DATA_DIR.mkdir(parents=True, exist_ok=True)
