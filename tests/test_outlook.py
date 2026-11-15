"""Fire-weather outlook (Fosberg index) rules."""
import outlook


def test_ffwi_rises_with_heat_dryness_and_wind():
    mild = outlook.ffwi(20, 60, 10)
    assert outlook.ffwi(35, 60, 10) >= mild
    assert outlook.ffwi(20, 15, 10) > mild
    assert outlook.ffwi(20, 60, 40) > mild
    assert outlook.ffwi(None, 60, 10) is None


def test_weather_level_bands():
    assert [outlook.weather_level(v) for v in (0, 19.9, 20, 34.9, 35, 49.9, 50)] == [0, 0, 1, 1, 2, 2, 3]
    assert outlook.weather_level(None) is None


def test_rain_lowers_and_fire_history_raises():
    quiet = {"season": 0, "recent": 0}
    busy = {"season": 12, "recent": 0}
    assert outlook.combine(2, 0, quiet)[0] == 2
    assert outlook.combine(2, 3.0, quiet)[0] == 1          # 2 mm or more of rain: one level down
    assert outlook.combine(2, 0, busy)[0] == 3             # fire-prone place: one level up
    assert outlook.combine(3, 0, busy)[0] == 3             # never above "very high"
    assert outlook.combine(None, 0, quiet)[0] is None
