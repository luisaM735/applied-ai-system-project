"""Care-plan agent.

The agent reads the household schedule, retrieves species notes, proposes
one task per missing care topic, and revises that proposal when a guardrail
fails. Nothing is written onto a pet until `apply_suggestion` is called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pawpal_system import Owner, Scheduler, Task

from pawpal_ai.guardrails import (
    check_input,
    check_output_text,
    find_open_slot,
    format_minutes,
    overlaps,
    to_minutes,
    validate_suggestion_content,
)
from pawpal_ai.logging_utils import log_event
from pawpal_ai.retriever import KnowledgeRetriever, tokenize


# Confidence below this is shown to the owner as "please review".
REVIEW_BELOW = 0.45
# Questions farther from every note than this are refused instead of answered.
ANSWER_MIN_SCORE = 0.35

TOPIC_ANCHORS = {
    "exercise": ["walk", "exercise", "jog", "run"],
    "feeding": ["feed", "feeding", "food", "meal", "breakfast", "dinner"],
    "litter": ["litter"],
    "play": ["play", "enrichment"],
    "water": ["water", "tank", "bowl"],
    "cleaning": ["clean", "cage", "habitat", "tidy"],
    "safety": ["vet", "veterinarian", "emergency"],
}


@dataclass
class Suggestion:
    pet_name: str
    species: str
    description: str
    scheduled_time: str
    duration_minutes: int
    priority: str
    frequency: str
    rationale: str
    citation_id: str
    source: str
    confidence: float
    needs_human_review: bool
    rescheduled_from: str | None = None

    def render_line(self) -> str:
        moved = ""
        if self.rescheduled_from:
            moved = f" (moved from {self.rescheduled_from})"
        review = "yes" if self.needs_human_review else "no"
        return (
            f"{self.pet_name} — {self.description} at {self.scheduled_time} "
            f"({self.duration_minutes} min, {self.priority}, {self.frequency}){moved}\n"
            f"    confidence {self.confidence:.2f} | human review: {review}\n"
            f"    citation: {self.citation_id} ({self.source})\n"
            f"    why: {self.rationale}"
        )


@dataclass
class Rejection:
    pet_name: str
    description: str
    reason: str
    code: str
    citation_id: str


@dataclass
class CarePlan:
    suggestions: list[Suggestion] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "Care plan",
            "---------",
            "Human review is required before any suggestion is saved.",
            "The review flag is also set when confidence is below 0.45, the pet is under 2, or the time was moved.",
        ]
        if not self.suggestions:
            lines.append("Suggestions: none")
        else:
            lines.append("Suggestions:")
            for index, suggestion in enumerate(self.suggestions, start=1):
                lines.append(f"{index}. {suggestion.render_line()}")
        lines.append("")
        if not self.rejections:
            lines.append("Guardrail rejections: none")
        else:
            lines.append("Guardrail rejections:")
            for rejection in self.rejections:
                lines.append(
                    f"- {rejection.pet_name} / {rejection.description or rejection.citation_id}: "
                    f"{rejection.reason} [{rejection.code}]"
                )
        lines.append("")
        lines.append("Trace:")
        for index, step in enumerate(self.trace, start=1):
            lines.append(f"{index}. {step}")
        return "\n".join(lines)


@dataclass
class Answer:
    question: str
    text: str
    refused: bool
    confidence: float
    citation_id: str | None
    source: str | None
    trace: list[str]
    code: str

    def render(self) -> str:
        status = "refused" if self.refused else "answered"
        citation = self.citation_id or "none"
        source = f" ({self.source})" if self.source else ""
        lines = [
            f"Question: {self.question}",
            f"Status: {status} [{self.code}]",
            f"Confidence: {self.confidence:.2f}",
            f"Citation: {citation}{source}",
            "",
            self.text,
            "",
            "Trace:",
        ]
        for index, step in enumerate(self.trace, start=1):
            lines.append(f"{index}. {step}")
        return "\n".join(lines)


def age_group_for(age: int) -> str:
    if age <= 1:
        return "young"
    if age >= 8:
        return "senior"
    return "adult"


def topic_is_covered(tasks: list[Task], topic: str) -> bool:
    anchors = TOPIC_ANCHORS.get(topic, [topic])
    for task in tasks:
        words = set(tokenize(task.description))
        if any(anchor in words for anchor in anchors):
            return True
    return False


class CarePlanAgent:
    """Plan, retrieve, check, and revise. The owner accepts tasks separately."""

    def __init__(
        self,
        scheduler: Scheduler,
        retriever: KnowledgeRetriever,
        log_path: Path | None = None,
    ):
        self.scheduler = scheduler
        self.retriever = retriever
        self.log_path = log_path

    @property
    def owner(self) -> Owner:
        return self.scheduler.owner

    def build_daily_plan(self) -> CarePlan:
        plan = CarePlan()
        for warning in self.retriever.warnings:
            plan.trace.append(f"Knowledge warning: {warning}")
        pets = self.owner.pets
        if not pets:
            plan.trace.append("No pets on this owner yet. Add a pet before asking for a plan.")
            self._log("plan_empty", reason="no_pets")
            return plan

        todays_tasks = self.scheduler.get_todays_tasks()
        plan.trace.append(
            f"Observed {len(pets)} pet(s) and {len(todays_tasks)} task(s) scheduled for today."
        )
        busy: list[tuple[int, int]] = []
        for task in todays_tasks:
            try:
                busy.append((to_minutes(task.scheduled_time), max(task.duration_minutes, 1)))
            except (ValueError, AttributeError):
                plan.trace.append(
                    f"Ignored '{task.description}' while checking overlaps because its time was not HH:MM."
                )

        for pet in pets:
            self._plan_for_pet(pet, plan, busy)

        self._log(
            "plan_ready",
            suggestions=len(plan.suggestions),
            rejections=len(plan.rejections),
        )
        return plan

    def answer_question(self, question: str, pet_name: str | None = None) -> Answer:
        trace: list[str] = []
        decision = check_input(question)
        if not decision.allowed:
            trace.append(f"Input guardrail blocked the question ({decision.code}).")
            trace.append("Retrieval was skipped.")
            self._log("question_refused", code=decision.code, pet=pet_name or "")
            return Answer(
                question=question or "",
                text=decision.message,
                refused=True,
                confidence=0.0,
                citation_id=None,
                source=None,
                trace=trace,
                code=decision.code,
            )

        pet = self.owner.get_pet(pet_name) if pet_name else None
        if pet_name and pet is None:
            trace.append(f"No pet named '{pet_name}' is on this account.")
            self._log("question_refused", code="unknown_pet", pet=pet_name)
            return Answer(
                question=question,
                text=f"I don't see a pet named '{pet_name}'. Add the pet, or ask without a pet name.",
                refused=True,
                confidence=0.0,
                citation_id=None,
                source=None,
                trace=trace,
                code="unknown_pet",
            )

        species = pet.species if pet else None
        age_group = age_group_for(pet.age) if pet else None
        hits = self.retriever.retrieve(
            question,
            species=species,
            age_group=age_group,
            k=3,
        )
        if hits:
            preview = ", ".join(f"{hit.passage.id} ({hit.score:.2f})" for hit in hits)
            trace.append(f"Retrieved: {preview}.")
        else:
            trace.append("The knowledge base had no notes for that species.")

        best = hits[0] if hits else None
        if best is None or best.score < ANSWER_MIN_SCORE:
            score = best.score if best else 0.0
            trace.append(
                f"Best score {score:.2f} is below {ANSWER_MIN_SCORE:.2f}, so no answer was generated."
            )
            self._log("question_refused", code="low_confidence", score=score, pet=pet_name or "")
            return Answer(
                question=question,
                text=(
                    f"I don't have a care note that matches that closely enough "
                    f"(best score {score:.2f}). I won't guess. Ask about feeding, walks, "
                    "litter, water, cage or tank care, or signs that need a veterinarian."
                ),
                refused=True,
                confidence=score,
                citation_id=best.passage.id if best else None,
                source=best.passage.source if best else None,
                trace=trace,
                code="low_confidence",
            )

        if pet and best.passage.species not in (pet.species, "all"):
            trace.append("Top note was for a different species, so it was discarded.")
            self._log("question_refused", code="species_mismatch", pet=pet.name)
            return Answer(
                question=question,
                text=(
                    f"The closest note is about a {best.passage.species}, not a {pet.species}. "
                    "I won't apply it."
                ),
                refused=True,
                confidence=best.score,
                citation_id=best.passage.id,
                source=best.passage.source,
                trace=trace,
                code="species_mismatch",
            )

        who = "From the care notes"
        if pet:
            breed = f", {pet.breed}" if pet.breed else ""
            who = f"For {pet.name} ({pet.species}{breed}, {age_group})"
        text = (
            f"{who}: {best.passage.text} "
            f"[source: {best.passage.id}, {best.passage.source}] "
            "This is general care information, not a veterinary diagnosis or a medication instruction."
        )
        output_check = check_output_text(text)
        if not output_check.allowed:
            trace.append(f"Output guardrail blocked the draft answer ({output_check.code}).")
            self._log("question_refused", code=output_check.code, pet=pet_name or "")
            return Answer(
                question=question,
                text=output_check.message,
                refused=True,
                confidence=best.score,
                citation_id=best.passage.id,
                source=best.passage.source,
                trace=trace,
                code=output_check.code,
            )

        trace.append(f"Answered from {best.passage.id} only. Other notes were not mixed in.")
        self._log(
            "question_answered",
            citation=best.passage.id,
            score=best.score,
            pet=pet_name or "",
        )
        return Answer(
            question=question,
            text=text,
            refused=False,
            confidence=best.score,
            citation_id=best.passage.id,
            source=best.passage.source,
            trace=trace,
            code="ok",
        )

    def apply_suggestion(self, suggestion: Suggestion) -> bool:
        """Add an accepted suggestion. Returns False if the slot was taken."""
        pet = self.owner.get_pet(suggestion.pet_name)
        if pet is None:
            self._log("apply_failed", reason="missing_pet", pet=suggestion.pet_name)
            return False
        try:
            start = to_minutes(suggestion.scheduled_time)
        except ValueError:
            self._log("apply_failed", reason="bad_time", pet=suggestion.pet_name)
            return False

        for task in self.scheduler.get_todays_tasks():
            try:
                task_start = to_minutes(task.scheduled_time)
            except ValueError:
                continue
            if overlaps(start, suggestion.duration_minutes, task_start, max(task.duration_minutes, 1)):
                self._log("apply_failed", reason="overlap", pet=suggestion.pet_name)
                return False

        pet.add_task(
            Task(
                description=suggestion.description,
                scheduled_time=suggestion.scheduled_time,
                duration_minutes=suggestion.duration_minutes,
                priority=suggestion.priority or "medium",
                frequency=suggestion.frequency or "once",
                scheduled_date=date.today(),
            )
        )
        self._log(
            "suggestion_applied",
            pet=suggestion.pet_name,
            description=suggestion.description,
            time=suggestion.scheduled_time,
            citation=suggestion.citation_id,
            confidence=suggestion.confidence,
        )
        return True

    def _plan_for_pet(self, pet, plan: CarePlan, busy: list[tuple[int, int]]) -> None:
        age_group = age_group_for(pet.age)
        query = " ".join(part for part in (pet.species, pet.breed, age_group) if part)
        hits = self.retriever.retrieve(
            query,
            species=pet.species,
            age_group=age_group,
            k=len(self.retriever.passages),
        )
        if not hits:
            plan.trace.append(f"{pet.name}: no care notes for a {pet.species}. Nothing was invented.")
            self._log("retrieve_empty", pet=pet.name, species=pet.species)
            return

        preview = ", ".join(f"{hit.passage.id} ({hit.score:.2f})" for hit in hits[:6])
        article = "an" if age_group[:1] in "aeiou" else "a"
        plan.trace.append(
            f"{pet.name}: retrieved notes for {article} {age_group} {pet.species} — {preview}."
        )
        self._log(
            "retrieve",
            pet=pet.name,
            species=pet.species,
            query=query,
            hits=[{"id": hit.passage.id, "score": hit.score} for hit in hits[:6]],
        )

        best_by_topic = {}
        for hit in hits:
            passage = hit.passage
            if not passage.task_description or not passage.recommended_time:
                continue
            current = best_by_topic.get(passage.topic)
            if current is None or hit.score > current.score:
                best_by_topic[passage.topic] = hit

        if not best_by_topic:
            plan.trace.append(f"{pet.name}: notes were reference-only, so no tasks were proposed.")
            return

        pet_tasks = [
            task for task in self.scheduler.get_todays_tasks() if task.pet_name == pet.name
        ]
        ordered = sorted(best_by_topic.values(), key=lambda hit: hit.passage.recommended_time)
        for hit in ordered:
            passage = hit.passage
            if topic_is_covered(pet_tasks, passage.topic):
                plan.trace.append(
                    f"{pet.name}: skipped {passage.topic} because today's tasks already cover it."
                )
                continue
            supporting = f"{passage.text} {passage.task_description}"
            decision = validate_suggestion_content(
                passage.task_description,
                supporting,
                pet.species,
                passage.species,
            )
            if not decision.allowed:
                plan.rejections.append(
                    Rejection(
                        pet_name=pet.name,
                        description=passage.task_description,
                        reason=decision.message,
                        code=decision.code,
                        citation_id=passage.id,
                    )
                )
                plan.trace.append(
                    f"{pet.name}: rejected '{passage.task_description}' ({decision.code})."
                )
                self._log(
                    "suggestion_rejected",
                    pet=pet.name,
                    citation=passage.id,
                    code=decision.code,
                )
                continue
            try:
                desired = to_minutes(passage.recommended_time)
            except ValueError:
                plan.rejections.append(
                    Rejection(
                        pet_name=pet.name,
                        description=passage.task_description,
                        reason="The care note had a time that was not HH:MM.",
                        code="bad_time",
                        citation_id=passage.id,
                    )
                )
                continue

            slot = find_open_slot(desired, passage.duration_minutes, busy)
            if slot is None:
                plan.rejections.append(
                    Rejection(
                        pet_name=pet.name,
                        description=passage.task_description,
                        reason="No open time between 06:00 and 21:00 avoided existing tasks.",
                        code="no_slot",
                        citation_id=passage.id,
                    )
                )
                plan.trace.append(
                    f"{pet.name}: rejected '{passage.task_description}' because the day is full."
                )
                self._log("suggestion_rejected", pet=pet.name, citation=passage.id, code="no_slot")
                continue

            scheduled = format_minutes(slot)
            moved_from = passage.recommended_time if scheduled != passage.recommended_time else None
            review = hit.score < REVIEW_BELOW or pet.age <= 1 or moved_from is not None
            rationale = (
                f"Retrieved {passage.id} (score {hit.score:.2f}) from {passage.source}. "
                f"{_first_sentence(passage.text)}"
            )
            if moved_from:
                rationale += (
                    f" Moved from {moved_from} to {scheduled} so it does not overlap a task "
                    "already on the schedule."
                )
            suggestion = Suggestion(
                pet_name=pet.name,
                species=pet.species,
                description=passage.task_description,
                scheduled_time=scheduled,
                duration_minutes=passage.duration_minutes,
                priority=passage.priority or "medium",
                frequency=passage.frequency or "daily",
                rationale=rationale,
                citation_id=passage.id,
                source=passage.source,
                confidence=hit.score,
                needs_human_review=review,
                rescheduled_from=moved_from,
            )
            plan.suggestions.append(suggestion)
            busy.append((slot, passage.duration_minutes))
            if moved_from:
                plan.trace.append(
                    f"{pet.name}: proposed '{passage.task_description}' at {scheduled} "
                    f"(rescheduled from {moved_from}) using {passage.id}."
                )
                self._log(
                    "suggestion_rescheduled",
                    pet=pet.name,
                    citation=passage.id,
                    from_time=moved_from,
                    to_time=scheduled,
                    confidence=hit.score,
                )
            else:
                plan.trace.append(
                    f"{pet.name}: proposed '{passage.task_description}' at {scheduled} "
                    f"using {passage.id} (score {hit.score:.2f})."
                )
                self._log(
                    "suggestion_proposed",
                    pet=pet.name,
                    citation=passage.id,
                    time=scheduled,
                    confidence=hit.score,
                )

    def _log(self, event: str, **fields) -> None:
        try:
            log_event(event, log_path=self.log_path, **fields)
        except OSError:
            return


def _first_sentence(text: str) -> str:
    stripped = " ".join(text.split())
    for index, character in enumerate(stripped):
        if character == "." and index > 40:
            return stripped[: index + 1]
    return stripped
