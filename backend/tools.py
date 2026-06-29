from __future__ import annotations

import json
from typing import Any

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import get_settings


def _call_mcp(path: str, params: dict[str, Any]) -> str:
    settings = get_settings()
    url = f"{settings.mcp_server_url}{path}"

    try:
        response = httpx.get(url, params=params, timeout=settings.request_timeout)
        payload = response.json()
    except httpx.TimeoutException:
        return (
            "Error: The data service timed out. Ask the user to try again in a moment."
        )
    except httpx.ConnectError:
        return (
            "Error: Could not reach the MCP server. "
            "It should be running at http://localhost:8001."
        )
    except httpx.RequestError as exc:
        return f"Error: Request to the data service failed — {exc}"
    except ValueError:
        return "Error: The data service returned an invalid response."

    if response.status_code >= 400 or not payload.get("success"):
        return f"Error: {payload.get('error', 'Unknown error from data service.')}"

    return json.dumps(payload["data"], indent=2)


def _normalize_text(value: Any, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required.")
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (list, tuple)) and value:
        text = str(value[0]).strip()
    else:
        text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} cannot be empty.")
    return text


def _coerce_days(value: Any) -> int:
    """Convert days to int whether Groq/LangChain passes str, int, or float."""
    if isinstance(value, bool):
        raise ValueError("days must be an integer from 1 to 5.")
    if isinstance(value, int):
        days = value
    elif isinstance(value, float):
        days = int(value)
    elif isinstance(value, str):
        days = int(value.strip())
    else:
        days = int(value)

    if days < 1 or days > 5:
        raise ValueError("days must be between 1 and 5.")
    return days


class CurrentWeatherInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str = Field(
        description="City name, e.g. London, Tokyo, or Paris, FR",
    )

    @field_validator("city", mode="before")
    @classmethod
    def validate_city(cls, value: Any) -> str:
        return _normalize_text(value, "city")


class WeatherForecastInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str = Field(
        description="City name, e.g. London, Tokyo, or Austin, US",
    )
    days: str = Field(
        description='Number of forecast days from 1 to 5, e.g. "3"',
    )

    @field_validator("city", mode="before")
    @classmethod
    def validate_city(cls, value: Any) -> str:
        return _normalize_text(value, "city")

    @field_validator("days", mode="before")
    @classmethod
    def validate_days(cls, value: Any) -> str:
        return str(_coerce_days(value))


class NewsHeadlinesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(
        description="News topic or keyword, e.g. technology, sports, or travel",
    )

    @field_validator("topic", mode="before")
    @classmethod
    def validate_topic(cls, value: Any) -> str:
        return _normalize_text(value, "topic")


class LocalNewsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str = Field(
        description="City name for local news, e.g. Berlin, Chicago, or Sydney, AU",
    )

    @field_validator("city", mode="before")
    @classmethod
    def validate_city(cls, value: Any) -> str:
        return _normalize_text(value, "city")


def _current_weather(city: str) -> str:
    return _call_mcp("/weather/current", {"city": city})


def _weather_forecast(city: str, days: str) -> str:
    days_int = _coerce_days(days)
    return _call_mcp("/weather/forecast", {"city": city, "days": days_int})


def _news_headlines(topic: str) -> str:
    return _call_mcp("/news/headlines", {"topic": topic, "page_size": 8})


def _local_news(city: str) -> str:
    return _call_mcp("/news/local", {"city": city, "page_size": 8})


current_weather = StructuredTool.from_function(
    func=_current_weather,
    name="current_weather",
    description="Get current weather conditions for a city.",
    args_schema=CurrentWeatherInput,
)

weather_forecast = StructuredTool.from_function(
    func=_weather_forecast,
    name="weather_forecast",
    description="Get a multi-day weather forecast for a city (1 to 5 days).",
    args_schema=WeatherForecastInput,
)

news_headlines = StructuredTool.from_function(
    func=_news_headlines,
    name="news_headlines",
    description="Get top news headlines for a topic or keyword.",
    args_schema=NewsHeadlinesInput,
)

local_news = StructuredTool.from_function(
    func=_local_news,
    name="local_news",
    description="Get recent local news and events for a specific city.",
    args_schema=LocalNewsInput,
)

ALL_TOOLS = [
    current_weather,
    weather_forecast,
    news_headlines,
    local_news,
]
