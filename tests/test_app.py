from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from cosmos.app.api import SESSION_COOKIE
from cosmos.app.db import make_engine, make_session_factory, migrate
from cosmos.app.main import create_app
from cosmos.app.models import Base, Leak, Membership, Tenant, User, UserSession
from cosmos.app.narrative import indian_grouping, narrate
from cosmos.app.pipeline import to_payload
from cosmos.app.security import LoginThrottle, hash_password, verify_password
from cosmos.app.settings import Settings
from cosmos.app.store import sync_leaks
from cosmos.engine import DailyPoint, Finding, RootCause

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
ORIGIN = "http://localhost:3000"
PASSWORD = "a-long-test-password"
NAMES = {"SKU-1": ("Acme Oats 1kg", "Acme"), "SKU-2": ("Acme Muesli 500g", "Acme")}


def _finding(**changes: object) -> Finding:
    base = Finding(
        id="LEAK-001",
        tenant_id="acme",
        cause=RootCause.STOCKOUT,
        platform="blinkit",
        category="Breakfast",
        cities=("Pune",),
        sku_ids=("SKU-1", "SKU-2"),
        start_date=date(2026, 7, 2),
        end_date=date(2026, 7, 6),
        expected_units=100.0,
        expected_gmv=20_000.0,
        actual_units=40.0,
        actual_gmv=8_000.0,
        loss_units={"availability": 55.0, "ads": 1.0},
        loss_gmv={"availability": 11_000.0, "ads": 200.0},
        loss_gmv_sd={"availability": 0.0, "ads": 0.0},
        unexplained_units=4.0,
        unexplained_gmv=800.0,
        noise_units=6.0,
        unreported_units=0.0,
        unreported_gmv=0.0,
        evidence={
            "availability_before": 0.96,
            "availability_during": 0.2,
            "warehouse_stockless_share": 0.0,
            "po_units_lapsed": 0.0,
        },
        daily=(
            DailyPoint(date(2026, 7, 1), 4_000.0, 4_100.0, True, False),
            DailyPoint(date(2026, 7, 2), 4_000.0, 1_500.0, True, True),
        ),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _payload(**changes: object) -> dict[str, Any]:
    return to_payload(_finding(**changes), NAMES)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}",
        cookie_secure=False,
        allowed_origins=(ORIGIN,),
        login_attempts=3,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def sessions(settings: Settings) -> Iterator[sessionmaker[Session]]:
    migrate(settings.database_url)
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    with factory() as db:
        for tenant, email in (("acme", "asha@acme.test"), ("globex", "gita@globex.test")):
            user_id = str(uuid.uuid4())
            db.add(Tenant(id=tenant, name=tenant.title()))
            db.add(
                User(
                    id=user_id,
                    email=email,
                    name=email.split("@")[0].title(),
                    password_hash=hash_password(PASSWORD),
                    created_at=NOW,
                )
            )
            db.flush()
            db.add(Membership(user_id=user_id, tenant_id=tenant))
        db.commit()
        sync_leaks(db, "acme", [_payload()], NOW)
        sync_leaks(db, "globex", [_payload(category="Snacks")], NOW)
    yield factory
    engine.dispose()


@pytest.fixture
def client(settings: Settings, sessions: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(create_app(settings), headers={"Origin": ORIGIN}) as test_client:
        yield test_client


def _sign_in(client: TestClient, email: str = "asha@acme.test", password: str = PASSWORD) -> Any:
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_migrations_build_exactly_the_schema_the_models_describe(settings: Settings) -> None:
    migrate(settings.database_url)
    migrate(settings.database_url)  # running it again is harmless
    engine = make_engine(settings.database_url)
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()
    assert differences == []


def test_password_hashes_verify_only_the_right_password() -> None:
    stored = hash_password(PASSWORD)
    assert stored != PASSWORD
    assert verify_password(stored, PASSWORD)
    assert not verify_password(stored, "something else")
    assert not verify_password(None, PASSWORD)
    assert not verify_password("not a hash", PASSWORD)


def test_throttle_pauses_after_repeated_failures_and_clears_on_success() -> None:
    throttle = LoginThrottle(attempts=2, window_seconds=60)
    assert throttle.retry_after("key") is None
    throttle.record_failure("key")
    throttle.record_failure("key")
    wait = throttle.retry_after("key")
    assert wait is not None and 0 < wait <= 61
    assert throttle.retry_after("another key") is None
    throttle.clear("key")
    assert throttle.retry_after("key") is None


def test_sign_in_sets_a_cookie_scripts_cannot_read(client: TestClient) -> None:
    response = _sign_in(client, email="  Asha@Acme.test ")
    assert response.status_code == 200
    assert response.json() == {
        "name": "Asha",
        "email": "asha@acme.test",
        "tenant_id": "acme",
        "tenant_name": "Acme",
    }
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/auth/me").json()["tenant_id"] == "acme"


def test_session_cookie_is_secure_unless_turned_off(
    settings: Settings, sessions: sessionmaker[Session]
) -> None:
    strict = settings.model_copy(update={"cookie_secure": True})
    with TestClient(create_app(strict), base_url="https://testserver") as secure_client:
        response = _sign_in(secure_client)
    assert "secure" in response.headers["set-cookie"].lower()


def test_session_token_is_stored_only_as_a_hash(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    _sign_in(client)
    token = client.cookies[SESSION_COOKIE]
    with sessions() as db:
        (stored,) = db.scalars(select(UserSession.token_hash)).all()
    assert stored != token and len(stored) == 64


@pytest.mark.parametrize(
    ("email", "password"),
    [("asha@acme.test", "wrong password"), ("nobody@acme.test", PASSWORD)],
)
def test_bad_credentials_get_one_answer(client: TestClient, email: str, password: str) -> None:
    response = _sign_in(client, email, password)
    assert response.status_code == 401
    assert response.json() == {"detail": "That email and password don't match."}
    assert SESSION_COOKIE not in client.cookies


def test_repeated_failures_are_paused_even_for_the_right_password(client: TestClient) -> None:
    for _ in range(3):
        assert _sign_in(client, password="wrong password").status_code == 401
    blocked = _sign_in(client)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    # Someone else signing in is unaffected.
    assert _sign_in(client, email="gita@globex.test").status_code == 200


def test_inactive_user_cannot_sign_in(client: TestClient, sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        db.execute(update(User).where(User.email == "asha@acme.test").values(is_active=False))
        db.commit()
    assert _sign_in(client).status_code == 401


def test_everything_else_needs_a_session(client: TestClient) -> None:
    for path in ("/api/auth/me", "/api/leaks", "/api/leaks/LK-0001"):
        assert client.get(path).status_code == 401, path


def test_sign_out_ends_the_session(client: TestClient) -> None:
    _sign_in(client)
    token = client.cookies[SESSION_COOKIE]
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set(SESSION_COOKIE, token)  # a copy of the old cookie is no use
    assert client.get("/api/auth/me").status_code == 401


def test_expired_session_is_refused(client: TestClient, sessions: sessionmaker[Session]) -> None:
    _sign_in(client)
    with sessions() as db:
        db.execute(update(UserSession).values(expires_at=NOW - timedelta(days=1)))
        db.commit()
    assert client.get("/api/leaks").status_code == 401


def test_request_from_another_site_cannot_change_anything(client: TestClient) -> None:
    _sign_in(client)
    response = client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert client.get("/api/auth/me").status_code == 200


def test_health_needs_no_session_and_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.json() == {"status": "ok"}
    assert len(response.headers["x-request-id"]) == 12


def test_leak_list_shows_only_the_signed_in_tenant(client: TestClient) -> None:
    _sign_in(client)
    body = client.get("/api/leaks").json()
    assert body["leaks"] == 1
    assert body["loss_gmv"] == 11_000.0
    (item,) = body["items"]
    assert item["reference"] == "LK-0001"
    assert item["category"] == "Breakfast"
    assert item["headline"] == "2 products went out of stock in Pune on Blinkit"
    assert item["cause_label"] == "Stockout"
    assert body["platforms"] == [{"id": "blinkit", "label": "Blinkit"}]


def test_leak_list_filters_scope_the_totals(client: TestClient) -> None:
    _sign_in(client)
    assert client.get("/api/leaks", params={"cause": "stockout"}).json()["leaks"] == 1
    empty = client.get("/api/leaks", params={"platform": "zepto"}).json()
    assert empty["items"] == [] and empty["loss_gmv"] == 0
    # The filter options do not shrink with the filter.
    assert empty["platforms"] == [{"id": "blinkit", "label": "Blinkit"}]


def test_leak_detail_carries_the_story_and_the_figures(client: TestClient) -> None:
    _sign_in(client)
    body = client.get("/api/leaks/LK-0001").json()
    assert "fell from 96% to 20% from 2 to 6 Jul" in body["what_happened"]
    assert "warehouse held stock" in body["why"]
    assert body["products"][0] == {"sku_id": "SKU-1", "name": "Acme Oats 1kg", "brand": "Acme"}
    assert body["unexplained_is_noise"] is True
    assert [driver["label"] for driver in body["drivers"]] == ["Availability", "Ads"]
    assert body["readings"][0] == {
        "label": "Dark stores with the products available",
        "unit": "percent",
        "before": 0.96,
        "during": 0.2,
    }
    assert body["daily"][1] == {
        "day": "2026-07-02",
        "expected_gmv": 4000.0,
        "actual_gmv": 1500.0,
        "complete": True,
        "in_window": True,
    }


def test_another_tenants_leak_looks_like_it_does_not_exist(client: TestClient) -> None:
    _sign_in(client, email="gita@globex.test")
    own = client.get("/api/leaks/LK-0001").json()
    assert own["category"] == "Snacks"
    assert client.get("/api/leaks/LK-9999").status_code == 404
    assert client.get("/api/leaks").json()["items"][0]["category"] == "Snacks"


def test_sync_is_idempotent_and_keeps_references(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        again = sync_leaks(db, "acme", [_payload()], NOW + timedelta(days=1))
        assert (again.created, again.updated, again.withdrawn) == (0, 1, 0)
        (leak,) = db.scalars(select(Leak).where(Leak.tenant_id == "acme")).all()
        assert leak.reference == "LK-0001"
        assert leak.detected_at == NOW
        assert leak.updated_at == NOW + timedelta(days=1)


def test_sync_follows_a_leak_as_it_grows_and_numbers_new_ones(
    sessions: sessionmaker[Session],
) -> None:
    grown = _payload(end_date=date(2026, 7, 9), cause=RootCause.SUPPLY_SHORTFALL)
    fresh = _payload(start_date=date(2026, 8, 1), end_date=date(2026, 8, 4))
    with sessions() as db:
        result = sync_leaks(db, "acme", [grown, fresh], NOW)
        assert (result.created, result.updated, result.withdrawn) == (1, 1, 0)
        leaks = {
            leak.reference: leak
            for leak in db.scalars(select(Leak).where(Leak.tenant_id == "acme"))
        }
        assert leaks["LK-0001"].end_date == date(2026, 7, 9)
        assert leaks["LK-0001"].cause == "supply_shortfall"
        assert leaks["LK-0002"].start_date == date(2026, 8, 1)


def test_sync_withdraws_what_the_engine_stops_reporting_and_can_reopen_it(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    _sign_in(client)
    with sessions() as db:
        assert sync_leaks(db, "acme", [], NOW).withdrawn == 1
    assert client.get("/api/leaks").json()["items"] == []
    with sessions() as db:
        back = sync_leaks(db, "acme", [_payload()], NOW)
        assert (back.created, back.updated) == (0, 1)
    assert client.get("/api/leaks").json()["items"][0]["reference"] == "LK-0001"


@pytest.mark.parametrize("cause", list(RootCause))
def test_every_cause_has_a_story(cause: RootCause) -> None:
    story = narrate(_payload(cause=cause))
    assert story.headline and story.what_happened and story.why
    assert "nan" not in f"{story.headline} {story.what_happened} {story.why}"


def test_story_mentions_a_second_driver_only_when_it_matters() -> None:
    minor = narrate(
        _payload(cause=RootCause.RANK_LOSS, loss_gmv={"visibility": 10_000.0, "ads": 500.0})
    )
    major = narrate(
        _payload(cause=RootCause.RANK_LOSS, loss_gmv={"visibility": 10_000.0, "ads": 4_000.0})
    )
    assert "No other driver moved enough to matter." in minor.why
    assert "Ads also cost sales" in major.why


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        (0, "0"),
        (999, "999"),
        (1_000, "1,000"),
        (123_456, "1,23,456"),
        (12_345_678.4, "1,23,45,678"),
    ],
)
def test_rupees_are_grouped_the_indian_way(value: float, shown: str) -> None:
    assert indian_grouping(value) == shown
