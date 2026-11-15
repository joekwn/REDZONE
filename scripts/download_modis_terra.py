import requests
import csv
import io
from pathlib import Path
from getpass import getpass

# NASA Earthdata authentication
token = "eyJ0eXAiOiJKV1QiLCJvcmlnaW4iOiJFYXJ0aGRhdGEgTG9naW4iLCJzaWciOiJlZGxqd3RwdWJrZXlfb3BzIiwiYWxnIjoiUlMyNTYifQ.eyJ0eXBlIjoiVXNlciIsInVpZCI6ImpvZS5raXdhbiIsInN1YiI6ImpvZS5raXdhbiIsImV4cCI6MTc5OTgzOTY3OSwiaWF0IjoxNzk0NjU1Njc5LCJpc3MiOiJodHRwczovL3Vycy5lYXJ0aGRhdGEubmFzYS5nb3YiLCJpZGVudGl0eV9wcm92aWRlciI6ImVkbF9vcHMiLCJhY3IiOiJlZGwiLCJhc3N1cmFuY2VfbGV2ZWwiOjN9.Q_D4Uul1URuavgYT6LLD0EH70l4oWYrUv--Jb2UdnXwpL84wFq_K-9LfdvjEt4RYZ6uEdASA4MvqN4XqU05pwTkO9MjiBMH2LR9_Y4RrgBbwQhGpyov5cwRha2WsTz2ZSVoSsaqTRvWprBv8gqfuLQuKF4rWw5ODnnr-zTo8iF7pSzKCn_QFTvuxnl2a-Glc3VaY10gx9HYB1ixYJEr3azjz5d18R2CQwUy0IjbiSc-nfWXa9dOORQhZ04Net5TSmWaxf-6vPjTg-78oZPm3HCIhPv6mAApSva5RdAaYBMYyEuZ1NZJjoohsS25-OxH-xeHTcP47yyiJQUoRyTEeUQ"

# Test one daily file: November 12, 2026
date = "2026316"

filename = f"MODIS_C6_1_Global_MCD14DL_NRT_{date}.txt"

url = (
    "https://nrt3.modaps.eosdis.nasa.gov/"
    "archive/FIRMS/modis-c6.1/Global/"
    + filename
)

headers = {
    "Authorization": f"Bearer {token}"
}

print("\nConnecting to NASA...")
response = requests.get(url, headers=headers, timeout=180)

print("HTTP status:", response.status_code)

if response.status_code != 200:
    print("Download failed.")
    print(response.text[:500])
    raise SystemExit(1)

# Make sure NASA returned data, not an HTML page
content = response.content

if content.lstrip().lower().startswith((b"<!doctype html", b"<html")):
    print("ERROR: NASA returned HTML instead of a data file.")
    raise SystemExit(1)

# Save the original combined file
raw_folder = Path("data/raw/MODIS_COMBINED")
raw_folder.mkdir(parents=True, exist_ok=True)

raw_path = raw_folder / filename
raw_path.write_bytes(content)

print("Original file saved:", raw_path)

# Read the CSV content
text = content.decode("utf-8-sig")
reader = csv.DictReader(io.StringIO(text))

if not reader.fieldnames:
    print("ERROR: No CSV header found.")
    raise SystemExit(1)

# Find the satellite column without assuming capitalization
satellite_column = next(
    (column for column in reader.fieldnames
     if column.lower() == "satellite"),
    None
)

if satellite_column is None:
    print("ERROR: Satellite column not found.")
    print("Available columns:", reader.fieldnames)
    raise SystemExit(1)

# Keep Terra records only
terra_rows = [
    row for row in reader
    if row.get(satellite_column, "").strip().upper() == "T"
]

# Save Terra-only data
terra_folder = Path("data/processed/MODIS_TERRA")
terra_folder.mkdir(parents=True, exist_ok=True)

terra_path = terra_folder / f"MODIS_TERRA_{date}.csv"

with terra_path.open("w", newline="", encoding="utf-8") as file:
    writer = csv.DictWriter(file, fieldnames=reader.fieldnames)
    writer.writeheader()
    writer.writerows(terra_rows)

print("\nSUCCESS!")
print("Total Terra detections:", len(terra_rows))
print("Terra file:", terra_path)
print("Original file size:", len(content), "bytes")
