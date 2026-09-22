from __future__ import annotations

import calendar
import json
from datetime import date, datetime
from typing import Any

import httpx
from dateutil import parser as date_parser
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langchain_groq import ChatGroq
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import get_settings
from db import insert_itinerary_item, update_audit_log_decision
from risk import assess_itinerary_risk


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


def _build_llm() -> ChatGroq:
    # Mirrors graph.py's _build_llm. Duplicated rather than imported to avoid
    # a tools.py <-> graph.py import cycle (graph.py imports tools for the
    # sub-agents' tool lists).
    settings = get_settings()
    settings.require_groq_key()
    return ChatGroq(
        model=settings.groq_model,
        temperature=0.2,
        groq_api_key=settings.groq_api_key,
    )


def _generate_context_note(
    city: str, start_date: str, end_date: str, notes: str, risk: dict
) -> str:
    """One cheap extra LLM call: a one-line heads-up for the human, not a score.

    Scoped to this proposal's own fields, not the full conversation — the
    tool only receives its declared args plus an injected RunnableConfig, not
    the graph's message history. Note: since interrupt() re-runs everything
    before it when the node resumes, this call (like assess_itinerary_risk)
    fires again on resume and its second result is discarded — wasted but
    harmless, since interrupt() only returns the resume value on that pass.
    """
    try:
        llm = _build_llm()
        prompt = (
            "In one short sentence, note anything worth a human's attention "
            "about this itinerary proposal, based only on the details given. "
            "Do not state a risk level or score — that is tracked separately.\n\n"
            f"City: {city}\nStart date: {start_date}\nEnd date: {end_date}\n"
            f"Notes: {notes or '(none)'}\n"
            f"Automated risk flags: {', '.join(risk['reasons']) or '(none)'}"
        )
        response = llm.invoke(prompt)
        text = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )
        return text.strip() or "No additional context."
    except Exception as exc:
        return f"Context note unavailable: {exc}"


def _propose_itinerary_item(
    city: str,
    start_date: str,
    end_date: str,
    notes: str = "",
    # NOTE: must be the bare `RunnableConfig` type — not `RunnableConfig | None`
    # or `Optional[RunnableConfig]`. langchain_core's tool config-injection
    # (_get_runnable_config_param in langchain_core/tools/base.py) resolves
    # the annotation via get_type_hints() and injects only when the resolved
    # type `is RunnableConfig` exactly; a union/Optional silently fails that
    # identity check and the parameter is left at its Python default instead
    # of the real invocation config, with no error — just a wrong/missing
    # thread_id at runtime. Confirmed by direct reproduction.
    config: RunnableConfig = None,
) -> str:
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "unknown")

    risk = assess_itinerary_risk(city, start_date, end_date)
    context_note = _generate_context_note(city, start_date, end_date, notes, risk)

    # Pauses graph execution here and surfaces this payload to the caller
    # (see main.py's pending_approval handling). Resuming via
    # Command(resume=...) re-enters this function from the top with the
    # resume value returned here instead of raising again.
    decision = interrupt(
        {
            "proposed_item": {
                "city": city,
                "start_date": start_date,
                "end_date": end_date,
                "notes": notes,
            },
            "risk": risk,
            "context_note": context_note,
        }
    )

    action = decision.get("decision") if isinstance(decision, dict) else None

    if action == "approve":
        final_city, final_start, final_end, final_notes = city, start_date, end_date, notes
    elif action == "edit":
        edited = decision.get("edited_item") or {}
        final_city = _normalize_text(edited.get("city", city), "city")
        final_start = _normalize_date(edited.get("start_date", start_date), "start_date")
        final_end = _normalize_date(edited.get("end_date", end_date), "end_date")
        final_notes = edited.get("notes", notes) or ""
    else:
        # Rejected, or any unrecognized decision — safest default is no write.
        update_audit_log_decision(
            thread_id=thread_id,
            human_decision="rejected",
            final_city=None,
            final_start_date=None,
            final_end_date=None,
            final_notes=None,
        )
        return "The proposed itinerary item was declined and was not saved."

    row_id = insert_itinerary_item(final_city, final_start, final_end, final_notes)
    update_audit_log_decision(
        thread_id=thread_id,
        human_decision="approved" if action == "approve" else "edited",
        final_city=final_city,
        final_start_date=final_start,
        final_end_date=final_end,
        final_notes=final_notes,
    )

    confirmation = f"Saved itinerary item #{row_id}: {final_city} from {final_start} to {final_end}."
    if final_notes:
        confirmation += f" Notes: {final_notes}"
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
