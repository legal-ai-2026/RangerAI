from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.config import Settings, settings
from src.contracts import GeoPoint, Phase, TerrainSnapshot, WeatherSnapshot


class EnvironmentProviderError(RuntimeError):
    pass


class EnvironmentClients:
    def __init__(self, config: Settings = settings) -> None:
        self.settings = config

    def weather(self, geo: GeoPoint) -> WeatherSnapshot:
        provider = self.settings.weather_provider.lower()
        if provider == "nws":
            try:
                return self._nws_weather(geo)
            except Exception:
                if not self.settings.allow_synthetic_environment_fallback:
                    raise
        if provider in {"open_meteo", "open-meteo"}:
            try:
                return self._open_meteo_weather(geo)
            except Exception:
                if not self.settings.allow_synthetic_environment_fallback:
                    raise
        return synthetic_weather(geo)

    def terrain(self, geo: GeoPoint, phase: Phase) -> TerrainSnapshot:
        provider = self.settings.terrain_provider.lower()
        if provider == "usgs_epqs":
            try:
                return self._usgs_epqs_terrain(geo, phase)
            except Exception:
                if not self.settings.allow_synthetic_environment_fallback:
                    raise
        return synthetic_terrain(geo, phase)

    def _nws_weather(self, geo: GeoPoint) -> WeatherSnapshot:
        if not self.settings.nws_user_agent:
            raise EnvironmentProviderError("NWS_USER_AGENT is required for NWS API calls")
        point_url = f"https://api.weather.gov/points/{geo.lat:.4f},{geo.lon:.4f}"
        point = _fetch_json(
            point_url,
            timeout_seconds=self.settings.environment_timeout_seconds,
            headers={"User-Agent": self.settings.nws_user_agent},
        )
        hourly_url = point.get("properties", {}).get("forecastHourly")
        if not isinstance(hourly_url, str):
            raise EnvironmentProviderError("NWS point response did not include forecastHourly")
        hourly = _fetch_json(
            hourly_url,
            timeout_seconds=self.settings.environment_timeout_seconds,
            headers={"User-Agent": self.settings.nws_user_agent},
        )
        periods = hourly.get("properties", {}).get("periods", [])
        first = periods[0] if periods and isinstance(periods[0], dict) else {}
        temperature_c = _fahrenheit_to_celsius(first.get("temperature"))
        wind_speed = _wind_speed_kph(first.get("windSpeed"))
        return WeatherSnapshot(
            provider="nws",
            source_ref=hourly_url,
            generated_at_utc=_parse_datetime(first.get("startTime")),
            temperature_c=temperature_c,
            apparent_temperature_c=temperature_c,
            wind_speed_kph=wind_speed,
            wind_gust_kph=None,
            precipitation_probability=_probability(first.get("probabilityOfPrecipitation")),
            precipitation_mm=None,
            alerts=[],
            confidence=0.88,
            synthetic=False,
        )

    def _open_meteo_weather(self, geo: GeoPoint) -> WeatherSnapshot:
        params = urlencode(
            {
                "latitude": geo.lat,
                "longitude": geo.lon,
                "current": ",".join(
                    [
                        "temperature_2m",
                        "apparent_temperature",
                        "precipitation",
                        "wind_speed_10m",
                        "wind_gusts_10m",
                    ]
                ),
                "timezone": "UTC",
            }
        )
        url = f"https://api.open-meteo.com/v1/forecast?{params}"
        payload = _fetch_json(url, timeout_seconds=self.settings.environment_timeout_seconds)
        current = payload.get("current", {})
        return WeatherSnapshot(
            provider="open_meteo",
            source_ref=url,
            generated_at_utc=_parse_datetime(current.get("time")),
            temperature_c=_float_or_none(current.get("temperature_2m")),
            apparent_temperature_c=_float_or_none(current.get("apparent_temperature")),
            wind_speed_kph=_float_or_none(current.get("wind_speed_10m")),
            wind_gust_kph=_float_or_none(current.get("wind_gusts_10m")),
            precipitation_probability=None,
            precipitation_mm=_float_or_none(current.get("precipitation")),
            alerts=[],
            confidence=0.8,
            synthetic=False,
        )

    def _usgs_epqs_terrain(self, geo: GeoPoint, phase: Phase) -> TerrainSnapshot:
        params = urlencode(
            {
                "x": geo.lon,
                "y": geo.lat,
                "units": "Meters",
                "output": "json",
            }
        )
        url = f"https://epqs.nationalmap.gov/v1/json?{params}"
        payload = _fetch_json(url, timeout_seconds=self.settings.environment_timeout_seconds)
        elevation = _epqs_elevation(payload)
        synthetic = synthetic_terrain(geo, phase)
        return TerrainSnapshot(
            provider="usgs_epqs",
            source_ref=url,
            elevation_m=elevation,
            slope_class=synthetic.slope_class,
            water_proximity_m=synthetic.water_proximity_m,
            terrain_class=synthetic.terrain_class,
            hazards=synthetic.hazards,
            confidence=0.78 if elevation is not None else 0.45,
            synthetic=False,
        )


def synthetic_weather(geo: GeoPoint) -> WeatherSnapshot:
    mountain_like = geo.lat >= 34.0
    return WeatherSnapshot(
        provider="synthetic",
        source_ref=f"synthetic://weather/{geo.lat:.4f},{geo.lon:.4f}",
        temperature_c=8.0 if mountain_like else 24.0,
        apparent_temperature_c=6.0 if mountain_like else 27.0,
        wind_speed_kph=14.0 if mountain_like else 9.0,
        wind_gust_kph=24.0 if mountain_like else 16.0,
        precipitation_probability=0.25 if mountain_like else 0.35,
        precipitation_mm=0.0,
        alerts=[],
        confidence=0.55,
        synthetic=True,
    )


def synthetic_terrain(geo: GeoPoint, phase: Phase) -> TerrainSnapshot:
    if phase == Phase.mountain:
        return TerrainSnapshot(
            provider="synthetic",
            source_ref=f"synthetic://terrain/{geo.grid_mgrs}",
            elevation_m=980.0,
            slope_class="moderate",
            water_proximity_m=420.0,
            terrain_class="mountain",
            hazards=["cold_exposure", "steep_slope"],
            confidence=0.55,
            synthetic=True,
        )
    if phase == Phase.florida:
        return TerrainSnapshot(
            provider="synthetic",
            source_ref=f"synthetic://terrain/{geo.grid_mgrs}",
            elevation_m=20.0,
            slope_class="flat",
            water_proximity_m=80.0,
            terrain_class="swamp",
            hazards=["water_feature", "heat_humidity"],
            confidence=0.55,
            synthetic=True,
        )
    return TerrainSnapshot(
        provider="synthetic",
        source_ref=f"synthetic://terrain/{geo.grid_mgrs}",
        elevation_m=210.0,
        slope_class="gentle",
        water_proximity_m=650.0,
        terrain_class="mixed",
        hazards=[],
        confidence=0.55,
        synthetic=True,
    )


def _fetch_json(
    url: str,
    *,
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request = Request(url, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except URLError as exc:
        raise EnvironmentProviderError(str(exc)) from exc
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, str) and value:
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _fahrenheit_to_celsius(value: Any) -> float | None:
    numeric = _float_or_none(value)
    if numeric is None:
        return None
    return round((numeric - 32) * 5 / 9, 1)


def _wind_speed_kph(value: Any) -> float | None:
    if not isinstance(value, str):
        return _float_or_none(value)
    digits = "".join(char if char.isdigit() or char == "." else " " for char in value)
    speeds = [_float_or_none(item) for item in digits.split()]
    clean = [item for item in speeds if item is not None]
    if not clean:
        return None
    mph = max(clean)
    return round(mph * 1.60934, 1)


def _probability(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    numeric = _float_or_none(value)
    if numeric is None:
        return None
    return max(0.0, min(1.0, numeric / 100 if numeric > 1 else numeric))


def _epqs_elevation(payload: dict[str, Any]) -> float | None:
    value = payload.get("value")
    if value is None:
        value = (
            payload.get("USGS_Elevation_Point_Query_Service", {})
            .get("Elevation_Query", {})
            .get("Elevation")
        )
    elevation = _float_or_none(value)
    if elevation is None or elevation < -1000:
        return None
    return elevation


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
