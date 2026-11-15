"""Harmonization rules shared by the research dataset and the live FIRMS updates."""
import pandas as pd

import redzone_rules as rules


def modis_row(**changes):
    row = dict(latitude="35.1", longitude="36.2", brightness="320", scan="1", track="1",
               acq_date="2026-11-10", acq_time="905", satellite="Terra", instrument="MODIS",
               confidence="50", version="6.1NRT", bright_t31="290", frp="12.5", daynight="D")
    row.update(changes)
    return row


def test_confidence_classes():
    assert rules.conf_class("MODIS", "29") == "low"
    assert rules.conf_class("MODIS", "30") == "nominal"
    assert rules.conf_class("MODIS", "79") == "nominal"
    assert rules.conf_class("MODIS", "80") == "high"
    assert rules.conf_class("VIIRS", "l") == "low"
    assert rules.conf_class("VIIRS", "n") == "nominal"
    assert rules.conf_class("VIIRS", "h") == "high"
    assert rules.conf_class("MODIS", "not a number") is None


def test_harmonize_keeps_text_and_builds_utc_time():
    good, rejected, _ = rules.harmonize(pd.DataFrame([modis_row()]), "MODIS", "nrt", "test")
    assert list(good.columns) == rules.FIRES_COLUMNS
    row = good.iloc[0]
    assert row["acq_time"] == "0905"                       # zero-padded text, not a number
    assert str(row["acq_datetime"]) == "2026-11-10 09:05:00"
    assert row["version"] == "6.1NRT"                      # version stays text
    assert row["sensor_family"] == "MODIS" and row["data_status"] == "nrt"
    assert len(rejected) == 0


def test_polar_rows_are_flagged_not_deleted():
    good, _, _ = rules.harmonize(pd.DataFrame([modis_row(latitude="80.5"), modis_row(latitude="-65"),
                                               modis_row()]), "MODIS", "nrt", "test")
    assert len(good) == 3
    assert list(good["polar_flag"]) == [True, True, False]


def test_bad_rows_are_rejected_with_a_reason():
    good, rejected, warnings = rules.harmonize(pd.DataFrame([modis_row(latitude="abc"), modis_row()]),
                                               "MODIS", "nrt", "test")
    assert len(good) == 1 and len(rejected) == 1
    assert any("latitude" in w for w in warnings)


def test_viirs_columns_are_renamed():
    raw = pd.DataFrame([dict(latitude="35", longitude="36", bright_ti4="340", scan="0.4", track="0.4",
                             acq_date="2026-11-10", acq_time="1130", satellite="N21", instrument="VIIRS",
                             confidence="h", version="2.0NRT", bright_ti5="280", frp="5", daynight="D")])
    good, _, _ = rules.harmonize(raw, "VIIRS", "nrt", "test")
    assert good.iloc[0]["brightness"] == 340 and good.iloc[0]["bright_t31"] == 280
