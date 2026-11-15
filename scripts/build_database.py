"""
REDZONE - Step 5: Build the database.

Loads all clean Parquet files into ONE DuckDB database file:
    data/redzone.duckdb   ->  table `fires`

Adds one helper column:
    acq_datetime : acq_date + acq_time combined into a real timestamp (UTC)
All original columns are kept unchanged.

Rows are stored sorted by date, which makes "show fires between date X and Y"
queries much faster for the website.
"""

from pathlib import Path
import sys

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLEAN_DIR = PROJECT_ROOT / "data" / "processed" / "clean"
DB_PATH = PROJECT_ROOT / "data" / "redzone.duckdb"
EXPECTED_ROWS = 21_167_202


def main():
    if not CLEAN_DIR.is_dir():
        sys.exit(f"STOP: folder not found: {CLEAN_DIR}")
    if DB_PATH.exists():
        sys.exit(f"STOP: {DB_PATH} already exists. Delete or rename it first.")

    con = duckdb.connect(str(DB_PATH))
    con.execute("SET memory_limit = '4GB'")
    con.execute("SET preserve_insertion_order = false")

    src = f"read_parquet('{(CLEAN_DIR / '*.parquet').as_posix()}')"

    print("Loading data into the database (this can take a few minutes)...")
    con.execute(f"""
        CREATE TABLE fires AS
        SELECT *,
               CAST(acq_date AS DATE)
                 + make_time(CAST(substr(acq_time, 1, 2) AS INT),
                             CAST(substr(acq_time, 3, 2) AS INT), 0) AS acq_datetime
        FROM {src}
        ORDER BY acq_date, sensor_family
    """)

    # ---- checks ----------------------------------------------------------
    total = con.execute("SELECT COUNT(*) FROM fires").fetchone()[0]
    print(f"\nRows in database: {total:,} (expected {EXPECTED_ROWS:,})")
    if total != EXPECTED_ROWS:
        sys.exit("STOP: row count does not match")

    print("\nRows per sensor and data status:")
    for row in con.execute("""
            SELECT sensor_family, data_status, COUNT(*) FROM fires
            GROUP BY ALL ORDER BY ALL""").fetchall():
        print(f"   {row[0]:<6} {row[1]:<8} {row[2]:>12,}")

    lo, hi = con.execute("SELECT MIN(acq_datetime), MAX(acq_datetime) FROM fires").fetchone()
    print(f"\nFirst detection: {lo} UTC")
    print(f"Last detection:  {hi} UTC")

    bad = con.execute("SELECT COUNT(*) FROM fires WHERE acq_datetime IS NULL").fetchone()[0]
    print(f"Rows with no acq_datetime: {bad:,} (expected 0)")

    con.close()
    size_mb = DB_PATH.stat().st_size / 1_000_000
    print(f"\nDONE - database saved: {DB_PATH} ({size_mb:,.0f} MB)")


if __name__ == "__main__":
    main()