from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

OPENWEATHER_BASE_URL = "https://api.openweathermap.org"
NEWSAPI_BASE_URL = "https://newsapi.org/v2"
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))


class Settings:
    openweathermap_api_key: str
    newsapi_key: str
    host: str
    port: int
    request_timeout: float

    def __init__(self) -> None:
        self.openweathermap_api_key = os.getenv("OPENWEATHERMAP_API_KEY", "").strip()
        self.newsapi_key = os.getenv("NEWSAPI_KEY", "").strip()
        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT", "8001"))
        self.request_timeout = DEFAULT_TIMEOUT_SECONDS

    def require_openweather_key(self) -> str:
        if not self.openweathermap_api_key:
            raise RuntimeError(
                "OPENWEATHERMAP_API_KEY is not set. Add it to your .env file."
            )
        return self.openweathermap_api_key

    def require_newsapi_key(self) -> str:
        if not self.newsapi_key:
            raise RuntimeError("NEWSAPI_KEY is not set. Add it to your .env file.")
        return self.newsapi_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
