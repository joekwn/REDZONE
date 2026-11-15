
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

folders = {
    "MODIS": BASE_DIR / "data/processed/standardized/MODIS",
    "VIIRS NOAA-21": BASE_DIR / "data/processed/standardized/VIIRS_NOAA21",
}

for name, folder in folders.items():
    print(f"\n========== {name} ==========")

    files = sorted(folder.glob("*.parquet"))

    print("Parquet files:", len(files))

    # Inspect one archive chunk and one NRT chunk
    for status in ["archive", "nrt"]:
        matching = [
            f for f in files if f.name.startswith(status + "_")
        ]

        if not matching:
            print(f"No {status} files found!")
            continue

        df = pd.read_parquet(matching[0])

        print(f"\n{status.upper()} sample:")
        print("Rows:", len(df))
        print("Acquisition time examples:", df["acq_time"].head().tolist())
        print("Confidence examples:", df["confidence"].head().tolist())
        print("Type:", df["type"].dtype)
        print("Data status:", df["data_status"].unique())
        print("Columns:", df.columns.tolist())
