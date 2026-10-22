"""Unit tests for Open-Meteo Weather Service."""
from betting_engine.weather_service import WeatherService, _classify_wind_bearing


def test_wind_bearing_classification():
    # Outfield azimuth = 45 deg (NE).
    # Wind blowing FROM 225 deg (SW) blows TO 45 deg (NE) -> straight into outfield
    assert _classify_wind_bearing(225, 45) == "outfield"
    # Wind blowing FROM 45 deg blows TO 225 deg -> straight into home plate (infield)
    assert _classify_wind_bearing(45, 45) == "infield"
    # Crosswind
    assert _classify_wind_bearing(135, 45) == "crosswind"


def test_weather_service_dome_and_unlisted():
    ws = WeatherService()
    indoor = ws.get_venue_weather("NBA", "Los Angeles Lakers")
    assert indoor["is_dome"] is True
    assert indoor["temperature_c"] == 21.0
    assert indoor["wind_speed_mph"] == 0.0


def test_weather_service_outdoor_caching():
    ws = WeatherService()
    res1 = ws.get_venue_weather("MLB", "Chicago Cubs")
    assert "temperature_c" in res1
    assert "wind_speed_mph" in res1
    assert "wind_bearing" in res1
    assert res1["is_dome"] is False


def test_weather_point_in_time_sync():
    from datetime import datetime, timezone, timedelta
    ws = WeatherService()
    # Query with future timestamp within 24 hours
    future_dt = (datetime.now(timezone.utc) + timedelta(hours=12)).replace(minute=0, second=0, microsecond=0)
    game_time = future_dt.strftime("%Y-%m-%dT%H:00:00Z")
    res = ws.get_venue_weather("MLB", "Chicago Cubs", game_time)
    assert res["time_synchronized"] is True
    assert res["time_sync_mode"] == "hourly_point_in_time"
    assert future_dt.strftime("%Y-%m-%dT%H:00") in res["forecast_hour_utc"]
    assert res["temperature_c"] > -40.0
    assert res["wind_speed_mph"] >= 0.0


