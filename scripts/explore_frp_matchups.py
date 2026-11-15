"""
REDZONE - FRP calibration, step 2: explore the matched cells.

Reads table `frp_cell_matchups` (only ~8,000 rows, so pandas is fine) and:
  1. Prints statistics for different subsets, to see WHAT weakens the relationship
     (time gap between passes, MODIS edge-of-scan pixels, small fires, day/night).
  2. Saves scatter plots (log10 FRP) to data/processed/plots/.
Nothing in the database is changed.
"""

from pathlib import Path
import argparse
import sys

import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")                 # save images without opening windows
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "redzone.duckdb"
PLOT_DIR = PROJECT_ROOT / "data" / "processed" / "plots"


def stats(df):
    """n, correlation, OLS slope, RMA slope, median VIIRS/MODIS ratio (log10 space)."""
    if len(df) < 30:
        return len(df), np.nan, np.nan, np.nan, np.nan
    x, y = np.log10(df["modis_frp"]), np.log10(df["viirs_frp"])
    r = np.corrcoef(x, y)[0, 1]
    ols = r * y.std() / x.std()
    rma = np.sign(r) * y.std() / x.std()
    ratio = np.median(10 ** (y - x))
    return len(df), r, ols, rma, ratio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default="frp_cell_matchups", help="table to explore")
    table = parser.parse_args().table
    print(f"Table: {table}\n")

    if not DB_PATH.exists():
        sys.exit(f"STOP: database not found: {DB_PATH}")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    df = con.execute(f"SELECT * FROM {table}").df()
    con.close()

    day = df[df["daynight"] == "D"]
    subsets = {
        "ALL (day + night)":                 df,
        "Day only":                          day,
        "Night only":                        df[df["daynight"] == "N"],
        "Day, gap <= 10 min":                day[day["minutes_apart"] <= 10],
        "Day, gap <= 5 min":                 day[day["minutes_apart"] <= 5],
        "Day, gap 20-30 min":                day[day["minutes_apart"] > 20],
        "Day, MODIS near nadir (scan<=1.5)": day[day["modis_max_scan"] <= 1.5],
        "Day, gap<=10 AND scan<=1.5":        day[(day["minutes_apart"] <= 10) & (day["modis_max_scan"] <= 1.5)],
        "Day, >= 3 MODIS detections":        day[day["modis_n"] >= 3],
        "Day, Aqua":                         day[day["modis_satellite"] == "Aqua"],
        "Day, Terra":                        day[day["modis_satellite"] == "Terra"],
    }

    print(f"{'subset':<36}{'n':>7}{'corr':>8}{'OLS':>8}{'RMA':>8}{'V/M':>7}")
    print("-" * 74)
    for name, sub in subsets.items():
        n, r, ols, rma, ratio = stats(sub)
        print(f"{name:<36}{n:>7,}{r:>8.3f}{ols:>8.3f}{rma:>8.3f}{ratio:>7.2f}")
    print("(subsets with fewer than 30 cells show nan)")

    # Where do the Aqua and Terra pairs come from? (to check if region explains the difference)
    print("\nWhere the daytime pairs are (by MODIS satellite):")
    print(f"{'satellite':<10}{'n':>7}{'median |lat|':>14}{'% tropics':>11}{'% above 50':>12}{'median gap':>12}")
    for sat, sub in day.groupby("modis_satellite"):
        alat = sub["cell_lat"].abs()
        print(f"{sat:<10}{len(sub):>7,}{alat.median():>14.1f}"
              f"{(alat <= 23.5).mean() * 100:>10.0f}%{(alat > 50).mean() * 100:>11.0f}%"
              f"{sub['minutes_apart'].median():>11.1f}m")

    # ---- plots ---------------------------------------------------------------
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5), sharex=True, sharey=True)
    panels = [("Day - all time gaps", day),
              ("Day - gap <= 10 min", day[day["minutes_apart"] <= 10])]
    for ax, (title, sub) in zip(axes, panels):
        x, y = np.log10(sub["modis_frp"]), np.log10(sub["viirs_frp"])
        sc = ax.scatter(x, y, c=sub["minutes_apart"], cmap="viridis",
                        s=6, alpha=0.5, vmin=0, vmax=30)
        lo, hi = min(x.min(), y.min()), max(x.max(), y.max())
        ax.plot([lo, hi], [lo, hi], "k--", lw=1, label="1:1 line")
        n, r, ols, rma, ratio = stats(sub)
        ax.set_title(f"{title}\nn={n:,}  r={r:.2f}  RMA slope={rma:.2f}")
        ax.set_xlabel("log10 MODIS total FRP per cell (MW)")
        ax.legend(loc="upper left")
    axes[0].set_ylabel("log10 VIIRS total FRP per cell (MW)")
    fig.colorbar(sc, ax=axes, label="minutes between passes")
    out = PLOT_DIR / f"{table}_scatter.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    print(f"\nPlot saved: {out}")


if __name__ == "__main__":
    main()