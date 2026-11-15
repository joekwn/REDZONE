
import pandas as pd
from pathlib import Path

# Project directories
BASE_DIR = Path(__file__).resolve().parent.parent

DATASETS = {
    "MODIS": {
        "archive": (
            BASE_DIR / "data/raw/MODIS_HISTORICAL/2025_2026/"
            "fire_archive_M-C61_848080.csv"
        ),
        "nrt": (
            BASE_DIR / "data/raw/MODIS_HISTORICAL/2025_2026/"
            "fire_nrt_M-C61_848080.csv"
        ),
        "output": BASE_DIR / "data/processed/standardized/MODIS",
    },
    "VIIRS_NOAA21": {
        "archive": (
            BASE_DIR / "data/raw/VIIRS_NOAA21/2025_2026/"
            "fire_archive_J2V-C2_848185.csv"
        ),
        "nrt": (
            BASE_DIR / "data/raw/VIIRS_NOAA21/2025_2026/"
            "fire_nrt_J2V-C2_848185.csv"
        ),
        "output": BASE_DIR / "data/processed/standardized/VIIRS_NOAA21",
    },
}

CHUNK_SIZE = 250_000

# Preserve fields that must not be automatically converted
COLUMN_TYPES = {
    "acq_date": "string",
    "acq_time": "string",
    "satellite": "string",
    "instrument": "string",
    "confidence": "string",
}


def process_file(file_path, status, output_dir):
    """Read a CSV in chunks and save each chunk as Parquet."""

    total_rows = 0

    for chunk_number, chunk in enumerate(
        pd.read_csv(
            file_path,
            chunksize=CHUNK_SIZE,
            dtype=COLUMN_TYPES,
        ),
        start=1,
    ):
        # Ensure both archive and NRT have the same columns
        if "type" not in chunk.columns:
            chunk["type"] = pd.Series(
                pd.NA,
                index=chunk.index,
                dtype="string",
            )
        else:
            chunk["type"] = chunk["type"].astype("string")

        # Preserve the origin of each record
        chunk["data_status"] = status
        chunk["source_file"] = file_path.name

        # Save this chunk
        output_file = output_dir / (
            f"{status}_part_{chunk_number:04d}.parquet"
        )

        chunk.to_parquet(output_file, index=False)

        total_rows += len(chunk)

        print(
            f"{file_path.name}: "
            f"saved chunk {chunk_number:,} "
            f"({len(chunk):,} rows)"
        )

    print(f"Finished {status}: {total_rows:,} rows\n")
    return total_rows


def main():
    for dataset_name, config in DATASETS.items():
        print(f"\n========== {dataset_name} ==========")

        output_dir = config["output"]
        output_dir.mkdir(parents=True, exist_ok=True)

        archive_rows = process_file(
            config["archive"], "archive", output_dir
        )

        nrt_rows = process_file(
            config["nrt"], "nrt", output_dir
        )

        print(
            f"{dataset_name} total: "
            f"{archive_rows + nrt_rows:,}"
        )


if __name__ == "__main__":
    main()
