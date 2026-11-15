# Data and methods

This page explains what REDZONE does to the satellite data, and what it deliberately does **not** do.
The guiding rule: **flag problems instead of deleting rows, and never merge or convert between
sensors without evidence.**

## 1. Data

| Sensor family | Product | Resolution | Source |
|---|---|---|---|
| MODIS (Terra + Aqua) | Collection 6.1 active fire, MCD14DL (near real-time) and MCD14ML (standard) | 1 km at nadir | NASA FIRMS |
| VIIRS (NOAA-21) | 375 m I-band active fire, VJ214IMGTDL_NRT | 375 m | NASA FIRMS |

**Research set:** 1 November 2025 to 1 November 2026, **21,167,202 rows**. All rules below were
developed and tested on this set. Data added later (live updates, past-data collection, look-ups)
goes through the same code (`scripts/redzone_rules.py`), but has not been re-validated as a whole.

**Live data:** FIRMS area API, world, 1-day requests, every 3 hours. FIRMS has no standard
(non-NRT) product for VIIRS NOAA-21, so all VIIRS rows are NRT. For MODIS, standard (`MODIS_SP`)
data replaces NRT rows when it becomes available (usually after about two months).

## 2. Harmonization (one table, two sensors)

Both sensors go into one `fires` table **without changing what the values mean**:

- **Column names:** VIIRS `bright_ti4` → `brightness`, `bright_ti5` → `bright_t31`, so both
  sensors share column names. The values are still each sensor's own channels; they are **not**
  comparable between sensors.
- **`sensor_family`:** `MODIS` or `VIIRS` on every row, so the sensors can always be separated.
- **Text stays text:** `acq_time` is zero-padded text (`905` → `0905`); `version` is text
  (`6.1NRT`, `2.0NRT`), never a number.
- **`acq_datetime`:** built from `acq_date` + `acq_time`, in **UTC**.
- **`type`:** empty for NRT data (FIRMS only provides it in standard data).
- **`data_status`:** `nrt` or `archive` (standard).

## 3. Quality flags (nothing silently deleted)

| Flag | Meaning |
|---|---|
| `frp_flag` | `ok`, `zero` or `negative`: whether the fire radiative power value is physically usable |
| `frp_usable` | the FRP value when `frp_flag = ok`, otherwise empty, so statistics never use bad values |
| `polar_flag` | `true` north of 75° N or south of 60° S. Our own cut-off (not a NASA rule) to keep high-latitude detections separate in maps and statistics. Hidden by default on the map, never deleted |

Rows that cannot be stored at all (for example a latitude that is not a number) are rejected,
**saved to a "rejected" file with the reason**, and counted in the logs.

Duplicates: exact duplicates were checked in the research set; live updates only insert a
detection if the same detection is not already stored.

## 4. Confidence classes

The two sensors report confidence differently. REDZONE shows a shared label but keeps the original value:

| Sensor | Low | Nominal | High |
|---|---|---|---|
| MODIS (0-100 %) | below 30 | 30 to 79 | 80 and above |
| VIIRS (letter) | `l` | `n` | `h` |

The same rule exists in SQL and Python (`CONF_CLASS_SQL`, `conf_class`), and a test checks they agree.

## 5. FRP cross-sensor calibration: tested, **not applied**

**Question:** can VIIRS fire radiative power (FRP) be converted to MODIS-equivalent FRP (or the
reverse), so the two can be added together?

**Method:**
1. Matchups: MODIS and VIIRS detections in the same grid cell (0.1° and 0.25°) close in time.
2. Regression fits: ordinary least squares, reduced major axis and Deming regression.
3. Validation: fit on alternating weeks, test on the other weeks (so the fit is not judged on its own data).

**Result:** the relationship was not stable or accurate enough to justify a conversion.
The pixel sizes (375 m vs 1 km), overpass times and detection sensitivity differ too much for a
single formula. **REDZONE therefore never converts or sums FRP across sensors**; statistics
are always shown per sensor family. The detailed numbers are produced by
`scripts/fit_frp_calibration.py` and `scripts/save_frp_calibration_notes.py`.

Brightness temperatures are also not converted (different channels).

## 6. Fire-weather outlook

A 7-day outlook for a place, from:

1. **Fosberg Fire Weather Index (FFWI)** from Open-Meteo hourly temperature, humidity and wind.
   Daily maximum, banded: below 20 low, 20-35 moderate, 35-50 high, 50+ very high.
2. **Rain:** 2 mm or more that day lowers the level by one.
3. **Fire history:** 10+ detections nearby in the same season last year, or 5+ in the last 14 days
   of data, raises the level by one.

This is a simple, transparent rule set for awareness. **It is not a validated fire-danger forecast**
and not a replacement for official fire-danger ratings.

## 7. Known limitations

- Active-fire detections are thermal anomalies: some are not vegetation fires (industry, gas flares,
  volcanoes, hot surfaces). Clouds and smoke hide fires; small or short fires are missed.
- NRT data can have geolocation errors and is reprocessed later.
- Satellite overpasses are a few times a day; a fire can start and end between them.
- Polar, FRP and confidence rules were tuned on one year of data.

## Data citations

- MODIS C6.1 NRT (MCD14DL), NASA FIRMS. doi:10.5067/FIRMS/MODIS/MCD14DL.NRT.0061
- MODIS C6.1 standard (MCD14ML), NASA FIRMS. doi:10.5067/FIRMS/MODIS/MCD14ML
- VIIRS NOAA-21 375 m NRT (VJ214IMGTDL_NRT), NASA FIRMS. doi:10.5067/VIIRS/VJ214IMG_NRT.002
- Giglio, L., Schroeder, W., & Justice, C.O. (2016). The collection 6 MODIS active fire detection
  algorithm and fire products. *Remote Sensing of Environment*, 178, 31-41. doi:10.1016/j.rse.2016.02.054
- Schroeder, W., Oliva, P., Giglio, L., & Csiszar, I. A. (2014). The New VIIRS 375 m active fire
  detection data product: algorithm description and initial assessment. *Remote Sensing of
  Environment*, 143, 85-96. doi:10.1016/j.rse.2013.12.008
- Fosberg, M. A. (1978). Weather in wildland fire management: the fire weather index.
  Proceedings of the Conference on Sierra Nevada Meteorology.
