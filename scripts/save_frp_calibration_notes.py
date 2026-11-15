"""
REDZONE - Record the FRP calibration RESULT in the database.

The test showed that no conversion formula beats "no conversion", so we do NOT
add a converted FRP column. Instead we store what we learned in
`calibration_notes`, so the website's "About the data" page can show it.
"""

from pathlib import Path
import sys

import duckdb

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "redzone.duckdb"

NOTES = [
    ("frp_conversion", "MODIS vs VIIRS",
     "Not applied. No formula (OLS, RMA, Deming, constant ratio) beat 'no conversion' "
     "on held-out data (typical error about x3 per 0.25 deg cell).",
     "REDZONE test: 0.25 deg cells, daytime, passes <= 10 min apart, alternate-week split"),
    ("frp_agreement", "MODIS vs VIIRS",
     "On average the two sensors' cell totals agree within about 10 percent, but a "
     "single cell can differ by about a factor of 3.",
     "Same test, 474 test cells"),
    ("frp_region_effect", "MODIS vs VIIRS",
     "Agreement depends on region: in the test, VIIRS reported less FRP than MODIS "
     "in the tropics and mid-latitudes, and slightly more above 50 deg N. "
     "Tropical result is based on only 28 cells.",
     "Same test, bias by latitude band"),
]


def main():
    if not DB_PATH.exists():
        sys.exit(f"STOP: database not found: {DB_PATH}")
    con = duckdb.connect(str(DB_PATH))
    con.execute("""CREATE TABLE IF NOT EXISTS calibration_notes (
                       item VARCHAR, sensor VARCHAR, rule VARCHAR, source VARCHAR)""")
    con.execute("DELETE FROM calibration_notes WHERE item LIKE 'frp_%'")   # safe to re-run
    con.executemany("INSERT INTO calibration_notes VALUES (?, ?, ?, ?)", NOTES)

    for item, sensor, rule, source in con.execute(
            "SELECT * FROM calibration_notes ORDER BY item").fetchall():
        print(f"[{item}] ({sensor})\n   {rule}\n   source: {source}\n")
    con.close()
    print("DONE - notes saved in table `calibration_notes`.")


if __name__ == "__main__":
    main()