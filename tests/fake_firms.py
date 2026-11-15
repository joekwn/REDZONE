"""A stand-in for the FIRMS API, so tests never need a key or the internet."""
import random

MODIS_HEADER = "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight"
VIIRS_HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"


def fake_fetch(url, **kwargs):
    rnd = random.Random(url)                       # same URL -> same rows
    source, day = url.split("/")[7], url.split("/")[-1]
    if source.startswith("MODIS"):
        rows = [MODIS_HEADER] + [
            f"{rnd.uniform(30, 37):.4f},{rnd.uniform(33, 40):.4f},320.1,1.0,1.0,{day},"
            f"{rnd.randint(0, 23):02d}{rnd.randint(0, 59):02d},Aqua,MODIS,{rnd.randint(0, 100)},6.1NRT,290.2,"
            f"{rnd.uniform(1, 90):.1f},D" for _ in range(40)]
    else:
        rows = [VIIRS_HEADER] + [
            f"{rnd.uniform(30, 37):.4f},{rnd.uniform(33, 40):.4f},340.0,0.4,0.4,{day},"
            f"{rnd.randint(0, 23):02d}{rnd.randint(0, 59):02d},N21,VIIRS,{rnd.choice('lnh')},2.0NRT,280.0,"
            f"{rnd.uniform(0.5, 60):.1f},N" for _ in range(150)]
    return "\n".join(rows) + "\n"
