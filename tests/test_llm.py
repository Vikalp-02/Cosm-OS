from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cosmos.app.ask import Answer, LeakRecord, Progress, Turn, ask
from cosmos.app.assistant import make_llm
from cosmos.app.calls import CallRecord, database_recorder
from cosmos.app.db import make_engine, make_session_factory, migrate
from cosmos.app.facts import leak_facts
from cosmos.app.main import create_app
from cosmos.app.models import Leak, LlmCall, Membership, Tenant, User
from cosmos.app.pipeline import to_payload
from cosmos.app.security import hash_password
from cosmos.app.settings import Settings
from cosmos.app.store import sync_leaks
from cosmos.app.summaries import summarise_leaks
from cosmos.engine import DailyPoint, Finding, RootCause
from cosmos.llm import (
    Completion,
    Effort,
    LLMError,
    LLMInvalidOutput,
    LLMRefused,
    LLMUnavailable,
    numbers_in,
    ungrounded_numbers,
)
from cosmos.llm.claude import ClaudeLLM
from cosmos.llm.provider import Shape

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
TODAY = date(2026, 9, 30)
ORIGIN = "http://localhost:3000"
PASSWORD = "a-long-test-password"


@dataclass
class FakeLLM:
    """A scripted model. Each reply is a dict to return, or an exception to raise."""

    replies: list[dict[str, Any] | Exception]
    model: str = "fake-model"
    prompts: list[str] = field(default_factory=list)
    efforts: list[str] = field(default_factory=list)

    def complete(
        self, *, system: str, prompt: str, shape: type[Shape], effort: Effort = "medium"
    ) -> Completion[Shape]:
        self.prompts.append(prompt)
        self.efforts.append(effort)
        if not self.replies:
            raise AssertionError("the model was called more often than the test scripted")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        try:
            value = shape.model_validate(reply)
        except ValidationError as error:
            # What a real provider reports when a reply does not fit the shape.
            raise LLMInvalidOutput("reply did not fit") from error
        return Completion(value, self.model, 120, 40)


def _plan(**filters: Any) -> dict[str, Any]:
    return {
        "about_leaks": True,
        "references": [],
        "platforms": [],
        "cities": [],
        "categories": [],
        "causes": [],
        "date_from": None,
        "date_to": None,
        "order": "largest",
        **filters,
    }


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
        loss_units={"availability": 55.0},
        loss_gmv={"availability": 11_250.0},
        loss_gmv_sd={"availability": 0.0},
        unexplained_units=4.0,
        unexplained_gmv=750.0,
        noise_units=6.0,
        unreported_units=0.0,
        unreported_gmv=0.0,
        evidence={"availability_before": 0.96, "availability_during": 0.2},
        daily=(DailyPoint(date(2026, 7, 2), 4_000.0, 1_500.0, True, True),),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


STOCKOUT = LeakRecord("LK-0001", to_payload(_finding(), {}))
PRICE_CUT = LeakRecord(
    "LK-0002",
    to_payload(
        _finding(
            cause=RootCause.COMPETITOR_PRICE_CUT,
            platform="zepto",
            category="Snacks",
            cities=None,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 7),
            loss_gmv={"rival_price": 93_988.0},
            loss_gmv_sd={"rival_price": 13_341.0},
            evidence={"rival_price_to_mrp_before": 0.88, "rival_price_to_mrp_during": 0.72},
        ),
        {},
    ),
)
LEAKS = [STOCKOUT, PRICE_CUT]


def _run(question: str, llm: FakeLLM, **options: Any) -> tuple[list[str], Answer, list[CallRecord]]:
    calls: list[CallRecord] = []
    events = list(ask(question, LEAKS, llm, calls.append, today=TODAY, **options))
    progress = [event.text for event in events if isinstance(event, Progress)]
    (answer,) = [event for event in events if isinstance(event, Answer)]
    assert isinstance(events[-1], Answer)
    return progress, answer, calls


def test_numbers_are_compared_without_their_grouping() -> None:
    assert numbers_in("₹1,23,456 lost, 88% to 72%, over 7 days. See LK-0009.") == {
        "123456",
        "88",
        "72",
        "7",
        "0009",
    }


def test_a_rounded_or_invented_figure_is_caught() -> None:
    facts = "Cost: ₹93,988 lost. Readings: 88% before, 72% during."
    assert ungrounded_numbers("About ₹93,988 was lost as prices fell to 72%.", facts) == []
    assert ungrounded_numbers("About ₹94,000 was lost, a 16 point drop.", facts) == ["16", "94000"]


def test_facts_state_every_figure_the_way_a_reader_sees_it() -> None:
    facts = leak_facts(PRICE_CUT.reference, PRICE_CUT.payload)
    assert "[LK-0002] Competitor price cut" in facts
    assert "Where: Zepto, Snacks, all cities" in facts
    assert "When: 1 Sep 2026 to 7 Sep 2026 (7 days)" in facts
    assert "₹93,988 of sales lost to this cause. This is an estimate" in facts
    assert "off by about ₹13,341 either way" in facts
    assert "88% before, 72% during" in facts
    direct = leak_facts(STOCKOUT.reference, STOCKOUT.payload)
    assert "₹11,250 of sales lost to this cause, worked out directly" in direct
    assert "which is within normal day-to-day variation" in direct


def test_question_is_planned_retrieved_written_and_checked() -> None:
    llm = FakeLLM(
        [
            _plan(platforms=["zepto"]),
            {
                "answer": "LK-0002 cost ₹93,988 as rivals went from 88% to 72%.",
                "cited": ["LK-0002"],
            },
        ]
    )
    progress, answer, calls = _run("What did we lose on Zepto?", llm)

    assert answer == Answer(
        "answered", "LK-0002 cost ₹93,988 as rivals went from 88% to 72%.", ("LK-0002",)
    )
    assert progress == [
        "Reading your question",
        "Finding the leaks that matter",
        "Writing the answer",
        "Checking the figures",
    ]
    assert [(call.purpose, call.outcome) for call in calls] == [("plan", "ok"), ("answer", "ok")]
    assert calls[0].prompt_version == "plan.v1" and calls[0].input_tokens == 120
    assert llm.efforts == ["low", "medium"]
    # Only the Zepto leak's facts reached the writer.
    assert "[LK-0002]" in llm.prompts[1] and "[LK-0001]" not in llm.prompts[1]


def test_the_planner_can_only_choose_values_that_exist() -> None:
    llm = FakeLLM([_plan(platforms=["amazon"])])
    _, answer, calls = _run("What about Amazon?", llm)
    # The reply does not fit the schema, which lists only this tenant's platforms.
    assert answer.status == "unavailable"
    assert [call.outcome for call in calls] == ["invalid"]


def test_an_ungrounded_answer_is_sent_back_once_with_the_reason() -> None:
    llm = FakeLLM(
        [
            _plan(causes=["competitor_price_cut"]),
            {"answer": "Roughly ₹94,000 was lost.", "cited": ["LK-0002"]},
            {"answer": "₹93,988 was lost.", "cited": ["LK-0002"]},
        ]
    )
    progress, answer, _ = _run("How much did the price cut cost?", llm)
    assert answer == Answer("answered", "₹93,988 was lost.", ("LK-0002",))
    assert progress[-2:] == ["Rewriting to match the figures", "Checking the figures"]
    assert "these figures are not in the facts: 94000" in llm.prompts[2]


def test_a_second_ungrounded_answer_falls_back_to_a_listing() -> None:
    wrong = {"answer": "Roughly ₹94,000 was lost.", "cited": ["LK-0002"]}
    _, answer, _ = _run("How much?", FakeLLM([_plan(platforms=["zepto"]), wrong, dict(wrong)]))
    assert answer.status == "listed"
    assert answer.references == ("LK-0002",)
    assert "LK-0002: Competitors cut prices on Snacks on Zepto" in answer.text
    assert "₹93,988 lost." in answer.text


def test_an_answer_citing_a_leak_it_was_not_given_is_rejected() -> None:
    llm = FakeLLM(
        [
            _plan(platforms=["zepto"]),
            {"answer": "See LK-0001 for the stockout.", "cited": []},
            {"answer": "₹93,988 was lost.", "cited": ["LK-0002"]},
        ]
    )
    _, answer, _ = _run("What happened?", llm)
    assert answer.references == ("LK-0002",)
    assert "leaks that were not provided: LK-0001" in llm.prompts[2]


def test_a_figure_from_the_question_may_be_repeated() -> None:
    llm = FakeLLM(
        [
            _plan(platforms=["zepto"]),
            {"answer": "Yes, it was above 50000: ₹93,988.", "cited": ["LK-0002"]},
        ]
    )
    _, answer, _ = _run("Did anything cost more than 50000?", llm)
    assert answer.status == "answered"


def test_off_topic_and_unmatched_questions_never_reach_the_writer() -> None:
    _, off_topic, calls = _run("What's the weather?", FakeLLM([_plan(about_leaks=False)]))
    assert off_topic.status == "off_topic" and len(calls) == 1

    _, no_match, calls = _run(
        "Anything on Instamart?", FakeLLM([_plan(causes=["stockout"], platforms=["zepto"])])
    )
    assert no_match.status == "no_match" and len(calls) == 1


def test_a_city_filter_includes_leaks_that_span_every_city() -> None:
    llm = FakeLLM([_plan(cities=["Pune"]), {"answer": "Two leaks touched Pune.", "cited": []}])
    _run("What hit Pune?", llm)
    assert "[LK-0001]" in llm.prompts[1] and "[LK-0002]" in llm.prompts[1]
    assert llm.prompts[1].startswith("Leaks provided: 2")


def test_dates_narrow_the_search_and_bad_dates_are_ignored() -> None:
    llm = FakeLLM(
        [_plan(date_from="2026-08-15", date_to="not a date"), {"answer": "One leak.", "cited": []}]
    )
    _run("Since mid August?", llm)
    assert "[LK-0002]" in llm.prompts[1] and "[LK-0001]" not in llm.prompts[1]


def test_a_bare_question_on_a_leak_page_is_about_that_leak() -> None:
    llm = FakeLLM([_plan(), {"answer": "Shelves emptied in Pune.", "cited": ["LK-0001"]}])
    _, answer, _ = _run("Why did this happen?", llm, on_page="LK-0001")
    assert answer.references == ("LK-0001",)
    assert "The reader is looking at leak LK-0001." in llm.prompts[0]
    assert "[LK-0002]" not in llm.prompts[1]


def test_a_follow_up_sees_the_conversation_and_can_name_earlier_leaks() -> None:
    history = [Turn("What did we lose on Zepto?", "LK-0002 cost ₹93,988.", ("LK-0002",))]
    llm = FakeLLM(
        [_plan(references=["lk-0002"]), {"answer": "From 1 Sep 2026 to 7 Sep 2026.", "cited": []}]
    )
    _, answer, _ = _run("When was that?", llm, history=history)
    assert answer.status == "answered"
    assert "Earlier answer (about LK-0002): LK-0002 cost ₹93,988." in llm.prompts[0]
    assert "[LK-0002]" in llm.prompts[1] and "[LK-0001]" not in llm.prompts[1]


@pytest.mark.parametrize("failure", [LLMUnavailable("down"), LLMRefused("declined")])
def test_a_planning_failure_says_so_plainly(failure: LLMError) -> None:
    _, answer, calls = _run("What did we lose?", FakeLLM([failure]))
    assert answer.status == "unavailable"
    assert calls[0].outcome in ("unavailable", "refused") and calls[0].input_tokens is None


def test_a_writing_failure_still_returns_what_is_on_record() -> None:
    _, answer, calls = _run("What did we lose?", FakeLLM([_plan(), LLMUnavailable("down")]))
    assert answer.status == "listed"
    assert set(answer.references) == {"LK-0001", "LK-0002"}
    assert [call.outcome for call in calls] == ["ok", "unavailable"]


def test_no_leaks_means_no_model_call() -> None:
    events = list(ask("Anything?", [], FakeLLM([]), lambda _: None, today=TODAY))
    assert events == [Answer("no_match", "There are no leaks on record yet.")]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}",
        cookie_secure=False,
        allowed_origins=(ORIGIN,),
        ask_per_minute=2,
        llm_provider="none",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def sessions(settings: Settings) -> Iterator[sessionmaker[Session]]:
    migrate(settings.database_url)
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    with factory() as db:
        user_id = str(uuid.uuid4())
        db.add(Tenant(id="acme", name="Acme"))
        db.add(
            User(
                id=user_id,
                email="asha@acme.test",
                name="Asha",
                password_hash=hash_password(PASSWORD),
                created_at=NOW,
            )
        )
        db.flush()
        db.add(Membership(user_id=user_id, tenant_id="acme"))
        db.commit()
        sync_leaks(db, "acme", [STOCKOUT.payload, PRICE_CUT.payload], NOW)
    yield factory
    engine.dispose()


def _client(settings: Settings, llm: FakeLLM | None) -> TestClient:
    client = TestClient(create_app(settings, llm), headers={"Origin": ORIGIN})
    response = client.post(
        "/api/auth/login", json={"email": "asha@acme.test", "password": PASSWORD}
    )
    assert response.status_code == 200
    return client


def test_summaries_are_written_once_and_kept_while_the_facts_hold(
    sessions: sessionmaker[Session],
) -> None:
    llm = FakeLLM(
        [
            {"text": "Shelves in Pune emptied, costing ₹11,250."},
            {"text": "Rivals cut prices from 88% to 72%, costing about ₹93,988."},
        ]
    )
    record = database_recorder(sessions, "acme")
    with sessions() as db:
        assert summarise_leaks(db, "acme", llm, record) == 2
        # Nothing has changed, so nothing is asked of the model again.
        assert summarise_leaks(db, "acme", llm, record) == 0
        texts = {leak.reference: leak.summary for leak in db.scalars(select(Leak))}
        logged = db.scalars(select(LlmCall)).all()
    assert texts["LK-0001"] is not None
    assert texts["LK-0001"]["text"] == "Shelves in Pune emptied, costing ₹11,250."
    assert texts["LK-0001"]["model"] == "fake-model"
    assert [(row.purpose, row.outcome, row.tenant_id) for row in logged] == [
        ("summary", "ok", "acme")
    ] * 2
    assert logged[0].prompt_version == "summary.v1" and logged[0].output_tokens == 40


def test_a_summary_with_an_invented_figure_is_retried_then_dropped(
    sessions: sessionmaker[Session],
) -> None:
    llm = FakeLLM(
        [
            {"text": "About ₹11,000 was lost."},
            {"text": "Still about ₹11,000."},
            {"text": "Rivals cut prices, costing about ₹93,988."},
        ]
    )
    with sessions() as db:
        assert summarise_leaks(db, "acme", llm, lambda _: None) == 1
        texts = {leak.reference: leak.summary for leak in db.scalars(select(Leak))}
    assert texts["LK-0001"] is None
    assert texts["LK-0002"] is not None
    assert "these figures are not in the facts: 11000" in llm.prompts[1]


def test_summaries_stop_at_the_first_outage(sessions: sessionmaker[Session]) -> None:
    llm = FakeLLM([LLMUnavailable("down")])
    with sessions() as db:
        assert summarise_leaks(db, "acme", llm, lambda _: None) == 0
    assert len(llm.prompts) == 1


def test_leak_page_carries_its_summary_only_when_there_is_one(
    settings: Settings, sessions: sessionmaker[Session]
) -> None:
    with _client(settings, None) as client:
        assert client.get("/api/leaks/LK-0001").json()["summary"] is None
        with sessions() as db:
            llm = FakeLLM([{"text": "Shelves emptied."}, LLMUnavailable("down")])
            summarise_leaks(db, "acme", llm, lambda _: None)
        assert client.get("/api/leaks/LK-0001").json()["summary"] == "Shelves emptied."


def test_without_a_model_the_app_says_questions_are_off(
    settings: Settings, sessions: sessionmaker[Session]
) -> None:
    with _client(settings, None) as client:
        assert client.get("/api/auth/me").json()["assistant"] is False
        response = client.post("/api/ask", json={"question": "What did we lose?"})
        assert response.status_code == 503


def test_ask_endpoint_streams_progress_then_the_answer(
    settings: Settings, sessions: sessionmaker[Session]
) -> None:
    llm = FakeLLM(
        [_plan(platforms=["zepto"]), {"answer": "₹93,988 was lost.", "cited": ["LK-0002"]}]
    )
    with _client(settings, llm) as client:
        assert client.get("/api/auth/me").json()["assistant"] is True
        response = client.post("/api/ask", json={"question": "What did we lose on Zepto?"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in response.text.splitlines()]

    assert [event["type"] for event in events] == ["progress"] * 4 + ["answer"]
    assert events[-1] == {
        "type": "answer",
        "status": "answered",
        "text": "₹93,988 was lost.",
        "citations": [
            {"reference": "LK-0002", "headline": "Competitors cut prices on Snacks on Zepto"}
        ],
    }
    with sessions() as db:
        assert {row.purpose for row in db.scalars(select(LlmCall))} == {"plan", "answer"}


def test_ask_endpoint_needs_a_session_limits_length_and_rate(
    settings: Settings, sessions: sessionmaker[Session]
) -> None:
    llm = FakeLLM([_plan(about_leaks=False), _plan(about_leaks=False)])
    with TestClient(create_app(settings, llm), headers={"Origin": ORIGIN}) as anonymous:
        assert anonymous.post("/api/ask", json={"question": "Hello there"}).status_code == 401
    with _client(settings, llm) as client:
        assert client.post("/api/ask", json={"question": "x" * 501}).status_code == 422
        assert client.post("/api/ask", json={"question": "First question"}).status_code == 200
        assert client.post("/api/ask", json={"question": "Second question"}).status_code == 200
        limited = client.post("/api/ask", json={"question": "Third question"})
        assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0


def test_ask_endpoint_survives_a_crash_in_the_model(
    settings: Settings, sessions: sessionmaker[Session]
) -> None:
    llm = FakeLLM([RuntimeError("boom")])
    with _client(settings, llm) as client:
        response = client.post("/api/ask", json={"question": "What did we lose?"})
    (last,) = [json.loads(line) for line in response.text.splitlines()][-1:]
    assert last["status"] == "unavailable"


def test_the_model_is_off_by_default_and_on_with_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert make_llm(Settings(_env_file=None)) is None  # type: ignore[call-arg]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    configured = Settings(_env_file=None)  # type: ignore[call-arg]
    llm = make_llm(configured)
    assert isinstance(llm, ClaudeLLM) and llm.model == "claude-opus-5-5"
    assert "test-key-not-real" not in repr(configured)
    off = Settings(llm_provider="none", _env_file=None)  # type: ignore[call-arg]
    assert make_llm(off) is None


def test_plan_shape_is_one_the_model_api_can_enforce() -> None:
    from cosmos.app.ask import _plan_shape

    schema = _plan_shape(LEAKS).model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])

    def allowed(field: str) -> list[str]:
        name = schema["properties"][field]["items"]["$ref"].rsplit("/", 1)[-1]
        values: list[str] = schema["$defs"][name]["enum"]
        return values

    assert allowed("platforms") == ["blinkit", "zepto"]
    assert allowed("cities") == ["Pune"]
    assert allowed("causes") == ["competitor_price_cut", "stockout"]
