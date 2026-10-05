"""Keep the stored leaks in step with what the engine reports."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cosmos.app.models import Leak

OPEN = "open"
WITHDRAWN = "withdrawn"


@dataclass(frozen=True, slots=True)
class SyncResult:
    created: int
    updated: int
    withdrawn: int


def sync_leaks(
    db: Session, tenant_id: str, payloads: Sequence[dict[str, Any]], now: datetime
) -> SyncResult:
    """Store the engine's latest findings for a tenant. Running it twice changes nothing.

    The engine numbers its findings afresh on every run, so a finding is tied
    to the stored leak it continues: same platform and category, overlapping
    dates. That leak keeps its reference while its figures are brought up to
    date, which keeps links and conversations about it valid as a leak grows.
    A leak the engine no longer reports is withdrawn, not deleted.
    """
    stored = list(db.scalars(select(Leak).where(Leak.tenant_id == tenant_id)))
    number = max((int(leak.reference.split("-")[1]) for leak in stored), default=0)
    unclaimed = list(stored)
    created = updated = 0

    for payload in payloads:
        start = date.fromisoformat(payload["start_date"])
        end = date.fromisoformat(payload["end_date"])
        same_event = [
            leak
            for leak in unclaimed
            if leak.platform == payload["platform"]
            and leak.category == payload["category"]
            and leak.start_date <= end
            and start <= leak.end_date
        ]
        if same_event:
            leak = max(same_event, key=lambda leak: _shared_days(leak, start, end))
            unclaimed.remove(leak)
            updated += 1
        else:
            number += 1
            leak = Leak(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                reference=f"LK-{number:04d}",
                detected_at=now,
            )
            db.add(leak)
            created += 1
        leak.status = OPEN
        leak.cause = payload["cause"]
        leak.platform = payload["platform"]
        leak.category = payload["category"]
        leak.start_date = start
        leak.end_date = end
        leak.updated_at = now
        leak.payload = payload

    withdrawn = 0
    for leak in unclaimed:
        if leak.status == OPEN:
            leak.status = WITHDRAWN
            leak.updated_at = now
            withdrawn += 1
    db.commit()
    return SyncResult(created, updated, withdrawn)


def _shared_days(leak: Leak, start: date, end: date) -> int:
    return (min(leak.end_date, end) - max(leak.start_date, start)).days + 1
