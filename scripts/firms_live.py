"""
REDZONE - live updater: FIRMS API -> same rules as before -> database -> alerts.

For each source (MODIS_NRT, VIIRS_NOAA21_NRT) and each day since the newest
data in the database (the last 2 days are always re-checked, because NRT files
keep growing during the day):
  1. download the day's worldwide CSV from the FIRMS area API
  2. save the untouched CSV in data/raw/API/   (provenance, like the raw folder)
  3. harmonize + check + flag it with redzone_rules.harmonize()
  4. insert only rows that are not already in `fires` (no duplicates)
  5. compare the NEW rows with the priority sites and write `alerts`
  6. email confirmed subscribers about NEW dangerous detections (notify.py)
Every run is recorded in table `update_log`.

The MAP_KEY is read from the environment variable FIRMS_MAP_KEY - never
written in code, files or logs.

Run on its own (with the website server STOPPED, because only one program
may write to the database at a time):
    py scripts\\firms_live.py
The website server can also run it for you on a schedule.
"""

from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import argparse
import os
import sys
import time
import urllib.error
import urllib.request

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redzone_rules import harmonize   # noqa: E402
import notify                          # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "redzone.duckdb"
RAW_API_DIR = PROJECT_ROOT / "data" / "raw" / "API"

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
WORLD = "-180,-90,180,90"                      # west,south,east,north
SOURCES = {"MODIS_NRT": "MODIS", "VIIRS_NOAA21_NRT": "VIIRS"}
RECHECK_DAYS = 2                               # re-download the newest days each run
MAX_DAYS_PER_RUN = 30                          # safety cap on API use per run


# ---------------------------------------------------------------------------
def ensure_tables(con):
    """Tables used by the live features (safe to call every time)."""
    con.execute("CREATE SEQUENCE IF NOT EXISTS site_id_seq")
    con.execute("""CREATE TABLE IF NOT EXISTS priority_sites (
        id INTEGER DEFAULT nextval('site_id_seq'), name VARCHAR, lat DOUBLE, lon DOUBLE,
        radius_km DOUBLE, created_at TIMESTAMP DEFAULT current_timestamp)""")
    con.execute("CREATE SEQUENCE IF NOT EXISTS alert_id_seq")
    con.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER DEFAULT nextval('alert_id_seq'), site_id INTEGER, site_name VARCHAR,
        distance_km DOUBLE, latitude DOUBLE, longitude DOUBLE, acq_datetime TIMESTAMP,
        sensor_family VARCHAR, satellite VARCHAR, confidence VARCHAR, frp DOUBLE,
        frp_flag VARCHAR, created_at TIMESTAMP DEFAULT current_timestamp)""")
    con.execute("""CREATE TABLE IF NOT EXISTS update_log (
        run_at TIMESTAMP, source VARCHAR, day DATE, downloaded INTEGER,
        inserted INTEGER, rejected INTEGER, message VARCHAR)""")


def build_url(map_key, source, day):
    return f"{API}/{map_key}/{source}/{WORLD}/1/{day.isoformat()}"


def fetch_csv(url, timeout=180, retries=2):
    """Download text from FIRMS. Error messages never include the URL (it holds the key)."""
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    raise ConnectionError(f"FIRMS download failed after {retries + 1} tries: {type(last).__name__}")


def days_to_fetch(con, start=None, end=None):
    today = datetime.now(timezone.utc).date()
    end = end or today
    if start is None:
        newest = con.execute("SELECT MAX(CAST(acq_date AS DATE)) FROM fires").fetchall()[0][0]
        start = (newest - timedelta(days=RECHECK_DAYS - 1)) if newest else end
    start = max(start, end - timedelta(days=MAX_DAYS_PER_RUN - 1))
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


INSERT_NEW_ROWS = """
    INSERT INTO fires BY NAME
    SELECT n.* FROM new_rows n
    WHERE NOT EXISTS (
        SELECT 1 FROM fires f
        WHERE f.acq_date BETWEEN ? AND ?          -- only the days being added (fast: date-sorted table)
          AND f.acq_date = n.acq_date AND f.acq_time = n.acq_time
          AND f.sensor_family = n.sensor_family AND f.satellite = n.satellite
          AND f.latitude = n.latitude AND f.longitude = n.longitude)
    RETURNING latitude, longitude, acq_datetime, sensor_family, satellite,
              confidence, frp, frp_flag
"""

MAKE_ALERTS = """
    INSERT INTO alerts (site_id, site_name, distance_km, latitude, longitude, acq_datetime,
                        sensor_family, satellite, confidence, frp, frp_flag)
    SELECT s.id, s.name, d.km, d.latitude, d.longitude, d.acq_datetime,
           d.sensor_family, d.satellite, d.confidence, d.frp, d.frp_flag
    FROM priority_sites s,
         LATERAL (
            SELECT i.*, 2 * 6371 * asin(sqrt(
                pow(sin(radians(i.latitude - s.lat) / 2), 2) +
                cos(radians(s.lat)) * cos(radians(i.latitude)) *
                pow(sin(radians(i.longitude - s.lon) / 2), 2))) AS km
            FROM inserted i) d
    WHERE d.km <= s.radius_km
    RETURNING id
"""


def download_day(map_key, source, day, fetch=None):
    """Download one day's worldwide CSV for one source; keep an untouched copy in data/raw/API/."""
    text = (fetch or fetch_csv)(build_url(map_key, source, day))
    if not text.lstrip().lower().startswith("latitude"):
        # FIRMS answers with a short message instead of CSV (e.g. bad key, limit reached)
        msg = text.strip().splitlines()[0][:200] if text.strip() else "empty answer"
        raise RuntimeError(f"FIRMS did not return CSV for {source} {day}: {msg}")
    folder = RAW_API_DIR / source
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day.isoformat()}.csv").write_text(text, encoding="utf-8")
    return text


def ingest_day(con, text, source, family, day, data_status):
    """
    Same rules as the original dataset (redzone_rules.harmonize), then insert only rows
    not already in `fires`. Returns (downloaded, inserted DataFrame, rejected, warnings).
    """
    raw = pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)
    good, rejected, warnings = harmonize(raw, family, data_status, f"api:{source}:{day.isoformat()}")
    if len(rejected):
        rejected.to_csv(RAW_API_DIR / source / f"{day.isoformat()}_rejected.csv", index=False)
    if good.empty:
        return len(raw), good.iloc[0:0][["latitude", "longitude", "acq_datetime", "sensor_family", "satellite",
                                         "confidence", "frp", "frp_flag"]], len(rejected), warnings
    con.register("new_rows", good)
    inserted = con.execute(INSERT_NEW_ROWS, [good["acq_date"].min(), good["acq_date"].max()]).df()
    con.unregister("new_rows")
    return len(raw), inserted, len(rejected), warnings


def update(con, map_key, start=None, end=None, log=print, fetch=None):
    """Run one update. Returns a summary dict."""
    fetch = fetch or fetch_csv
    if not map_key:
        raise ValueError("FIRMS_MAP_KEY is not set")
    ensure_tables(con)
    notify.ensure_tables(con)
    run_at = datetime.now(timezone.utc).replace(tzinfo=None)
    summary = {"run_at": str(run_at), "downloaded": 0, "inserted": 0, "rejected": 0,
               "alerts": 0, "emails": 0, "warnings": []}
    all_new = []
    days = days_to_fetch(con, start, end)
    log(f"Checking {len(days)} day(s): {days[0]} to {days[-1]}")

    for source, family in SOURCES.items():
        for day in days:
            text = download_day(map_key, source, day, fetch)
            n_raw, inserted, n_rejected, warnings = ingest_day(con, text, source, family, day, "nrt")

            n_alerts = 0
            if len(inserted):
                all_new.append(inserted)
                con.register("inserted", inserted)
                n_alerts = len(con.execute(MAKE_ALERTS).fetchall())
                con.unregister("inserted")

            con.execute("INSERT INTO update_log VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [run_at, source, day, n_raw, len(inserted), n_rejected,
                         "; ".join(warnings)])
            summary["downloaded"] += n_raw
            summary["inserted"] += len(inserted)
            summary["rejected"] += n_rejected
            summary["alerts"] += n_alerts
            summary["warnings"] += [f"{source} {day}: {w}" for w in warnings]
            log(f"  {source:<17} {day}  downloaded {n_raw:>7,}  new {len(inserted):>7,}"
                f"  rejected {n_rejected:>4,}  alerts {n_alerts}")
    if all_new:
        summary["emails"] = notify.process_new_detections(con, pd.concat(all_new, ignore_index=True), log)
    return summary


def main():
    p = argparse.ArgumentParser(description="Download new FIRMS detections into REDZONE.")
    p.add_argument("--start", type=date.fromisoformat, help="first day (YYYY-MM-DD)")
    p.add_argument("--end", type=date.fromisoformat, help="last day (YYYY-MM-DD)")
    args = p.parse_args()

    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        sys.exit("STOP: environment variable FIRMS_MAP_KEY is not set.")
    if not DB_PATH.exists():
        sys.exit(f"STOP: database not found: {DB_PATH}")
    try:
        con = duckdb.connect(str(DB_PATH))
    except duckdb.IOException:
        sys.exit("STOP: the database is in use (is the website server running?). Stop it first.")

    try:
        s = update(con, key, args.start, args.end)
    except (RuntimeError, ConnectionError) as exc:
        sys.exit(f"STOP: {exc}")
    finally:
        con.close()                                  # always release the database, even after an error
    print(f"\nDONE  downloaded {s['downloaded']:,}  new rows {s['inserted']:,}  "
          f"rejected {s['rejected']:,}  new alerts {s['alerts']:,}  emails {s['emails']:,}")
    for w in s["warnings"]:
        print("  WARNING:", w)


if __name__ == "__main__":
    main()
