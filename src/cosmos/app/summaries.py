"""AI-written summaries of leaks, made when leaks are refreshed and never at page load."""

from __future__ import annotations

import hashlib
import logging
from typing import Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from cosmos.app.calls import Recorder, call
from cosmos.app.facts import leak_facts
from cosmos.app.models import Leak
from cosmos.app.prompts import SUMMARY_SYSTEM, SUMMARY_VERSION
from cosmos.app.store import OPEN
from cosmos.llm import LLM, LLMError, LLMUnavailable, ungrounded_numbers

log = logging.getLogger("cosmos.llm")

MAX_WORDS: Final = 90
MAX_ATTEMPTS: Final = 2


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


def summarise_leaks(db: Session, tenant_id: str, llm: LLM, record: Recorder) -> int:
    """Write a summary for each open leak that lacks an up-to-date one. Returns how many.

    A summary is kept only if every figure in it appears in the leak's facts.
    One that fails is discarded and the page shows its rule-based text instead.
    """
    written = 0
    leaks = db.scalars(select(Leak).where(Leak.tenant_id == tenant_id, Leak.status == OPEN))
    for leak in leaks:
        facts = leak_facts(leak.reference, leak.payload)
        # A summary stays valid for as long as the facts, the prompt and the model do.
        key = hashlib.sha256(f"{SUMMARY_VERSION}|{llm.model}|{facts}".encode()).hexdigest()
        if leak.summary is not None and leak.summary.get("key") == key:
            continue
        try:
            text = _summarise(llm, record, facts)
        except LLMUnavailable:
            # The provider is down or refusing us. The remaining leaks would fail the same way.
            log.warning("summaries stopped early: the model is unavailable")
            break
        except LLMError:
            text = None
        leak.summary = None if text is None else {"text": text, "key": key, "model": llm.model}
        written += text is not None
    db.commit()
    return written


def _summarise(llm: LLM, record: Recorder, facts: str) -> str | None:
    prompt = f"Facts:\n{facts}"
    for _ in range(MAX_ATTEMPTS):
        completion = call(
            llm,
            record,
            purpose="summary",
            prompt_version=SUMMARY_VERSION,
            system=SUMMARY_SYSTEM,
            prompt=prompt,
            shape=Summary,
        )
        text = " ".join(completion.value.text.split())
        problem = _problem(text, facts)
        if problem is None:
            return text
        prompt = f"Facts:\n{facts}\n\nYour last attempt was rejected: {problem} Write it again."
    return None


def _problem(text: str, facts: str) -> str | None:
    if not text:
        return "it was empty."
    if len(text.split()) > MAX_WORDS:
        return "it was too long."
    stray = ungrounded_numbers(text, facts)
    if stray:
        return f"these figures are not in the facts: {', '.join(stray)}."
    return None
