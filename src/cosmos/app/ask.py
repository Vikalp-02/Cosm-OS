"""Answer a question about a tenant's leaks.

A small graph of four steps:

    plan     the model turns the question into filters over the leaks on record
    retrieve code picks the matching leaks; the model never sees the others
    write    the model answers from those leaks' facts alone
    check    code rejects any figure or reference the facts do not contain

A rejected answer goes back to be rewritten once. If that fails too, or the
model cannot be reached, the reader gets a plain listing built by rule, so a
question always gets something true back.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Final, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, create_model

from cosmos.app.calls import Recorder, call
from cosmos.app.facts import leak_facts, long_day, rupees
from cosmos.app.narrative import CAUSE_LABELS, narrate, platform_label
from cosmos.app.prompts import ANSWER_SYSTEM, ANSWER_VERSION, PLAN_SYSTEM, PLAN_VERSION
from cosmos.llm import LLM, LLMError, ungrounded_numbers

MAX_LEAKS: Final = 5
MAX_ATTEMPTS: Final = 2
MAX_WORDS: Final = 160
_REFERENCE: Final = re.compile(r"\bLK-\d+\b")

Status = Literal["answered", "listed", "no_match", "off_topic", "unavailable"]


@dataclass(frozen=True, slots=True)
class LeakRecord:
    reference: str
    payload: dict[str, Any]

    @property
    def start(self) -> date:
        return date.fromisoformat(self.payload["start_date"])

    @property
    def end(self) -> date:
        return date.fromisoformat(self.payload["end_date"])


@dataclass(frozen=True, slots=True)
class Turn:
    question: str
    answer: str
    references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Progress:
    """A line saying what is happening, in the reader's terms."""

    text: str


@dataclass(frozen=True, slots=True)
class Answer:
    # "answered" is the model's own words, checked. "listed" is the rule-built
    # fallback. The rest say why there is nothing to report.
    status: Status
    text: str
    references: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Plan:
    about_leaks: bool
    references: tuple[str, ...]
    platforms: tuple[str, ...]
    cities: tuple[str, ...]
    categories: tuple[str, ...]
    causes: tuple[str, ...]
    date_from: date | None
    date_to: date | None
    order: str

    @property
    def narrows(self) -> bool:
        return bool(
            self.platforms
            or self.cities
            or self.categories
            or self.causes
            or self.date_from
            or self.date_to
        )


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    cited: list[str]


class _State(TypedDict, total=False):
    plan: _Plan
    picked: list[LeakRecord]
    draft: _Draft
    feedback: str
    attempts: int
    answer: Answer


# What the reader is told once each step has finished and the next begins.
_NEXT: Final = {
    "plan": "Finding the leaks that matter",
    "retrieve": "Writing the answer",
    "write": "Checking the figures",
    "check": "Rewriting to match the figures",
}
_OFF_TOPIC: Final = (
    "I can answer questions about your revenue leaks: what was lost, where, when and why."
)
_NO_MATCH: Final = (
    "No leaks on record match that. Try a different platform, city, category or period."
)
_UNAVAILABLE: Final = "I couldn't work on that just now. Please try again in a moment."


def ask(
    question: str,
    leaks: Sequence[LeakRecord],
    llm: LLM,
    record: Recorder,
    *,
    today: date,
    history: Sequence[Turn] = (),
    on_page: str | None = None,
) -> Iterator[Progress | Answer]:
    """Yield progress lines, then exactly one `Answer`.

    `on_page` is the reference of the leak the reader is looking at, if any. A
    question that names no other leak and no filter is taken to be about it.
    """
    if not leaks:
        yield Answer("no_match", "There are no leaks on record yet.")
        return

    by_reference = {leak.reference: leak for leak in leaks}

    def plan(_: _State) -> _State:
        try:
            completion = call(
                llm,
                record,
                purpose="plan",
                prompt_version=PLAN_VERSION,
                system=PLAN_SYSTEM,
                prompt=_plan_prompt(question, leaks, today, history, on_page),
                shape=_plan_shape(leaks),
                effort="low",
            )
        except LLMError:
            return {"answer": Answer("unavailable", _UNAVAILABLE)}
        chosen = _read_plan(completion.value)
        if not chosen.about_leaks:
            return {"answer": Answer("off_topic", _OFF_TOPIC)}
        return {"plan": chosen}

    def retrieve(state: _State) -> _State:
        picked = _select(leaks, by_reference, state["plan"], on_page)
        if not picked:
            return {"answer": Answer("no_match", _NO_MATCH)}
        return {"picked": picked}

    def write(state: _State) -> _State:
        facts = _facts(state["picked"])
        prompt = f"Leaks provided: {len(state['picked'])}\n\n{facts}\n\n"
        prompt += _conversation(history)
        prompt += f"Question: {question}"
        if state.get("feedback"):
            prompt += f"\n\nYour last answer was rejected: {state['feedback']} Write it again."
        try:
            completion = call(
                llm,
                record,
                purpose="answer",
                prompt_version=ANSWER_VERSION,
                system=ANSWER_SYSTEM,
                prompt=prompt,
                shape=_Draft,
            )
        except LLMError:
            return {"answer": _listing(state["picked"])}
        return {"draft": completion.value, "attempts": state.get("attempts", 0) + 1}

    def check(state: _State) -> _State:
        picked = state["picked"]
        allowed = {leak.reference for leak in picked}
        text = " ".join(state["draft"].answer.split())
        named = set(_REFERENCE.findall(text)) | set(state["draft"].cited)

        problem = None
        if not text:
            problem = "it was empty."
        elif len(text.split()) > MAX_WORDS:
            problem = "it was too long."
        elif named - allowed:
            problem = (
                f"it refers to leaks that were not provided: {', '.join(sorted(named - allowed))}."
            )
        # Figures may come from the facts, the count of leaks, or the question itself.
        elif stray := ungrounded_numbers(text, f"{len(picked)}\n{question}\n{_facts(picked)}"):
            problem = f"these figures are not in the facts: {', '.join(stray)}."

        if problem is None:
            cited = tuple(leak.reference for leak in picked if leak.reference in named)
            return {"answer": Answer("answered", text, cited)}
        if state["attempts"] >= MAX_ATTEMPTS:
            return {"answer": _listing(picked)}
        return {"feedback": problem}

    def done_or(step: str) -> Any:
        return lambda state: END if "answer" in state else step

    steps: dict[str, Any] = {"plan": plan, "retrieve": retrieve, "write": write, "check": check}
    graph = StateGraph(_State)
    for name, step in steps.items():
        graph.add_node(name, step)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", done_or("retrieve"), ["retrieve", END])
    graph.add_conditional_edges("retrieve", done_or("write"), ["write", END])
    graph.add_conditional_edges("write", done_or("check"), ["check", END])
    graph.add_conditional_edges("check", done_or("write"), ["write", END])

    yield Progress("Reading your question")
    for update in graph.compile().stream({"attempts": 0}, stream_mode="updates"):
        for step, change in update.items():
            if change and "answer" in change:
                yield change["answer"]
                return
            yield Progress(_NEXT[step])
    yield Answer("unavailable", _UNAVAILABLE)


def _plan_shape(leaks: Sequence[LeakRecord]) -> type[BaseModel]:
    """The plan's shape, with each filter limited to values that exist for this tenant.

    The model cannot name a platform or city that is not on record, because
    the schema it has to fill in does not contain one.
    """

    def any_of(name: str, values: set[str]) -> Any:
        if not values:
            return list[str]
        # An enumeration, so the schema lists the allowed values even when there is one.
        enumeration: Any = StrEnum
        choices: Any = enumeration(name, {value: value for value in sorted(values)})
        return list[choices]

    cities = {city for leak in leaks for city in leak.payload["cities"] or ()}
    categories = {leak.payload["category"] for leak in leaks if leak.payload["category"]}
    shape: type[BaseModel] = create_model(
        "Plan",
        __config__=ConfigDict(extra="forbid"),
        about_leaks=(bool, ...),
        references=(list[str], ...),
        platforms=(any_of("Platform", {leak.payload["platform"] for leak in leaks}), ...),
        cities=(any_of("City", cities), ...),
        categories=(any_of("Category", categories), ...),
        causes=(any_of("Cause", {leak.payload["cause"] for leak in leaks}), ...),
        date_from=(str | None, ...),
        date_to=(str | None, ...),
        order=(Literal["largest", "newest"], ...),
    )
    return shape


def _read_plan(raw: BaseModel) -> _Plan:
    values = raw.model_dump(mode="json")

    def day(text: str | None) -> date | None:
        try:
            return date.fromisoformat(text) if text else None
        except ValueError:
            return None

    return _Plan(
        about_leaks=bool(values["about_leaks"]),
        references=tuple(ref.upper() for ref in values["references"]),
        platforms=tuple(values["platforms"]),
        cities=tuple(values["cities"]),
        categories=tuple(values["categories"]),
        causes=tuple(values["causes"]),
        date_from=day(values["date_from"]),
        date_to=day(values["date_to"]),
        order=values["order"],
    )


def _plan_prompt(
    question: str,
    leaks: Sequence[LeakRecord],
    today: date,
    history: Sequence[Turn],
    on_page: str | None,
) -> str:
    cities = sorted({city for leak in leaks for city in leak.payload["cities"] or ()})
    categories = sorted({leak.payload["category"] for leak in leaks if leak.payload["category"]})
    platforms = sorted({leak.payload["platform"] for leak in leaks})
    causes = sorted({leak.payload["cause"] for leak in leaks})
    lines = [
        f"Today: {today.isoformat()}",
        f"Leaks on record: {len(leaks)}, from {min(leak.start for leak in leaks).isoformat()}"
        f" to {max(leak.end for leak in leaks).isoformat()}",
        "Platforms: " + ", ".join(f"{name} ({platform_label(name)})" for name in platforms),
        "Cities: " + (", ".join(cities) or "none named"),
        "Categories: " + (", ".join(categories) or "none"),
        "Causes: " + ", ".join(f"{name} ({CAUSE_LABELS.get(name, name)})" for name in causes),
    ]
    if on_page:
        lines.append(f"The reader is looking at leak {on_page}.")
    return "\n".join(lines) + "\n\n" + _conversation(history) + f"Question: {question}"


def _conversation(history: Sequence[Turn]) -> str:
    if not history:
        return ""
    turns = [
        f"Earlier question: {turn.question}\nEarlier answer"
        f" (about {', '.join(turn.references) or 'no particular leak'}): {turn.answer}"
        for turn in history
    ]
    return "\n".join(turns) + "\n\n"


def _select(
    leaks: Sequence[LeakRecord],
    by_reference: dict[str, LeakRecord],
    plan: _Plan,
    on_page: str | None,
) -> list[LeakRecord]:
    named = [by_reference[ref] for ref in plan.references if ref in by_reference]
    if named:
        return named[:MAX_LEAKS]
    if not plan.narrows and on_page in by_reference:
        return [by_reference[on_page]]

    def matches(leak: LeakRecord) -> bool:
        payload = leak.payload
        cities = payload["cities"]
        return (
            (not plan.platforms or payload["platform"] in plan.platforms)
            and (not plan.categories or payload["category"] in plan.categories)
            and (not plan.causes or payload["cause"] in plan.causes)
            # A leak across all cities includes any city asked about.
            and (not plan.cities or cities is None or bool(set(cities) & set(plan.cities)))
            and (plan.date_from is None or leak.end >= plan.date_from)
            and (plan.date_to is None or leak.start <= plan.date_to)
        )

    def size(leak: LeakRecord) -> float:
        return float(leak.payload["cause_loss_gmv"] or leak.payload["unexplained_gmv"] or 0.0)

    found = [leak for leak in leaks if matches(leak)]
    if plan.order == "largest":
        found.sort(key=lambda leak: (-size(leak), leak.reference))
    else:
        found.sort(key=lambda leak: (leak.start, leak.reference), reverse=True)
    return found[:MAX_LEAKS]


def _facts(leaks: Sequence[LeakRecord]) -> str:
    return "\n\n".join(leak_facts(leak.reference, leak.payload) for leak in leaks)


def _listing(leaks: Sequence[LeakRecord]) -> Answer:
    """What the reader gets when the model's own answer cannot be used."""
    lines = []
    for leak in leaks:
        payload = leak.payload
        lost = payload["cause_loss_gmv"]
        cost = f" {rupees(lost)} lost." if lost is not None else ""
        lines.append(
            f"{leak.reference}: {narrate(payload).headline}, {long_day(leak.start)}"
            f" to {long_day(leak.end)}.{cost}"
        )
    text = "I couldn't put a full answer together, but here is what is on record. " + " ".join(
        lines
    )
    return Answer("listed", text, tuple(leak.reference for leak in leaks))
