"""HTTP routes: sign-in, and the leaks a signed-in user's tenant may see."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from cosmos.app.models import Leak, Membership, Tenant, User, UserSession
from cosmos.app.narrative import (
    CAUSE_LABELS,
    DRIVER_LABELS,
    narrate,
    platform_label,
    readings,
)
from cosmos.app.security import LoginThrottle, hash_token, new_session_token, verify_password
from cosmos.app.settings import Settings
from cosmos.app.store import OPEN

SESSION_COOKIE = "cosmos_session"
# An unexplained figure within this many standard deviations of ordinary variation is chance.
NOISE_BAND = 2.0

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


@router.get("/health")
def health(db: Database) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post("/auth/login")
def login(credentials: Credentials, request: Request, response: Response, db: Database) -> Account:
    settings: Settings = request.app.state.settings
    throttle: LoginThrottle = request.app.state.throttle
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
        throttle.record_failure(key)
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
    return _account(user, tenant)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Database) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/auth/me")
def me(principal: SignedIn) -> Account:
    return _account(principal.user, principal.tenant)


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


def _account(user: User, tenant: Tenant) -> Account:
    return Account(name=user.name, email=user.email, tenant_id=tenant.id, tenant_name=tenant.name)


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
