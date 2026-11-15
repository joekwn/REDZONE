"""
REDZONE - create an EMPTY database, so anyone can run the website without the
one-year research dataset (21 million rows, not in this repository).

    py scripts/init_db.py                                      # creates data/redzone.duckdb
    py scripts/firms_live.py --start 2026-11-08 --end 2026-11-15  # fill it with real FIRMS data
                                                                  # (needs a free FIRMS_MAP_KEY)

The fires table uses the same harmonized columns as the research database
(see scripts/redzone_rules.py). Refuses to overwrite an existing database.
"""
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redzone_db                                    # noqa: E402

FIRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS fires (
    latitude DOUBLE, longitude DOUBLE, brightness DOUBLE, scan DOUBLE, track DOUBLE,
    acq_date VARCHAR, acq_time VARCHAR, sensor_family VARCHAR, satellite VARCHAR,
    instrument VARCHAR, confidence VARCHAR, version VARCHAR, bright_t31 DOUBLE,
    frp DOUBLE, daynight VARCHAR, type VARCHAR, data_status VARCHAR, source_file VARCHAR,
    frp_flag VARCHAR, frp_usable DOUBLE, polar_flag BOOLEAN, acq_datetime TIMESTAMP)
"""


def create(path):
    con = duckdb.connect(str(path))
    con.execute(FIRES_SCHEMA)
    import firms_live
    import firms_backfill
    import notify
    firms_live.ensure_tables(con)
    notify.ensure_tables(con)
    firms_backfill.ensure_tables(con)
    con.close()


def main():
    target = redzone_db.target()
    if redzone_db.is_cloud(target):
        sys.exit("STOP: init_db.py only creates a local file. Unset REDZONE_DB first.")
    path = Path(target)
    if path.exists():
        sys.exit(f"STOP: {path} already exists - nothing changed.")
    path.parent.mkdir(parents=True, exist_ok=True)
    create(path)
    print(f"Created an empty database: {path}")
    print("Next: set FIRMS_MAP_KEY, then  py scripts/firms_live.py --start YYYY-MM-DD --end YYYY-MM-DD")


if __name__ == "__main__":
    main()
