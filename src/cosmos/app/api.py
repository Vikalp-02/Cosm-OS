"""HTTP routes: sign-in, and the leaks a signed-in user's tenant may see."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from cosmos.app.ask import Answer, LeakRecord, Progress, Turn, ask
from cosmos.app.calls import database_recorder
from cosmos.app.facts import NOISE_BAND
from cosmos.app.models import Leak, Membership, Tenant, User, UserSession
from cosmos.app.narrative import (
    CAUSE_LABELS,
    DRIVER_LABELS,
    narrate,
    platform_label,
    readings,
)
from cosmos.app.security import Throttle, hash_token, new_session_token, verify_password
from cosmos.app.settings import Settings
from cosmos.app.store import OPEN

SESSION_COOKIE = "cosmos_session"
log = logging.getLogger("cosmos.api")

router = APIRouter(prefix="/api")


@dataclass(frozen=True, slots=True)
class Principal:
    user: User
    tenant: Tenant
    token_hash: str


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as db:
        yield db


Database = Annotated[Session, Depends(get_db)]


def require_user(request: Request, db: Database) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    row = None
    if token:
        row = db.execute(
            select(UserSession, User, Tenant)
            .join(User, User.id == UserSession.user_id)
            .join(Tenant, Tenant.id == UserSession.tenant_id)
            .where(
                UserSession.token_hash == hash_token(token),
                UserSession.expires_at > datetime.now(UTC),
                User.is_active,
            )
        ).first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    session, user, tenant = row
    return Principal(user=user, tenant=tenant, token_hash=session.token_hash)


SignedIn = Annotated[Principal, Depends(require_user)]


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class Account(BaseModel):
    name: str
    email: str
    tenant_id: str
    tenant_name: str
    # Whether questions can be asked. False when no language model is configured.
    assistant: bool


class Option(BaseModel):
    id: str
    label: str


class LeakSummary(BaseModel):
    reference: str
    cause: str
    cause_label: str
    platform: str
    platform_label: str
    category: str | None
    # None when every city is affected.
    cities: list[str] | None
    product_count: int
    start_date: date
    end_date: date
    headline: str
    # Rupees lost to the named cause. None when there is no such figure: a data
    # gap loses no sales, and an unexplained drop has no cause to charge.
    loss_gmv: float | None
    loss_gmv_sd: float
    unexplained_gmv: float | None
    unreported_units: float


class CauseTotal(BaseModel):
    cause: str
    cause_label: str
    leaks: int
    loss_gmv: float


class LeakList(BaseModel):
    items: list[LeakSummary]
    leaks: int
    loss_gmv: float
    by_cause: list[CauseTotal]
    # What the filters can be set to, whatever they are set to now.
    platforms: list[Option]
    causes: list[Option]


class DriverLoss(BaseModel):
    key: str
    label: str
    loss_gmv: float
    loss_gmv_sd: float


class ReadingOut(BaseModel):
    label: str
    unit: str
    before: float | None
    during: float


class Day(BaseModel):
    day: date
    expected_gmv: float
    actual_gmv: float | None
    complete: bool
    in_window: bool


class Product(BaseModel):
    sku_id: str
    name: str
    brand: str


class LeakDetail(LeakSummary):
    # An AI-written summary, checked against the figures below. None if there is none.
    summary: str | None
    what_happened: str
    why: str
    products: list[Product]
    expected_gmv: float
    actual_gmv: float | None
    # True when the unexplained figure is no larger than ordinary day-to-day variation.
    unexplained_is_noise: bool
    unreported_gmv: float
    drivers: list[DriverLoss]
    readings: list[ReadingOut]
    daily: list[Day]
    detected_at: datetime


class PastTurn(BaseModel):
    question: str = Field(max_length=500)
    answer: str = Field(max_length=4000)
    references: list[str] = Field(default_factory=list, max_length=10)


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    # The leak whose page the question was asked from, if any.
    reference: str | None = Field(default=None, max_length=16)
    # The conversation so far, oldest first. The server keeps none of it.
    history: list[PastTurn] = Field(default_factory=list, max_length=4)


@router.get("/health")
def health(db: Database) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post("/auth/login")
def login(credentials: Credentials, request: Request, response: Response, db: Database) -> Account:
    settings: Settings = request.app.state.settings
    throttle: Throttle = request.app.state.throttle
    email = credentials.email.strip().lower()
    key = f"{email}|{request.client.host if request.client else ''}"

    wait = throttle.retry_after(key)
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many attempts. Try again in a few minutes.",
            headers={"Retry-After": str(wait)},
        )

    user = db.scalar(select(User).where(User.email == email, User.is_active))
    tenant = None
    if user is not None:
        tenant = db.scalar(
            select(Tenant)
            .join(Membership, Membership.tenant_id == Tenant.id)
            .where(Membership.user_id == user.id)
            .order_by(Tenant.id)
        )
    password_ok = verify_password(user.password_hash if user else None, credentials.password)
    if user is None or tenant is None or not password_ok:
        throttle.record(key)
        # One message for every failure, so it cannot be used to find out which emails exist.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That email and password don't match.")
    throttle.clear(key)

    now = datetime.now(UTC)
    token, token_hash = new_session_token()
    db.execute(delete(UserSession).where(UserSession.expires_at <= now))
    db.add(
        UserSession(
            token_hash=token_hash,
            user_id=user.id,
            tenant_id=tenant.id,
            created_at=now,
            expires_at=now + timedelta(hours=settings.session_hours),
        )
    )
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return _account(request, user, tenant)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Database) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/auth/me")
def me(request: Request, principal: SignedIn) -> Account:
    return _account(request, principal.user, principal.tenant)


@router.get("/leaks")
def list_leaks(
    principal: SignedIn,
    db: Database,
    platform: Annotated[str | None, Query(max_length=32)] = None,
    cause: Annotated[str | None, Query(max_length=32)] = None,
) -> LeakList:
    # The tenant comes from the session and nowhere else.
    leaks = list(
        db.scalars(
            select(Leak)
            .where(Leak.tenant_id == principal.tenant.id, Leak.status == OPEN)
            .order_by(Leak.start_date.desc(), Leak.reference)
        )
    )
    shown = [
        leak
        for leak in leaks
        if (platform is None or leak.platform == platform)
        and (cause is None or leak.cause == cause)
    ]
    items = [_summary(leak) for leak in shown]

    by_cause: dict[str, CauseTotal] = {}
    for item in items:
        total = by_cause.setdefault(
            item.cause,
            CauseTotal(cause=item.cause, cause_label=item.cause_label, leaks=0, loss_gmv=0.0),
        )
        total.leaks += 1
        total.loss_gmv += item.loss_gmv or 0.0
    return LeakList(
        items=items,
        leaks=len(items),
        loss_gmv=sum(item.loss_gmv or 0.0 for item in items),
        by_cause=sorted(by_cause.values(), key=lambda total: -total.loss_gmv),
        platforms=[
            Option(id=name, label=platform_label(name))
            for name in sorted({leak.platform for leak in leaks})
        ],
        causes=[
            Option(id=name, label=CAUSE_LABELS.get(name, name))
            for name in sorted({leak.cause for leak in leaks})
        ],
    )


@router.get("/leaks/{reference}")
def get_leak(reference: str, principal: SignedIn, db: Database) -> LeakDetail:
    leak = db.scalar(
        select(Leak).where(Leak.tenant_id == principal.tenant.id, Leak.reference == reference)
    )
    if leak is None:
        # Also what another tenant's reference gets: its existence is not confirmed.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No leak with that reference.")

    payload = leak.payload
    story = narrate(payload)
    unexplained_units = payload["unexplained_units"]
    return LeakDetail(
        **_summary(leak).model_dump(),
        summary=leak.summary["text"] if leak.summary else None,
        what_happened=story.what_happened,
        why=story.why,
        products=[Product(**product) for product in payload["products"]],
        expected_gmv=payload["expected_gmv"],
        actual_gmv=payload["actual_gmv"],
        unexplained_is_noise=(
            unexplained_units is not None
            and abs(unexplained_units) <= NOISE_BAND * payload["noise_units"]
        ),
        unreported_gmv=payload["unreported_gmv"],
        drivers=[
            DriverLoss(
                key=key,
                label=DRIVER_LABELS.get(key, key),
                loss_gmv=lost,
                loss_gmv_sd=payload["loss_gmv_sd"].get(key, 0.0),
            )
            for key, lost in payload["loss_gmv"].items()
        ],
        readings=[ReadingOut(**asdict(reading)) for reading in readings(payload["evidence"])],
        daily=[Day(**point) for point in payload["daily"]],
        detected_at=leak.detected_at,
    )


@router.post("/ask")
def ask_question(
    body: Question, request: Request, principal: SignedIn, db: Database
) -> StreamingResponse:
    """Answer a question about the tenant's leaks, as a stream of JSON lines.

    Each line is either `{"type": "progress", "text": ...}` or, last, the answer.
    Progress is sent as it happens because an answer can take several seconds.
    """
    llm = request.app.state.llm
    if llm is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Questions are not switched on yet."
        )
    throttle: Throttle = request.app.state.ask_throttle
    wait = throttle.retry_after(principal.user.id)
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "That's a lot of questions. Give it a minute and try again.",
            headers={"Retry-After": str(wait)},
        )
    throttle.record(principal.user.id)

    # Everything is read before streaming starts: the database session belongs
    # to the request and may be closed by the time the stream is consumed.
    leaks = [
        LeakRecord(leak.reference, leak.payload)
        for leak in db.scalars(
            select(Leak).where(Leak.tenant_id == principal.tenant.id, Leak.status == OPEN)
        )
    ]
    headlines = {leak.reference: narrate(leak.payload).headline for leak in leaks}
    record = database_recorder(request.app.state.sessions, principal.tenant.id)
    history = [Turn(turn.question, turn.answer, tuple(turn.references)) for turn in body.history]

    def lines() -> Iterator[str]:
        try:
            events = ask(
                body.question.strip(),
                leaks,
                llm,
                record,
                today=datetime.now(UTC).date(),
                history=history,
                on_page=body.reference,
            )
            for event in events:
                yield json.dumps(_event(event, headlines)) + "\n"
        except Exception:
            log.exception("answering a question failed")
            failed = Answer("unavailable", "Something went wrong on our side. Please try again.")
            yield json.dumps(_event(failed, headlines)) + "\n"

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        # Keep proxies from holding the lines back until the stream ends.
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _event(event: Progress | Answer, headlines: dict[str, str]) -> dict[str, Any]:
    if isinstance(event, Progress):
        return {"type": "progress", "text": event.text}
    return {
        "type": "answer",
        "status": event.status,
        "text": event.text,
        "citations": [
            {"reference": reference, "headline": headlines.get(reference, "")}
            for reference in event.references
        ],
    }


def _account(request: Request, user: User, tenant: Tenant) -> Account:
    return Account(
        name=user.name,
        email=user.email,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        assistant=request.app.state.llm is not None,
    )


def _summary(leak: Leak) -> LeakSummary:
    payload: dict[str, Any] = leak.payload
    return LeakSummary(
        reference=leak.reference,
        cause=leak.cause,
        cause_label=CAUSE_LABELS.get(leak.cause, leak.cause),
        platform=leak.platform,
        platform_label=platform_label(leak.platform),
        category=leak.category,
        cities=payload["cities"],
        product_count=len(payload["sku_ids"]),
        start_date=leak.start_date,
        end_date=leak.end_date,
        headline=narrate(payload).headline,
        loss_gmv=payload["cause_loss_gmv"],
        loss_gmv_sd=payload["cause_loss_gmv_sd"],
        unexplained_gmv=payload["unexplained_gmv"],
        unreported_units=payload["unreported_units"],
    )
