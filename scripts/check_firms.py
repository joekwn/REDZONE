"""
REDZONE - diagnose past-data collection.

Checks, step by step:
  1. Is FIRMS_MAP_KEY set (and does it look like a FIRMS key)?
  2. Is REDZONE_ADMIN_EMAILS set (needed to see the Past data controls on the website)?
  3. Does FIRMS accept the key (data_availability)? Which dates does each product cover?
  4. Can one small past day be downloaded from MODIS_SP and VIIRS_NOAA21_NRT?
  5. What did the last past-data jobs in the database do, and why were days skipped?

Run with the website server STOPPED (step 5 opens the database):
    py scripts\\check_firms.py
The key is never printed.
"""

from datetime import date, timedelta
from pathlib import Path
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "redzone.duckdb"


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def hide(text, key):
    return text.replace(key, "<KEY>") if key else text


def main():
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    print("1. FIRMS_MAP_KEY:", "set" if key else "NOT SET",
          f"({len(key)} characters" + (", looks like a FIRMS key)" if re.fullmatch(r"[0-9a-f]{32}", key) else
                                        ", expected 32 characters 0-9/a-f - check for spaces or quotes)") if key else "")
    admins = os.environ.get("REDZONE_ADMIN_EMAILS", "").strip()
    print("2. REDZONE_ADMIN_EMAILS:", f"set ({len([a for a in admins.split(',') if a.strip()])} address)" if admins
          else "NOT SET - the 'Collect past data' controls are hidden on the website")
    if not key:
        sys.exit("\nSet FIRMS_MAP_KEY first (setx FIRMS_MAP_KEY \"...\"), then open a NEW PowerShell window.")

    print("\n3. FIRMS data_availability:")
    code, text = get(f"https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{key}/ALL")
    lines = text.strip().splitlines()
    if code != 200 or not lines or "data_id" not in lines[0]:
        print(f"   PROBLEM (HTTP {code}): {hide(text.strip()[:300], key)}")
    else:
        for line in lines:
            if line.split(",")[0] in ("data_id", "MODIS_SP", "MODIS_NRT", "VIIRS_NOAA21_NRT"):
                print("   ", line)

    print("\n4. Test downloads (small area over Brazil, one day):")
    test_day = date.today() - timedelta(days=400)
    for source in ("MODIS_SP", "VIIRS_NOAA21_NRT"):
        url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/-60,-20,-40,0/1/{test_day}"
        code, text = get(url)
        rows = text.strip().splitlines()
        if code == 200 and rows and rows[0].lower().startswith("latitude"):
            print(f"   {source:<17} {test_day}: OK, {len(rows) - 1} detections")
        else:
            print(f"   {source:<17} {test_day}: PROBLEM (HTTP {code}): {hide(text.strip()[:200], key)}")

    print("\n5. Past-data jobs in the database:")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import redzone_db
        con = redzone_db.connect(read_only=True)
        print("   database:", redzone_db.describe())
    except Exception as exc:
        print(f"   cannot open the database ({type(exc).__name__}) - stop the website server and run again")
        return
    try:
        jobs = con.execute("""SELECT id, start_day, end_day, status, days_done, days_total, inserted,
                   replaced, skipped, message FROM backfill_jobs ORDER BY id DESC LIMIT 5""").fetchall()
    except Exception:
        print("   no past-data job has been started yet")
        return
    if not jobs:
        print("   no past-data job has been started yet")
    for j in jobs:
        print(f"   job {j[0]}: {j[1]} to {j[2]} | {j[3]} | {j[4]}/{j[5]} sensor-days | added {j[6]:,} | "
              f"replaced {j[7]:,} | skipped {j[8]}" + (f" | {j[9]}" if j[9] else ""))
    if jobs:
        print("\n   Why days were skipped in the last job:")
        for fam, msg, n in con.execute("""SELECT family, message, COUNT(*) FROM backfill_days
                WHERE job_id = ? AND status = 'skipped' GROUP BY 1, 2 ORDER BY 1""", [jobs[0][0]]).fetchall():
            print(f"   {fam}: {n} day(s) - {msg}")
    con.close()


if __name__ == "__main__":
    main()
