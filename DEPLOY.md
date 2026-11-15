# Taking REDZONE online

## 1. Server
A small Linux VPS (2 vCPU, 4 GB RAM) is enough. Copy the project folder, including data/redzone.duckdb.

    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt

## 2. Settings (environment variables, never in code)
Email is optional. Without SMTP_HOST the site runs with no email at all: accounts work
immediately (no verification), and email alerts / password reset by email are hidden.
    FIRMS_MAP_KEY=...                       # NASA FIRMS key
    SMTP_HOST=smtp-relay.brevo.com          # Brevo free plan: 300 emails/day
    SMTP_PORT=587
    SMTP_USER=...                           # the SMTP login shown on Brevo's SMTP page
    SMTP_PASSWORD=...                       # a Brevo SMTP key (not the API key)
    SMTP_FROM=REDZONE <alerts@your-domain>  # a sender verified in Brevo
    REDZONE_SECRET=...                      # long random text; keeps email codes valid across servers
    REDZONE_BASE_URL=https://your-domain    # used in email links; https also turns on Secure cookies
    REDZONE_TRUST_PROXY=1                   # only when running behind your own proxy (step 3)
    REDZONE_ADMIN_EMAILS=you@example.com    # who may press "Check FIRMS now" and collect past data
    REDZONE_BACKFILL_PAUSE=2                # seconds between past-data requests to FIRMS

## 3. HTTPS with Caddy (automatic certificates)
Caddyfile:

    your-domain {
        reverse_proxy 127.0.0.1:8000
    }

Run the app on the local address only, one worker (DuckDB allows one writing process):

    uvicorn webapp.server:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers

Run it as a systemd service so it restarts on failure and after reboots.

## 4. Backups
Copy data/redzone.duckdb once a day (stop the service for a moment, or use DuckDB's EXPORT DATABASE).
Users, sessions, events and sites live in the same file.

## 5. When usage grows
- Move users/events/sites to PostgreSQL (many writers) and keep DuckDB for the fire data.
- Move rate limits to Redis (today they are per process, in memory).
- Add a Content-Security-Policy header once scripts are moved out of index.html.
