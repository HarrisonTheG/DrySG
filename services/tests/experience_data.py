"""A small synthetic day of NEA history in the archive's page format, for Experience tests."""

from datetime import datetime, timedelta

from drysg.experience.replay import DayData

START = datetime.fromisoformat("2024-11-12T09:00:00+08:00")
HOT_DRY = {"temp": 33.0, "humidity": 55.0, "wind": 6.0}  # scores high
COOL_HUMID = {"temp": 25.0, "humidity": 96.0, "wind": 0.5}  # scores low


def make_day(*, marks: int = 14, rain_from: int | None = None, weather=HOT_DRY, skip=()) -> DayData:
    """One station (S1) beside the only forecast area (Bishan), one reading per 5 minutes.

    Rain starts at mark `rain_from`. Marks in `skip` are missing from the wind feed.
    """
    times = [START + timedelta(minutes=5 * i) for i in range(marks)]
    station = {"id": "S1", "location": {"latitude": 1.35, "longitude": 103.84}}

    def feed(unit: str, value) -> dict:
        return {
            "stations": [station],
            "readingUnit": unit,
            "readings": [
                {"timestamp": t.isoformat(), "data": [{"stationId": "S1", "value": value(i)}]}
                for i, t in enumerate(times)
                if not (unit == "knots" and i in skip)
            ],
        }

    raining = lambda i: 1.2 if rain_from is not None and i >= rain_from else 0.0  # noqa: E731
    forecast = {
        "area_metadata": [
            {"name": "Bishan", "label_location": {"latitude": 1.35, "longitude": 103.84}}
        ],
        "items": [
            {
                "update_timestamp": "2025-05-08T22:09:46+08:00",  # archive re-publication date
                "timestamp": START.isoformat(),
                "valid_period": {
                    "start": START.isoformat(),
                    "end": (START + timedelta(hours=2)).isoformat(),
                    "text": "9 am to 11 am",
                },
                "forecasts": [{"area": "Bishan", "forecast": "Partly Cloudy (Day)"}],
            }
        ],
    }
    psi = {
        "regionMetadata": [
            {"name": "central", "labelLocation": {"latitude": 1.35735, "longitude": 103.82}}
        ],
        "items": [
            {
                "timestamp": START.isoformat(),
                "updatedTimestamp": START.isoformat(),
                "readings": {"psi_twenty_four_hourly": {"central": 55}},
            }
        ],
    }
    return DayData.from_pages(
        {
            "rainfall": [feed("mm", raining)],
            "relative-humidity": [feed("percentage", lambda i: weather["humidity"])],
            "air-temperature": [feed("deg C", lambda i: weather["temp"])],
            "wind-speed": [feed("knots", lambda i: weather["wind"])],
            "two-hr-forecast": [forecast],
            "psi": [psi],
        }
    )
