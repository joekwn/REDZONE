"""
REDZONE - Step 4: Cleaning by FLAGGING (no rows removed, no values changed).

Reads each harmonized file one at a time and adds 3 new columns:
  frp_flag    : 'ok', 'zero', 'negative' (or 'missing' if FRP is empty)
  frp_usable  : the FRP value when it is > 0, otherwise empty (null)
  polar_flag  : True if latitude > 75 or latitude < -60

Output: data/processed/clean/  (same file names as harmonized)
"""

from pathlib import Path
import sys

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "data" / "processed" / "harmonized"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "clean"

POLAR_NORTH = 75.0    # our own simple cut-off, not a NASA rule
POLAR_SOUTH = -60.0
EXPECTED_ROWS = 21_167_202


def add_flags(table):
    frp = table.column("frp")
    lat = table.column("latitude")

    frp_flag = pc.if_else(
        pc.is_null(frp), "missing",
        pc.if_else(pc.less(frp, 0), "negative",
                   pc.if_else(pc.equal(frp, 0), "zero", "ok")))
    frp_flag = pc.cast(frp_flag, pa.large_string())

    null_frp = pa.nulls(len(frp), type=pa.float64())
    frp_usable = pc.if_else(pc.greater(frp, 0), frp, null_frp)

    polar_flag = pc.or_(pc.greater(lat, POLAR_NORTH), pc.less(lat, POLAR_SOUTH))

    table = table.append_column("frp_flag", frp_flag)
    table = table.append_column("frp_usable", frp_usable)
    table = table.append_column("polar_flag", polar_flag)
    return table


def main():
    if not INPUT_DIR.is_dir():
        sys.exit(f"STOP: folder not found: {INPUT_DIR}")
    if OUTPUT_DIR.exists() and any(OUTPUT_DIR.glob("*.parquet")):
        sys.exit(f"STOP: {OUTPUT_DIR} already has Parquet files. "
                 "Delete or rename it first, so nothing is overwritten.")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(INPUT_DIR.glob("*.parquet"))
    total = 0
    counts = {"ok": 0, "zero": 0, "negative": 0, "missing": 0}
    polar = 0

    for path in files:
        table = pq.read_table(path)
        rows_in = table.num_rows
        out = add_flags(table)
        if out.num_rows != rows_in:
            sys.exit(f"STOP: row count changed in {path.name}")

        pq.write_table(out, OUTPUT_DIR / path.name)

        total += rows_in
        for value, n in zip(*pc.value_counts(out.column("frp_flag")).flatten()):
            counts[value.as_py()] += n.as_py()
        polar += pc.sum(out.column("polar_flag")).as_py() or 0
        print(f"  {path.name:<36} {rows_in:>9,} rows")

    print("\nDONE")
    print(f"Files written: {len(files)} (expected 87)")
    print(f"Rows: {total:,} (expected {EXPECTED_ROWS:,})")
    print("frp_flag counts:")
    for k, v in counts.items():
        print(f"   {k:<9} {v:>12,}")
    print(f"polar_flag = True: {polar:,}")


if __name__ == "__main__":
    main()