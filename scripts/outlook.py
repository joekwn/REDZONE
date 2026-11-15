"""
REDZONE - fire-risk outlook (next days) = WEATHER + HISTORY.

1. Weather: hourly forecast from Open-Meteo (free, no key) -> Fosberg Fire Weather
   Index (FFWI, Fosberg 1978) for every hour -> the day's highest value.
   FFWI uses only temperature, relative humidity and wind. It runs 0-100;
   NOAA notes values of 50 or more are generally significant.
2. History: our own satellite record (the `fires` table) around the place:
     - same season last year  (+/- 15 days, one year before the forecast day)
     - recent activity        (last 14 days of data)
   Only moderate/high confidence detections are counted.

The result is a RISK LEVEL with its reasons - not a prediction that a fire
will happen. It ignores fuels/vegetation and long-term drought, and our
history covers only one year.

Level bands and adjustments are REDZONE project choices (documented here):
    FFWI  < 20  low | 20-35 moderate | 35-50 high | >= 50 very high
    rain >= 2 mm that day      -> one level lower
    seasonal fires >= 10 OR recent fires >= 5 within the radius -> one level higher
"""

from datetime import date, datetime, timedelta, timezone
import json
import math
import urllib.parse
import urllib.request

LEVELS = ["low", "moderate", "high", "very high"]
import os
_contact = os.environ.get("REDZONE_CONTACT_EMAIL", "").strip()
USER_AGENT = "REDZONE-prototype/1.0 (NASA Space Apps project" + (f"; {_contact})" if _contact else ")")

CONF_OK_SQL = """(
    (sensor_family = 'MODIS' AND TRY_CAST(confidence AS INT) >= 30) OR
    (sensor_family = 'VIIRS' AND confidence IN ('n', 'h')))"""

DIST_SQL = """2 * 6371 * asin(sqrt(pow(sin(radians(latitude - ?) / 2), 2) +
    cos(radians(?)) * cos(radians(latitude)) * pow(sin(radians(longitude - ?) / 2), 2)))"""


# ---------------------------------------------------------------------------
def ffwi(temp_c, rh, wind_kmh):
    """Fosberg Fire Weather Index from temperature (C), relative humidity (%), wind (km/h)."""
    if temp_c is None or rh is None or wind_kmh is None:
        return None
    t = temp_c * 9 / 5 + 32                       # Fahrenheit
    h = min(max(rh, 0), 100)
    u = wind_kmh * 0.621371                       # mph
    if h < 10:
        m = 0.03229 + 0.281073 * h - 0.000578 * h * t
    elif h <= 50:
        m = 2.22749 + 0.160107 * h - 0.01478 * t
    else:
        m = 21.0606 + 0.005565 * h ** 2 - 0.00035 * h * t - 0.483199 * h
    r = min(max(m, 0), 30) / 30
    eta = 1 - 2 * r + 1.5 * r ** 2 - 0.5 * r ** 3
    return min(max(eta * math.sqrt(1 + u ** 2) / 0.3002, 0), 100)


def weather_level(f):
    if f is None:
        return None
    return 0 if f < 20 else 1 if f < 35 else 2 if f < 50 else 3


def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_hourly(lat, lon, days=7, tz="auto"):
    """Hourly forecast from Open-Meteo. Returns list of dicts (time is local unless tz='GMT')."""
    q = urllib.parse.urlencode({
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
        "forecast_days": min(max(days, 1), 16), "timezone": tz})
    d = get_json("https://api.open-meteo.com/v1/forecast?" + q)
    h = d["hourly"]
    rows = []
    for i, t in enumerate(h["time"]):
        rows.append({"time": t, "temp": h["temperature_2m"][i], "rh": h["relative_humidity_2m"][i],
                     "wind": h["wind_speed_10m"][i], "rain": h["precipitation"][i] or 0.0})
        rows[-1]["ffwi"] = ffwi(rows[-1]["temp"], rows[-1]["rh"], rows[-1]["wind"])
    return rows, d.get("timezone", tz)


def history_counts(cur, lat, lon, radius_km, day):
    """Moderate/high-confidence detections near the place: same season last year + recent."""
    radius_km = min(max(radius_km, 1), 200)
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    box = "latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?"
    boxp = [lat - dlat, lat + dlat, lon - dlon, lon + dlon]

    def count(start, end):
        return cur.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT {DIST_SQL} AS km FROM fires
                WHERE acq_date BETWEEN ? AND ? AND {box} AND {CONF_OK_SQL}) WHERE km <= ?""",
            [lat, lat, lon, start.isoformat(), end.isoformat(), *boxp, radius_km]).fetchall()[0][0]

    lo, hi = cur.execute("SELECT MIN(CAST(acq_date AS DATE)), MAX(CAST(acq_date AS DATE)) FROM fires").fetchall()[0]
    centre = day - timedelta(days=365)
    s_start, s_end = centre - timedelta(days=15), centre + timedelta(days=15)
    season = count(s_start, s_end) if lo and s_end >= lo and s_start <= hi else None
    r_end = hi
    r_start = hi - timedelta(days=13)
    recent = count(r_start, r_end) if hi else 0
    return {"season": season, "season_window": f"{s_start} to {s_end}",
            "recent": recent, "recent_window": f"{r_start} to {r_end}"}


def combine(wlevel, rain_mm, hist):
    """Weather level + adjustments -> (level index, list of reasons)."""
    reasons = []
    if wlevel is None:
        return None, ["no weather forecast"]
    level = wlevel
    reasons.append(f"fire weather (FFWI) {LEVELS[wlevel]}")
    if rain_mm >= 2:
        level -= 1
        reasons.append(f"{rain_mm:.1f} mm rain expected (lowers risk)")
    if (hist["season"] or 0) >= 10 or hist["recent"] >= 5:
        level += 1
        parts = []
        if (hist["season"] or 0) >= 10:
            parts.append(f"{hist['season']} fires here in this season last year")
        if hist["recent"] >= 5:
            parts.append(f"{hist['recent']} fires here in the last 14 days of data")
        reasons.append(" and ".join(parts) + " (raises risk)")
    return min(max(level, 0), 3), reasons


def outlook(cur, lat, lon, radius_km=25, days=7, fetch=None):
    """7-day outlook for a place. `fetch` can replace fetch_hourly (for tests)."""
    rows, tz = (fetch or fetch_hourly)(lat, lon, days)
    by_day = {}
    for r in rows:
        by_day.setdefault(r["time"][:10], []).append(r)
    out = []
    for day_s, hours in sorted(by_day.items()):
        day = date.fromisoformat(day_s)
        vals = [h["ffwi"] for h in hours if h["ffwi"] is not None]
        f = max(vals) if vals else None
        rain = sum(h["rain"] for h in hours)
        hist = history_counts(cur, lat, lon, radius_km, day)
        level, reasons = combine(weather_level(f), rain, hist)
        out.append({
            "date": day_s, "level": LEVELS[level] if level is not None else "unknown",
            "ffwi": None if f is None else round(f, 1),
            "tmax": max((h["temp"] for h in hours if h["temp"] is not None), default=None),
            "rhmin": min((h["rh"] for h in hours if h["rh"] is not None), default=None),
            "windmax": max((h["wind"] for h in hours if h["wind"] is not None), default=None),
            "rain": round(rain, 1), "season_fires": hist["season"], "recent_fires": hist["recent"],
            "reasons": reasons})
    return {"lat": lat, "lon": lon, "radius_km": radius_km, "timezone": tz, "days": out,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}


def window_weather(lat, lon, start_utc, hours=12, fetch=None):
    """Highest FFWI / rain in the hours after an event starts (UTC). For event checks."""
    days_ahead = (start_utc.date() - datetime.now(timezone.utc).date()).days + 2
    rows, _ = (fetch or fetch_hourly)(lat, lon, max(days_ahead, 2), tz="GMT")
    end = start_utc + timedelta(hours=hours)
    sel = [r for r in rows if start_utc <= datetime.fromisoformat(r["time"]).replace(tzinfo=timezone.utc) < end]
    vals = [r["ffwi"] for r in sel if r["ffwi"] is not None]
    if not vals:
        return None
    return {"ffwi": round(max(vals), 1), "level": LEVELS[weather_level(max(vals))],
            "rain": round(sum(r["rain"] for r in sel), 1),
            "tmax": max(r["temp"] for r in sel), "rhmin": min(r["rh"] for r in sel),
            "windmax": max(r["wind"] for r in sel)}
