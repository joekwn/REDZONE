"""
REDZONE - Inspect the actual values of `version` and `type`.

Compares the values stored in the standardized Parquet files with the
values in the raw FIRMS CSVs (read as plain text, no type conversion).
Read-only: this script changes no files.
"""

from collections import Counter
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STANDARDIZED = PROJECT_ROOT / "data" / "processed" / "standardized"
RAW = PROJECT_ROOT / "data" / "raw"

INSTRUMENTS = {
    "MODIS": {
        "parquet_dir": STANDARDIZED / "MODIS",
        "raw": {
            "archive": RAW / "MODIS_HISTORICAL" / "2025_2026" / "fire_archive_M-C61_848080.csv",
            "nrt": RAW / "MODIS_HISTORICAL" / "2025_2026" / "fire_nrt_M-C61_848080.csv",
        },
    },
    "VIIRS_NOAA21": {
        "parquet_dir": STANDARDIZED / "VIIRS_NOAA21",
        "raw": {
            "archive": RAW / "VIIRS_NOAA21" / "2025_2026" / "fire_archive_J2V-C2_848185.csv",
            "nrt": RAW / "VIIRS_NOAA21" / "2025_2026" / "fire_nrt_J2V-C2_848185.csv",
        },
    },
}

COLUMNS = ["version", "type"]
CSV_CHUNK = 1_000_000


def print_counts(title, counter):
    print(f"  {title}")
    if not counter:
        print("    (column not present)")
        return
    for value, n in sorted(counter.items(), key=lambda kv: -kv[1]):
        # repr() shows the exact value, e.g. '6.1NRT' vs 6.1 vs None
        print(f"    {value!r:<20} {n:>12,}")


def parquet_counts(folder):
    """{(data_status, column): Counter} built from the Parquet files."""
    counts = {}
    for path in sorted(folder.glob("*.parquet")):
        table = pq.read_table(path, columns=COLUMNS + ["data_status"])
        df = table.to_pandas()
        for status, group in df.groupby("data_status", dropna=False):
            for col in COLUMNS:
                key = (status, col)
                vc = group[col].value_counts(dropna=False)
                counts.setdefault(key, Counter()).update(
                    {(None if pd.isna(k) else k): int(v) for k, v in vc.items()}
                )
    return counts


def raw_csv_counts(csv_path):
    """{column: Counter} from a raw CSV, every value read as text."""
    header = pd.read_csv(csv_path, nrows=0).columns
    present = [c for c in COLUMNS if c in header]
    counts = {c: Counter() for c in COLUMNS}
    if not present:
        return counts
    for chunk in pd.read_csv(csv_path, usecols=present, dtype=str,
                             keep_default_na=False, chunksize=CSV_CHUNK):
        for col in present:
            counts[col].update(chunk[col].value_counts().to_dict())
    return counts


def main():
    for name, cfg in INSTRUMENTS.items():
        print("=" * 70)
        print(name)
        print("=" * 70)

        pq_counts = parquet_counts(cfg["parquet_dir"])

        for status, csv_path in cfg["raw"].items():
            print(f"\n--- data_status = {status} ---")
            print(f"[Parquet]")
            for col in COLUMNS:
                print_counts(f"{col}:", pq_counts.get((status, col), Counter()))

            print(f"[Raw CSV as text] {csv_path.name}")
            if not csv_path.is_file():
                print("    ERROR: file not found")
                continue
            raw = raw_csv_counts(csv_path)
            for col in COLUMNS:
                print_counts(f"{col}:", raw[col])
        print()


if __name__ == "__main__":
    main()