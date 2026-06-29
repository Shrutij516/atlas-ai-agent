from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., min_length=1)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The user's latest message")
    history: list[ChatMessage] = Field(
        default_factory=list,
        description="Prior conversation turns",
    )


class ChatResponse(BaseModel):
    response: str
    history: list[ChatMessage]
