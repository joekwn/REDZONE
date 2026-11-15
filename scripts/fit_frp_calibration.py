"""
REDZONE - FRP calibration, steps 3 + 4: fit formulas AND test them on unseen months.

Model (log10 space):   log10(VIIRS_FRP) = a + b * log10(MODIS_FRP)

Uses only the cleanest matched cells (by default: daytime, passes <= 10 min apart).
Split modes:  --split-mode date  : train before --split-date, test after
              --split-mode weeks : train on even weeks, test on odd weeks
The formulas never see the test data while being fitted.

Compared on the test data:
  - no calibration     : VIIRS = MODIS                    (baseline)
  - constant ratio     : VIIRS = ratio * MODIS            (simple baseline)
  - OLS                : ordinary least squares
  - RMA                : reduced major axis (error on both sides)
  - Orthogonal/Deming  : Deming regression with equal error on both sides
Nothing in the database is changed.
"""

from pathlib import Path
import argparse
import sys

import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "redzone.duckdb"
PLOT_DIR = PROJECT_ROOT / "data" / "processed" / "plots"


def fit_models(x, y):
    """Return {name: (a, b)} for every model, fitted on x, y (log10 values)."""
    mx, my = x.mean(), y.mean()
    sxx, syy = np.var(x, ddof=1), np.var(y, ddof=1)
    sxy = np.cov(x, y, ddof=1)[0, 1]
    r = sxy / np.sqrt(sxx * syy)

    b_ols = sxy / sxx
    b_rma = np.sign(r) * np.sqrt(syy / sxx)
    lam = 1.0   # assumed ratio of error variances (y / x) in log space
    b_dem = (syy - lam * sxx + np.sqrt((syy - lam * sxx) ** 2 + 4 * lam * sxy ** 2)) / (2 * sxy)

    return {
        "no calibration (1:1)": (0.0, 1.0),
        "constant ratio":       (np.median(y - x), 1.0),
        "OLS":                  (my - b_ols * mx, b_ols),
        "RMA":                  (my - b_rma * mx, b_rma),
        "Orthogonal (Deming)":  (my - b_dem * mx, b_dem),
    }


def scores(x, y, a, b):
    pred = a + b * x
    err = pred - y
    rmse = np.sqrt(np.mean(err ** 2))
    r2 = 1 - np.sum(err ** 2) / np.sum((y - y.mean()) ** 2)
    return rmse, err.mean(), r2


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--table", default="frp_cell_matchups_025")
    p.add_argument("--max-gap", type=float, default=10, help="max minutes between passes")
    p.add_argument("--split-date", default="2026-07-01", help="train before, test on/after")
    p.add_argument("--daynight", default="D", help="D, N, or ALL")
    p.add_argument("--split-mode", default="date", choices=["date", "weeks"],
                   help="date = train before split-date, test after; "
                        "weeks = alternate weeks (both sets cover all seasons and regions)")
    args = p.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"STOP: database not found: {DB_PATH}")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    df = con.execute(f"SELECT * FROM {args.table}").df()
    con.close()

    df = df[df["minutes_apart"] <= args.max_gap]
    if args.daynight != "ALL":
        df = df[df["daynight"] == args.daynight]
    df = df.assign(x=np.log10(df["modis_frp"]), y=np.log10(df["viirs_frp"]),
                   date=df["acq_date"].astype(str))
    if args.split_mode == "date":
        is_test = df["date"] >= args.split_date
        label_train, label_test = f"before {args.split_date}", f"from {args.split_date}"
    else:
        week = (np.asarray(df["acq_date"], dtype="datetime64[D]").astype("int64") // 7)
        is_test = (week % 2 == 1)
        label_train, label_test = "even weeks", "odd weeks"
    train, test = df[~is_test], df[is_test]

    print(f"Table={args.table}  day/night={args.daynight}  max gap={args.max_gap} min  split={args.split_mode}")
    print(f"Training cells ({label_train}): {len(train):,}")
    print(f"Test cells     ({label_test}): {len(test):,}")
    if len(train) < 100 or len(test) < 50:
        sys.exit("STOP: too few cells to fit and test reliably.")

    models = fit_models(train["x"].to_numpy(), train["y"].to_numpy())

    print("\nFormulas (fitted on training data):   log10(VIIRS) = a + b * log10(MODIS)")
    print(f"{'model':<24}{'a':>8}{'b':>8}")
    for name, (a, b) in models.items():
        print(f"{name:<24}{a:>8.3f}{b:>8.3f}")

    print("\nScores on TEST data (never seen during fitting):")
    print(f"{'model':<24}{'RMSE':>8}{'x factor':>10}{'bias':>8}{'R2':>8}")
    for name, (a, b) in models.items():
        rmse, bias, r2 = scores(test["x"].to_numpy(), test["y"].to_numpy(), a, b)
        print(f"{name:<24}{rmse:>8.3f}{10 ** rmse:>10.2f}{bias:>8.3f}{r2:>8.3f}")
    print("RMSE is in log10 units; 'x factor' = typical error as a multiplication factor"
          " (e.g. 2.0 = typically off by 2x).")

    # Does the error depend on region? (checked with the OLS and RMA formulas)
    print("\nTest error by latitude band (bias = average of predicted - observed):")
    bands = [("tropics |lat|<=23.5", test["cell_lat"].abs() <= 23.5),
             ("23.5 < |lat| <= 50", (test["cell_lat"].abs() > 23.5) & (test["cell_lat"].abs() <= 50)),
             ("|lat| > 50", test["cell_lat"].abs() > 50)]
    print(f"{'band':<22}{'n':>6}{'OLS bias':>10}{'RMA bias':>10}")
    for label, mask in bands:
        sub = test[mask]
        if len(sub) < 20:
            print(f"{label:<22}{len(sub):>6}   too few")
            continue
        _, b_ols, _ = scores(sub["x"].to_numpy(), sub["y"].to_numpy(), *models["OLS"])
        _, b_rma, _ = scores(sub["x"].to_numpy(), sub["y"].to_numpy(), *models["RMA"])
        print(f"{label:<22}{len(sub):>6}{b_ols:>10.3f}{b_rma:>10.3f}")

    # ---- plot -----------------------------------------------------------------
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.5, 6))
    ax.scatter(train["x"], train["y"], s=6, alpha=0.3, c="grey", label="training cells")
    ax.scatter(test["x"], test["y"], s=8, alpha=0.6, c="tab:red", label="test cells")
    xs = np.linspace(df["x"].min(), df["x"].max(), 50)
    for name, style in [("no calibration (1:1)", "k--"), ("OLS", "b-"), ("RMA", "g-")]:
        a, b = models[name]
        ax.plot(xs, a + b * xs, style, lw=1.5, label=f"{name}: b={b:.2f}")
    ax.set_xlabel("log10 MODIS total FRP per cell (MW)")
    ax.set_ylabel("log10 VIIRS total FRP per cell (MW)")
    ax.set_title(f"FRP calibration fit and test ({args.table}, gap <= {args.max_gap:g} min)")
    ax.legend(fontsize=8, loc="upper left")
    out = PLOT_DIR / f"frp_fit_{args.table}_{args.split_mode}.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    print(f"\nPlot saved: {out}")


if __name__ == "__main__":
    main()