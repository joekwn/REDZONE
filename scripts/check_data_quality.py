"""
REDZONE - Step 3: Data quality check (REPORT ONLY - nothing is changed).

Looks for impossible or unexpected values in the harmonized dataset:
  - empty (null) values in each column
  - coordinates outside the globe
  - dates outside the study period, times that are not valid HHMM
  - unexpected values in text columns (satellite, confidence, daynight, ...)
  - negative or zero values where they should be positive

Results are printed per sensor. Decide what to do with them AFTER reading.
"""

from pathlib import Path
import sys

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARMONIZED = PROJECT_ROOT / "data" / "processed" / "harmonized"
START_DATE = "2025-11-01"
END_DATE = "2026-11-01"   # inclusive

ALL_COLUMNS = [
    "latitude", "longitude", "brightness", "scan", "track", "acq_date",
    "acq_time", "sensor_family", "satellite", "instrument", "confidence",
    "version", "bright_t31", "frp", "daynight", "type", "data_status",
    "source_file",
]

# name -> SQL condition that marks a row as a PROBLEM
RULES = {
    "latitude outside -90..90":        "latitude NOT BETWEEN -90 AND 90",
    "longitude outside -180..180":     "longitude NOT BETWEEN -180 AND 180",
    "acq_date not a valid date":       "TRY_CAST(acq_date AS DATE) IS NULL",
    "acq_date outside study period":
        f"TRY_CAST(acq_date AS DATE) NOT BETWEEN DATE '{START_DATE}' AND DATE '{END_DATE}'",
    "acq_time not 4 digits":           "NOT regexp_full_match(acq_time, '[0-9]{4}')",
    "acq_time not a real clock time":
        "regexp_full_match(acq_time, '[0-9]{4}') AND "
        "(TRY_CAST(substr(acq_time,1,2) AS INT) > 23 OR TRY_CAST(substr(acq_time,3,2) AS INT) > 59)",
    "MODIS confidence not 0..100":
        "sensor_family = 'MODIS' AND (TRY_CAST(confidence AS INT) IS NULL "
        "OR TRY_CAST(confidence AS INT) NOT BETWEEN 0 AND 100)",
    "VIIRS confidence not l/n/h":
        "sensor_family = 'VIIRS' AND confidence NOT IN ('l','n','h')",
    "daynight not D/N":                "daynight NOT IN ('D','N')",
    "frp negative":                    "frp < 0",
    "frp exactly 0":                   "frp = 0",
    "brightness <= 0":                 "brightness <= 0",
    "bright_t31 <= 0":                 "bright_t31 <= 0",
    "scan or track <= 0":              "scan <= 0 OR track <= 0",
    "archive row with no type":        "data_status = 'archive' AND type IS NULL",
}

# Text columns whose distinct values we simply list, to eyeball them.
LIST_VALUES = ["satellite", "instrument", "daynight", "data_status", "type"]


def main():
    if not HARMONIZED.is_dir():
        sys.exit(f"STOP: folder not found: {HARMONIZED}")

    con = duckdb.connect()
    con.execute("SET memory_limit = '4GB'")
    src = f"read_parquet('{(HARMONIZED / '*.parquet').as_posix()}')"
    con.execute(f"CREATE VIEW fires AS SELECT * FROM {src}")

    # ---- 1. Empty values ---------------------------------------------------
    print("=" * 70)
    print("1. EMPTY (NULL) VALUES per column")
    print("=" * 70)
    null_sql = ", ".join(f"COUNT(*) - COUNT({c}) AS {c}" for c in ALL_COLUMNS)
    for family, *counts in con.execute(
            f"SELECT sensor_family, {null_sql} FROM fires GROUP BY 1 ORDER BY 1").fetchall():
        found = [(c, n) for c, n in zip(ALL_COLUMNS, counts) if n]
        print(f"{family}:")
        if not found:
            print("   none")
        for c, n in found:
            print(f"   {c:<14} {n:>12,}")

    # ---- 2. Rule checks ----------------------------------------------------
    print("\n" + "=" * 70)
    print("2. PROBLEM CHECKS (rows that break each rule)")
    print("=" * 70)
    rule_sql = ", ".join(
        f"COUNT(*) FILTER (WHERE COALESCE({cond}, FALSE)) AS r{i}"
        for i, cond in enumerate(RULES.values()))
    rows = con.execute(
        f"SELECT sensor_family, COUNT(*), {rule_sql} FROM fires GROUP BY 1 ORDER BY 1"
    ).fetchall()
    print(f"{'rule':<34}" + "".join(f"{r[0]:>14}" for r in rows))
    print(f"{'(total rows)':<34}" + "".join(f"{r[1]:>14,}" for r in rows))
    for i, name in enumerate(RULES):
        print(f"{name:<34}" + "".join(f"{r[2 + i]:>14,}" for r in rows))

    # ---- 3. Distinct values ------------------------------------------------
    print("\n" + "=" * 70)
    print("3. VALUES FOUND in text columns")
    print("=" * 70)
    for col in LIST_VALUES:
        print(f"{col}:")
        for family, value, n in con.execute(
                f"SELECT sensor_family, {col}, COUNT(*) FROM fires "
                f"GROUP BY 1, 2 ORDER BY 1, 3 DESC").fetchall():
            print(f"   {family:<6} {value!r:<12} {n:>12,}")

    # ---- 4. Value ranges ---------------------------------------------------
    print("\n" + "=" * 70)
    print("4. RANGES of numeric columns (min / max)")
    print("=" * 70)
    for col in ["latitude", "longitude", "brightness", "bright_t31", "frp", "scan", "track"]:
        for family, lo, hi in con.execute(
                f"SELECT sensor_family, MIN({col}), MAX({col}) FROM fires "
                f"GROUP BY 1 ORDER BY 1").fetchall():
            print(f"   {col:<11} {family:<6} min={lo:<12} max={hi}")


if __name__ == "__main__":
    main()