from __future__ import annotations

"""MCP server wrapping OpenWeatherMap and NewsAPI."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from config import get_settings
from routers import news, weather
from services.errors import ServiceError


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.http_client = httpx.AsyncClient(
        headers={"User-Agent": "atlas-ai-agent-mcp-server/0.1.0"},
        timeout=settings.request_timeout,
    )
    yield
    await app.state.http_client.aclose()


app = FastAPI(
    title="Weather & News MCP Server",
    description="REST API exposing OpenWeatherMap and NewsAPI as tools",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(weather.router)
app.include_router(news.router)


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.message},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    messages: list[str] = []

    for error in exc.errors():
        loc = error.get("loc", ())
        field = loc[-1] if loc else "input"
        error_type = error.get("type", "")

        if field == "city" and error_type in {"missing", "string_too_short"}:
            messages.append(
                "City is required. Provide a city name, for example: London or New York."
            )
        elif field == "topic" and error_type in {"missing", "string_too_short"}:
            messages.append(
                "Topic is required. Provide a keyword such as technology, sports, or climate."
            )
        elif field == "days":
            if error_type == "missing":
                messages.append("Number of days is required. Choose between 1 and 5.")
            elif error_type in {"greater_than_equal", "less_than_equal", "int_parsing"}:
                messages.append(
                    "Invalid number of days. Forecast supports 1 to 5 days."
                )
        else:
            messages.append(error.get("msg", "Invalid request."))

    return JSONResponse(
        status_code=422,
        content={"success": False, "error": " ".join(dict.fromkeys(messages))},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
