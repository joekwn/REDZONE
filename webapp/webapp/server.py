"""
REDZONE - website prototype: the server.

Reads data/redzone.duckdb (read-only) and answers the map page's questions:
  /api/meta    date range and totals
  /api/fires   fires in the visible map area (grid cells when zoomed out, points when zoomed in)
  /api/stats   statistics for the visible map area
  /api/near    fires within X km of a priority site
  /api/notes   calibration notes for the "About the data" panel

Run from the project root:
    py -m uvicorn webapp.server:app --port 8000
Then open http://127.0.0.1:8000 in your browser.
"""

from pathlib import Path
import math

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "redzone.duckdb"
STATIC = Path(__file__).resolve().parent / "static"

MAX_POINTS = 20_000      # never send more individual points than this
POINT_ZOOM = 8           # from this zoom level on, show individual detections

if not DB_PATH.exists():
    raise SystemExit(f"Database not found: {DB_PATH}")
DB = duckdb.connect(str(DB_PATH), read_only=True)

# Shared confidence label (same rule as add_confidence_classes.py)
CONF_CLASS = """
    CASE WHEN sensor_family = 'MODIS' THEN
             CASE WHEN TRY_CAST(confidence AS INT) < 30 THEN 'low'
                  WHEN TRY_CAST(confidence AS INT) < 80 THEN 'nominal'
                  ELSE 'high' END
         ELSE CASE confidence WHEN 'l' THEN 'low' WHEN 'n' THEN 'nominal'
                              WHEN 'h' THEN 'high' END
    END"""

app = FastAPI(title="REDZONE prototype")


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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta():
    lo, hi, total = cursor().execute(
        "SELECT MIN(acq_date), MAX(acq_date), COUNT(*) FROM fires").fetchone()
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


@app.get("/api/notes")
def notes():
    try:
        rows = cursor().execute("SELECT * FROM calibration_notes ORDER BY item").fetchall()
    except duckdb.CatalogException:
        rows = []
    return [{"item": r[0], "sensor": r[1], "rule": r[2], "source": r[3]} for r in rows]
