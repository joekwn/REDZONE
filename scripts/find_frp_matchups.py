"""
REDZONE - FRP calibration, step 1: matched grid cells.

FRP adds up over area, so single MODIS and VIIRS pixels cannot be compared.
Instead:
  1. Divide the world into grid cells of CELL_DEG degrees (~11 km).
  2. For each sensor, group detections into "overpasses":
     same cell + same date + same satellite + same day/night.
  3. Pair a MODIS overpass with a VIIRS overpass of the same cell when they
     are within MAX_MINUTES of each other.
  4. Compare the TOTAL usable FRP of each sensor in that cell.

Only `frp_usable` is used (zero and negative FRP are left out).
Saves table `frp_cell_matchups` (or the name given with --table). Nothing in `fires` is changed.

Limitation: we only see cells where BOTH sensors detected fire. If one sensor
saw nothing (clouds, or fire too small), that cell is not in the comparison.
"""

from pathlib import Path
import argparse
import sys

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "redzone.duckdb"

CELL_DEG = 0.1        # grid cell size in degrees (~11 km at the equator)
MAX_MINUTES = 30      # max time between the two overpasses


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cell", type=float, default=CELL_DEG, help="grid cell size in degrees")
    parser.add_argument("--table", default="frp_cell_matchups", help="name of the output table")
    args = parser.parse_args()
    cell, table = args.cell, args.table
    print(f"Cell size: {cell} degrees  ->  table `{table}`")

    if not DB_PATH.exists():
        sys.exit(f"STOP: database not found: {DB_PATH}")

    con = duckdb.connect(str(DB_PATH))
    con.execute("SET memory_limit = '4GB'")
    con.execute("SET preserve_insertion_order = false")

    print("Building matched grid cells (can take a few minutes)...")
    con.execute(f"""
        CREATE OR REPLACE TABLE {table} AS
        WITH cells AS (
            SELECT sensor_family, satellite, daynight, acq_date, acq_datetime,
                   frp_usable, scan,
                   floor(latitude  / {cell})::INT AS cy,
                   floor(longitude / {cell})::INT AS cx
            FROM fires
        ),
        overpass AS (
            SELECT sensor_family, satellite, daynight, acq_date, cy, cx,
                   to_timestamp(AVG(epoch(acq_datetime))) AS pass_time,
                   COUNT(*)                     AS n_detections,
                   COUNT(frp_usable)            AS n_frp,
                   SUM(frp_usable)              AS frp_total,
                   MAX(scan)                    AS max_scan
            FROM cells
            GROUP BY ALL
        ),
        m AS (SELECT * FROM overpass WHERE sensor_family = 'MODIS'),
        v AS (SELECT * FROM overpass WHERE sensor_family = 'VIIRS')
        SELECT m.acq_date, m.daynight, m.satellite AS modis_satellite,
               m.cy, m.cx,
               (m.cy + 0.5) * {cell} AS cell_lat,
               (m.cx + 0.5) * {cell} AS cell_lon,
               abs(epoch(v.pass_time) - epoch(m.pass_time)) / 60.0 AS minutes_apart,
               m.n_detections AS modis_n, v.n_detections AS viirs_n,
               m.frp_total AS modis_frp, v.frp_total AS viirs_frp,
               m.max_scan AS modis_max_scan
        FROM m JOIN v
          ON m.acq_date = v.acq_date AND m.daynight = v.daynight
         AND m.cy = v.cy AND m.cx = v.cx
        WHERE abs(epoch(v.pass_time) - epoch(m.pass_time)) <= {MAX_MINUTES} * 60
          AND m.frp_total > 0 AND v.frp_total > 0
    """)

    # ---- report ------------------------------------------------------------
    n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"\nMatched cells: {n:,}")
    if n == 0:
        print("No matched cells - FRP regression is not possible with these limits.")
        return

    print("\nBy MODIS satellite and day/night:")
    for row in con.execute(f"""
            SELECT modis_satellite, daynight, COUNT(*) FROM {table}
            GROUP BY ALL ORDER BY ALL""").fetchall():
        print(f"   {row[0]:<6} {row[1]}  cells={row[2]:>9,}")

    print("\nTime gap between overpasses:")
    for row in con.execute(f"""
            SELECT CASE WHEN minutes_apart <= 5  THEN '0-5 min'
                        WHEN minutes_apart <= 10 THEN '5-10 min'
                        WHEN minutes_apart <= 20 THEN '10-20 min'
                        ELSE '20-30 min' END, COUNT(*)
            FROM {table} GROUP BY 1 ORDER BY MIN(minutes_apart)""").fetchall():
        print(f"   {row[0]:<10} {row[1]:>9,}")

    print("\nFirst look (log10 of total FRP per cell):")
    print("   x = MODIS, y = VIIRS.  RMA slope treats both sides as having error.")
    for row in con.execute(f"""
            WITH d AS (SELECT daynight, log10(modis_frp) AS x, log10(viirs_frp) AS y
                       FROM {table})
            SELECT daynight, COUNT(*),
                   corr(x, y),
                   regr_slope(y, x),                          -- ordinary regression
                   sign(corr(x, y)) * stddev(y) / stddev(x),  -- RMA slope
                   median(power(10, y - x))                   -- typical VIIRS/MODIS ratio
            FROM d GROUP BY daynight ORDER BY daynight""").fetchall():
        dn, cnt, r, ols, rma, ratio = row
        print(f"   {dn}  n={cnt:>8,}  correlation={r:.3f}  "
              f"OLS slope={ols:.3f}  RMA slope={rma:.3f}  median VIIRS/MODIS={ratio:.2f}")

    con.close()
    print(f"\nDONE - saved as table `{table}`.")


if __name__ == "__main__":
    main()