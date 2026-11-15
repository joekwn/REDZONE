"""
REDZONE - Step 1: Harmonize MODIS and VIIRS into one format.

What it does, for each of the 87 standardized Parquet files (one at a time):
  1. Converts `version` to text (it was a number in archive files).
  2. Adds `sensor_family` = "MODIS" or "VIIRS".
  3. Forces the file into ONE fixed schema (same columns, order, types).
  4. Checks nothing was lost, then writes it to data/processed/harmonized/.

What it does NOT do: remove rows, remove duplicates, or change any values.
The standardized input files are only read, never modified.
"""

from pathlib import Path
import sys

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STANDARDIZED = PROJECT_ROOT / "data" / "processed" / "standardized"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "harmonized"

# input folder -> sensor_family value
SOURCES = {
    STANDARDIZED / "MODIS": "MODIS",
    STANDARDIZED / "VIIRS_NOAA21": "VIIRS",
}

# The only version values we found in the value check. Anything else = stop.
ALLOWED_VERSIONS = {"61.03", "6.1NRT", "2", "2.0NRT"}

S = pa.large_string()
TARGET_SCHEMA = pa.schema([
    ("latitude", pa.float64()),
    ("longitude", pa.float64()),
    ("brightness", pa.float64()),
    ("scan", pa.float64()),
    ("track", pa.float64()),
    ("acq_date", S),
    ("acq_time", S),
    ("sensor_family", S),      # NEW
    ("satellite", S),
    ("instrument", S),
    ("confidence", S),
    ("version", S),            # was double / int64 in archive files
    ("bright_t31", pa.float64()),
    ("frp", pa.float64()),
    ("daynight", S),
    ("type", S),
    ("data_status", S),
    ("source_file", S),
])


def harmonize_table(table, sensor_family):
    # 1. version -> text
    idx = table.schema.get_field_index("version")
    version_text = pc.cast(table.column("version"), S)
    table = table.set_column(idx, "version", version_text)

    # 2. add sensor_family (same value for every row in this file)
    family = pa.array([sensor_family] * table.num_rows, type=S)
    table = table.append_column("sensor_family", family)

    # 3. put columns in the target order, then force the target types
    table = table.select(TARGET_SCHEMA.names)
    return table.cast(TARGET_SCHEMA)


def check_versions(table, file_name):
    found = set(pc.unique(table.column("version")).to_pylist())
    unexpected = found - ALLOWED_VERSIONS
    if unexpected:
        sys.exit(f"STOP: unexpected version value(s) {unexpected} in {file_name}")


def main():
    if OUTPUT_DIR.exists() and any(OUTPUT_DIR.glob("*.parquet")):
        sys.exit(f"STOP: {OUTPUT_DIR} already has Parquet files. "
                 "Delete or rename that folder first, so nothing is mixed or overwritten.")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total_in = total_out = n_files = 0

    for folder, family in SOURCES.items():
        files = sorted(folder.glob("*.parquet"))
        print(f"{family}: {len(files)} files from {folder}")

        for path in files:
            table = pq.read_table(path)
            rows_in = table.num_rows

            out_table = harmonize_table(table, family)
            check_versions(out_table, path.name)
            if out_table.num_rows != rows_in:
                sys.exit(f"STOP: row count changed in {path.name}")

            out_path = OUTPUT_DIR / f"{family}_{path.name}"   # e.g. MODIS_nrt_part_0001.parquet
            pq.write_table(out_table, out_path)

            total_in += rows_in
            total_out += out_table.num_rows
            n_files += 1
            print(f"  {path.name:<28} {rows_in:>9,} rows -> {out_path.name}")

    print("\nDONE")
    print(f"Files written: {n_files} (expected 87)")
    print(f"Rows in:  {total_in:,}")
    print(f"Rows out: {total_out:,} (expected 21,167,202)")


if __name__ == "__main__":
    main()