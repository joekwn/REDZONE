"""
REDZONE - copy your local database (data/redzone.duckdb) to MotherDuck, for the Vercel option.

Before running:
  1. Stop the website server (the file must not be in use).
  2. In this PowerShell window:   $env:MOTHERDUCK_TOKEN = "paste-your-token"
Run:
     py scripts\\upload_to_motherduck.py              # creates the cloud database redzone_cloud
     py scripts\\upload_to_motherduck.py --replace    # overwrite it (e.g. after big local changes)

The cloud name is different from the file name on purpose: MotherDuck refuses an upload
when the two names are the same.
"""
import argparse
import os
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "data" / "redzone.duckdb"
SUPPORTED = ((1, 4, 1), (1, 5, 5))          # DuckDB versions MotherDuck accepted when this was written


def version_tuple(v):
    return tuple(int(x) for x in v.split("-")[0].split(".")[:3])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="redzone_cloud", help="cloud database name (default redzone_cloud)")
    p.add_argument("--replace", action="store_true", help="overwrite the cloud database if it exists")
    args = p.parse_args()

    v = duckdb.__version__
    print("DuckDB version on this computer:", v)
    if not (SUPPORTED[0] <= version_tuple(v) <= SUPPORTED[1]):
        sys.exit("STOP: MotherDuck needs DuckDB 1.4.1 to 1.5.5. Run:  py -m pip install \"duckdb==1.5.5\"")
    token = os.environ.get("MOTHERDUCK_TOKEN", "").strip()
    if not token:
        sys.exit('STOP: set the token first:  $env:MOTHERDUCK_TOKEN = "..."')
    os.environ["motherduck_token"] = token
    if not LOCAL.exists():
        sys.exit(f"STOP: {LOCAL} not found")

    print(f"Local file: {LOCAL}  ({LOCAL.stat().st_size / 1e9:.2f} GB)")
    try:
        local = duckdb.connect(str(LOCAL), read_only=True)
    except duckdb.IOException:
        sys.exit("STOP: the database is in use. Stop the website server first.")
    local_rows = local.execute("SELECT COUNT(*) FROM fires").fetchone()[0]
    local.close()
    print(f"Local fires: {local_rows:,} rows")

    con = duckdb.connect("md:")
    print("Connected to MotherDuck. Uploading - this can take a while for a large file...")
    verb = "CREATE OR REPLACE DATABASE" if args.replace else "CREATE DATABASE"
    try:
        con.execute(f"{verb} {args.name} FROM '{LOCAL.as_posix()}'")
    except duckdb.Error as exc:
        if "already exists" in str(exc):
            sys.exit(f"STOP: '{args.name}' already exists in MotherDuck. Add --replace to overwrite it.")
        raise
    cloud_rows = con.execute(f"SELECT COUNT(*) FROM {args.name}.main.fires").fetchone()[0]
    con.close()
    print(f"Cloud fires: {cloud_rows:,} rows")
    if cloud_rows != local_rows:
        sys.exit("WARNING: the row counts differ - run again with --replace")
    print(f"\nDONE. On Vercel set:  REDZONE_DB = md:{args.name}")


if __name__ == "__main__":
    main()
