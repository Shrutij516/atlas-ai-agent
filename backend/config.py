from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    groq_api_key: str
    groq_model: str
    mcp_server_url: str
    host: str
    port: int
    request_timeout: float

    def __init__(self) -> None:
        self.groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.mcp_server_url = os.getenv("MCP_SERVER_URL", "http://localhost:8001").rstrip("/")
        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT", "8000"))
        self.request_timeout = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))

    def require_groq_key(self) -> str:
        if not self.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file.")
        return self.groq_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
