# Changelog

## 1.0.0 - 2026-11-15

First public release.

- Global map of MODIS C6.1 and VIIRS NOAA-21 active-fire detections (one-year research set, Nov 2025 - Nov 2026, 21,167,202 rows) with live FIRMS updates every 3 hours.
- Harmonization and cleaning rules shared by the research set and live data; problem rows flagged, not deleted.
- FRP cross-sensor calibration tested and **not applied** (see `docs/SCIENCE.md`).
- Statistics, "near me", 7-day fire-weather outlook (Fosberg index), watched sites, events with a 12-hour check.
- On-demand look-ups of older FIRMS data; admin past-data collection.
- Accounts with Argon2id passwords, hashed session tokens, CSRF protection, rate limits.
- Deployment: own server (Caddy + systemd), Vercel + MotherDuck, or Cloudflare quick tunnel.
- Test suite and continuous integration.
