"""
REDZONE - website server (live version).

Map data:      /api/meta  /api/fires  /api/stats  /api/near  /api/notes
Search:        /api/geocode        place suggestions (Photon, OpenStreetMap data)
               /api/boundary       outline of a chosen place (Nominatim, OpenStreetMap data)
Email alerts:  /api/subscriptions (POST), /confirm/..., /unsubscribe/...   (scripts/notify.py)
Outlook:       /api/outlook        7-day fire-risk outlook = weather + history (scripts/outlook.py)
Events:        /api/events         save events; checked 12 h before start (scripts/notify.py)
Accounts:      /api/auth/...       register, sign in/out, verify email, reset password (webapp/auth.py)
               Guests use the map; events, priority sites and their alerts need an account (no email verification).
Location:      /api/iplocation     approximate location from the visitor's IP address
Past data:     /api/backfill       collect older days from the FIRMS API (administrators), same rules
Look-up:       /api/lookup         anyone: older data for one area and up to 10 days, NOT saved (webapp/lookup.py)
Live features: /api/status         last update, newest detection, is an update running
               /api/update (POST)  start an update from the FIRMS API now
               /api/sites          list / add (POST) / delete priority sites (saved in the database)
               /api/alerts         newest alerts (new fires near a priority site)

If the environment variable FIRMS_MAP_KEY is set, the server downloads new data
every UPDATE_HOURS hours (default 3) using scripts/firms_live.py - the same
harmonizing, checking and flagging rules as the original dataset.

Run from the project root:
    py -m uvicorn webapp.server:app --port 8000
Then open http://127.0.0.1:8000 in your browser.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
import hmac
import math
import os
import sys
import ipaddress
import json
import threading
import time
import urllib.parse
import urllib.request
from functools import lru_cache

import duckdb
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "redzone.duckdb"
STATIC = Path(__file__).resolve().parent / "static"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import firms_live                                   # noqa: E402
import firms_backfill                               # noqa: E402
import notify                                       # noqa: E402
import outlook as outlook_mod                       # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth                                         # noqa: E402
import lookup                                       # noqa: E402
from redzone_rules import CONF_CLASS_SQL            # noqa: E402

MAP_KEY = os.environ.get("FIRMS_MAP_KEY", "").strip()
CONTACT = os.environ.get("REDZONE_CONTACT_EMAIL", "").strip()     # shown on /privacy and /terms
UPDATE_HOURS = float(os.environ.get("REDZONE_UPDATE_HOURS", "3"))

MAX_POINTS = 20_000      # never send more individual points than this
POINT_ZOOM = 8           # from this zoom level on, show individual detections

import redzone_db                                   # noqa: E402
SERVERLESS = redzone_db.SERVERLESS                  # True on Vercel: no background loops, no local files
CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()

# read-write: the server itself adds live data, sites and alerts
DB = redzone_db.connect()
print("[server] database:", redzone_db.describe(), "| serverless" if SERVERLESS else "")
firms_live.ensure_tables(DB)
notify.ensure_tables(DB)
auth.init(DB)
firms_backfill.ensure_tables(DB)
if not SERVERLESS:
    # on a normal server a job cannot outlive the server; on Vercel jobs run from your computer
    DB.execute("""UPDATE backfill_jobs SET status = 'interrupted', message = 'server stopped during the job',
                  finished_at = current_timestamp WHERE status = 'running'""")
EVENT_CHECK_MINUTES = 10

CONF_CLASS = CONF_CLASS_SQL         # shared confidence label (redzone_rules.py)

# ---------- live updates ----------
STATE = {"running": False, "last_run": None, "last_result": None, "last_error": None}
LOCK = threading.Lock()
STOP = threading.Event()             # set at shutdown: background loops finish and exit
THREADS = []
BACKFILL = {"job": None, "thread": None}


def run_update(reason):
    if not LOCK.acquire(blocking=False):
        return False                                  # an update is already running
    STATE.update(running=True, last_error=None)
    cur = DB.cursor()
    try:
        result = firms_live.update(cur, MAP_KEY, log=lambda m: print("[update]", m))
        STATE["last_result"] = {k: result[k] for k in
                                ("downloaded", "inserted", "rejected", "alerts", "emails", "warnings")}
    except Exception as exc:                          # keep the website running
        STATE["last_error"] = f"{type(exc).__name__}: {exc}"
        print("[update] ERROR", STATE["last_error"])
    finally:
        cur.close()
        STATE.update(running=False, last_run=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                     reason=reason)
        LOCK.release()
    return True


def scheduler():
    while not STOP.is_set():
        run_update("scheduled")
        STOP.wait(UPDATE_HOURS * 3600)          # sleeps, but wakes at once on shutdown


def event_scheduler():
    while not STOP.is_set():
        cur = DB.cursor()
        try:
            notify.run_due_events(cur, log=print)
        except Exception as exc:
            print("[events] ERROR", type(exc).__name__, exc)
        finally:
            cur.close()
        STOP.wait(EVENT_CHECK_MINUTES * 60)


@asynccontextmanager
async def lifespan(app):
    if SERVERLESS:
        # Vercel: nothing may keep running between requests. Updates and event checks
        # come from scheduled calls to /api/cron/update and /api/cron/events instead.
        print("[server] serverless mode: updates and event checks run from /api/cron/*")
        yield
        try:
            DB.close()
        except Exception:
            pass
        return
    THREADS.append(threading.Thread(target=event_scheduler, daemon=True, name="events"))
    THREADS[-1].start()
    print(f"[email] {'SMTP ' + os.environ['SMTP_HOST'] if os.environ.get('SMTP_HOST') else 'test mode: emails saved to data/outbox/'}")
    if MAP_KEY:
        THREADS.append(threading.Thread(target=scheduler, daemon=True, name="updates"))
        THREADS[-1].start()
        print(f"[update] live updates every {UPDATE_HOURS:g} h")
    else:
        print("[update] FIRMS_MAP_KEY not set - live updates are off")
    yield
    # Clean shutdown (Ctrl+C): stop the background loops, then close the database,
    # so DuckDB releases everything before Python exits.
    STOP.set()
    if BACKFILL["job"]:
        BACKFILL["job"].stop.set()
    for t in THREADS + ([BACKFILL["thread"]] if BACKFILL["thread"] else []):
        t.join(timeout=10)
        if t.is_alive():
            print(f"[server] '{t.name}' is still busy (e.g. a FIRMS download); stopping anyway")
    try:
        DB.close()
        print("[server] database closed")
    except Exception:
        pass


app = FastAPI(title="REDZONE", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(auth.router)
app.include_router(lookup.router)
lookup.init(DB, MAP_KEY)


@app.middleware("http")
async def security(request: Request, call_next):
    # CSRF protection: state-changing API calls must carry a header that other sites cannot send
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-redzone") != "1":
            return JSONResponse({"detail": "Missing X-RedZone header"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
    if auth.secure_cookies():
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.url.path.startswith("/api/auth/") or request.url.path in ("/api/sites", "/api/events", "/api/alerts"):
        response.headers["Cache-Control"] = "no-store"
    return response


def cursor():
    return DB.cursor()          # one cursor per request = safe with several requests


def build_filter(start, end, sensors, conf, show_polar, show_bad_frp, bbox=None):
    """WHERE clause + parameters shared by all endpoints."""
    sensors = [s for s in sensors.split(",") if s in ("MODIS", "VIIRS")] or ["NONE"]
    conf = [c for c in conf.split(",") if c in ("low", "nominal", "high")] or ["NONE"]
    sql = [
        "acq_date BETWEEN ? AND ?",
        f"sensor_family IN ({','.join('?' * len(sensors))})",
        f"({CONF_CLASS}) IN ({','.join('?' * len(conf))})",
    ]
    params = [start, end, *sensors, *conf]
    if not show_polar:
        sql.append("NOT polar_flag")
    if not show_bad_frp:
        sql.append("frp_flag = 'ok'")
    if bbox:
        w, s, e, n = bbox
        sql.append("latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?")
        params += [s, n, w, e]
    return " AND ".join(sql), params


def parse_bbox(bbox):
    try:
        w, s, e, n = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be west,south,east,north")
    if e - w >= 360:
        w, e = -180, 180
    return max(w, -180), max(s, -90), min(e, 180), min(n, 90)


# Filters every data endpoint accepts
def common(start: str, end: str, sensors: str = "MODIS,VIIRS",
           conf: str = "low,nominal,high", show_polar: bool = False,
           show_bad_frp: bool = True):
    return dict(start=start, end=end, sensors=sensors, conf=conf,
                show_polar=show_polar, show_bad_frp=show_bad_frp)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def welcome():
    return FileResponse(STATIC / "welcome.html")          # landing page with About + Get started


@app.get("/app")
def index():
    return FileResponse(STATIC / "index.html")            # the map


def legal_page(name):
    html = (STATIC / f"{name}.html").read_text(encoding="utf-8")
    updated = datetime.fromtimestamp((STATIC / f"{name}.html").stat().st_mtime, timezone.utc).strftime("%d %B %Y")
    return HTMLResponse(html.replace("{{CONTACT}}", CONTACT or "(contact address not set)").replace("{{UPDATED}}", updated))


@app.get("/privacy")
def privacy():
    return legal_page("privacy")


@app.get("/terms")
def terms():
    return legal_page("terms")


@app.get("/healthz")
def healthz():
    """For uptime monitors: is the server up and can it read the database?"""
    try:
        cursor().execute("SELECT 1").fetchall()
        return {"ok": True}
    except Exception:
        return JSONResponse({"ok": False}, status_code=503)


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /api/\nAllow: /\n", media_type="text/plain")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "img" / "favicon.png", media_type="image/png")


@app.get("/api/meta")
def meta():
    lo, hi, total = cursor().execute(
        "SELECT MIN(acq_date), MAX(acq_date), COUNT(*) FROM fires").fetchone()
    if not total:                    # empty database (fresh install): use today so the date pickers work
        lo = hi = datetime.now(timezone.utc).date().isoformat()
    return {"min_date": str(lo), "max_date": str(hi), "total": total}


@app.get("/api/fires")
def fires(start: str, end: str, bbox: str, zoom: int,
          sensors: str = "MODIS,VIIRS", conf: str = "low,nominal,high",
          show_polar: bool = False, show_bad_frp: bool = True):
    box = parse_bbox(bbox)
    where, params = build_filter(**common(start, end, sensors, conf, show_polar, show_bad_frp), bbox=box)
    cur = cursor()

    if zoom >= POINT_ZOOM:
        rows = cur.execute(f"""
            SELECT latitude, longitude, sensor_family, satellite,
                   strftime(acq_datetime, '%Y-%m-%d %H:%M'), confidence, {CONF_CLASS},
                   frp, frp_flag, daynight, type, data_status
            FROM fires WHERE {where} LIMIT {MAX_POINTS + 1}""", params).fetchall()
        return {"mode": "points", "truncated": len(rows) > MAX_POINTS,
                "points": rows[:MAX_POINTS]}

    cell = max(0.05, 180 / 2 ** (zoom + 3))          # grid cell size in degrees
    rows = cur.execute(f"""
        SELECT floor(latitude / {cell}) * {cell} AS s, floor(longitude / {cell}) * {cell} AS w,
               COUNT(*) AS n,
               COUNT(*) FILTER (WHERE sensor_family = 'MODIS') AS n_modis,
               COUNT(*) FILTER (WHERE sensor_family = 'VIIRS') AS n_viirs
        FROM fires WHERE {where}
        GROUP BY 1, 2 ORDER BY n DESC LIMIT 30000""", params).fetchall()
    return {"mode": "grid", "cell": cell, "cells": rows}


@app.get("/api/stats")
def stats(start: str, end: str, bbox: str,
          sensors: str = "MODIS,VIIRS", conf: str = "low,nominal,high",
          show_polar: bool = False, show_bad_frp: bool = True):
    box = parse_bbox(bbox)
    where, params = build_filter(**common(start, end, sensors, conf, show_polar, show_bad_frp), bbox=box)
    cur = cursor()
    by_sensor = cur.execute(f"""
        SELECT sensor_family, COUNT(*), SUM(frp_usable), COUNT(frp_usable),
               COUNT(*) FILTER (WHERE daynight = 'D'), COUNT(*) FILTER (WHERE daynight = 'N')
        FROM fires WHERE {where} GROUP BY 1 ORDER BY 1""", params).fetchall()
    by_conf = cur.execute(f"""
        SELECT sensor_family, {CONF_CLASS} AS c, COUNT(*)
        FROM fires WHERE {where} GROUP BY 1, 2 ORDER BY 1, 2""", params).fetchall()
    daily = cur.execute(f"""
        SELECT acq_date, sensor_family, COUNT(*)
        FROM fires WHERE {where} GROUP BY 1, 2 ORDER BY 1""", params).fetchall()
    return {
        "by_sensor": [{"sensor": r[0], "n": r[1], "frp_sum": r[2], "frp_n": r[3],
                       "day": r[4], "night": r[5]} for r in by_sensor],
        "by_conf": [{"sensor": r[0], "conf": r[1], "n": r[2]} for r in by_conf],
        "daily": [{"date": str(r[0]), "sensor": r[1], "n": r[2]} for r in daily],
    }


@app.get("/api/near")
def near(lat: float, lon: float, radius_km: float, start: str, end: str,
         sensors: str = "MODIS,VIIRS", conf: str = "low,nominal,high",
         show_polar: bool = False, show_bad_frp: bool = True):
    radius_km = min(max(radius_km, 0.1), 500)
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    box = (lon - dlon, lat - dlat, lon + dlon, lat + dlat)
    where, params = build_filter(**common(start, end, sensors, conf, show_polar, show_bad_frp), bbox=box)
    dist = """2 * 6371 * asin(sqrt(pow(sin(radians(latitude - ?) / 2), 2) +
              cos(radians(?)) * cos(radians(latitude)) * pow(sin(radians(longitude - ?) / 2), 2)))"""
    row = cursor().execute(f"""
        WITH d AS (SELECT sensor_family, acq_datetime, {dist} AS km
                   FROM fires WHERE {where})
        SELECT COUNT(*), MIN(km),
               strftime(MAX(acq_datetime), '%Y-%m-%d %H:%M'),
               COUNT(*) FILTER (WHERE sensor_family = 'MODIS'),
               COUNT(*) FILTER (WHERE sensor_family = 'VIIRS')
        FROM d WHERE km <= ?""", [lat, lat, lon, *params, radius_km]).fetchone()
    return {"count": row[0], "nearest_km": row[1], "latest": row[2],
            "modis": row[3], "viirs": row[4]}


@app.get("/api/status")
def status():
    cur = cursor()
    newest, total, max_date = cur.execute(
        "SELECT strftime(MAX(acq_datetime), '%Y-%m-%d %H:%M'), COUNT(*), MAX(acq_date) FROM fires").fetchone()
    state = dict(STATE)
    if SERVERLESS and not state["running"]:
        # each request may land on a different copy of the app: read the last run from the database
        last = cur.execute("SELECT strftime(MAX(run_at), '%Y-%m-%d %H:%M UTC') FROM update_log").fetchone()[0]
        state["last_run"] = last
    return {"key_configured": bool(MAP_KEY), "update_hours": UPDATE_HOURS,
            "smtp_configured": bool(os.environ.get("SMTP_HOST")), "serverless": SERVERLESS,
            "newest_detection": newest, "total": total, "max_date": str(max_date), **state}


@app.post("/api/update", status_code=202)
def start_update(admin=Depends(auth.require_admin)):
    if not MAP_KEY:
        raise HTTPException(400, "FIRMS_MAP_KEY is not set on the server")
    if STATE["running"]:
        return {"started": False, "message": "an update is already running"}
    if SERVERLESS:                       # no background threads: run it inside this request
        run_update("manual")
        return {"started": True, "finished": True, "result": STATE["last_result"], "error": STATE["last_error"]}
    threading.Thread(target=run_update, args=("manual",), daemon=True).start()
    return {"started": True}


# ---------- scheduled calls (Vercel cron / GitHub Actions) ----------
def require_cron(request: Request):
    """Only the scheduler knows CRON_SECRET (sent as 'Authorization: Bearer <secret>')."""
    if not CRON_SECRET:
        raise HTTPException(503, "CRON_SECRET is not set on the server")
    got = request.headers.get("authorization", "")
    if not hmac.compare_digest(got.encode(), f"Bearer {CRON_SECRET}".encode()):
        raise HTTPException(401, "not allowed")


@app.get("/api/cron/update")
def cron_update(request: Request):
    require_cron(request)
    if not MAP_KEY:
        raise HTTPException(400, "FIRMS_MAP_KEY is not set on the server")
    ran = run_update("scheduled")
    return {"ran": ran, "result": STATE["last_result"], "error": STATE["last_error"]}


@app.get("/api/cron/events")
def cron_events(request: Request):
    require_cron(request)
    cur = DB.cursor()
    try:
        notify.run_due_events(cur, log=print)
    finally:
        cur.close()
    return {"ok": True}


# ---------- past data (backfill) ----------
class BackfillRequest(BaseModel):
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


_coverage_cache = [0.0, None]
_avail_cache = [0.0, None]


def coverage():
    if _coverage_cache[1] is None or time.time() - _coverage_cache[0] > 60:
        rows = cursor().execute("""SELECT sensor_family, data_status, MIN(acq_date), MAX(acq_date),
                   COUNT(DISTINCT acq_date), COUNT(*) FROM fires GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
        _coverage_cache[:] = [time.time(), [
            {"sensor": r[0], "status": r[1], "first": str(r[2]), "last": str(r[3]), "days": r[4], "rows": r[5]}
            for r in rows]]
    return _coverage_cache[1]


def backfill_running():
    return BACKFILL["thread"] is not None and BACKFILL["thread"].is_alive()


@app.get("/api/backfill")
def backfill_status():
    """Public: what is loaded, and the current / last past-data job."""
    cur = cursor()
    last = cur.execute("SELECT MAX(id) FROM backfill_jobs").fetchall()[0][0]
    return {"core_tested": {"first": "2025-11-01", "last": "2026-11-01"},
            "coverage": coverage(), "running": backfill_running(),
            "job": firms_backfill.job_summary(cur, last) if last else None}


@app.get("/api/backfill/availability")
def backfill_availability(admin=Depends(auth.require_admin)):
    if not MAP_KEY:
        raise HTTPException(400, "FIRMS_MAP_KEY is not set on the server")
    if _avail_cache[1] is None or time.time() - _avail_cache[0] > 6 * 3600:
        try:
            avail = firms_backfill.availability(MAP_KEY)
        except Exception as exc:
            raise HTTPException(502, f"FIRMS availability check failed: {exc}")
        _avail_cache[:] = [time.time(), avail]
    wanted = {s for plan in firms_backfill.PLAN.values() for s, _ in plan}
    return [{"source": k, "first": str(v[0]), "last": str(v[1])}
            for k, v in sorted(_avail_cache[1].items()) if k in wanted]


@app.post("/api/backfill", status_code=202)
def start_backfill(body: BackfillRequest, admin=Depends(auth.require_admin)):
    if not MAP_KEY:
        raise HTTPException(400, "FIRMS_MAP_KEY is not set on the server")
    if SERVERLESS:
        raise HTTPException(400, "On this host, past data is collected from your computer: "
                                 "py scripts\\firms_backfill.py --start YYYY-MM-DD --end YYYY-MM-DD "
                                 "(with REDZONE_DB and MOTHERDUCK_TOKEN set). Look-ups still work here.")
    if backfill_running():
        raise HTTPException(409, "A past-data job is already running")
    try:
        start, end = date.fromisoformat(body.start), date.fromisoformat(body.end)
    except ValueError:
        raise HTTPException(400, "Dates must be real dates (YYYY-MM-DD)")
    if end < start:
        raise HTTPException(400, "The end date is before the start date")
    if (end - start).days + 1 > firms_backfill.MAX_DAYS_PER_JOB:
        raise HTTPException(400, f"At most {firms_backfill.MAX_DAYS_PER_JOB} days per job; split the range")
    job = firms_backfill.Job()

    def work():
        cur = DB.cursor()
        try:
            firms_backfill.run(cur, MAP_KEY, start, end, log=print, job=job)
        except Exception as exc:
            print("[backfill] ERROR", type(exc).__name__, exc)
        finally:
            cur.close()
            _coverage_cache[1] = None

    BACKFILL["job"] = job
    BACKFILL["thread"] = threading.Thread(target=work, daemon=True, name="backfill")
    BACKFILL["thread"].start()
    return {"started": True}


@app.post("/api/backfill/cancel")
def cancel_backfill(admin=Depends(auth.require_admin)):
    if not backfill_running():
        return {"cancelled": False, "message": "No job is running"}
    BACKFILL["job"].stop.set()
    return {"cancelled": True}


class Site(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_km: float = Field(gt=0, le=500)


@app.get("/api/sites")
def list_sites(user=Depends(auth.require_user)):
    rows = cursor().execute("SELECT id, name, lat, lon, radius_km FROM priority_sites WHERE user_id = ? ORDER BY id",
                            [user["id"]]).fetchall()
    return [{"id": r[0], "name": r[1], "lat": r[2], "lon": r[3], "radius_km": r[4]} for r in rows]


@app.post("/api/sites")
def add_site(site: Site, user=Depends(auth.require_verified)):
    cur = cursor()
    if cur.execute("SELECT COUNT(*) FROM priority_sites WHERE user_id = ?", [user["id"]]).fetchall()[0][0] >= 50:
        raise HTTPException(400, "You can have at most 50 priority sites.")
    new_id = cur.execute(
        "INSERT INTO priority_sites (name, lat, lon, radius_km, user_id) VALUES (?, ?, ?, ?, ?) RETURNING id",
        [site.name.strip(), site.lat, site.lon, site.radius_km, user["id"]]).fetchall()[0][0]
    return {"id": new_id}


@app.delete("/api/sites/{site_id}")
def delete_site(site_id: int, user=Depends(auth.require_user)):
    cur = cursor()
    if not cur.execute("DELETE FROM priority_sites WHERE id = ? AND user_id = ? RETURNING id",
                       [site_id, user["id"]]).fetchall():
        raise HTTPException(404, "site not found")
    cur.execute("DELETE FROM alerts WHERE site_id = ?", [site_id])
    return {"deleted": site_id}


@app.get("/api/alerts")
def alerts(limit: int = 50, user=Depends(auth.require_user)):
    rows = cursor().execute("""
        SELECT a.id, a.site_id, a.site_name, a.distance_km, a.latitude, a.longitude,
               strftime(a.acq_datetime, '%Y-%m-%d %H:%M'), a.sensor_family, a.confidence, a.frp, a.frp_flag,
               strftime(a.created_at, '%Y-%m-%d %H:%M')
        FROM alerts a JOIN priority_sites s ON s.id = a.site_id
        WHERE s.user_id = ? ORDER BY a.id DESC LIMIT ?""", [user["id"], min(max(limit, 1), 500)]).fetchall()
    keys = ["id", "site_id", "site", "km", "lat", "lon", "time", "sensor", "confidence",
            "frp", "frp_flag", "created"]
    return [dict(zip(keys, r)) for r in rows]


# ---------- place search ----------
# OpenStreetMap services ask every app to identify itself, with a contact address if possible
USER_AGENT = "REDZONE-prototype/1.0 (NASA Space Apps project" + (f"; {CONTACT})" if CONTACT else ")")


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


@lru_cache(maxsize=512)
def _geocode(q):
    data = get_json("https://photon.komoot.io/api/?" + urllib.parse.urlencode({"q": q, "limit": 6}))
    out = []
    for f in data.get("features", []):
        p, (lon, lat) = f["properties"], f["geometry"]["coordinates"]
        detail = [p.get(k) for k in ("city", "county", "state", "country") if p.get(k) and p.get(k) != p.get("name")]
        out.append({"name": p.get("name") or p.get("street") or q, "detail": ", ".join(dict.fromkeys(detail)),
                    "kind": (p.get("osm_value") or "").replace("_", " "), "lat": lat, "lon": lon,
                    "extent": p.get("extent"),                 # [west, north, east, south] or None
                    "osm_type": p.get("osm_type"), "osm_id": p.get("osm_id")})
    return out


@app.get("/api/geocode")
def geocode(q: str, request: Request):
    auth.rate_limit("geocode", auth.client_ip(request), 120, 60)
    q = q.strip()
    if len(q) < 2:
        return []
    try:
        return _geocode(q[:120])
    except Exception as exc:
        raise HTTPException(502, f"place search unavailable: {type(exc).__name__}")


_last_nominatim = [0.0]


@lru_cache(maxsize=256)
def _boundary(osm_key):
    wait = 1.0 - (time.time() - _last_nominatim[0])     # Nominatim policy: max 1 request / second
    if wait > 0:
        time.sleep(wait)
    _last_nominatim[0] = time.time()
    data = get_json("https://nominatim.openstreetmap.org/lookup?" + urllib.parse.urlencode(
        {"osm_ids": osm_key, "format": "geojson", "polygon_geojson": 1, "polygon_threshold": 0.005}))
    feats = data.get("features", [])
    return feats[0]["geometry"] if feats else None


@app.get("/api/boundary")
def boundary(osm_type: str, osm_id: int):
    if osm_type not in ("N", "W", "R"):
        raise HTTPException(400, "osm_type must be N, W or R")
    try:
        geom = _boundary(f"{osm_type}{osm_id}")
    except Exception as exc:
        raise HTTPException(502, f"boundary unavailable: {type(exc).__name__}")
    return {"geometry": geom if geom and geom["type"] in ("Polygon", "MultiPolygon") else None}


# ---------- email alert subscriptions ----------
class Subscription(BaseModel):
    email: str = Field(max_length=200)
    name: str = Field(min_length=1, max_length=80)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_km: float = Field(gt=0, le=200)
    min_frp: float = Field(ge=0, le=5000)


def page(title, body):
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
      <style>body{{font:16px system-ui,sans-serif;background:#0f1115;color:#e6e8ec;display:flex;
      justify-content:center;padding-top:12vh}}.b{{max-width:520px;background:#171a21;border:1px solid #262b36;
      border-radius:10px;padding:26px 30px}}a{{color:#ff4d2e}}</style></head>
      <body><div class="b"><h2>{title}</h2><p>{body}</p><p><a href="/">Open the REDZONE map</a></p></div></body></html>""")


@app.post("/api/subscriptions")
def subscribe(sub: Subscription, request: Request):
    auth.rate_limit("subscribe", auth.client_ip(request), 10, 3600)
    if not auth.email_enabled():
        raise HTTPException(503, "Email alerts are not available on this server.")
    email = sub.email.strip().lower()
    if not notify.valid_email(email):
        raise HTTPException(400, "Please enter a valid email address")
    cur = cursor()
    n = cur.execute("SELECT COUNT(*) FROM subscriptions WHERE email = ? AND active", [email]).fetchall()[0][0]
    if n >= 20:
        raise HTTPException(400, "This email already has 20 active subscriptions")
    notify.create_subscription(cur, email, sub.name.strip(), sub.lat, sub.lon, sub.radius_km, sub.min_frp)
    test = not os.environ.get("SMTP_HOST")
    return {"ok": True, "test_mode": test,
            "message": "Confirmation saved to data/outbox/ (test mode - no SMTP configured)." if test
            else "Check your inbox and click the confirmation link."}


@app.get("/confirm/{kind}/{token}")
def confirm(kind: str, token: str):
    table = {"sub": "subscriptions", "event": "events"}.get(kind)
    if not table:
        raise HTTPException(404)
    row = cursor().execute(f"UPDATE {table} SET confirmed = true WHERE token = ? RETURNING name", [token]).fetchall()
    if not row:
        return page("Link not valid", "This confirmation link is unknown or was cancelled.")
    what = "fire alerts" if kind == "sub" else "the fire check email"
    extra = ""
    if kind == "event":                      # already checked before you confirmed? send it now
        ev = cursor().execute("SELECT * FROM events WHERE token = ?", [token]).fetchdf().to_dict("records")[0]
        if ev["status"] == "checked" and ev["result"]:
            ev["starts_at"] = ev["starts_at"].to_pydatetime()
            notify.email_event_result(cursor(), ev, ev["verdict"], json.loads(ev["result"]))
            extra = " The check was already done - the result has just been emailed to you."
    return page("Confirmed ✓", f"You will receive {what} for <b>{row[0][0]}</b>.{extra}")


@app.get("/unsubscribe/{token}")
def unsubscribe(token: str):
    cur = cursor()
    s = cur.execute("UPDATE subscriptions SET active = false WHERE token = ? RETURNING name", [token]).fetchall()
    e = cur.execute("UPDATE events SET email = NULL, confirmed = false WHERE token = ? RETURNING name", [token]).fetchall()
    if not s and not e:
        return page("Link not valid", "Nothing was found for this link.")
    return page("Unsubscribed", f"No more emails for <b>{(s or e)[0][0]}</b>.")


# ---------- fire-risk outlook ----------
_outlook_cache = {}


@app.get("/api/outlook")
def get_outlook(request: Request, lat: float, lon: float, radius_km: float = 25):
    auth.rate_limit("outlook", auth.client_ip(request), 30, 60)
    key = (round(lat, 2), round(lon, 2), round(radius_km))
    hit = _outlook_cache.get(key)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    try:
        res = outlook_mod.outlook(cursor(), lat, lon, radius_km)
    except Exception as exc:
        raise HTTPException(502, f"weather forecast unavailable: {type(exc).__name__}")
    _outlook_cache[key] = (time.time(), res)
    return res


# ---------- events ----------
class Event(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    details: str = Field(default="", max_length=1000)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_km: float = Field(gt=0, le=200)
    starts_at: datetime                                 # sent by the browser in UTC (ISO 8601)
    notify: bool = True                                 # email the check result to the account email


@app.get("/api/events")
def list_events(user=Depends(auth.require_user)):
    rows = cursor().execute("""
        SELECT id, name, details, lat, lon, radius_km, strftime(starts_at, '%Y-%m-%dT%H:%M:00Z'),
               email IS NOT NULL, confirmed, status, verdict, result,
               strftime(checked_at, '%Y-%m-%d %H:%M')
        FROM events WHERE user_id = ? ORDER BY starts_at""", [user["id"]]).fetchall()
    keys = ["id", "name", "details", "lat", "lon", "radius_km", "starts_at", "has_email",
            "confirmed", "status", "verdict", "result", "checked_at"]
    out = [dict(zip(keys, r)) for r in rows]
    for e in out:
        e["result"] = json.loads(e["result"]) if e["result"] else None
    return out


@app.post("/api/events")
def add_event(ev: Event, user=Depends(auth.require_verified)):
    cur = cursor()
    if cur.execute("SELECT COUNT(*) FROM events WHERE user_id = ? AND status = 'scheduled'",
                   [user["id"]]).fetchall()[0][0] >= 100:
        raise HTTPException(400, "You can have at most 100 upcoming events.")
    # Account emails are not verified, so event results are shown on the website only (no email).
    email = None
    start = ev.starts_at.astimezone(timezone.utc).replace(tzinfo=None) if ev.starts_at.tzinfo else ev.starts_at
    if start < notify.utcnow():
        raise HTTPException(400, "The event start is in the past")
    eid = notify.create_event(cur, ev.name.strip(), ev.details.strip(), ev.lat, ev.lon,
                              ev.radius_km, start, email, user_id=user["id"], confirmed=True)
    return {"id": eid, "check_at": str(start - notify.timedelta(hours=notify.CHECK_HOURS_BEFORE))}


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int, user=Depends(auth.require_user)):
    if not cursor().execute("DELETE FROM events WHERE id = ? AND user_id = ? RETURNING id",
                            [event_id, user["id"]]).fetchall():
        raise HTTPException(404, "event not found")
    return {"deleted": event_id}


@app.post("/api/events/{event_id}/check")
def check_event_now(event_id: int, request: Request, user=Depends(auth.require_user)):
    auth.rate_limit("event-check", str(user["id"]), 20, 3600)
    cur = cursor()
    if not cur.execute("SELECT 1 FROM events WHERE id = ? AND user_id = ?", [event_id, user["id"]]).fetchall():
        raise HTTPException(404, "event not found")
    notify.run_due_events(cur, force_id=event_id)
    return {"checked": event_id}


# ---------- approximate location from IP address ----------
def client_ip(request):
    """The visitor's IP. Behind a proxy it is in X-Forwarded-For."""
    ip = auth.client_ip(request)                     # proxy header trusted only if REDZONE_TRUST_PROXY=1
    try:
        return ip if ipaddress.ip_address(ip).is_global else ""
    except ValueError:
        return ""
    # "" = the visitor is on this computer / local network: the lookup service
    #      then uses this computer's own public IP, which is the same person.


@app.get("/api/iplocation")
def iplocation(request: Request):
    auth.rate_limit("iplocation", auth.client_ip(request), 10, 60)
    ip = client_ip(request)
    providers = [
        ("ipapi.co", f"https://ipapi.co/{ip + '/' if ip else ''}json/",
         lambda d: (d.get("latitude"), d.get("longitude"), d.get("city"), d.get("region"), d.get("country_name"))),
        ("ipwho.is", f"https://ipwho.is/{ip}",
         lambda d: (d.get("latitude"), d.get("longitude"), d.get("city"), d.get("region"), d.get("country"))),
    ]
    for name, url, pick in providers:
        try:
            lat, lon, city, region, country = pick(get_json(url))
            if lat is not None and lon is not None:
                return {"lat": float(lat), "lon": float(lon), "source": name,
                        "place": ", ".join(dict.fromkeys(p for p in (city, region, country) if p))}
        except Exception:
            continue                                  # try the next provider
    raise HTTPException(502, "IP location services are unavailable")


@app.get("/api/notes")
def notes():
    try:
        rows = cursor().execute("SELECT * FROM calibration_notes ORDER BY item").fetchall()
    except duckdb.CatalogException:
        rows = []
    return [{"item": r[0], "sensor": r[1], "rule": r[2], "source": r[3]} for r in rows]
