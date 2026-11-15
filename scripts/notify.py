"""
REDZONE - email alerts and event checks.

Email alert subscriptions
  - A subscription = email + place + radius + minimum FRP.
  - Double opt-in: nothing is sent until the owner clicks the confirmation link.
  - After each live update, NEW detections are matched to confirmed subscriptions.
    Only "dangerous" detections are reported:
        * confidence moderate or high (MODIS >= 30, VIIRS n/h), and
        * a valid FRP (frp_flag = 'ok') of at least the subscription's minimum.
    One summary email per subscription per update. Every email has an unsubscribe link.

Events
  - An event = name, place, start time (UTC), radius, optional email.
  - 12 hours before the start (or right away if created later than that) the event
    is checked: moderate/high-confidence fires within the radius in the last 48 h of
    data + the fire-weather forecast for the 12 h after the start.
  - The verdict never says "safe": it says whether fire activity was DETECTED, and
    always reminds people to follow local authorities.

Sending email
  SMTP settings come from environment variables:
      SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM
  Works with any SMTP provider (e.g. Brevo: smtp-relay.brevo.com, port 587, login = your Brevo
  account email, password = an SMTP key from the Brevo dashboard; ports 465 and 2525 also work).
  Without SMTP_HOST, emails are written to data/outbox/ as .eml files (test mode).
  REDZONE_BASE_URL (default http://127.0.0.1:8000) is used for links in emails.
"""

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
import json
import math
import os
import re
import secrets
import smtplib
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outlook  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import redzone_db                                    # noqa: E402
OUTBOX = redzone_db.WORK_DIR / "outbox"              # data/outbox (or /tmp on Vercel)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CHECK_HOURS_BEFORE = 12
RECENT_HOURS = 48

CONF_CLASS = """CASE WHEN sensor_family = 'MODIS' THEN
        CASE WHEN TRY_CAST(confidence AS INT) < 30 THEN 'low'
             WHEN TRY_CAST(confidence AS INT) < 80 THEN 'nominal' ELSE 'high' END
    ELSE CASE confidence WHEN 'l' THEN 'low' WHEN 'n' THEN 'nominal' WHEN 'h' THEN 'high' END END"""

DIST = lambda a, b: f"""2 * 6371 * asin(sqrt(pow(sin(radians({a}.latitude - {b}.lat) / 2), 2) +
    cos(radians({b}.lat)) * cos(radians({a}.latitude)) * pow(sin(radians({a}.longitude - {b}.lon) / 2), 2)))"""


def base_url():
    return os.environ.get("REDZONE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_token():
    return secrets.token_urlsafe(24)


def valid_email(e):
    return bool(EMAIL_RE.match(e or "")) and len(e) <= 200


# ---------------------------------------------------------------------------
def ensure_tables(con):
    con.execute("CREATE SEQUENCE IF NOT EXISTS sub_id_seq")
    con.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER DEFAULT nextval('sub_id_seq'), email VARCHAR, name VARCHAR,
        lat DOUBLE, lon DOUBLE, radius_km DOUBLE, min_frp DOUBLE,
        token VARCHAR, confirmed BOOLEAN DEFAULT false, active BOOLEAN DEFAULT true,
        created_at TIMESTAMP DEFAULT current_timestamp)""")
    con.execute("CREATE SEQUENCE IF NOT EXISTS event_id_seq")
    con.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER DEFAULT nextval('event_id_seq'), name VARCHAR, details VARCHAR,
        lat DOUBLE, lon DOUBLE, radius_km DOUBLE, starts_at TIMESTAMP,
        email VARCHAR, token VARCHAR, confirmed BOOLEAN DEFAULT false,
        status VARCHAR DEFAULT 'scheduled', verdict VARCHAR, result VARCHAR,
        checked_at TIMESTAMP, created_at TIMESTAMP DEFAULT current_timestamp)""")
    con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS user_id INTEGER")
    con.execute("""CREATE TABLE IF NOT EXISTS email_log (
        sent_at TIMESTAMP, to_addr VARCHAR, kind VARCHAR, subject VARCHAR,
        ok BOOLEAN, error VARCHAR)""")


def send_email(cur, to, subject, text, kind):
    """Send with SMTP if configured, otherwise save to data/outbox/. Returns True if OK."""
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "redzone@localhost"
    msg.set_content(text)
    ok, err = True, None
    try:
        host = os.environ.get("SMTP_HOST", "").strip()
        if host:
            port = int(os.environ.get("SMTP_PORT", "587"))
            # 465 = encrypted from the start (SSL); 587 / 2525 = upgraded with STARTTLS
            smtp = smtplib.SMTP_SSL(host, port, timeout=30) if port == 465 else smtplib.SMTP(host, port, timeout=30)
            with smtp as s:
                if port != 465:
                    s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
                s.send_message(msg)
        else:
            OUTBOX.mkdir(parents=True, exist_ok=True)
            name = f"{utcnow():%Y%m%d_%H%M%S}_{kind}_{secrets.token_hex(3)}.eml"
            (OUTBOX / name).write_bytes(bytes(msg))
    except Exception as exc:                       # never crash the caller
        ok, err = False, f"{type(exc).__name__}: {exc}"[:300]
    cur.execute("INSERT INTO email_log VALUES (?, ?, ?, ?, ?, ?)", [utcnow(), to, kind, subject, ok, err])
    return ok


def footer(unsub_url):
    return ("\n--\nREDZONE - satellite fire detections from NASA FIRMS (MODIS and VIIRS NOAA-21).\n"
            "Satellites can miss fires (clouds, timing) and can report false detections.\n"
            "Always follow official guidance from local authorities.\n"
            f"Unsubscribe / cancel: {unsub_url}\n")


# ---------- subscriptions ----------
def create_subscription(cur, email, name, lat, lon, radius_km, min_frp):
    token = new_token()
    sid = cur.execute("""INSERT INTO subscriptions (email, name, lat, lon, radius_km, min_frp, token)
        VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id""",
        [email, name, lat, lon, radius_km, min_frp, token]).fetchall()[0][0]
    text = (f"Hello,\n\nSomeone (hopefully you) asked REDZONE to email this address when a fire is "
            f"detected near '{name}' ({lat:.4f}, {lon:.4f}), within {radius_km:g} km.\n\n"
            f"Alerts include only moderate/high-confidence detections with FRP of at least {min_frp:g} MW.\n\n"
            f"To start the alerts, confirm here:\n{base_url()}/confirm/sub/{token}\n\n"
            "If this wasn't you, ignore this email - nothing will be sent.\n" + footer(f"{base_url()}/unsubscribe/{token}"))
    send_email(cur, email, "REDZONE: confirm your fire alert subscription", text, "confirm")
    return sid


def process_new_detections(cur, inserted, log=print):
    """Match NEW detections (DataFrame from the live update) to confirmed subscriptions."""
    if inserted is None or len(inserted) == 0:
        return 0
    cur.register("newdet", inserted)
    rows = cur.execute(f"""
        SELECT s.id, s.email, s.name, s.radius_km, s.min_frp, s.token, s.lat, s.lon,
               d.latitude, d.longitude, strftime(d.acq_datetime, '%Y-%m-%d %H:%M') AS t,
               d.sensor_family, d.satellite, d.confidence, d.conf_class, d.frp, d.km
        FROM subscriptions s,
             LATERAL (SELECT n.*, ({CONF_CLASS}) AS conf_class, {DIST('n', 's')} AS km
                      FROM newdet n) d
        WHERE s.confirmed AND s.active
          AND d.km <= s.radius_km
          AND d.conf_class IN ('nominal', 'high')
          AND d.frp_flag = 'ok' AND d.frp >= s.min_frp
        ORDER BY s.id, d.km""").fetchall()
    cur.unregister("newdet")

    by_sub = {}
    for r in rows:
        by_sub.setdefault(r[0], []).append(r)
    sent = 0
    for sid, det in by_sub.items():
        _, email, name, radius, min_frp, token, slat, slon = det[0][:8]
        lines = [f"  - {d[10]} UTC | {d[16]:.1f} km away | {d[11]} ({d[12]}) | "
                 f"confidence {d[14]} ({d[13]}) | FRP {d[15]:.1f} MW" for d in det[:25]]
        if len(det) > 25:
            lines.append(f"  ... and {len(det) - 25} more")
        nearest = det[0]
        text = (f"REDZONE fire alert for '{name}'\n\n"
                f"{len(det)} new satellite fire detection(s) within {radius:g} km "
                f"(moderate/high confidence, FRP >= {min_frp:g} MW).\n"
                f"Nearest: {nearest[16]:.1f} km away at {nearest[10]} UTC.\n\n" + "\n".join(lines) +
                f"\n\nSee the map: {base_url()}/app?lat={nearest[8]:.4f}&lon={nearest[9]:.4f}&z=11\n"
                "\nFRP (fire radiative power) shows how much heat a detected fire released when the "
                "satellite passed. MODIS and VIIRS values are not directly comparable.\n" +
                footer(f"{base_url()}/unsubscribe/{token}"))
        subject = f"REDZONE alert: {len(det)} fire detection(s) within {radius:g} km of {name}"
        if send_email(cur, email, subject, text, "alert"):
            sent += 1
        log(f"[email] alert for subscription {sid}: {len(det)} detection(s)")
    return sent


# ---------- events ----------
def create_event(cur, name, details, lat, lon, radius_km, starts_at, email, user_id=None, confirmed=False):
    """confirmed=True when the email is the owner's already-verified account email (no extra confirmation)."""
    token = new_token()
    eid = cur.execute("""INSERT INTO events (name, details, lat, lon, radius_km, starts_at, email, token, confirmed, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
        [name, details, lat, lon, radius_km, starts_at, email, token, confirmed, user_id]).fetchall()[0][0]
    if email and not confirmed:
        text = (f"Hello,\n\nYou saved the event '{name}' on REDZONE, starting {starts_at:%Y-%m-%d %H:%M} UTC.\n"
                f"About {CHECK_HOURS_BEFORE} hours before it starts, REDZONE will check for satellite-detected "
                f"fires within {radius_km:g} km and the fire-weather forecast, and email you the result.\n\n"
                f"Confirm this email address:\n{base_url()}/confirm/event/{token}\n" +
                footer(f"{base_url()}/unsubscribe/{token}"))
        send_email(cur, email, f"REDZONE: confirm fire check for '{name}'", text, "confirm")
    return eid


def check_event(cur, ev, fetch=None):
    """ev = dict with lat, lon, radius_km, starts_at (UTC naive). Returns (verdict, result dict)."""
    lat, lon, radius = ev["lat"], ev["lon"], ev["radius_km"]
    newest = cur.execute("SELECT MAX(acq_datetime) FROM fires").fetchall()[0][0]
    since = (newest or utcnow()) - timedelta(hours=RECENT_HOURS)
    dlat = radius / 111.0
    dlon = radius / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    near = cur.execute(f"""
        SELECT strftime(acq_datetime, '%Y-%m-%d %H:%M'), sensor_family, confidence, frp, km FROM (
            SELECT f.*, {DIST('f', 'p')} AS km
            FROM fires f, (SELECT ? AS lat, ? AS lon) p
            WHERE f.acq_datetime >= ? AND f.latitude BETWEEN ? AND ? AND f.longitude BETWEEN ? AND ?
              AND ({CONF_CLASS.replace('sensor_family', 'f.sensor_family').replace('confidence', 'f.confidence')})
                  IN ('nominal', 'high'))
        WHERE km <= ? ORDER BY km LIMIT 50""",
        [lat, lon, since, lat - dlat, lat + dlat, lon - dlon, lon + dlon, radius]).fetchall()

    try:
        start = ev["starts_at"].replace(tzinfo=timezone.utc)
        weather = outlook.window_weather(lat, lon, start, 12, fetch=fetch)
    except Exception as exc:
        weather = None
        weather_err = f"{type(exc).__name__}"
    else:
        weather_err = None

    stale = newest is None or (utcnow() - newest) > timedelta(hours=24)
    if near:
        verdict = "fire_nearby"
        headline = (f"FIRE ACTIVITY DETECTED: {len(near)} moderate/high-confidence detection(s) within "
                    f"{radius:g} km in the last {RECENT_HOURS} h of data (nearest {near[0][4]:.1f} km).")
    elif weather and weather["level"] in ("high", "very high"):
        verdict = "caution"
        headline = (f"CAUTION: no fires detected within {radius:g} km, but fire weather is "
                    f"{weather['level']} during the event (FFWI {weather['ffwi']}).")
    else:
        verdict = "clear"
        headline = (f"No fire activity detected within {radius:g} km in the last {RECENT_HOURS} h of "
                    "satellite data" + (f", and fire weather is {weather['level']}." if weather else "."))
    result = {"headline": headline, "nearby": [list(n) for n in near[:10]], "nearby_count": len(near),
              "weather": weather, "weather_error": weather_err,
              "newest_data": str(newest) if newest else None, "data_stale": stale}
    return verdict, result


def run_due_events(cur, log=print, fetch=None, force_id=None):
    """
    Check events that start within the next 12 h and were not checked yet, and email the owner.
    With force_id: check that one event now (from the website's "Check now" button) - this only
    updates the shown result; it sends no email and does not replace the 12-hour check.
    """
    manual = force_id is not None
    now = utcnow()
    if force_id is not None:
        due = cur.execute("SELECT * FROM events WHERE id = ?", [force_id]).fetchdf()
    else:
        due = cur.execute("""SELECT * FROM events WHERE status = 'scheduled'
            AND starts_at > ? AND starts_at <= ?""", [now, now + timedelta(hours=CHECK_HOURS_BEFORE)]).fetchdf()
    done = 0
    for ev in due.to_dict("records"):
        ev["starts_at"] = ev["starts_at"].to_pydatetime()
        verdict, result = check_event(cur, ev, fetch=fetch)
        cur.execute("UPDATE events SET verdict = ?, result = ?, checked_at = ? WHERE id = ?",
                    [verdict, json.dumps(result), now, ev["id"]])
        if manual:
            log(f"[events] manual check of event {ev['id']}: {verdict}")
            done += 1
            continue
        cur.execute("UPDATE events SET status = 'checked' WHERE id = ?", [ev["id"]])
        if ev.get("email") and ev.get("confirmed"):
            email_event_result(cur, ev, verdict, result)
        log(f"[events] checked event {ev['id']}: {verdict}")
        done += 1
    return done


def email_event_result(cur, ev, verdict, result):
    """Email the result of an event check (ev needs name, starts_at, lat, lon, email, token)."""
    w = result["weather"]
    text = (f"REDZONE fire check for your event '{ev['name']}'\n"
            f"Starts: {ev['starts_at']:%Y-%m-%d %H:%M} UTC  |  Place: {ev['lat']:.4f}, {ev['lon']:.4f}\n\n"
            f"{result['headline']}\n\n")
    for n in result["nearby"][:10]:
        text += f"  - {n[0]} UTC | {n[4]:.1f} km | {n[1]} | confidence {n[2]} | FRP {n[3]} MW\n"
    if w:
        text += (f"\nWeather during the event (next 12 h from start): max temperature {w['tmax']} C, "
                 f"min humidity {w['rhmin']} %, max wind {w['windmax']} km/h, rain {w['rain']} mm, "
                 f"fire weather index {w['ffwi']} ({w['level']}).\n")
    if result["data_stale"]:
        text += f"\nNote: the newest satellite data is from {result['newest_data']} UTC - it may be out of date.\n"
    text += (f"\nSee the map: {base_url()}/app?lat={ev['lat']:.4f}&lon={ev['lon']:.4f}&z=10\n"
             "\nThis is not a safety guarantee. Satellites pass only a few times a day and can miss fires.\n" +
             footer(f"{base_url()}/unsubscribe/{ev['token']}"))
    send_email(cur, ev["email"], f"REDZONE fire check: {ev['name']} - {verdict.replace('_', ' ')}", text, "event")
