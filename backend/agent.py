from __future__ import annotations

from functools import lru_cache

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_groq import ChatGroq
from langgraph.prebuilt import create_react_agent

from config import get_settings
from prompts import SYSTEM_PROMPT
from tools import ALL_TOOLS


@lru_cache
def get_agent():
    settings = get_settings()
    settings.require_groq_key()

    llm = ChatGroq(
        model=settings.groq_model,
        temperature=0.2,
        groq_api_key=settings.groq_api_key,
    )

    # Groq requires OpenAI-style tool schemas with tool_choice="auto".
    # Pre-bind tools so create_react_agent does not re-bind with defaults.
    model_with_tools = llm.bind_tools(ALL_TOOLS, tool_choice="auto")

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder("messages"),
        ]
    )

    return create_react_agent(model_with_tools, ALL_TOOLS, prompt=prompt)
