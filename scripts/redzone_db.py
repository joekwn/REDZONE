"""
REDZONE - where the database lives, and how to open it.

  * On your computer or your own server (default): the file data/redzone.duckdb
  * On Vercel (or any host without a disk you can keep): a MotherDuck cloud database.
    Set these environment variables:
        REDZONE_DB=md:redzone          (md: + the MotherDuck database name)
        MOTHERDUCK_TOKEN=...           (from the MotherDuck settings page; never put it in code)

The same scripts (website, firms_live.py, firms_backfill.py, check_firms.py) work with both.

SERVERLESS is True on Vercel (Vercel sets VERCEL=1), or when REDZONE_SERVERLESS=1.
There, the code folder is read-only and nothing keeps running between requests, so
temporary files go to /tmp and background loops are replaced by scheduled calls.
"""
import os
import tempfile
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DB = PROJECT_ROOT / "data" / "redzone.duckdb"
SERVERLESS = bool(os.environ.get("VERCEL")) or os.environ.get("REDZONE_SERVERLESS") == "1"
# where files that are only useful for a short time can go (read-only code folder on Vercel)
WORK_DIR = Path(tempfile.gettempdir()) / "redzone" if SERVERLESS else PROJECT_ROOT / "data"


def target():
    """The database to open: REDZONE_DB if set, otherwise the local file."""
    return os.environ.get("REDZONE_DB", "").strip() or str(LOCAL_DB)


def is_cloud(t=None):
    return (t or target()).startswith("md:")


def describe():
    """Safe to print: never contains the token."""
    t = target()
    return f"MotherDuck database '{t[3:] or '(default)'}'" if is_cloud(t) else t


def connect(read_only=False):
    """Open the database. Raises SystemExit with a plain message if it cannot."""
    t = target()
    if is_cloud(t):
        token = os.environ.get("MOTHERDUCK_TOKEN", "").strip() or os.environ.get("motherduck_token", "").strip()
        if not token:
            raise SystemExit("STOP: REDZONE_DB points to MotherDuck, but MOTHERDUCK_TOKEN is not set.")
        os.environ["motherduck_token"] = token            # the MotherDuck extension reads this name
        config = {}
        if SERVERLESS:                                     # home folder is read-only on Vercel
            tmp = Path(tempfile.gettempdir())
            config = {"home_directory": str(tmp), "extension_directory": str(tmp / "duckdb_extensions")}
        try:
            return duckdb.connect(t, config=config)
        except duckdb.Error as exc:
            raise SystemExit(f"STOP: could not open the MotherDuck database ({type(exc).__name__}: {exc})")
    if not Path(t).exists():
        raise SystemExit(f"STOP: database not found: {t}")
    try:
        return duckdb.connect(t, read_only=read_only)
    except duckdb.IOException:
        raise SystemExit("STOP: the database is in use (is the website server running?). Stop it first.")
