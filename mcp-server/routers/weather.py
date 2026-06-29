import httpx
from fastapi import APIRouter, Query, Request

from services.openweather import get_current_weather, get_weather_forecast

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("/current")
async def current_weather(
    request: Request,
    city: str = Query(..., min_length=1, description="City name, optionally with country code"),
) -> dict:
    client: httpx.AsyncClient = request.app.state.http_client
    data = await get_current_weather(client, city)
    return {"success": True, "data": data}


@router.get("/forecast")
async def weather_forecast(
    request: Request,
    city: str = Query(..., min_length=1, description="City name, optionally with country code"),
    days: int = Query(..., ge=1, le=5, description="Number of forecast days (1-5)"),
) -> dict:
    client: httpx.AsyncClient = request.app.state.http_client
    data = await get_weather_forecast(client, city, days)
    return {"success": True, "data": data}
