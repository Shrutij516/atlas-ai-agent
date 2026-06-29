from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from agent import get_agent
from config import get_settings
from schemas import ChatMessage, ChatRequest, ChatResponse
from status_messages import DEFAULT_STATUS_MESSAGE, status_message_for_tool

app = FastAPI(
    title="Agent Chat Backend",
    description="LangChain agent backend with weather and news tool calling",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _to_langchain_messages(history: list[ChatMessage], user_message: str):
    messages = []
    for item in history:
        if item.role == "user":
            messages.append(HumanMessage(content=item.content))
        elif item.role == "assistant":
            messages.append(AIMessage(content=item.content))
    messages.append(HumanMessage(content=user_message))
    return messages


def _extract_assistant_reply(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content:
            if isinstance(message.content, str):
                return message.content
            if isinstance(message.content, list):
                text_parts = [
                    block.get("text", "")
                    for block in message.content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                combined = "\n".join(part for part in text_parts if part)
                if combined:
                    return combined
    return "Sorry, I couldn't generate a response. Please try again."


def _build_updated_history(
    request: ChatRequest, reply: str
) -> list[ChatMessage]:
    return request.history + [
        ChatMessage(role="user", content=request.message),
        ChatMessage(role="assistant", content=reply),
    ]


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_agent_response(
    request: ChatRequest,
) -> AsyncIterator[str]:
    settings = get_settings()

    try:
        settings.require_groq_key()
    except RuntimeError as exc:
        yield _sse_event("error", {"detail": str(exc)})
        return

    agent = get_agent()
    messages = _to_langchain_messages(request.history, request.message)
    final_output = None

    yield _sse_event("status", {"message": DEFAULT_STATUS_MESSAGE})

    try:
        async for event in agent.astream_events(
            {"messages": messages}, version="v2"
        ):
            if event.get("event") == "on_tool_start":
                tool_name = event.get("name", "")
                yield _sse_event(
                    "status",
                    {"message": status_message_for_tool(tool_name)},
                )
            elif (
                event.get("event") == "on_chain_end"
                and event.get("name") == "LangGraph"
            ):
                final_output = event.get("data", {}).get("output")
    except Exception as exc:
        yield _sse_event(
            "error",
            {"detail": f"Agent failed to generate a response: {exc}"},
        )
        return

    if not final_output or "messages" not in final_output:
        yield _sse_event("error", {"detail": "No response generated."})
        return

    reply = _extract_assistant_reply(final_output["messages"])
    updated_history = _build_updated_history(request, reply)

    yield _sse_event(
        "done",
        {
            "response": reply,
            "history": [item.model_dump() for item in updated_history],
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    settings = get_settings()

    try:
        settings.require_groq_key()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    agent = get_agent()
    messages = _to_langchain_messages(request.history, request.message)

    try:
        result = await agent.ainvoke({"messages": messages})
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Agent failed to generate a response: {exc}",
        ) from exc

    reply = _extract_assistant_reply(result["messages"])
    updated_history = _build_updated_history(request, reply)

    return ChatResponse(response=reply, history=updated_history)


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream_agent_response(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
