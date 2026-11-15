"""
Test setup: a fresh empty database in a temp folder, a fake FIRMS API, and
"serverless" mode (no background threads, temporary files go to the temp folder).
No internet, no FIRMS key and no research dataset are needed.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="redzone_test_"))
DB_FILE = TMP / "test.duckdb"

os.environ.update({
    "REDZONE_SERVERLESS": "1",
    "REDZONE_DB": str(DB_FILE),
    "REDZONE_SECRET": "test-secret",
    "CRON_SECRET": "test-cron-secret",
    "FIRMS_MAP_KEY": "test-key",
    "REDZONE_ADMIN_EMAILS": "admin@example.com",
    "REDZONE_CONTACT_EMAIL": "team@example.com",
})
for name in ("SMTP_HOST", "VERCEL", "REDZONE_TRUST_PROXY"):
    os.environ.pop(name, None)
os.environ["TMPDIR"] = str(TMP)
tempfile.tempdir = str(TMP)

sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests")]

import init_db                                   # noqa: E402
import firms_live                                # noqa: E402
from fake_firms import fake_fetch                # noqa: E402

init_db.create(DB_FILE)
firms_live.fetch_csv = fake_fetch                # every download goes to the fake API


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from webapp.server import app
    with TestClient(app) as c:
        c.headers["X-RedZone"] = "1"             # the anti-CSRF header the website sends
        yield c
