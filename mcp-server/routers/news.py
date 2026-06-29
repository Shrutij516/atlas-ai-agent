import httpx
from fastapi import APIRouter, Query, Request

from services.newsapi import get_headlines_by_topic, get_local_news_by_city

router = APIRouter(prefix="/news", tags=["news"])


@router.get("/headlines")
async def headlines_by_topic(
    request: Request,
    topic: str = Query(..., min_length=1, description="News topic or keyword"),
    page_size: int = Query(10, ge=1, le=100, description="Number of articles to return"),
) -> dict:
    client: httpx.AsyncClient = request.app.state.http_client
    data = await get_headlines_by_topic(client, topic, page_size)
    return {"success": True, "data": data}


@router.get("/local")
async def local_news_by_city(
    request: Request,
    city: str = Query(..., min_length=1, description="City name for local news"),
    page_size: int = Query(10, ge=1, le=100, description="Number of articles to return"),
) -> dict:
    client: httpx.AsyncClient = request.app.state.http_client
    data = await get_local_news_by_city(client, city, page_size)
    return {"success": True, "data": data}
