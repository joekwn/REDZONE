from pathlib import Path
import pandas as pd

# ---------------------------------------
# CONFIGURATION
# ---------------------------------------

INPUT_DIR = Path("data/raw/MODIS_HISTORICAL")

OUTPUT_DIR = Path("data/processed/MODIS_TERRA/historical")

CHUNK_SIZE = 250_000

# Terra identifiers used in FIRMS data
TERRA_VALUES = {"T", "TERRA"}


# ---------------------------------------
# FIND INPUT FILES
# ---------------------------------------

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

files = sorted(
    list(INPUT_DIR.rglob("*.csv")) +
    list(INPUT_DIR.rglob("*.txt"))
)

if not files:
    raise SystemExit(
        f"No CSV or TXT files found inside {INPUT_DIR}"
    )

print(f"Found {len(files)} input files.")

total_terra = 0
total_processed = 0
total_files = 0


# ---------------------------------------
# PROCESS EACH FILE IN CHUNKS
# ---------------------------------------

for file_path in files:

    print(f"\nProcessing: {file_path.name}")

    file_terra = 0
    file_parts = 0

    try:
        chunks = pd.read_csv(
            file_path,
            chunksize=CHUNK_SIZE,
            encoding="utf-8-sig",
            low_memory=False
        )

        for chunk in chunks:

            total_processed += len(chunk)

            # Locate satellite column
            satellite_col = next(
                (
                    col for col in chunk.columns
                    if str(col).strip().lower() == "satellite"
                ),
                None
            )

            if satellite_col is None:
                raise ValueError(
                    f"Satellite column missing in {file_path.name}. "
                    f"Columns: {list(chunk.columns)}"
                )

            # Keep Terra records only
            satellite_values = (
                chunk[satellite_col]
                .astype("string")
                .str.strip()
                .str.upper()
            )

            terra_chunk = chunk[
                satellite_values.isin(TERRA_VALUES)
            ].copy()

            if terra_chunk.empty:
                continue

            # Preserve provenance
            terra_chunk["source_file"] = file_path.name

            # Write a separate Parquet part
            output_name = (
                f"{file_path.stem}_part_{file_parts:04d}.parquet"
            )

            output_path = OUTPUT_DIR / output_name

            terra_chunk.to_parquet(
                output_path,
                index=False,
                engine="pyarrow"
            )

            rows = len(terra_chunk)

            file_terra += rows
            file_parts += 1

        print("Terra detections:", file_terra)
        print("Parquet parts:", file_parts)

        total_terra += file_terra
        total_files += 1

    except Exception as error:
        print(f"ERROR processing {file_path}: {error}")
        raise


# ---------------------------------------
# FINAL SUMMARY
# ---------------------------------------

print("\n" + "=" * 45)
print("MODIS TERRA PROCESSING COMPLETE")
print("=" * 45)

print("Files processed:", total_files)
print("Total input records:", total_processed)
print("Total Terra detections:", total_terra)
print("Output directory:", OUTPUT_DIR)

print("\nOriginal NASA files were not modified.")
