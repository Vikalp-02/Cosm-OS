"""Make a model call and leave a record of it, whatever the outcome."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from cosmos.app.models import LlmCall
from cosmos.llm import (
    LLM,
    Completion,
    Effort,
    LLMError,
    LLMInvalidOutput,
    LLMRefused,
    LLMUnavailable,
)
from cosmos.llm.provider import Shape

log = logging.getLogger("cosmos.llm")


@dataclass(frozen=True, slots=True)
class CallRecord:
    purpose: str
    prompt_version: str
    # "ok", or why not: "unavailable", "refused", "invalid" or "error".
    outcome: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


Recorder = Callable[[CallRecord], None]


def call(
    llm: LLM,
    record: Recorder,
    *,
    purpose: str,
    prompt_version: str,
    system: str,
    prompt: str,
    shape: type[Shape],
    effort: Effort = "medium",
) -> Completion[Shape]:
    """Ask the model, record the call, and re-raise any failure for the caller to handle."""
    started = time.perf_counter()

    def note(outcome: str, completion: Completion[Shape] | None = None) -> None:
        record(
            CallRecord(
                purpose=purpose,
                prompt_version=prompt_version,
                outcome=outcome,
                model=completion.model if completion else llm.model,
                input_tokens=completion.input_tokens if completion else None,
                output_tokens=completion.output_tokens if completion else None,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        )

    try:
        completion = llm.complete(system=system, prompt=prompt, shape=shape, effort=effort)
    except LLMUnavailable:
        note("unavailable")
        raise
    except LLMRefused:
        note("refused")
        raise
    except LLMInvalidOutput:
        note("invalid")
        raise
    except LLMError:
        note("error")
        raise
    note("ok", completion)
    return completion


def database_recorder(sessions: sessionmaker[Session], tenant_id: str | None) -> Recorder:
    """A recorder that writes each call to the database in its own short transaction."""

    def record(entry: CallRecord) -> None:
        try:
            with sessions() as db:
                db.add(
                    LlmCall(
                        id=str(uuid.uuid4()),
                        tenant_id=tenant_id,
                        purpose=entry.purpose,
                        prompt_version=entry.prompt_version,
                        outcome=entry.outcome,
                        model=entry.model,
                        input_tokens=entry.input_tokens,
                        output_tokens=entry.output_tokens,
                        latency_ms=entry.latency_ms,
                        created_at=datetime.now(UTC),
                    )
                )
                db.commit()
        except Exception:
            # Losing a log line must never cost the user their answer.
            log.exception("could not record a model call")

    return record
