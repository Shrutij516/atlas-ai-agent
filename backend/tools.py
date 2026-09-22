from __future__ import annotations

import calendar
import json
from datetime import date, datetime
from typing import Any

import httpx
from dateutil import parser as date_parser
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import get_settings
from db import insert_itinerary_item


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


def _with_year(day: date, year: int) -> date:
    if day.month == 2 and day.day == 29:
        # Feb 29 has no equivalent in a non-leap year; roll forward to the
        # next one instead of letting date.replace raise ValueError.
        while not calendar.isleap(year):
            year += 1
    return day.replace(year=year)


def _next_occurrence(month_day: date) -> date:
    """Resolve a year-less month/day to the next upcoming occurrence."""
    today = date.today()
    candidate = _with_year(month_day, today.year)
    if candidate < today:
        candidate = _with_year(month_day, today.year + 1)
    return candidate


def _normalize_date(value: Any, field_name: str) -> str:
    """Parse a natural-language or ISO date into strict ISO YYYY-MM-DD.

    Groq/LangChain may pass "May 5", "2026-05-05", or "May 5, 2026". When no
    year is given, dateutil.parser fills one in from `default` — parsing
    twice with two different default years and comparing the results is how
    we detect whether the year actually came from the text.
    """
    text = _normalize_text(value, field_name)

    try:
        parsed_a = date_parser.parse(text, default=datetime(1904, 1, 1))
        parsed_b = date_parser.parse(text, default=datetime(1905, 1, 1))
    except (ValueError, OverflowError) as exc:
        raise ValueError(
            f'{field_name} "{text}" could not be parsed as a date. '
            'Use a format like "2026-05-05" or "May 5, 2026".'
        ) from exc

    year_given = parsed_a.year == parsed_b.year
    result = parsed_a.date()
    if not year_given:
        result = _next_occurrence(result)

    return result.isoformat()


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


class ProposeItineraryItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str = Field(
        description="City for this itinerary item, e.g. Lisbon or Kyoto",
    )
    start_date: str = Field(
        description=(
            'Start date, e.g. "2026-04-10", "April 10", or "April 10, 2026". '
            "If no year is given, the next upcoming occurrence is assumed. "
            "Normalized to ISO YYYY-MM-DD."
        ),
    )
    end_date: str = Field(
        description=(
            'End date, e.g. "2026-04-13", "April 13", or "April 13, 2026". '
            "If no year is given, the next upcoming occurrence is assumed. "
            "Normalized to ISO YYYY-MM-DD."
        ),
    )
    notes: str = Field(
        default="",
        description="Optional notes about this itinerary item",
    )

    @field_validator("city", mode="before")
    @classmethod
    def validate_city(cls, value: Any) -> str:
        return _normalize_text(value, "city")

    @field_validator("start_date", mode="before")
    @classmethod
    def validate_start_date(cls, value: Any) -> str:
        return _normalize_date(value, "start_date")

    @field_validator("end_date", mode="before")
    @classmethod
    def validate_end_date(cls, value: Any) -> str:
        return _normalize_date(value, "end_date")

    @field_validator("notes", mode="before")
    @classmethod
    def validate_notes(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


def _current_weather(city: str) -> str:
    return _call_mcp("/weather/current", {"city": city})


def _weather_forecast(city: str, days: str) -> str:
    days_int = _coerce_days(days)
    return _call_mcp("/weather/forecast", {"city": city, "days": days_int})


def _news_headlines(topic: str) -> str:
    return _call_mcp("/news/headlines", {"topic": topic, "page_size": 8})


def _local_news(city: str) -> str:
    return _call_mcp("/news/local", {"city": city, "page_size": 8})


def _propose_itinerary_item(
    city: str, start_date: str, end_date: str, notes: str = ""
) -> str:
    # Phase 2 (human-in-the-loop) will intercept here with an approval gate
    # (e.g. a LangGraph interrupt()) before the DB write, so the user can
    # approve, edit, or reject the proposal. For now it writes straight
    # through so the sub-agent and tool work end to end.
    row_id = insert_itinerary_item(city, start_date, end_date, notes)
    confirmation = f"Saved itinerary item #{row_id}: {city} from {start_date} to {end_date}."
    if notes:
        confirmation += f" Notes: {notes}"
    return confirmation


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

propose_itinerary_item = StructuredTool.from_function(
    func=_propose_itinerary_item,
    name="propose_itinerary_item",
    description=(
        "Propose and save a concrete itinerary item (a trip or stay in a "
        "city with start/end dates) to the user's itinerary."
    ),
    args_schema=ProposeItineraryItemInput,
)

ALL_TOOLS = [
    current_weather,
    weather_forecast,
    news_headlines,
    local_news,
    propose_itinerary_item,
]
