from __future__ import annotations

from typing import Literal

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
    conversation_id: str | None = Field(
        default=None,
        description=(
            "Client-generated id, stable for the lifetime of one conversation "
            "(e.g. crypto.randomUUID() created once per page load/chat "
            "session). Used as the LangGraph checkpointer's thread_id so an "
            "interrupted itinerary proposal can be resumed via /approve, and "
            "so a pending proposal is visible across turns in the same "
            "conversation. If omitted, a random one-off id is used instead — "
            "fine for a single turn, but a later /approve call has no stable "
            "id to reference."
        ),
    )


class ChatResponse(BaseModel):
    response: str
    history: list[ChatMessage]


class ItineraryItemPayload(BaseModel):
    city: str
    start_date: str
    end_date: str
    notes: str = ""


class RiskAssessment(BaseModel):
    level: Literal["low", "medium", "high"]
    reasons: list[str]


class PendingApproval(BaseModel):
    thread_id: str
    proposed_item: ItineraryItemPayload
    risk: RiskAssessment
    context_note: str


class ApproveRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    decision: Literal["approve", "edit", "reject"]
    edited_item: ItineraryItemPayload | None = Field(
        default=None,
        description="Required when decision is 'edit'; the corrected item to save instead.",
    )
