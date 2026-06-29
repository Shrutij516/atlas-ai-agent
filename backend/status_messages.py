from __future__ import annotations

TOOL_STATUS_MESSAGES: dict[str, str] = {
    "current_weather": "Atlas is checking the weather...",
    "weather_forecast": "Atlas is checking the forecast...",
    "news_headlines": "Atlas is fetching the news...",
    "local_news": "Atlas is fetching local news...",
}

DEFAULT_STATUS_MESSAGE = "Atlas is thinking..."


def status_message_for_tool(tool_name: str) -> str:
    return TOOL_STATUS_MESSAGES.get(tool_name, DEFAULT_STATUS_MESSAGE)
