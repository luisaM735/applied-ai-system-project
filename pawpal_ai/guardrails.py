"""Input and output checks for the care advisor.

These checks are deterministic on purpose. A suggestion is blocked when it
asks for a medication dose, cites another species, or overlaps a task the
owner already has. The caller records the reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


STOPWORDS = {
    "a", "an", "the", "and", "or", "to", "of", "for", "my", "me", "i", "is",
    "are", "was", "be", "should", "how", "what", "when", "does", "do", "can",
    "with", "on", "in", "each", "day", "please", "long", "often", "much",
    "many", "give", "get", "need", "needs", "about", "from", "that", "this",
    "it", "you", "your", "our", "we", "their", "they", "if", "at", "by",
}

INJECTION_PATTERNS = (
    r"ignore (all |any |the )?(previous |prior |above )?instructions",
    r"disregard (your |the )?(rules|instructions|prompt)",
    r"\bsystem prompt\b",
    r"\byou are now\b",
    r"\bjailbreak\b",
    r"\bdeveloper mode\b",
)

DOSAGE_PATTERNS = (
    r"\b\d+(\.\d+)?\s*(mg|mcg|ml|iu|cc|milligrams?|millilitres?|milliliters?)\b",
    r"\bhow (many|much).{0,40}\b(mg|milligram|dose|dosage|medication|medicine)\b",
    r"\b(dosage|dose)\b",
    r"\b(ibuprofen|acetaminophen|paracetamol|tylenol|aspirin|advil|motrin|naproxen|meloxicam)\b",
)

MAX_QUESTION_LENGTH = 500
DAY_START = 6 * 60
DAY_END = 21 * 60
SLOT_STEP = 15


@dataclass
class GuardrailResult:
    allowed: bool
    message: str
    code: str = "ok"
    reasons: list[str] = field(default_factory=list)


def check_input(text: str) -> GuardrailResult:
    """Decide whether a free-text question may be answered."""
    if text is None or not str(text).strip():
        return GuardrailResult(
            False,
            "Enter a care question, or generate a daily plan from the pets you already added.",
            code="empty",
        )
    cleaned = str(text).strip()
    if len(cleaned) > MAX_QUESTION_LENGTH:
        return GuardrailResult(
            False,
            f"Please keep questions under {MAX_QUESTION_LENGTH} characters.",
            code="too_long",
        )
    lowered = cleaned.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            return GuardrailResult(
                False,
                "I can't follow instructions to ignore PawPal's care rules. Ask a pet-care question instead.",
                code="injection",
            )
    for pattern in DOSAGE_PATTERNS:
        if re.search(pattern, lowered):
            return GuardrailResult(
                False,
                "I can't recommend a medication dose. Call a veterinarian before giving any medicine, including human pain relievers.",
                code="dosage",
            )
    return GuardrailResult(True, "Question accepted.", code="ok")


def contains_dosage(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in DOSAGE_PATTERNS)


def validate_suggestion_content(
    description: str,
    supporting_text: str,
    pet_species: str,
    passage_species: str,
) -> GuardrailResult:
    """Block suggestions that are empty, cross-species, or dose-like."""
    if passage_species not in (pet_species, "all"):
        return GuardrailResult(
            False,
            f"Guidance written for a {passage_species} was not applied to a {pet_species}.",
            code="species_mismatch",
        )
    if not description or not description.strip():
        return GuardrailResult(
            False,
            "The retrieved note did not name a task, so nothing was scheduled.",
            code="empty_task",
        )
    blob = f"{description} {supporting_text}"
    if contains_dosage(blob):
        return GuardrailResult(
            False,
            "A retrieved note included a medication dose, so PawPal dropped that suggestion.",
            code="dosage",
        )
    return GuardrailResult(True, "Suggestion content accepted.", code="ok")


def check_output_text(text: str) -> GuardrailResult:
    """Last check on text that would be shown to the owner."""
    if contains_dosage(text):
        return GuardrailResult(
            False,
            "I can't recommend a medication dose. Call a veterinarian before giving any medicine.",
            code="dosage",
        )
    return GuardrailResult(True, "Output accepted.", code="ok")


def to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def format_minutes(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def overlaps(start: int, duration: int, other_start: int, other_duration: int) -> bool:
    end = start + duration
    other_end = other_start + other_duration
    return start < other_end and other_start < end


def find_open_slot(
    desired: int,
    duration: int,
    busy: list[tuple[int, int]],
) -> int | None:
    """Return the first start time at or after `desired` that does not overlap."""
    if duration <= 0 or duration > 180:
        return None
    candidate = max(desired, DAY_START)
    latest_start = DAY_END - duration
    while candidate <= latest_start:
        if not any(overlaps(candidate, duration, start, length) for start, length in busy):
            return candidate
        candidate += SLOT_STEP
    return None
