from pathlib import Path
import pandas as pd
import re

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

INPUT_DIR = Path("data/raw/MODIS_HISTORICAL")

OUTPUT_DIRS = {
    "TERRA": Path("data/processed/MODIS_TERRA/historical"),
    "AQUA": Path("data/processed/MODIS_AQUA/historical"),
    "UNCLASSIFIED": Path("data/processed/MODIS_UNCLASSIFIED/historical"),
}

CHUNK_SIZE = 250_000

SATELLITE_MAPPING = {
    "T": "TERRA",
    "TERRA": "TERRA",
    "MODIS_TERRA": "TERRA",
    "A": "AQUA",
    "AQUA": "AQUA",
    "MODIS_AQUA": "AQUA",
}

# --------------------------------------------------
# PREPARE OUTPUT DIRECTORIES
# --------------------------------------------------

for directory in OUTPUT_DIRS.values():
    directory.mkdir(parents=True, exist_ok=True)

files = sorted(
    list(INPUT_DIR.rglob("*.csv")) +
    list(INPUT_DIR.rglob("*.txt"))
)

if not files:
    raise SystemExit(
        f"No CSV or TXT files found inside {INPUT_DIR}"
    )

print(f"Found {len(files)} input files.")

# --------------------------------------------------
# PROCESSING COUNTERS
# --------------------------------------------------

totals = {
    "TERRA": 0,
    "AQUA": 0,
    "UNCLASSIFIED": 0,
}

total_input_records = 0
processed_files = 0

# --------------------------------------------------
# PROCESS EACH INPUT FILE
# --------------------------------------------------

for file_path in files:

    relative_path = file_path.relative_to(INPUT_DIR)

    print("\n" + "=" * 55)
    print("Processing:", relative_path)
    print("=" * 55)

    file_identifier = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        relative_path.as_posix()
    )

    chunk_number = 0

    chunks = pd.read_csv(
        file_path,
        chunksize=CHUNK_SIZE,
        encoding="utf-8-sig",
        low_memory=False
    )

    for chunk in chunks:

        total_input_records += len(chunk)

        # Find satellite column regardless of capitalization.
        satellite_col = next(
            (
                col for col in chunk.columns
                if str(col).strip().lower() == "satellite"
            ),
            None
        )

        if satellite_col is None:
            raise ValueError(
                f"Satellite column missing in {relative_path}. "
                f"Available columns: {list(chunk.columns)}"
            )

        # Normalize satellite names.
        satellite_values = (
            chunk[satellite_col]
            .astype("string")
            .str.strip()
            .str.upper()
        )

        groups = satellite_values.map(SATELLITE_MAPPING)
        groups = groups.fillna("UNCLASSIFIED")

        # Preserve the original fields and add provenance.
        chunk = chunk.copy()
        chunk["source_file"] = relative_path.as_posix()
        chunk["satellite_group"] = groups

        # Save each satellite separately.
        for group_name, output_dir in OUTPUT_DIRS.items():

            selected = chunk[
                chunk["satellite_group"] == group_name
            ].copy()

            if selected.empty:
                continue

            output_file = (
                f"{file_identifier}_part_{chunk_number:04d}.parquet"
            )

            output_path = output_dir / output_file

            selected.to_parquet(
                output_path,
                index=False,
                engine="pyarrow"
            )

            rows = len(selected)
            totals[group_name] += rows

            print(f"{group_name}: {rows:,} records")

        chunk_number += 1

    processed_files += 1

# --------------------------------------------------
# FINAL REPORT
# --------------------------------------------------

print("\n")
print("=" * 55)
print("MODIS HISTORICAL PROCESSING COMPLETE")
print("=" * 55)

print(f"Files processed: {processed_files}")
print(f"Total input records: {total_input_records:,}")

for group_name, count in totals.items():
    print(f"{group_name} detections: {count:,}")

print("\nOutput directories:")

for group_name, directory in OUTPUT_DIRS.items():
    print(f"{group_name}: {directory}")

print("\nOriginal NASA files were not modified.")
print("No detections were intentionally discarded.")
