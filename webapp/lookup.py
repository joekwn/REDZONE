"""
REDZONE - look up older data WITHOUT saving it (option C).

A visitor picks an area (at most MAX_DEG x MAX_DEG degrees) and up to 10 days.
For each sensor and day:
  - if the day is already in the database, the rows come from the database;
  - otherwise only that box and those days are requested from NASA FIRMS
    (MODIS_SP when available, else MODIS_NRT; VIIRS_NOAA21_NRT),
    run through the same rules (redzone_rules.harmonize) and returned.
Nothing from FIRMS is written to the database, so the curated dataset never changes.

Protection for the shared FIRMS key: area and day limits, a per-hour limit per person
(guests by IP address, signed-in users by account), at most 2 FIRMS look-ups at the same
time, and a 1-hour memory cache so identical look-ups do not ask FIRMS again.
"""

from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import csv
import sys
import threading
import time
from collections import OrderedDict

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import firms_backfill  # noqa: E402
import firms_live      # noqa: E402
from redzone_rules import conf_class, harmonize  # noqa: E402

import auth  # noqa: E402

MAX_DEG = 10.0                  # largest box side, in degrees (about 1,100 km at the equator)
MAX_DAYS = 10                   # FIRMS' own limit per request
MAX_POINTS = 30_000
GUEST_PER_HOUR = 5
USER_PER_HOUR = 20
CACHE_SECONDS = 3600
FIRST_DAY = date(2000, 11, 1)   # first MODIS day in FIRMS

router = APIRouter()
STATE = {"db": None, "key": ""}
_cache = OrderedDict()
_cache_lock = threading.Lock()
_firms_slots = threading.Semaphore(2)
_avail = [0.0, None]

POINT_COLS = ["latitude", "longitude", "sensor_family", "satellite", "time", "confidence", "conf_class",
              "frp", "frp_flag", "daynight", "type", "data_status", "origin"]


def init(db, map_key):
    STATE.update(db=db, key=map_key)


def availability():
    """FIRMS product date ranges, cached for 6 hours. {} if the key is missing or FIRMS fails."""
    if not STATE["key"]:
        return {}
    if _avail[1] is None or time.time() - _avail[0] > 6 * 3600:
        try:
            _avail[:] = [time.time(), firms_backfill.availability(STATE["key"])]
        except Exception:
            return _avail[1] or {}
    return _avail[1]


def product_for(family, day, avail):
    for source, status in firms_backfill.PLAN[family]:
        rng = avail.get(source)
        if rng and rng[0] <= day <= rng[1]:
            return source, status
    return None, None


def limits_for(user):
    return USER_PER_HOUR if user else GUEST_PER_HOUR


@router.get("/api/lookup/info")
def lookup_info(request: Request):
    user = auth.current_user(request)
    avail = availability()
    wanted = {s for plan in firms_backfill.PLAN.values() for s, _ in plan}
    return {"enabled": bool(STATE["key"]), "max_deg": MAX_DEG, "max_days": MAX_DAYS,
            "per_hour": limits_for(user), "signed_in": bool(user),
            "first_day": str(FIRST_DAY),
            "products": [{"source": k, "first": str(v[0]), "last": str(v[1])}
                         for k, v in sorted(avail.items()) if k in wanted]}


def validate(west, south, east, north, start, days):
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(400, "The area is not valid. Pan the map so it does not cross the date line.")
    if east - west > MAX_DEG + 1e-9 or north - south > MAX_DEG + 1e-9:
        raise HTTPException(400, f"The area is too large: zoom in until it is at most {MAX_DEG:g} by {MAX_DEG:g} degrees.")
    if not 1 <= days <= MAX_DAYS:
        raise HTTPException(400, f"Choose between 1 and {MAX_DAYS} days.")
    try:
        d0 = date.fromisoformat(start)
    except ValueError:
        raise HTTPException(400, "The date must be YYYY-MM-DD.")
    today = datetime.now(timezone.utc).date()
    d1 = d0 + timedelta(days=days - 1)
    if d0 < FIRST_DAY or d1 > today:
        raise HTTPException(400, f"Choose days between {FIRST_DAY} and today.")
    return d0, d1


def db_days(cur, family, d0, d1):
    """Days of this sensor that the database already has (complete enough to use)."""
    rows = cur.execute("""SELECT acq_date, MAX(CASE WHEN data_status = 'archive' THEN 1 ELSE 0 END)
        FROM fires WHERE acq_date BETWEEN ? AND ? AND sensor_family = ? GROUP BY 1""",
        [d0.isoformat(), d1.isoformat(), family]).fetchall()
    recent = datetime.now(timezone.utc).date() - timedelta(days=2)
    # archive days are final; NRT days older than 2 days no longer change
    return {date.fromisoformat(str(r[0])[:10]) for r in rows if r[1] == 1 or date.fromisoformat(str(r[0])[:10]) < recent}


def from_db(cur, family, days, box):
    if not days:
        return []
    west, south, east, north = box
    marks = ",".join("?" * len(days))
    rows = cur.execute(f"""
        SELECT latitude, longitude, sensor_family, satellite, strftime(acq_datetime, '%Y-%m-%d %H:%M'),
               confidence, frp, frp_flag, daynight, type, data_status
        FROM fires WHERE sensor_family = ? AND acq_date IN ({marks})
          AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?""",
        [family, *[d.isoformat() for d in sorted(days)], south, north, west, east]).fetchall()
    return [[r[0], r[1], r[2], r[3], r[4], r[5], conf_class(r[2], r[5]), r[6], r[7], r[8], r[9], r[10], "database"]
            for r in rows]


def blocks(days, family, avail):
    """Group missing days into runs of consecutive days that use the same FIRMS product."""
    out, skipped = [], []
    for d in sorted(days):
        source, status = product_for(family, d, avail)
        if not source:
            skipped.append(d)
            continue
        if out and out[-1]["source"] == source and out[-1]["end"] + timedelta(days=1) == d:
            out[-1]["end"] = d
        else:
            out.append({"source": source, "status": status, "start": d, "end": d})
    return out, skipped


def from_firms(family, blk, box):
    west, south, east, north = box
    n_days = (blk["end"] - blk["start"]).days + 1
    url = (f"{firms_live.API}/{STATE['key']}/{blk['source']}/{west:.4f},{south:.4f},{east:.4f},{north:.4f}"
           f"/{n_days}/{blk['start'].isoformat()}")
    text = firms_live.fetch_csv(url, timeout=60, retries=1)
    if not text.lstrip().lower().startswith("latitude"):
        msg = text.strip().splitlines()[0][:160] if text.strip() else "empty answer"
        raise RuntimeError(f"FIRMS did not return data for {blk['source']}: {msg}")
    raw = pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)
    good, rejected, warnings = harmonize(raw, family, blk["status"], f"lookup:{blk['source']}")
    good = good[(good["acq_date"] >= blk["start"].isoformat()) & (good["acq_date"] <= blk["end"].isoformat())]
    pts = [[r.latitude, r.longitude, r.sensor_family, r.satellite, r.acq_datetime.strftime("%Y-%m-%d %H:%M"),
            r.confidence, conf_class(r.sensor_family, r.confidence),
            None if pd.isna(r.frp) else float(r.frp), r.frp_flag, r.daynight,
            None if (r.type is None or (isinstance(r.type, float) and pd.isna(r.type))) else r.type,
            r.data_status, "FIRMS"] for r in good.itertuples(index=False)]
    return pts, len(rejected), warnings


def run_lookup(request, west, south, east, north, start, days):
    d0, d1 = validate(west, south, east, north, start, days)
    box = (round(west, 4), round(south, 4), round(east, 4), round(north, 4))
    key = (tuple(round(v, 2) for v in box), d0, days)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            _cache.move_to_end(key)
            return hit[1]

    cur = STATE["db"].cursor()
    all_days = [d0 + timedelta(days=i) for i in range(days)]
    avail = availability()
    points, notes, sources, rejected = [], [], [], 0
    firms_plan = {}
    for family in ("MODIS", "VIIRS"):
        have = db_days(cur, family, d0, d1)
        rows = from_db(cur, family, have, box)
        points += rows
        if have:
            sources.append(f"{family}: {len(have)} day(s) from the REDZONE database")
        missing = [d for d in all_days if d not in have]
        blk, skipped = blocks(missing, family, avail) if missing else ([], [])
        firms_plan[family] = blk
        if missing and not STATE["key"]:
            notes.append(f"{family}: {len(missing)} day(s) not in the database, and FIRMS look-up is not set up on this server.")
        elif skipped:
            names = " / ".join(s for s, _ in firms_backfill.PLAN[family])
            notes.append(f"{family}: {len(skipped)} day(s) are not available from FIRMS ({names}).")
    cur.close()

    need_firms = any(firms_plan.values()) and STATE["key"]
    if need_firms:
        user = auth.current_user(request)
        who = f"user:{user['id']}" if user else f"ip:{auth.client_ip(request)}"
        auth.rate_limit("lookup", who, limits_for(user), 3600)
        if not _firms_slots.acquire(timeout=30):
            raise HTTPException(503, "Many look-ups are running right now. Please try again in a minute.")
        try:
            for family, blk_list in firms_plan.items():
                for blk in blk_list:
                    pts, n_rej, warns = from_firms(family, blk, box)
                    points += pts
                    rejected += n_rej
                    days_txt = f"{blk['start']}" + (f" to {blk['end']}" if blk["end"] != blk["start"] else "")
                    sources.append(f"{family}: {days_txt} from NASA FIRMS ({blk['source']})")
                    notes += [f"{family}: {w}" for w in warns]
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))
        except ConnectionError:
            raise HTTPException(502, "NASA FIRMS could not be reached. Please try again later.")
        finally:
            _firms_slots.release()

    if rejected:
        notes.append(f"{rejected} row(s) from FIRMS broke a data rule and were left out.")
    counts = {}
    for p in points:
        c = counts.setdefault(p[2], {"total": 0, "database": 0, "FIRMS": 0})
        c["total"] += 1
        c[p[12]] += 1
    result = {"box": box, "start": str(d0), "end": str(d1), "counts": counts, "sources": sources,
              "notes": notes, "truncated": len(points) > MAX_POINTS, "points": points[:MAX_POINTS],
              "all_points": points}
    with _cache_lock:
        _cache[key] = (time.time(), result)
        while len(_cache) > 100:
            _cache.popitem(last=False)
    return result


@router.get("/api/lookup")
def lookup(request: Request, west: float, south: float, east: float, north: float, start: str, days: int = 1):
    r = run_lookup(request, west, south, east, north, start, days)
    return {k: v for k, v in r.items() if k != "all_points"}


@router.get("/api/lookup.csv")
def lookup_csv(request: Request, west: float, south: float, east: float, north: float, start: str, days: int = 1):
    r = run_lookup(request, west, south, east, north, start, days)
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(POINT_COLS)
    w.writerows(r["all_points"])
    name = f"redzone_lookup_{r['start']}_{r['end']}.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
