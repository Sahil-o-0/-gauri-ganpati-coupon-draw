"""Put the last hourly backup back as the live database.

    python -m luckydraw.restore

Stop the software first (close its window). The current database is kept as data/before-restore-<time>.db.
After restoring, start the software again: it re-reads Tally and catches up on anything newer than the backup.
"""
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from . import config, db


def main():
    conn = db.connect()
    try:
        folder = Path(db.get_setting(conn, "backup_dir"))
    finally:
        conn.close()
    backup = folder / "luckydraw-backup.db"
    if not backup.exists():
        print(f"No backup found at {backup}")
        return 1
    check = sqlite3.connect(backup)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if ok != "ok":
        print("The backup file is damaged, so it was NOT restored:", ok)
        return 1
    print(f"Backup found: {backup}  (saved {datetime.fromtimestamp(backup.stat().st_mtime):%d-%m-%Y %H:%M})")
    if input("Type YES to replace the current data with this backup: ").strip() != "YES":
        print("Cancelled. Nothing changed.")
        return 0
    if config.DB_PATH.exists():
        keep = config.DATA_DIR / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.db"
        shutil.copy2(config.DB_PATH, keep)
        print("Current data saved as", keep)
    for suffix in ("-wal", "-shm"):
        Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
    shutil.copy2(backup, config.DB_PATH)
    print("Restored. You can start the software again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
