import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="ld-test-")
os.environ["LUCKYDRAW_DATA"] = _tmp
os.environ["LUCKYDRAW_BACKUP"] = str(Path(_tmp) / "backup")
os.environ["LUCKYDRAW_NO_BACKGROUND"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from luckydraw import config, db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    for suffix in ("", "-wal", "-shm"):
        Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
    db.init_db()
    yield
