from __future__ import annotations

TOOL_STATUS_MESSAGES: dict[str, str] = {
    "current_weather": "Atlas is checking the weather...",
    "weather_forecast": "Atlas is checking the forecast...",
    "news_headlines": "Atlas is fetching the news...",
    "local_news": "Atlas is fetching local news...",
}

NODE_STATUS_MESSAGES: dict[str, str] = {
    "supervisor": "Atlas is figuring out what you need...",
    "weather_agent": "Atlas is consulting the weather specialist...",
    "news_agent": "Atlas is consulting the news specialist...",
    "itinerary_agent": "Atlas is planning your itinerary...",
    "aggregator": "Atlas is putting together your answer...",
}

DEFAULT_STATUS_MESSAGE = "Atlas is thinking..."


def status_message_for_tool(tool_name: str) -> str:
    return TOOL_STATUS_MESSAGES.get(tool_name, DEFAULT_STATUS_MESSAGE)


def status_message_for_node(node_name: str) -> str:
    return NODE_STATUS_MESSAGES.get(node_name, DEFAULT_STATUS_MESSAGE)
