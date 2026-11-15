"""
REDZONE - past-data collection (backfill) from the FIRMS API.

Downloads older days, one day per request, and runs every file through exactly the
same harmonizing, checking and flagging rules as the original dataset
(firms_live.ingest_day -> redzone_rules.harmonize).

Which product is used for each day (checked with FIRMS' data_availability service):
  MODIS  : MODIS_SP (standard quality, stored as data_status 'archive') when available,
           otherwise MODIS_NRT ('nrt').
  VIIRS  : VIIRS_NOAA21_NRT only - FIRMS offers no standard-quality NOAA-21 product
           through the API - and only for the days FIRMS still keeps.

Rules that keep the database consistent:
  - A day that already has ARCHIVE rows for a sensor is skipped (never downloaded twice).
  - Standard-quality (SP) data replaces near-real-time (NRT) rows of the same sensor and day.
  - NRT days only add rows that are not already there (same rule as live updates); days older
    than 2 days that already have NRT rows are skipped, because those files no longer change.
  - Past data never creates alerts or emails.

Progress is stored in tables `backfill_jobs` and `backfill_days`. A stopped job can be
run again with the same dates: finished days are skipped.

Run on its own (with the website server STOPPED):
    py scripts\\firms_backfill.py --start 2025-01-01 --end 2025-10-31
or start it from the website (administrators).
"""

from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import argparse
import os
import sys
import threading
import time

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import firms_live  # noqa: E402

DB_PATH = firms_live.DB_PATH
AVAILABILITY_URL = "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{key}/ALL"
PLAN = {                       # sensor family -> products to try, best first
    "MODIS": [("MODIS_SP", "archive"), ("MODIS_NRT", "nrt")],
    "VIIRS": [("VIIRS_NOAA21_NRT", "nrt")],
}
PAUSE_SECONDS = float(os.environ.get("REDZONE_BACKFILL_PAUSE", "2"))   # seconds between requests: gentle on FIRMS
LIMIT_WAIT_SECONDS = 600       # if FIRMS says the transaction limit is reached
MAX_DAYS_PER_JOB = 1100        # about 3 years


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_tables(con):
    con.execute("CREATE SEQUENCE IF NOT EXISTS backfill_id_seq")
    con.execute("""CREATE TABLE IF NOT EXISTS backfill_jobs (
        id INTEGER DEFAULT nextval('backfill_id_seq'), start_day DATE, end_day DATE,
        status VARCHAR, days_total INTEGER, days_done INTEGER DEFAULT 0,
        inserted BIGINT DEFAULT 0, replaced BIGINT DEFAULT 0, rejected BIGINT DEFAULT 0,
        skipped INTEGER DEFAULT 0, message VARCHAR, created_at TIMESTAMP, finished_at TIMESTAMP)""")
    con.execute("""CREATE TABLE IF NOT EXISTS backfill_days (
        job_id INTEGER, family VARCHAR, day DATE, source VARCHAR, status VARCHAR,
        downloaded INTEGER, inserted INTEGER, replaced INTEGER, rejected INTEGER,
        message VARCHAR, done_at TIMESTAMP)""")


# ---------- which product covers which day ----------
def availability(map_key, fetch=None):
    """{source: (min_date, max_date)} from FIRMS' data_availability service."""
    text = (fetch or firms_live.fetch_csv)(AVAILABILITY_URL.format(key=map_key))
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    if "data_id" not in first_line:
        msg = text.strip().splitlines()[0][:200] if text.strip() else "empty answer"
        raise RuntimeError(f"FIRMS data_availability did not return CSV: {msg}")
    df = pd.read_csv(StringIO(text), dtype=str)
    out = {}
    for _, r in df.iterrows():
        try:
            out[r["data_id"].strip()] = (date.fromisoformat(r["min_date"].strip()[:10]),
                                         date.fromisoformat(r["max_date"].strip()[:10]))
        except (KeyError, ValueError, AttributeError):
            continue
    return out


def choose(family, day, avail):
    """(source, data_status) to use for this sensor and day, or (None, reason)."""
    for source, status in PLAN[family]:
        rng = avail.get(source)
        if rng and rng[0] <= day <= rng[1]:
            return source, status
    names = ", ".join(s for s, _ in PLAN[family])
    return None, f"not available from FIRMS ({names})"


def day_state(con, family, day):
    """'archive' if the day already has archive rows for this sensor, 'nrt' if only NRT rows, else None."""
    rows = con.execute("""SELECT data_status, COUNT(*) FROM fires
        WHERE acq_date = ? AND sensor_family = ? GROUP BY 1""", [day.isoformat(), family]).fetchall()
    kinds = {r[0] for r in rows}
    return "archive" if "archive" in kinds else ("nrt" if "nrt" in kinds else None)


# ---------- a job ----------
class Job:
    """One backfill run. `stop` is set to cancel; progress is written to the database."""
    def __init__(self):
        self.stop = threading.Event()
        self.id = None


def run(con, map_key, start, end, log=print, fetch=None, job=None, pause=None):
    pause = PAUSE_SECONDS if pause is None else pause
    if not map_key:
        raise ValueError("FIRMS_MAP_KEY is not set")
    if end < start:
        raise ValueError("The end date is before the start date")
    today = utcnow().date()
    end = min(end, today - timedelta(days=1))
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if not days:
        raise ValueError("Nothing to collect: the range must end before today")
    if len(days) > MAX_DAYS_PER_JOB:
        raise ValueError(f"At most {MAX_DAYS_PER_JOB} days per job; split the range")

    job = job or Job()
    firms_live.ensure_tables(con)
    ensure_tables(con)
    job.id = con.execute("""INSERT INTO backfill_jobs (start_day, end_day, status, days_total, created_at)
        VALUES (?, ?, 'running', ?, ?) RETURNING id""", [start, end, len(days) * len(PLAN), utcnow()]).fetchall()[0][0]
    log(f"[backfill] job {job.id}: {start} to {end} ({len(days)} days x {len(PLAN)} sensors)")

    try:
        avail = availability(map_key, fetch)
        for src, (lo, hi) in sorted(avail.items()):
            if any(src == s for plan in PLAN.values() for s, _ in plan):
                log(f"[backfill] FIRMS has {src}: {lo} to {hi}")

        for day in days:
            for family in PLAN:
                if job.stop.is_set():
                    raise InterruptedError
                done = do_day(con, map_key, job.id, family, day, avail, log, fetch, job, pause)
                con.execute(f"""UPDATE backfill_jobs SET days_done = days_done + 1,
                    inserted = inserted + ?, replaced = replaced + ?, rejected = rejected + ?,
                    skipped = skipped + ? WHERE id = ?""",
                    [done["inserted"], done["replaced"], done["rejected"], int(done["skipped"]), job.id])
        status, message = "finished", None
    except InterruptedError:
        status, message = "cancelled", "stopped by an administrator"
    except Exception as exc:
        status, message = "failed", f"{type(exc).__name__}: {exc}"[:300]
    con.execute("UPDATE backfill_jobs SET status = ?, message = ?, finished_at = ? WHERE id = ?",
                [status, message, utcnow(), job.id])
    log(f"[backfill] job {job.id} {status}" + (f": {message}" if message else ""))
    return job_summary(con, job.id)


def do_day(con, map_key, job_id, family, day, avail, log, fetch, job, pause):
    result = {"inserted": 0, "replaced": 0, "rejected": 0, "skipped": False}
    state = day_state(con, family, day)
    if state == "archive":
        record(con, job_id, family, day, None, "skipped", 0, 0, 0, 0, "already has standard-quality data")
        result["skipped"] = True
        return result
    source, status_or_reason = choose(family, day, avail)
    if source is None:
        record(con, job_id, family, day, None, "skipped", 0, 0, 0, 0, status_or_reason)
        result["skipped"] = True
        return result
    data_status = status_or_reason
    if data_status == "nrt" and state == "nrt" and day < utcnow().date() - timedelta(days=2):
        # NRT files of older days no longer change: nothing new to fetch
        record(con, job_id, family, day, source, "skipped", 0, 0, 0, 0, "already has NRT data")
        result["skipped"] = True
        return result

    text = download_with_patience(map_key, source, day, fetch, log, job)
    replaced = 0
    if data_status == "archive" and state == "nrt":
        # standard-quality data replaces the near-real-time rows of this sensor and day
        replaced = len(con.execute("""DELETE FROM fires WHERE acq_date = ? AND sensor_family = ?
            AND data_status = 'nrt' RETURNING 1""", [day.isoformat(), family]).fetchall())
    n_raw, inserted, n_rejected, warnings = firms_live.ingest_day(con, text, source, family, day, data_status)
    record(con, job_id, family, day, source, "done", n_raw, len(inserted), replaced, n_rejected, "; ".join(warnings))
    log(f"[backfill] {day} {source:<17} downloaded {n_raw:>7,}  added {len(inserted):>7,}"
        + (f"  replaced NRT {replaced:,}" if replaced else "") + (f"  rejected {n_rejected}" if n_rejected else ""))
    result.update(inserted=len(inserted), replaced=replaced, rejected=n_rejected)
    if job.stop.wait(pause):
        raise InterruptedError
    return result


def download_with_patience(map_key, source, day, fetch, log, job, tries=4):
    """Download one day; if FIRMS reports its transaction limit, wait and try again."""
    for attempt in range(tries):
        try:
            return firms_live.download_day(map_key, source, day, fetch)
        except RuntimeError as exc:
            text = str(exc).lower()
            if ("limit" in text or "exceed" in text or "too many" in text) and attempt < tries - 1:
                log(f"[backfill] FIRMS limit reached - waiting {LIMIT_WAIT_SECONDS // 60} minutes")
                if job.stop.wait(LIMIT_WAIT_SECONDS):
                    raise InterruptedError
                continue
            raise


def record(con, job_id, family, day, source, status, downloaded, inserted, replaced, rejected, message):
    con.execute("INSERT INTO backfill_days VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [job_id, family, day, source, status, downloaded, inserted, replaced, rejected,
                 message[:300] if message else None, utcnow()])


def job_summary(con, job_id):
    row = con.execute("""SELECT id, start_day, end_day, status, days_total, days_done, inserted, replaced,
               rejected, skipped, message, strftime(created_at, '%Y-%m-%d %H:%M'),
               strftime(finished_at, '%Y-%m-%d %H:%M')
        FROM backfill_jobs WHERE id = ?""", [job_id]).fetchall()
    if not row:
        return None
    keys = ["id", "start", "end", "status", "days_total", "days_done", "inserted", "replaced",
            "rejected", "skipped", "message", "created", "finished"]
    out = dict(zip(keys, row[0]))
    out["start"], out["end"] = str(out["start"]), str(out["end"])
    return out


def main():
    p = argparse.ArgumentParser(description="Collect past FIRMS data into REDZONE.")
    p.add_argument("--start", type=date.fromisoformat, required=True, help="first day (YYYY-MM-DD)")
    p.add_argument("--end", type=date.fromisoformat, required=True, help="last day (YYYY-MM-DD)")
    args = p.parse_args()
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        sys.exit("STOP: environment variable FIRMS_MAP_KEY is not set.")
    try:
        con = duckdb.connect(str(DB_PATH))
    except duckdb.IOException:
        sys.exit("STOP: the database is in use (is the website server running?). Stop it first.")
    try:
        s = run(con, key, args.start, args.end)
    except ValueError as exc:
        sys.exit(f"STOP: {exc}")
    finally:
        con.close()
    print(f"\n{s['status'].upper()}: {s['days_done']}/{s['days_total']} sensor-days, added {s['inserted']:,}, "
          f"replaced NRT {s['replaced']:,}, rejected {s['rejected']:,}, skipped {s['skipped']}"
          + (f"\n{s['message']}" if s["message"] else ""))


if __name__ == "__main__":
    main()
