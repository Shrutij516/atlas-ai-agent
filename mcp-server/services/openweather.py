from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import httpx

from config import OPENWEATHER_BASE_URL, get_settings
from services.errors import ServiceError

FORECAST_MIN_DAYS = 1
FORECAST_MAX_DAYS = 5


async def _request(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> Any:
    settings = get_settings()
    timeout = settings.request_timeout

    try:
        response = await client.get(
            f"{OPENWEATHER_BASE_URL}{path}",
            params=params,
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        raise ServiceError(
            "The weather service took too long to respond. Please try again in a moment.",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ServiceError(
            "Unable to reach the weather service. Check your network connection and try again.",
            status_code=502,
        ) from exc

    if response.status_code == 401:
        raise ServiceError(
            "Weather API authentication failed. Verify OPENWEATHERMAP_API_KEY in your .env file.",
            status_code=502,
        )

    if response.status_code >= 500:
        raise ServiceError(
            "The weather service is temporarily unavailable. Please try again later.",
            status_code=502,
        )

    data = response.json()

    if response.status_code != 200:
        message = data.get("message", "Unexpected error from weather service.")
        raise ServiceError(message, status_code=502)

    return data


def _validate_city(city: str | None) -> str:
    if city is None or not city.strip():
        raise ServiceError(
            "City is required. Provide a city name, for example: London or New York.",
            status_code=422,
        )
    return city.strip()


def _validate_days(days: int | None) -> int:
    if days is None:
        raise ServiceError(
            f"Number of days is required. Choose between {FORECAST_MIN_DAYS} and {FORECAST_MAX_DAYS}.",
            status_code=422,
        )
    if days < FORECAST_MIN_DAYS or days > FORECAST_MAX_DAYS:
        raise ServiceError(
            f"Invalid number of days: {days}. Forecast supports {FORECAST_MIN_DAYS} to {FORECAST_MAX_DAYS} days.",
            status_code=422,
        )
    return days


async def geocode_city(client: httpx.AsyncClient, city: str) -> dict[str, Any]:
    settings = get_settings()
    results = await _request(
        client,
        "/geo/1.0/direct",
        {
            "q": city,
            "limit": 1,
            "appid": settings.require_openweather_key(),
        },
    )

    if not results:
        raise ServiceError(
            f'City "{city}" was not found. Please check the spelling and try again '
            '(include country if needed, e.g. "Paris, FR").',
            status_code=404,
        )

    location = results[0]
    return {
        "name": location.get("name", city),
        "country": location.get("country"),
        "state": location.get("state"),
        "lat": location["lat"],
        "lon": location["lon"],
    }


def _format_location(location: dict[str, Any]) -> dict[str, Any]:
    return {
        "city": location["name"],
        "country": location.get("country"),
        "state": location.get("state"),
        "coordinates": {"lat": location["lat"], "lon": location["lon"]},
    }


async def get_current_weather(client: httpx.AsyncClient, city: str) -> dict[str, Any]:
    city = _validate_city(city)
    location = await geocode_city(client, city)
    settings = get_settings()

    data = await _request(
        client,
        "/data/2.5/weather",
        {
            "lat": location["lat"],
            "lon": location["lon"],
            "units": "metric",
            "appid": settings.require_openweather_key(),
        },
    )

    weather = data.get("weather", [{}])[0]
    main = data.get("main", {})
    wind = data.get("wind", {})

    return {
        "location": _format_location(location),
        "current": {
            "temperature_c": main.get("temp"),
            "feels_like_c": main.get("feels_like"),
            "humidity_percent": main.get("humidity"),
            "pressure_hpa": main.get("pressure"),
            "condition": weather.get("main"),
            "description": weather.get("description"),
            "wind_speed_mps": wind.get("speed"),
            "wind_direction_deg": wind.get("deg"),
            "cloudiness_percent": data.get("clouds", {}).get("all"),
            "visibility_m": data.get("visibility"),
            "observed_at": datetime.fromtimestamp(
                data.get("dt", 0), tz=timezone.utc
            ).isoformat(),
        },
    }


def _aggregate_forecast_by_day(
    forecast_items: list[dict[str, Any]], days: int
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for item in forecast_items:
        date_key = datetime.fromtimestamp(item["dt"], tz=timezone.utc).date().isoformat()
        grouped[date_key].append(item)

    daily_forecasts: list[dict[str, Any]] = []

    for date_key in sorted(grouped.keys())[:days]:
        entries = grouped[date_key]
        temps = [entry["main"]["temp"] for entry in entries if "main" in entry]
        midday = entries[len(entries) // 2]
        weather = midday.get("weather", [{}])[0]

        daily_forecasts.append(
            {
                "date": date_key,
                "temperature_min_c": min(temps) if temps else None,
                "temperature_max_c": max(temps) if temps else None,
                "condition": weather.get("main"),
                "description": weather.get("description"),
                "humidity_percent": midday.get("main", {}).get("humidity"),
                "wind_speed_mps": midday.get("wind", {}).get("speed"),
                "cloudiness_percent": midday.get("clouds", {}).get("all"),
                "periods": len(entries),
            }
        )

    return daily_forecasts


async def get_weather_forecast(
    client: httpx.AsyncClient, city: str, days: int
) -> dict[str, Any]:
    city = _validate_city(city)
    days = _validate_days(days)
    location = await geocode_city(client, city)
    settings = get_settings()

    data = await _request(
        client,
        "/data/2.5/forecast",
        {
            "lat": location["lat"],
            "lon": location["lon"],
            "units": "metric",
            "appid": settings.require_openweather_key(),
        },
    )

    forecast_items = data.get("list", [])
    if not forecast_items:
        raise ServiceError(
            f'No forecast data is available for "{location["name"]}". Please try again later.',
            status_code=502,
        )

    return {
        "location": _format_location(location),
        "days_requested": days,
        "days_available": min(days, FORECAST_MAX_DAYS),
        "forecast": _aggregate_forecast_by_day(forecast_items, days),
    }
