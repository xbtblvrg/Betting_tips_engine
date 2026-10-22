"""Open-Meteo Live Stadium Weather Ingestion and Context Service.

Provides real-time, zero-credential, open-access meteorological data
(temperature, wind speed, wind direction, precipitation) for outdoor MLB
and NFL stadiums to quantify weather impact on Totals and Spreads.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import urllib.request
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache/weather")
CACHE_TTL_SECONDS = 3600  # 1 hour

# Outdoor MLB stadiums with home plate -> center field compass orientation
MLB_STADIUMS: Dict[str, Dict[str, Any]] = {
    "Chicago Cubs": {"lat": 41.9484, "lon": -87.6553, "outfield_azimuth": 45, "is_dome": False},
    "Boston Red Sox": {"lat": 42.3467, "lon": -71.0972, "outfield_azimuth": 45, "is_dome": False},
    "New York Yankees": {"lat": 40.8296, "lon": -73.9262, "outfield_azimuth": 65, "is_dome": False},
    "Colorado Rockies": {"lat": 39.7559, "lon": -104.9942, "outfield_azimuth": 0, "is_dome": False},
    "Los Angeles Dodgers": {"lat": 34.0739, "lon": -118.2400, "outfield_azimuth": 25, "is_dome": False},
    "San Francisco Giants": {"lat": 37.7786, "lon": -122.3893, "outfield_azimuth": 90, "is_dome": False},
    "Atlanta Braves": {"lat": 33.8908, "lon": -84.4678, "outfield_azimuth": 35, "is_dome": False},
    "New York Mets": {"lat": 40.7571, "lon": -73.8458, "outfield_azimuth": 40, "is_dome": False},
    "Philadelphia Phillies": {"lat": 39.9061, "lon": -75.1665, "outfield_azimuth": 10, "is_dome": False},
    "St. Louis Cardinals": {"lat": 38.6226, "lon": -90.1928, "outfield_azimuth": 60, "is_dome": False},
    "Cincinnati Reds": {"lat": 39.0979, "lon": -84.5082, "outfield_azimuth": 115, "is_dome": False},
    "Pittsburgh Pirates": {"lat": 40.4469, "lon": -80.0057, "outfield_azimuth": 110, "is_dome": False},
    "Detroit Tigers": {"lat": 42.3390, "lon": -83.0485, "outfield_azimuth": 135, "is_dome": False},
    "Cleveland Guardians": {"lat": 41.4962, "lon": -81.6852, "outfield_azimuth": 0, "is_dome": False},
    "Minnesota Twins": {"lat": 44.9817, "lon": -93.2778, "outfield_azimuth": 65, "is_dome": False},
    "Kansas City Royals": {"lat": 39.0517, "lon": -94.4803, "outfield_azimuth": 45, "is_dome": False},
    "Chicago White Sox": {"lat": 41.8299, "lon": -87.6338, "outfield_azimuth": 135, "is_dome": False},
    "Baltimore Orioles": {"lat": 39.2840, "lon": -76.6215, "outfield_azimuth": 20, "is_dome": False},
    "Washington Nationals": {"lat": 38.8730, "lon": -77.0074, "outfield_azimuth": 25, "is_dome": False},
    "San Diego Padres": {"lat": 32.7073, "lon": -117.1566, "outfield_azimuth": 15, "is_dome": False},
    "Los Angeles Angels": {"lat": 33.8003, "lon": -117.8827, "outfield_azimuth": 45, "is_dome": False},
    "Oakland Athletics": {"lat": 37.7516, "lon": -122.2005, "outfield_azimuth": 65, "is_dome": False},
}

# Outdoor NFL stadiums
NFL_STADIUMS: Dict[str, Dict[str, Any]] = {
    "Green Bay Packers": {"lat": 44.5013, "lon": -88.0622, "is_dome": False},
    "Chicago Bears": {"lat": 41.8623, "lon": -87.6167, "is_dome": False},
    "Buffalo Bills": {"lat": 42.7738, "lon": -78.7870, "is_dome": False},
    "Kansas City Chiefs": {"lat": 39.0489, "lon": -94.4839, "is_dome": False},
    "New England Patriots": {"lat": 42.0909, "lon": -71.2643, "is_dome": False},
    "Pittsburgh Steelers": {"lat": 40.4468, "lon": -80.0158, "is_dome": False},
    "Cleveland Browns": {"lat": 41.5061, "lon": -81.6995, "is_dome": False},
    "New York Giants": {"lat": 40.8128, "lon": -74.0742, "is_dome": False},
    "New York Jets": {"lat": 40.8128, "lon": -74.0742, "is_dome": False},
    "Philadelphia Eagles": {"lat": 39.9008, "lon": -75.1675, "is_dome": False},
    "Baltimore Ravens": {"lat": 39.2780, "lon": -76.6227, "is_dome": False},
    "Cincinnati Bengals": {"lat": 39.0954, "lon": -84.5161, "is_dome": False},
    "Tennessee Titans": {"lat": 36.1665, "lon": -86.7713, "is_dome": False},
    "Denver Broncos": {"lat": 39.7439, "lon": -105.0201, "is_dome": False},
    "Seattle Seahawks": {"lat": 47.5952, "lon": -122.3316, "is_dome": False},
    "San Francisco 49ers": {"lat": 37.4032, "lon": -121.9698, "is_dome": False},
    "Tampa Bay Buccaneers": {"lat": 27.9759, "lon": -82.5033, "is_dome": False},
    "Miami Dolphins": {"lat": 25.9580, "lon": -80.2389, "is_dome": False},
    "Carolina Panthers": {"lat": 35.2258, "lon": -80.8528, "is_dome": False},
    "Washington Commanders": {"lat": 38.9076, "lon": -76.8645, "is_dome": False},
}


def _classify_wind_bearing(wind_deg: float, outfield_azimuth: Optional[float]) -> str:
    """Classifies wind direction relative to baseball outfield or football axis.
    
    wind_deg: meteorological direction wind is blowing FROM (0=North, 90=East, etc.)
    outfield_azimuth: direction from home plate to center field
    """
    if outfield_azimuth is None:
        return "neutral"
    # Direction wind is blowing TO:
    wind_to = (wind_deg + 180) % 360
    diff = abs(wind_to - outfield_azimuth)
    if diff > 180:
        diff = 360 - diff
    
    if diff <= 45:
        return "outfield"  # blowing out towards center field
    elif diff >= 135:
        return "infield"   # blowing in towards home plate
    else:
        return "crosswind" # blowing across


class WeatherService:
    """Fetches and caches live meteorological conditions for sports venues."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_venue_weather(
        self,
        league: str,
        home_team: str,
        game_start_utc: Optional[str] = None
    ) -> Dict[str, Any]:
        """Returns structured weather observations for a game fixture."""
        stadium_info = None
        if league == "MLB":
            stadium_info = MLB_STADIUMS.get(home_team)
        elif league in ("NFL", "NCAAF"):
            stadium_info = NFL_STADIUMS.get(home_team)

        if not stadium_info:
            # Indoors, dome, or unlisted stadium
            return {
                "venue_type": "dome_or_unlisted",
                "is_dome": True,
                "temperature_c": 21.0,
                "wind_speed_mph": 0.0,
                "wind_direction_deg": 0.0,
                "wind_bearing": "none",
                "precipitation_prob_pct": 0,
                "source": "indoor_baseline"
            }

        lat = stadium_info["lat"]
        lon = stadium_info["lon"]
        azimuth = stadium_info.get("outfield_azimuth")

        # Check local cache
        cache_key = f"{league}_{home_team.replace(' ', '_')}_{lat}_{lon}.json"
        cache_file = self.cache_dir / cache_key
        now = datetime.now(timezone.utc)

        data = None
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                cached_at = datetime.fromisoformat(cached.get("fetched_at", ""))
                if (now - cached_at).total_seconds() < CACHE_TTL_SECONDS:
                    data = cached.get("payload")
            except Exception:
                pass

        if data is None:
            # Fetch from Open-Meteo REST API (hourly + current, UTC, 3 days forecast)
            url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={lat}&longitude={lon}&"
                f"current=temperature_2m,wind_speed_10m,wind_direction_10m,precipitation&"
                f"hourly=temperature_2m,wind_speed_10m,wind_direction_10m,precipitation&"
                f"wind_speed_unit=mph&timezone=UTC&forecast_days=3"
            )
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "BettingTipsEngineQuant/1.0"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    # Save raw response to cache
                    try:
                        cache_file.write_text(
                            json.dumps({"fetched_at": now.isoformat(), "payload": data}),
                            encoding="utf-8"
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("Weather fetch skipped or failed: %s", e)
                return {
                    "venue_type": "outdoor_fallback",
                    "is_dome": False,
                    "temperature_c": 18.0,
                    "wind_speed_mph": 5.0,
                    "wind_direction_deg": 0.0,
                    "wind_bearing": "neutral",
                    "source": "fallback",
                    "time_synchronized": False
                }

        # Resolve exact game hour point-in-time
        hourly = data.get("hourly", {})
        hourly_times = hourly.get("time", [])
        chosen_idx = None
        sync_mode = "current_observation"
        target_hour_str = ""

        if game_start_utc and hourly_times:
            try:
                # Format game start into UTC hour 'YYYY-MM-DDTHH:00'
                dt = datetime.fromisoformat(str(game_start_utc).replace("Z", "+00:00"))
                dt_utc = dt.astimezone(timezone.utc)
                target_hour_str = dt_utc.strftime("%Y-%m-%dT%H:00")
                if target_hour_str in hourly_times:
                    chosen_idx = hourly_times.index(target_hour_str)
                    sync_mode = "hourly_point_in_time"
            except Exception:
                pass

        if chosen_idx is not None and chosen_idx < len(hourly_times):
            temp_c = float(hourly.get("temperature_2m", [20.0])[chosen_idx])
            wind_mph = float(hourly.get("wind_speed_10m", [0.0])[chosen_idx])
            wind_deg = float(hourly.get("wind_direction_10m", [0.0])[chosen_idx])
            precip = float(hourly.get("precipitation", [0.0])[chosen_idx])
        else:
            current = data.get("current", {})
            temp_c = float(current.get("temperature_2m", 20.0))
            wind_mph = float(current.get("wind_speed_10m", 0.0))
            wind_deg = float(current.get("wind_direction_10m", 0.0))
            precip = float(current.get("precipitation", 0.0))

        # Defensive sanity bounds (protect against corrupted feeds)
        if not (-40.0 <= temp_c <= 55.0):
            temp_c = 20.0
        if not (0.0 <= wind_mph <= 120.0):
            wind_mph = 5.0
        if not (0.0 <= wind_deg <= 360.0):
            wind_deg = 0.0
        if not (0.0 <= precip <= 500.0):
            precip = 0.0

        bearing = _classify_wind_bearing(wind_deg, azimuth)

        return {
            "venue_type": "outdoor",
            "is_dome": False,
            "temperature_c": round(temp_c, 1),
            "wind_speed_mph": round(wind_mph, 1),
            "wind_direction_deg": round(wind_deg, 1),
            "wind_bearing": bearing,
            "precipitation_mm": round(precip, 2),
            "time_sync_mode": sync_mode,
            "forecast_hour_utc": target_hour_str or now.strftime("%Y-%m-%dT%H:00"),
            "game_start_utc": game_start_utc,
            "time_synchronized": sync_mode == "hourly_point_in_time",
            "source": "open-meteo"
        }

