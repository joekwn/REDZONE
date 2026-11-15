"""
REDZONE - the shared rules, written down ONCE.

Every rule here comes from a step we already did and checked:
  - standardize      : strings for acq_date/acq_time/satellite/instrument/confidence,
                       data_status, source_file, `type` empty when missing (NRT)
  - harmonize        : sensor_family, version stored as text
  - quality check    : valid coordinates, dates, times, confidence, day/night
  - cleaning (flags) : frp_flag, frp_usable, polar_flag
  - database         : acq_datetime (UTC)
  - calibration      : confidence_class (MODIS C6.1 User's Guide, Table 11)

The live updater (firms_live.py) and the website both import this file,
so new data is treated exactly like the data we already have.
"""

import pandas as pd

# ---- column layout of the `fires` table (same order as the database) -------
FIRES_COLUMNS = [
    "latitude", "longitude", "brightness", "scan", "track", "acq_date", "acq_time",
    "sensor_family", "satellite", "instrument", "confidence", "version",
    "bright_t31", "frp", "daynight", "type", "data_status", "source_file",
    "frp_flag", "frp_usable", "polar_flag", "acq_datetime",
]
NUMERIC_COLUMNS = ["latitude", "longitude", "brightness", "scan", "track", "bright_t31", "frp"]

# Columns the FIRMS CSV must contain (after renaming, see below)
REQUIRED_INPUT = ["latitude", "longitude", "brightness", "scan", "track", "acq_date",
                  "acq_time", "satellite", "instrument", "confidence", "version",
                  "bright_t31", "frp", "daynight"]

# FIRMS VIIRS files name the two brightness channels differently.
# Our harmonized dataset stores them as brightness / bright_t31.
RENAME = {"bright_ti4": "brightness", "bright_ti5": "bright_t31"}

# ---- values we have seen and checked ------------------------------------------
SATELLITES = {"MODIS": {"Terra", "Aqua"}, "VIIRS": {"N21"}}
SATELLITE_ALIASES = {"T": "Terra", "A": "Aqua", "NOAA-21": "N21", "NOAA21": "N21"}
KNOWN_VERSIONS = {"61.03", "6.1NRT", "2", "2.0NRT"}

# ---- cleaning flag rules --------------------------------------------------------
POLAR_NORTH = 75.0       # our own display cut-off, not a NASA rule
POLAR_SOUTH = -60.0

# ---- shared confidence label (SQL, used by the website) -------------------------
CONF_CLASS_SQL = """
    CASE WHEN sensor_family = 'MODIS' THEN
             CASE WHEN TRY_CAST(confidence AS INT) < 30 THEN 'low'
                  WHEN TRY_CAST(confidence AS INT) < 80 THEN 'nominal'
                  ELSE 'high' END
         ELSE CASE confidence WHEN 'l' THEN 'low' WHEN 'n' THEN 'nominal'
                              WHEN 'h' THEN 'high' END
    END"""


def conf_class(sensor_family, confidence):
    """Shared confidence label in Python (same rule as CONF_CLASS_SQL)."""
    if sensor_family == "MODIS":
        try:
            c = int(float(confidence))
        except (TypeError, ValueError):
            return None
        return "low" if c < 30 else "nominal" if c < 80 else "high"
    return {"l": "low", "n": "nominal", "h": "high"}.get(str(confidence).strip())


def harmonize(raw, sensor_family, data_status, source_file):
    """
    Turn one FIRMS CSV (as a DataFrame of strings) into rows for the `fires` table.

    Returns (good_rows, rejected_rows, warnings).
    Rows that break a hard rule (impossible coordinate, bad date/time, unknown
    confidence...) are NOT inserted; they are returned separately so they can be
    saved and inspected - never silently dropped.
    """
    warnings = []
    df = raw.rename(columns=RENAME).copy()

    missing = [c for c in REQUIRED_INPUT if c not in df.columns]
    if missing:
        raise ValueError(f"FIRMS file is missing columns {missing}; got {list(raw.columns)}")

    # -- standardize: strings stay strings ------------------------------------------
    for col in ["acq_date", "satellite", "instrument", "confidence", "version", "daynight"]:
        df[col] = df[col].astype(str).str.strip()
    df["acq_time"] = df["acq_time"].astype(str).str.strip().str.zfill(4)   # 5 -> 0005
    df["satellite"] = df["satellite"].replace(SATELLITE_ALIASES)
    df["type"] = df["type"].astype(str) if "type" in df.columns else None   # NRT has no type
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # -- harmonize ---------------------------------------------------------------------
    df["sensor_family"] = sensor_family
    df["data_status"] = data_status
    df["source_file"] = source_file
    unknown_versions = sorted(set(df["version"]) - KNOWN_VERSIONS)
    if unknown_versions:
        warnings.append(f"new version label(s) {unknown_versions} - kept as text, please review")

    # -- quality check (same rules as check_data_quality.py) -------------------------
    dates = pd.to_datetime(df["acq_date"], format="%Y-%m-%d", errors="coerce")
    hh = pd.to_numeric(df["acq_time"].str[:2], errors="coerce")
    mm = pd.to_numeric(df["acq_time"].str[2:], errors="coerce")
    if sensor_family == "MODIS":
        conf_num = pd.to_numeric(df["confidence"], errors="coerce")
        conf_ok = conf_num.between(0, 100)
    else:
        conf_ok = df["confidence"].isin(["l", "n", "h"])

    checks = {
        "bad latitude": ~df["latitude"].between(-90, 90),
        "bad longitude": ~df["longitude"].between(-180, 180),
        "bad date": dates.isna(),
        "bad time": ~(df["acq_time"].str.fullmatch(r"\d{4}") & hh.between(0, 23) & mm.between(0, 59)),
        "bad confidence": ~conf_ok,
        "bad day/night": ~df["daynight"].isin(["D", "N"]),
        "unexpected satellite": ~df["satellite"].isin(SATELLITES[sensor_family]),
        "missing FRP": df["frp"].isna(),
    }
    bad = pd.Series(False, index=df.index)
    for name, mask in checks.items():
        mask = mask.fillna(True)
        if mask.any():
            warnings.append(f"{int(mask.sum())} row(s) rejected: {name}")
        bad |= mask

    # -- cleaning flags + timestamp ----------------------------------------------------
    df["frp_flag"] = "ok"
    df.loc[df["frp"] == 0, "frp_flag"] = "zero"
    df.loc[df["frp"] < 0, "frp_flag"] = "negative"
    df["frp_usable"] = df["frp"].where(df["frp"] > 0)
    df["polar_flag"] = (df["latitude"] > POLAR_NORTH) | (df["latitude"] < POLAR_SOUTH)
    df["acq_datetime"] = dates + pd.to_timedelta(hh.fillna(0) * 60 + mm.fillna(0), unit="min")

    good = df.loc[~bad, FIRES_COLUMNS].reset_index(drop=True)
    rejected = raw.loc[bad.to_numpy()].reset_index(drop=True)
    return good, rejected, warnings
