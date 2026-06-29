from __future__ import annotations

from typing import Any

import httpx

from config import NEWSAPI_BASE_URL, get_settings
from services.errors import ServiceError
from services.openweather import geocode_city


def _validate_topic(topic: str | None) -> str:
    if topic is None or not topic.strip():
        raise ServiceError(
            "Topic is required. Provide a keyword such as technology, sports, or climate.",
            status_code=422,
        )
    return topic.strip()


def _validate_city(city: str | None) -> str:
    if city is None or not city.strip():
        raise ServiceError(
            "City is required for local news. Provide a city name, for example: Austin or Berlin.",
            status_code=422,
        )
    return city.strip()


def _format_article(article: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": article.get("title"),
        "description": article.get("description"),
        "source": (article.get("source") or {}).get("name"),
        "author": article.get("author"),
        "url": article.get("url"),
        "published_at": article.get("publishedAt"),
    }


async def _request(
    client: httpx.AsyncClient, path: str, params: dict[str, Any]
) -> dict[str, Any]:
    settings = get_settings()
    timeout = settings.request_timeout

    try:
        response = await client.get(
            f"{NEWSAPI_BASE_URL}{path}",
            params=params,
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        raise ServiceError(
            "The news service took too long to respond. Please try again in a moment.",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ServiceError(
            "Unable to reach the news service. Check your network connection and try again.",
            status_code=502,
        ) from exc

    data = response.json()

    if response.status_code == 401:
        raise ServiceError(
            "News API authentication failed. Verify NEWSAPI_KEY in your .env file.",
            status_code=502,
        )

    if data.get("status") != "ok":
        message = data.get("message", "Unexpected error from news service.")
        code = data.get("code", "")

        if code == "rateLimited":
            raise ServiceError(
                "News API rate limit reached. Please wait and try again.",
                status_code=429,
            )

        raise ServiceError(message, status_code=502)

    return data


async def get_headlines_by_topic(
    client: httpx.AsyncClient, topic: str, page_size: int = 10
) -> dict[str, Any]:
    topic = _validate_topic(topic)
    settings = get_settings()

    data = await _request(
        client,
        "/top-headlines",
        {
            "q": topic,
            "pageSize": min(max(page_size, 1), 100),
            "apiKey": settings.require_newsapi_key(),
        },
    )

    articles = [_format_article(article) for article in data.get("articles", [])]

    return {
        "topic": topic,
        "total_results": data.get("totalResults", len(articles)),
        "articles": articles,
    }


async def get_local_news_by_city(
    client: httpx.AsyncClient, city: str, page_size: int = 10
) -> dict[str, Any]:
    city = _validate_city(city)
    location = await geocode_city(client, city)
    settings = get_settings()

    search_query = location["name"]
    if location.get("state"):
        search_query = f'{location["name"]} {location["state"]}'

    params: dict[str, Any] = {
        "q": search_query,
        "searchIn": "title,description",
        "sortBy": "publishedAt",
        "pageSize": min(max(page_size, 1), 100),
        "apiKey": settings.require_newsapi_key(),
    }

    country = location.get("country")
    if country:
        params["language"] = _country_default_language(country)

    data = await _request(client, "/everything", params)
    articles = [_format_article(article) for article in data.get("articles", [])]

    return {
        "location": {
            "city": location["name"],
            "country": location.get("country"),
            "state": location.get("state"),
        },
        "search_query": search_query,
        "total_results": data.get("totalResults", len(articles)),
        "articles": articles,
    }


def _country_default_language(country_code: str) -> str:
    # NewsAPI supported language codes: ar de en es fr he it nl no pt ru sv ud zh
    language_map = {
        "US": "en",
        "GB": "en",
        "CA": "en",
        "AU": "en",
        "DE": "de",
        "FR": "fr",
        "ES": "es",
        "IT": "it",
        "PT": "pt",
        "BR": "pt",
        "NL": "nl",
        "RU": "ru",
        "CN": "zh",
        "MX": "es",
        "AR": "es",
        "NO": "no",
        "SE": "sv",
        "IL": "he",
        "SA": "ar",
    }
    return language_map.get(country_code.upper(), "en")
