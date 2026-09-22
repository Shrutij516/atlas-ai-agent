from __future__ import annotations

import asyncio
from typing import Annotated, Literal, TypedDict

import aiosqlite
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig
from langchain_groq import ChatGroq
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from config import get_settings
from db import DB_PATH
from prompts import SYSTEM_PROMPT
from tools import (
    current_weather,
    local_news,
    news_headlines,
    propose_itinerary_item,
    weather_forecast,
)

# Name given to the compiled graph so we can unambiguously pick its own
# on_chain_end event out of the stream (see main.py). Sub-agents below are
# also compiled StateGraphs and default to the name "LangGraph", so matching
# on name alone is not enough — we also check for the absence of
# "langgraph_node" in the event metadata, which only the root graph run lacks.
GRAPH_NAME = "atlas_graph"

WEATHER_TOOLS = [current_weather, weather_forecast]
NEWS_TOOLS = [news_headlines, local_news]
ITINERARY_TOOLS = [propose_itinerary_item]


class RouteDecision(BaseModel):
    route: list[Literal["weather", "news", "itinerary"]] = Field(
        default_factory=list,
        description=(
            "Which specialist sub-agents are needed to answer the user's "
            "latest message. Include 'weather' for current conditions, "
            "forecasts, or packing/outdoor questions. Include 'news' for "
            "headlines, events, or what's happening in a place. Include "
            "'itinerary' when the user wants to add, save, or plan a "
            "specific trip item (a city with dates), not just ask about "
            "weather or news. Include multiple when the message needs "
            "them. Leave empty for casual conversation that needs neither."
        ),
    )


def _merge_sub_results(current: dict[str, str], update: dict[str, str]) -> dict[str, str]:
    # weather_agent and news_agent can both write this key in the same
    # superstep (fan-out from the supervisor), so a reducer is required —
    # without one, LangGraph raises on concurrent writes to the same key.
    return {**(current or {}), **(update or {})}


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    route: list[str]
    sub_results: Annotated[dict[str, str], _merge_sub_results]


SUPERVISOR_PROMPT = (
    "You are the routing supervisor for Atlas, a travel and lifestyle agent. "
    "Decide which specialist sub-agents must run to answer the user's latest "
    "message. Route to 'weather' for anything about current conditions, "
    "forecasts, packing, or outdoor plans. Route to 'news' for anything about "
    "headlines, local events, or what's happening in a place. Route to "
    "'itinerary' when the user wants to add, save, or plan a specific trip "
    "item — a concrete city with start/end dates — not just ask about "
    "weather or news in passing. Route to multiple sub-agents when the "
    "message needs more than one. Route to none for greetings or general "
    "conversation that doesn't need live data or a saved itinerary item."
)

WEATHER_AGENT_PROMPT = (
    "You are Atlas's weather specialist. Use your tools to gather the "
    "weather information the user needs and report the factual results "
    "(conditions, temperatures, forecast details). Do not add travel advice "
    "or persona flourishes — another step handles that."
)

NEWS_AGENT_PROMPT = (
    "You are Atlas's news specialist. Use your tools to gather the news or "
    "local events information the user needs and report the factual results "
    "(headlines, summaries, sources). Do not add travel advice or persona "
    "flourishes — another step handles that."
)

ITINERARY_AGENT_PROMPT = (
    "You are Atlas's itinerary specialist. Help the user plan concrete "
    "itinerary items — a city with a start and end date, plus optional "
    "notes. Confirm the city and dates are clear from the conversation "
    "before calling propose_itinerary_item; if a detail is genuinely "
    "ambiguous, make a reasonable assumption and state it plainly rather "
    "than leaving the item unsaved. "
    "propose_itinerary_item goes through a human approval step before "
    "anything is saved, and that human can change the city, dates, or notes "
    "before approving. The tool's return string always states the city, "
    "dates, and notes that were ACTUALLY saved — this may differ from what "
    "the user originally asked for. There is only ONE outcome per proposal: "
    "either exactly what the tool's return value says was saved (quote its "
    "city and dates verbatim), or — if the tool says it was declined — "
    "nothing was saved. Never mention the user's originally-requested city "
    "or dates as if they were also saved, saved separately, or 'already "
    "scheduled' alongside the tool's result. If the tool's saved city/dates "
    "differ from the request, that difference means the original request "
    "was NOT saved — only the tool's output was. Do not add travel advice "
    "or persona flourishes — another step handles that."
)


def _extract_text(message: BaseMessage) -> str:
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


def _last_ai_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = _extract_text(message)
            if text:
                return text
    return ""


def _last_tool_message_text(messages: list[BaseMessage], tool_name: str) -> str | None:
    """Find the exact string a specific tool returned, bypassing any LLM paraphrase.

    Used for propose_itinerary_item specifically: its return value states
    which city/dates a human approver actually confirmed, which can differ
    from the original request. The sub-agent's own follow-up AIMessage is an
    LLM paraphrase of that ToolMessage and, empirically, isn't reliable
    about not also mentioning the original (unsaved) request — even with
    explicit prompt instructions not to. Relaying the tool's exact words
    removes that failure mode entirely for this one fact.
    """
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == tool_name:
            return _extract_text(message) or None
    return None


def _build_llm() -> ChatGroq:
    settings = get_settings()
    settings.require_groq_key()
    return ChatGroq(
        model=settings.groq_model,
        temperature=0.2,
        groq_api_key=settings.groq_api_key,
    )


def _build_sub_agent(tools: list, persona_prompt: str):
    llm = _build_llm()
    # Groq requires OpenAI-style tool schemas with tool_choice="auto".
    # Pre-bind tools so create_react_agent does not re-bind with defaults.
    model_with_tools = llm.bind_tools(tools, tool_choice="auto")

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", persona_prompt),
            MessagesPlaceholder("messages"),
        ]
    )

    return create_react_agent(model_with_tools, tools, prompt=prompt)


_graph_lock = asyncio.Lock()
_compiled_graph = None


async def get_graph():
    """Build (once) and return the compiled graph, with an async checkpointer.

    Async, not @lru_cache like the rest of this module's builders: opening
    the checkpointer needs an awaited aiosqlite connection, so this uses a
    double-checked-locking singleton instead. interrupt()/resume requires a
    checkpointer, and AsyncSqliteSaver specifically — the sync SqliteSaver
    raises NotImplementedError on aget_tuple/aput/alist, which is what this
    graph's astream_events-based execution calls.
    """
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph

    async with _graph_lock:
        if _compiled_graph is not None:
            return _compiled_graph

        conn = await aiosqlite.connect(DB_PATH)
        checkpointer = AsyncSqliteSaver(conn)
        await checkpointer.setup()

        _compiled_graph = _build_graph(checkpointer)
        return _compiled_graph


def _build_graph(checkpointer: AsyncSqliteSaver):
    supervisor_llm = _build_llm().with_structured_output(RouteDecision)
    supervisor_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SUPERVISOR_PROMPT),
            MessagesPlaceholder("messages"),
        ]
    )
    supervisor_chain = supervisor_prompt | supervisor_llm

    weather_sub_agent = _build_sub_agent(WEATHER_TOOLS, WEATHER_AGENT_PROMPT)
    news_sub_agent = _build_sub_agent(NEWS_TOOLS, NEWS_AGENT_PROMPT)
    itinerary_sub_agent = _build_sub_agent(ITINERARY_TOOLS, ITINERARY_AGENT_PROMPT)

    aggregator_llm = _build_llm()
    aggregator_prompt_template = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder("messages"),
            ("system", "{findings}"),
        ]
    )
    aggregator_chain = aggregator_prompt_template | aggregator_llm

    def supervisor_node(state: AgentState, config: RunnableConfig) -> dict:
        decision: RouteDecision = supervisor_chain.invoke(
            {"messages": state["messages"]}, config
        )
        route = list(dict.fromkeys(decision.route))
        return {"route": route}

    def weather_agent_node(state: AgentState, config: RunnableConfig) -> dict:
        result = weather_sub_agent.invoke({"messages": state["messages"]}, config)
        text = _last_ai_text(result["messages"]) or "No weather information was returned."
        return {"sub_results": {"weather": text}}

    def news_agent_node(state: AgentState, config: RunnableConfig) -> dict:
        result = news_sub_agent.invoke({"messages": state["messages"]}, config)
        text = _last_ai_text(result["messages"]) or "No news information was returned."
        return {"sub_results": {"news": text}}

    def itinerary_agent_node(state: AgentState, config: RunnableConfig) -> dict:
        result = itinerary_sub_agent.invoke({"messages": state["messages"]}, config)
        # Prefer the tool's own exact confirmation over the sub-agent's
        # paraphrase of it — see _last_tool_message_text's docstring.
        text = (
            _last_tool_message_text(result["messages"], "propose_itinerary_item")
            or _last_ai_text(result["messages"])
            or "No itinerary changes were made."
        )
        return {"sub_results": {"itinerary": text}}

    def aggregator_node(state: AgentState, config: RunnableConfig) -> dict:
        sub_results = state.get("sub_results") or {}
        if sub_results:
            findings = (
                "Specialist findings to use in your reply (do not mention the "
                "specialists by name). Treat these as authoritative and more "
                "current than anything said earlier in the conversation — the "
                "itinerary finding in particular reflects what a human "
                "approver actually confirmed, which can differ from the "
                "user's original request (e.g. a different city or dates). "
                "Always defer to the finding's stated facts over the user's "
                "original message, and do not also mention the user's "
                "original request as a separate or additional saved item — "
                "there is only what the finding states.\n\n"
            ) + "\n\n".join(f"[{key}]\n{value}" for key, value in sub_results.items())
        else:
            findings = "No specialist data was needed for this message."

        response = aggregator_chain.invoke(
            {"messages": state["messages"], "findings": findings}, config
        )
        reply_text = _extract_text(response) or (
            "Sorry, I couldn't generate a response. Please try again."
        )
        return {"messages": [AIMessage(content=reply_text)]}

    def route_from_supervisor(state: AgentState) -> list[str]:
        route = state.get("route") or []
        destinations = []
        if "weather" in route:
            destinations.append("weather_agent")
        if "news" in route:
            destinations.append("news_agent")
        if "itinerary" in route:
            destinations.append("itinerary_agent")
        return destinations or ["aggregator"]

    builder = StateGraph(AgentState)

    builder.add_node("supervisor", supervisor_node)
    builder.add_node("weather_agent", weather_agent_node)
    builder.add_node("news_agent", news_agent_node)
    builder.add_node("itinerary_agent", itinerary_agent_node)
    builder.add_node("aggregator", aggregator_node)

    builder.set_entry_point("supervisor")

    builder.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        ["weather_agent", "news_agent", "itinerary_agent", "aggregator"],
    )

    builder.add_edge("weather_agent", "aggregator")
    builder.add_edge("news_agent", "aggregator")
    builder.add_edge("itinerary_agent", "aggregator")
    builder.add_edge("aggregator", END)

    return builder.compile(checkpointer=checkpointer, name=GRAPH_NAME)
