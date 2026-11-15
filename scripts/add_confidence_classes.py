"""
REDZONE - Step 6: Calibration part 1 - shared confidence labels.

Adds a VIEW (a saved rule, not a copy of the data) called `fires_v`:
    fires_v = every column of `fires` + confidence_class ('low' / 'nominal' / 'high')

MODIS (number 0-100), from the MODIS C6/C6.1 Active Fire User's Guide, Table 11:
    low      0 <= C < 30
    nominal 30 <= C < 80
    high    80 <= C <= 100
VIIRS (already a category):  l -> low,  n -> nominal,  h -> high

The original `confidence` column is NOT changed.
Because this is a view, the rule can be edited later with no reprocessing.
"""

from pathlib import Path
import sys

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "redzone.duckdb"


def main():
    if not DB_PATH.exists():
        sys.exit(f"STOP: database not found: {DB_PATH}")

    con = duckdb.connect(str(DB_PATH))

    con.execute("""
        CREATE OR REPLACE VIEW fires_v AS
        SELECT *,
               CASE
                 WHEN sensor_family = 'MODIS' THEN
                   CASE
                     WHEN CAST(confidence AS INT) < 30 THEN 'low'
                     WHEN CAST(confidence AS INT) < 80 THEN 'nominal'
                     ELSE 'high'
                   END
                 WHEN sensor_family = 'VIIRS' THEN
                   CASE confidence WHEN 'l' THEN 'low'
                                   WHEN 'n' THEN 'nominal'
                                   WHEN 'h' THEN 'high'
                   END
               END AS confidence_class
        FROM fires
    """)

    # A small table that records HOW the labels were made (for the report / website).
    con.execute("""
        CREATE OR REPLACE TABLE calibration_notes (
            item VARCHAR, sensor VARCHAR, rule VARCHAR, source VARCHAR)
    """)
    con.execute("""
        INSERT INTO calibration_notes VALUES
        ('confidence_class', 'MODIS', 'low: 0-29, nominal: 30-79, high: 80-100',
         'MODIS C6/C6.1 Active Fire Product User''s Guide v1.0, Table 11'),
        ('confidence_class', 'VIIRS', 'l -> low, n -> nominal, h -> high',
         'FIRMS VIIRS 375 m attribute definitions (categories used as given)')
    """)

    # ---- checks ----------------------------------------------------------
    print("Confidence classes per sensor:")
    for family, cls, n in con.execute("""
            SELECT sensor_family, confidence_class, COUNT(*)
            FROM fires_v GROUP BY sensor_family, confidence_class
            ORDER BY sensor_family,
                     CASE confidence_class WHEN 'low' THEN 1 WHEN 'nominal' THEN 2
                                           WHEN 'high' THEN 3 ELSE 4 END
        """).fetchall():
        print(f"   {family:<6} {str(cls):<8} {n:>12,}")

    missing = con.execute(
        "SELECT COUNT(*) FROM fires_v WHERE confidence_class IS NULL").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM fires_v").fetchone()[0]
    print(f"\nRows without a class: {missing:,} (expected 0)")
    print(f"Rows in view: {total:,} (expected 21,167,202)")

    con.close()
    print("\nDONE - view `fires_v` and table `calibration_notes` saved in the database.")


if __name__ == "__main__":
    main()