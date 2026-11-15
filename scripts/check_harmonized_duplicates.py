"""
REDZONE - Step 2: Check the harmonized files and COUNT exact duplicates.

Part A: every harmonized file has the same 18-column layout (reads file footers only).
Part B: uses DuckDB to find rows that are exact copies of each other
        within the same sensor. Nothing is deleted - this only reports.

A "duplicate" here = same sensor_family AND identical values in every
scientific column. `data_status` and `source_file` are NOT compared, so
the same detection appearing in both an archive and an NRT file would
also be caught.
"""

from pathlib import Path
import sys

import duckdb
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARMONIZED = PROJECT_ROOT / "data" / "processed" / "harmonized"
EXPECTED_FILES = 87
EXPECTED_ROWS = 21_167_202

KEY_COLUMNS = [
    "sensor_family", "satellite", "instrument",
    "latitude", "longitude", "acq_date", "acq_time",
    "brightness", "scan", "track", "bright_t31", "frp",
    "confidence", "version", "daynight", "type",
]


def part_a_schema():
    print("=" * 70)
    print("PART A - Layout of harmonized files")
    print("=" * 70)
    files = sorted(HARMONIZED.glob("*.parquet"))
    layouts = {}
    rows = 0
    for f in files:
        pf = pq.ParquetFile(f)
        sig = tuple((fld.name, str(fld.type)) for fld in pf.schema_arrow)
        layouts.setdefault(sig, []).append(f.name)
        rows += pf.metadata.num_rows

    print(f"Files: {len(files)} (expected {EXPECTED_FILES})")
    print(f"Different layouts: {len(layouts)} (expected 1)")
    print(f"Rows: {rows:,} (expected {EXPECTED_ROWS:,})")
    ok = len(files) == EXPECTED_FILES and len(layouts) == 1 and rows == EXPECTED_ROWS
    print("RESULT:", "OK" if ok else "PROBLEM - paste this output")
    return ok


def part_b_duplicates():
    print("\n" + "=" * 70)
    print("PART B - Exact duplicates (counted, not removed)")
    print("=" * 70)

    con = duckdb.connect()
    con.execute("SET memory_limit = '4GB'")          # keeps RAM use safe
    con.execute("SET preserve_insertion_order = false")

    src = f"read_parquet('{(HARMONIZED / '*.parquet').as_posix()}')"
    keys = ", ".join(KEY_COLUMNS)

    # Each row of `dups` = one detection that appears more than once.
    con.execute(f"""
        CREATE TEMP TABLE dups AS
        SELECT {keys},
               COUNT(*)                                   AS copies,
               STRING_AGG(DISTINCT data_status, '+')      AS statuses,
               COUNT(DISTINCT source_file)                AS n_files
        FROM {src}
        GROUP BY ALL
        HAVING COUNT(*) > 1
    """)

    summary = con.execute("""
        SELECT sensor_family,
               COUNT(*)            AS duplicated_detections,
               SUM(copies - 1)     AS extra_rows
        FROM dups GROUP BY sensor_family ORDER BY sensor_family
    """).fetchall()

    if not summary:
        print("No exact duplicates found.")
        return

    for family, groups, extra in summary:
        print(f"{family}: {groups:,} detections appear more than once "
              f"-> {extra:,} extra rows")

    print("\nWhere the copies come from (statuses / number of files):")
    for row in con.execute("""
        SELECT sensor_family, statuses, n_files, COUNT(*) AS detections
        FROM dups GROUP BY ALL ORDER BY detections DESC
    """).fetchall():
        print(f"  {row[0]:<6} statuses={row[1]:<12} files={row[2]}  -> {row[3]:,} detections")

    print("\nExamples (up to 5):")
    for row in con.execute("""
        SELECT sensor_family, satellite, latitude, longitude, acq_date, acq_time,
               confidence, frp, copies, statuses
        FROM dups ORDER BY copies DESC LIMIT 5
    """).fetchall():
        print("  ", row)


def main():
    if not HARMONIZED.is_dir():
        sys.exit(f"STOP: folder not found: {HARMONIZED}")
    part_a_schema()
    part_b_duplicates()


if __name__ == "__main__":
    main()