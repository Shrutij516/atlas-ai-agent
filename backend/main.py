from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.types import Command

from agent import get_agent
from config import get_settings
from db import insert_audit_log_proposal
from graph import GRAPH_NAME
from logging_config import get_logger, setup_logging
from schemas import ApproveRequest, ChatMessage, ChatRequest, ChatResponse
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


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(part for part in text_parts if part)
    return ""


def _extract_assistant_reply(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = _message_text(message)
            if text:
                return text
    return "Sorry, I couldn't generate a response. Please try again."


def _messages_to_chat_history(messages: list) -> list[ChatMessage]:
    """Rebuild ChatMessage history from a graph run's final message list.

    Used by /approve, which — unlike /chat/stream — doesn't receive the
    client's prior history in its request body; the checkpointer's
    persisted state already has the full conversation for this thread_id.
    """
    history: list[ChatMessage] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            text = _message_text(message)
            if text:
                history.append(ChatMessage(role="user", content=text))
        elif isinstance(message, AIMessage):
            text = _message_text(message)
            if text:
                history.append(ChatMessage(role="assistant", content=text))
    return history


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


def _pending_approval_fields(state) -> dict[str, Any]:
    interrupt_value = state.interrupts[0].value if state.interrupts else {}
    return {
        "proposed_item": interrupt_value.get("proposed_item", {}),
        "risk": interrupt_value.get("risk", {"level": "high", "reasons": []}),
        "context_note": interrupt_value.get("context_note", ""),
    }


def _log_assistant_response(response: str) -> None:
    preview = response[:200] + "..." if len(response) > 200 else response
    logger.info(
        "assistant_response_sent response_length=%s preview=%s",
        len(response),
        preview,
    )


async def _run_agent(request: ChatRequest) -> str:
    agent = await get_agent()
    messages = _to_langchain_messages(request.history, request.message)
    thread_id = request.conversation_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    pending = await agent.aget_state(config)
    if pending.next:
        # A prior turn on this thread is still waiting on /approve. Starting
        # a fresh run now would silently orphan that interrupt (LangGraph
        # treats it as a brand-new invocation, not a resume) rather than
        # erroring — confirmed by direct reproduction. /chat has no
        # pending_approval event to report this properly, so fail loudly.
        raise RuntimeError(
            "A previous itinerary proposal on this conversation is still "
            "awaiting approval. Resolve it via /approve before sending "
            "another message."
        )

    final_output = None

    async for event in agent.astream_events(
        {"messages": messages}, config, version="v2"
    ):
        if event.get("event") == "on_tool_start":
            tool_name = event.get("name", "")
            tool_input = event.get("data", {}).get("input", {})
            _log_tool_call(tool_name, tool_input)
        elif _is_root_graph_end(event):
            final_output = event.get("data", {}).get("output")

    if not final_output or "messages" not in final_output:
        # Also raised when the graph interrupted (e.g. an itinerary proposal
        # needs human approval) instead of completing — /chat has no
        # approval flow, only /chat/stream + /approve do. See main.py's
        # module docstring-equivalent note in _stream_agent_response.
        raise RuntimeError("No response generated.")

    return _extract_assistant_reply(final_output["messages"])


async def _stream_graph_run(
    agent,
    graph_input: Any,
    config: dict,
    thread_id: str,
    build_history: Any,
) -> AsyncIterator[str]:
    """Drive one graph run (fresh turn or a resumed approval) as SSE events.

    Shared by /chat/stream and /approve since both need the same event loop:
    forward on_tool_start/on_chain_start as `status`, then end with either
    `pending_approval` (graph interrupted — a human decision is needed) or
    `done` (graph completed). `build_history(final_messages, reply)` lets
    each caller supply its own way of producing the ChatMessage list, since
    /approve has no client-supplied request.history to append to.
    """
    final_output = None

    try:
        async for event in agent.astream_events(graph_input, config, version="v2"):
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
            "graph_stream_failed thread_id=%s error=%s",
            thread_id,
            exc,
            exc_info=True,
        )
        yield _sse_event(
            "error",
            {"detail": f"Agent failed to generate a response: {exc}"},
        )
        return

    state = await agent.aget_state(config)
    if state.next:
        # Graph paused on an interrupt() rather than completing — surface
        # the proposal for a human decision instead of a final reply. This
        # is a FRESH interrupt from the run that just happened (the
        # pre-flight check in _run_agent/_stream_agent_response catches an
        # already-pending one before we even get here), so recording it in
        # the audit log now is a one-time insert, not a re-insert.
        fields = _pending_approval_fields(state)
        proposed_item = fields["proposed_item"]
        risk = fields["risk"]
        context_note = fields["context_note"]

        insert_audit_log_proposal(
            thread_id=thread_id,
            proposed_city=proposed_item.get("city", ""),
            proposed_start_date=proposed_item.get("start_date", ""),
            proposed_end_date=proposed_item.get("end_date", ""),
            proposed_notes=proposed_item.get("notes", ""),
            risk_level=risk.get("level", "high"),
            risk_reasons=risk.get("reasons", []),
            context_note=context_note,
        )
        logger.info(
            "itinerary_proposal_pending thread_id=%s risk=%s",
            thread_id,
            risk.get("level"),
        )
        yield _sse_event("pending_approval", {"thread_id": thread_id, **fields})
        return

    if not final_output or "messages" not in final_output:
        logger.error(
            "graph_stream_failed thread_id=%s error=no_response_generated",
            thread_id,
        )
        yield _sse_event("error", {"detail": "No response generated."})
        return

    reply = _extract_assistant_reply(final_output["messages"])
    _log_assistant_response(reply)
    history = build_history(final_output["messages"], reply)

    yield _sse_event(
        "done",
        {
            "response": reply,
            "thread_id": thread_id,
            "history": [item.model_dump() for item in history],
        },
    )


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

    agent = await get_agent()
    messages = _to_langchain_messages(request.history, request.message)
    # Per-conversation, not per-request: the client generates conversation_id
    # once (e.g. crypto.randomUUID() at chat-session start) and resends it
    # every turn, so this thread_id is stable across the whole conversation —
    # required for a pending itinerary proposal to still be there when
    # /approve is called later. Falls back to a one-off id for clients that
    # don't send one yet, which still works for a single turn but can't be
    # resumed by a later /approve call with a different id.
    thread_id = request.conversation_id or str(uuid.uuid4())
    if not request.conversation_id:
        logger.info(
            "chat_stream_no_conversation_id thread_id=%s", thread_id
        )
    config = {"configurable": {"thread_id": thread_id}}

    pending = await agent.aget_state(config)
    if pending.next:
        # A prior turn on this same conversation is still waiting on
        # /approve. Starting a fresh run now would silently orphan that
        # interrupt rather than erroring (confirmed by direct reproduction:
        # LangGraph treats a plain input on a thread with pending state as a
        # brand-new invocation, not a resume, and the original interrupt is
        # never revisitable again). Re-surface the same pending proposal
        # instead of proceeding.
        logger.info("chat_stream_pending_approval_blocked thread_id=%s", thread_id)
        yield _sse_event(
            "pending_approval",
            {"thread_id": thread_id, **_pending_approval_fields(pending)},
        )
        return

    yield _sse_event("status", {"message": DEFAULT_STATUS_MESSAGE})

    def build_history(_final_messages: list, reply: str) -> list[ChatMessage]:
        return _build_updated_history(request, reply)

    async for chunk in _stream_graph_run(
        agent, {"messages": messages}, config, thread_id, build_history
    ):
        yield chunk


async def _stream_approval_resume(request: ApproveRequest) -> AsyncIterator[str]:
    settings = get_settings()

    try:
        settings.require_groq_key()
    except RuntimeError as exc:
        logger.error("approve_failed error=%s", exc)
        yield _sse_event("error", {"detail": str(exc)})
        return

    agent = await get_agent()
    config = {"configurable": {"thread_id": request.thread_id}}

    resume_value: dict[str, Any] = {"decision": request.decision}
    if request.decision == "edit" and request.edited_item is not None:
        resume_value["edited_item"] = request.edited_item.model_dump()

    logger.info(
        "itinerary_decision_received thread_id=%s decision=%s",
        request.thread_id,
        request.decision,
    )

    yield _sse_event("status", {"message": DEFAULT_STATUS_MESSAGE})

    def build_history(final_messages: list, _reply: str) -> list[ChatMessage]:
        return _messages_to_chat_history(final_messages)

    async for chunk in _stream_graph_run(
        agent,
        Command(resume=resume_value),
        config,
        request.thread_id,
        build_history,
    ):
        yield chunk


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


@app.post("/approve")
async def approve(request: ApproveRequest) -> StreamingResponse:
    if request.decision == "edit" and request.edited_item is None:
        raise HTTPException(
            status_code=422,
            detail="edited_item is required when decision is 'edit'.",
        )

    return StreamingResponse(
        _stream_approval_resume(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
