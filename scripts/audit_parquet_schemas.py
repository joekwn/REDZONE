"""
REDZONE - Full Parquet schema audit.

Reads ONLY the schema and footer metadata of every standardized Parquet file
(no row data is loaded), then reports:
  1. File counts per instrument folder.
  2. Per-file column names, order, and data types vs. the expected 17 columns.
  3. Schema variants within each instrument (does any chunk differ?).
  4. Cross-instrument compatibility (MODIS vs VIIRS).
  5. Row counts from Parquet metadata vs. the known record totals.

This script makes NO changes to any file.
Exit code 0 = no discrepancies found, 1 = discrepancies found.
"""

from collections import Counter, defaultdict
from pathlib import Path
import sys

import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]   # scripts/ -> REDZONE/
STANDARDIZED = PROJECT_ROOT / "data" / "processed" / "standardized"

INSTRUMENTS = {
    # name: (folder, expected file count, expected total rows)
    "MODIS": (STANDARDIZED / "MODIS", 14, 3_386_208),
    "VIIRS_NOAA21": (STANDARDIZED / "VIIRS_NOAA21", 73, 17_780_994),
}

EXPECTED_COLUMNS = [
    "latitude", "longitude", "brightness", "scan", "track",
    "acq_date", "acq_time", "satellite", "instrument", "confidence",
    "version", "bright_t31", "frp", "daynight", "type",
    "data_status", "source_file",
]

# Columns the standardization step was supposed to keep as strings.
EXPECTED_STRING_COLUMNS = ["acq_date", "acq_time", "satellite",
                           "instrument", "confidence"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def schema_signature(schema):
    """Hashable (name, type) tuple. Ignores pandas metadata on purpose."""
    return tuple((field.name, str(field.type)) for field in schema)


def is_string_type(type_str):
    return type_str in ("string", "large_string")


def describe_difference(reference_sig, other_sig):
    """Human-readable lines explaining how other_sig differs from reference_sig."""
    lines = []
    ref_names = [n for n, _ in reference_sig]
    oth_names = [n for n, _ in other_sig]
    ref_types = dict(reference_sig)
    oth_types = dict(other_sig)

    missing = [n for n in ref_names if n not in oth_types]
    extra = [n for n in oth_names if n not in ref_types]
    if missing:
        lines.append(f"missing columns: {missing}")
    if extra:
        lines.append(f"extra columns: {extra}")

    for name in ref_names:
        if name in oth_types and ref_types[name] != oth_types[name]:
            lines.append(f"type of '{name}': {ref_types[name]} -> {oth_types[name]}")

    common_ref = [n for n in ref_names if n in oth_types]
    common_oth = [n for n in oth_names if n in ref_types]
    if common_ref != common_oth:
        lines.append("column ORDER differs")
    return lines


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------
def main():
    problems = []
    reference_by_instrument = {}

    for instrument, (folder, expected_count, expected_rows) in INSTRUMENTS.items():
        print("=" * 78)
        print(f"{instrument}: {folder}")
        print("=" * 78)

        if not folder.is_dir():
            msg = f"[{instrument}] folder not found: {folder}"
            print("  ERROR:", msg)
            problems.append(msg)
            continue

        files = sorted(folder.glob("*.parquet"))
        print(f"Files found: {len(files)} (expected {expected_count})")
        if len(files) != expected_count:
            problems.append(f"[{instrument}] found {len(files)} files, expected {expected_count}")

        sig_to_files = defaultdict(list)
        total_rows = 0

        for path in files:
            try:
                pf = pq.ParquetFile(path)          # opens footer only
                schema = pf.schema_arrow
                rows = pf.metadata.num_rows
            except Exception as exc:
                msg = f"[{instrument}] cannot read {path.name}: {exc}"
                print("  ERROR:", msg)
                problems.append(msg)
                continue

            total_rows += rows
            sig = schema_signature(schema)
            sig_to_files[sig].append(path.name)

            # --- per-file checks against the expected column list ----------
            names = [n for n, _ in sig]
            types = dict(sig)
            file_issues = []
            if names != EXPECTED_COLUMNS:
                if sorted(names) == sorted(EXPECTED_COLUMNS):
                    file_issues.append("column order differs from expected")
                else:
                    miss = [c for c in EXPECTED_COLUMNS if c not in names]
                    extra = [c for c in names if c not in EXPECTED_COLUMNS]
                    file_issues.append(f"missing={miss} extra={extra}")
            for col in EXPECTED_STRING_COLUMNS:
                if col in types and not is_string_type(types[col]):
                    file_issues.append(f"'{col}' is {types[col]}, expected string")
            for col, t in types.items():
                if t == "null":
                    file_issues.append(f"'{col}' has Arrow type null (all values missing, no real type)")

            for issue in file_issues:
                problems.append(f"[{instrument}] {path.name}: {issue}")

        # --- schema variants within this instrument --------------------------
        print(f"Distinct schemas: {len(sig_to_files)}")
        if not sig_to_files:
            continue

        # The most common schema is used as the reference for this instrument.
        ranked = sorted(sig_to_files.items(), key=lambda kv: len(kv[1]), reverse=True)
        reference_sig, ref_files = ranked[0]
        reference_by_instrument[instrument] = reference_sig

        print(f"\nReference schema (shared by {len(ref_files)} file(s)):")
        for i, (name, t) in enumerate(reference_sig, 1):
            print(f"  {i:>2}. {name:<12} {t}")

        for sig, sig_files in ranked[1:]:
            msg = f"[{instrument}] {len(sig_files)} file(s) differ from the reference schema"
            problems.append(msg)
            print(f"\nVARIANT used by {len(sig_files)} file(s):")
            for line in describe_difference(reference_sig, sig):
                print(f"    - {line}")
            for name in sig_files:
                print(f"      {name}")

        # --- row counts from metadata -----------------------------------------
        print(f"\nTotal rows (from metadata): {total_rows:,} (expected {expected_rows:,})")
        if total_rows != expected_rows:
            problems.append(f"[{instrument}] row total {total_rows:,} != expected {expected_rows:,}")
        print()

    # --- cross-instrument comparison ------------------------------------------
    print("=" * 78)
    print("MODIS vs VIIRS_NOAA21 (reference schemas)")
    print("=" * 78)
    if len(reference_by_instrument) == 2:
        diff = describe_difference(reference_by_instrument["MODIS"],
                                   reference_by_instrument["VIIRS_NOAA21"])
        if diff:
            for line in diff:
                print("  -", line)
                problems.append(f"[MODIS vs VIIRS] {line}")
        else:
            print("  Identical column names, order, and types.")
    else:
        print("  Skipped: one of the instruments could not be audited.")

    # --- summary ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    if problems:
        print(f"{len(problems)} discrepancy item(s):")
        for p in problems:
            print("  -", p)
        return 1
    print("No discrepancies found. All files match the expected schema.")
    return 0


if __name__ == "__main__":
    sys.exit(main())