from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from agent import get_agent
from config import get_settings
from graph import GRAPH_NAME
from logging_config import get_logger, setup_logging
from schemas import ChatMessage, ChatRequest, ChatResponse
from status_messages import (
    DEFAULT_STATUS_MESSAGE,
    NODE_STATUS_MESSAGES,
    status_message_for_node,
    status_message_for_tool,
)

setup_logging()
logger = get_logger("main")

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


def _log_tool_call(tool_name: str, tool_input: dict) -> None:
    logger.info("agent_tool_selected tool=%s args=%s", tool_name, tool_input)


def _log_user_message(message: str, history_length: int) -> None:
    logger.info(
        "user_message_received message=%s history_length=%s",
        message,
        history_length,
    )


def _is_root_graph_end(event: dict) -> bool:
    """Identify the compiled graph's own on_chain_end event.

    We use astream_events(..., version="v2") because it's the only stream
    mode that also surfaces on_tool_start/on_chain_start events for status
    updates alongside the final state, so we still need to pick the final
    state out of that event stream by hand. The old check
    (`event.get("name") == "LangGraph"`) worked for a single create_react_agent
    node, but weather_agent/news_agent are themselves compiled graphs
    (create_react_agent) that default to the same name "LangGraph", so
    matching on name alone would also match their completion, not just the
    top-level graph's. The root graph run is the only one whose metadata has
    no "langgraph_node" key — every node-level and nested run inherits one —
    so we check both name and that.
    """
    return (
        event.get("event") == "on_chain_end"
        and event.get("name") == GRAPH_NAME
        and "langgraph_node" not in event.get("metadata", {})
    )


def _node_start_name(event: dict) -> str | None:
    if event.get("event") != "on_chain_start":
        return None
    name = event.get("name", "")
    metadata = event.get("metadata", {})
    if name in NODE_STATUS_MESSAGES and metadata.get("langgraph_node") == name:
        return name
    return None


def _log_assistant_response(response: str) -> None:
    preview = response[:200] + "..." if len(response) > 200 else response
    logger.info(
        "assistant_response_sent response_length=%s preview=%s",
        len(response),
        preview,
    )


async def _run_agent(request: ChatRequest) -> str:
    agent = get_agent()
    messages = _to_langchain_messages(request.history, request.message)
    final_output = None

    async for event in agent.astream_events({"messages": messages}, version="v2"):
        if event.get("event") == "on_tool_start":
            tool_name = event.get("name", "")
            tool_input = event.get("data", {}).get("input", {})
            _log_tool_call(tool_name, tool_input)
        elif _is_root_graph_end(event):
            final_output = event.get("data", {}).get("output")

    if not final_output or "messages" not in final_output:
        raise RuntimeError("No response generated.")

    return _extract_assistant_reply(final_output["messages"])


async def _stream_agent_response(
    request: ChatRequest,
) -> AsyncIterator[str]:
    settings = get_settings()

    try:
        settings.require_groq_key()
    except RuntimeError as exc:
        logger.error("chat_stream_failed error=%s", exc)
        yield _sse_event("error", {"detail": str(exc)})
        return

    _log_user_message(request.message, len(request.history))

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
                tool_input = event.get("data", {}).get("input", {})
                _log_tool_call(tool_name, tool_input)
                yield _sse_event(
                    "status",
                    {"message": status_message_for_tool(tool_name)},
                )
            elif (node_name := _node_start_name(event)) is not None:
                yield _sse_event(
                    "status",
                    {"message": status_message_for_node(node_name)},
                )
            elif _is_root_graph_end(event):
                final_output = event.get("data", {}).get("output")
    except Exception as exc:
        logger.error(
            "chat_stream_failed message=%s error=%s",
            request.message,
            exc,
            exc_info=True,
        )
        yield _sse_event(
            "error",
            {"detail": f"Agent failed to generate a response: {exc}"},
        )
        return

    if not final_output or "messages" not in final_output:
        logger.error(
            "chat_stream_failed message=%s error=no_response_generated",
            request.message,
        )
        yield _sse_event("error", {"detail": "No response generated."})
        return

    reply = _extract_assistant_reply(final_output["messages"])
    _log_assistant_response(reply)
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
        logger.error("chat_failed error=%s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    _log_user_message(request.message, len(request.history))

    try:
        reply = await _run_agent(request)
    except Exception as exc:
        logger.error(
            "chat_failed message=%s error=%s",
            request.message,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Agent failed to generate a response: {exc}",
        ) from exc

    _log_assistant_response(reply)
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
