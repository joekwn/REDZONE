"""Live FIRMS updates: harmonize, insert, and never insert the same detection twice."""
from datetime import date

import duckdb

import firms_live
import redzone_db


def test_update_inserts_once(tmp_path):
    con = redzone_db.connect()
    try:
        day = date(2026, 11, 10)
        first = firms_live.update(con, "test-key", day, day, log=lambda m: None)
        again = firms_live.update(con, "test-key", day, day, log=lambda m: None)
        n = con.execute("SELECT COUNT(*) FROM fires WHERE acq_date = '2026-11-10'").fetchone()[0]
    finally:
        con.close()
    assert first["inserted"] > 0
    assert again["inserted"] == 0                          # duplicates are skipped
    assert n == first["inserted"]


def test_sql_and_python_confidence_rules_match():
    import redzone_rules as rules
    con = duckdb.connect()
    cases = [("MODIS", "10"), ("MODIS", "30"), ("MODIS", "85"), ("VIIRS", "l"), ("VIIRS", "n"), ("VIIRS", "h")]
    for family, conf in cases:
        sql = con.execute(f"SELECT {rules.CONF_CLASS_SQL} FROM (SELECT ? AS sensor_family, ? AS confidence)",
                          [family, conf]).fetchone()[0]
        assert sql == rules.conf_class(family, conf), (family, conf)
