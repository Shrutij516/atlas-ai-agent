from __future__ import annotations

from datetime import date, datetime

from db import list_itinerary_items

MAX_TRIP_NIGHTS = 21


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def assess_itinerary_risk(city: str, start_date: str, end_date: str) -> dict:
    """Deterministic, rule-based risk assessment for a proposed itinerary item.

    No LLM call — dates must already be normalized to ISO YYYY-MM-DD (see
    tools._normalize_date) before reaching this function.
    """
    try:
        start = _parse_iso_date(start_date)
    except ValueError:
        return {
            "level": "high",
            "reasons": [f'start_date "{start_date}" is not a valid ISO date.'],
        }
    try:
        end = _parse_iso_date(end_date)
    except ValueError:
        return {
            "level": "high",
            "reasons": [f'end_date "{end_date}" is not a valid ISO date.'],
        }

    reasons: list[str] = []
    severities: list[str] = []
    today = date.today()

    if start < today:
        reasons.append(f"Start date {start_date} is in the past.")
        severities.append("high")

    if end < start:
        reasons.append(f"End date {end_date} is before start date {start_date}.")
        severities.append("high")

    nights = (end - start).days
    if nights > MAX_TRIP_NIGHTS:
        reasons.append(
            f"Trip is {nights} nights, longer than the {MAX_TRIP_NIGHTS}-night threshold."
        )
        severities.append("medium")

    for item in list_itinerary_items():
        if item["city"].strip().lower() != city.strip().lower():
            continue
        try:
            existing_start = _parse_iso_date(item["start_date"])
            existing_end = _parse_iso_date(item["end_date"])
        except ValueError:
            continue
        # Strict inequality: end_date is a checkout day, so a trip starting
        # the day another ends is a normal back-to-back stay, not a clash.
        if start < existing_end and existing_start < end:
            reasons.append(
                f"Overlaps an existing {item['city']} trip from "
                f"{item['start_date']} to {item['end_date']} (id {item['id']})."
            )
            severities.append("medium")

    if "high" in severities:
        level = "high"
    elif "medium" in severities:
        level = "medium"
    else:
        level = "low"

    return {"level": level, "reasons": reasons}
