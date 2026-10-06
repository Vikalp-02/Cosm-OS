from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx2 as httpx
import pytest
from pydantic import BaseModel, ConfigDict
from test_llm import LEAKS

from cosmos.app.ask import _plan_shape
from cosmos.app.assistant import make_llm
from cosmos.app.settings import Settings
from cosmos.llm import LLMError, LLMInvalidOutput, LLMRefused, LLMUnavailable
from cosmos.llm.claude import ClaudeLLM
from cosmos.llm.openai_compatible import OpenAICompatibleLLM, inline_references

Handler = Callable[[httpx.Request], httpx.Response]


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


def _llm(handler: Handler, **options: Any) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        base_url="https://llm.test/v1/",
        api_key="test-key-not-real",
        model="asked-model",
        timeout_seconds=5,
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **options,
    )


def _reply(content: str | None, finish: str = "stop") -> dict[str, Any]:
    return {
        "id": "reply-1",
        "object": "chat.completion",
        "created": 0,
        "model": "served-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def _answering(body: dict[str, Any], status: int = 200) -> Handler:
    return lambda _: httpx.Response(status, json=body)


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def no_keys_from_the_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_request_asks_for_the_shape_and_the_reply_is_parsed() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=_reply('{"text": "all good"}'))

    completion = _llm(handler).complete(system="Be brief.", prompt="How is it?", shape=Verdict)

    assert completion.value == Verdict(text="all good")
    assert (completion.model, completion.input_tokens, completion.output_tokens) == (
        "served-model",
        11,
        7,
    )
    (request,) = sent
    assert str(request.url) == "https://llm.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key-not-real"
    body = json.loads(request.content)
    assert body["model"] == "asked-model"
    assert body["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "How is it?"},
    ]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "Verdict"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"]["required"] == ["text"]
    # Not sent unless the provider is known to accept it.
    assert "reasoning_effort" not in body


def test_effort_is_sent_only_to_providers_that_take_it() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=_reply('{"text": "ok"}'))

    _llm(handler, send_effort=True).complete(system="s", prompt="p", shape=Verdict, effort="low")
    assert sent[0]["reasoning_effort"] == "low"


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_provider_trouble_is_reported_as_unavailable(status: int) -> None:
    llm = _llm(_answering({"error": {"message": "no"}}, status))
    with pytest.raises(LLMUnavailable):
        llm.complete(system="s", prompt="p", shape=Verdict)


def test_a_request_the_provider_calls_malformed_is_not_treated_as_an_outage() -> None:
    llm = _llm(_answering({"error": {"message": "bad schema"}}, 400))
    with pytest.raises(LLMError) as raised:
        llm.complete(system="s", prompt="p", shape=Verdict)
    assert not isinstance(raised.value, LLMUnavailable)


def test_a_dropped_connection_is_reported_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(LLMUnavailable):
        _llm(handler).complete(system="s", prompt="p", shape=Verdict)


@pytest.mark.parametrize(
    "body",
    [
        _reply("not json at all"),
        _reply('{"text": "ok", "extra": 1}'),
        _reply('{"text": "cut o', finish="length"),
        _reply(None),
        {**_reply("{}"), "choices": []},
    ],
)
def test_a_reply_that_does_not_fit_is_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(LLMInvalidOutput):
        _llm(_answering(body)).complete(system="s", prompt="p", shape=Verdict)


def test_a_filtered_reply_is_a_refusal() -> None:
    with pytest.raises(LLMRefused):
        _llm(_answering(_reply(None, finish="content_filter"))).complete(
            system="s", prompt="p", shape=Verdict
        )


def test_shared_definitions_are_written_out_in_place() -> None:
    original = _plan_shape(LEAKS).model_json_schema()
    assert "$defs" in original  # the case worth testing

    inlined = inline_references(original)
    text = json.dumps(inlined)
    assert "$ref" not in text and "$defs" not in text
    assert inlined["properties"]["platforms"]["items"]["enum"] == ["blinkit", "zepto"]
    assert inlined["properties"]["order"]["enum"] == ["largest", "newest"]
    assert inlined["required"] == original["required"]


def test_a_gemini_key_switches_gemini_on() -> None:
    llm = make_llm(_settings(GEMINI_API_KEY="test-key-not-real"))
    assert isinstance(llm, OpenAICompatibleLLM)
    assert llm.model == "gemini-3.8-flash"
    assert make_llm(_settings(GEMINI_API_KEY="k", llm_model="gemini-3.5-flash")).model == (  # type: ignore[union-attr]
        "gemini-3.5-flash"
    )


def test_an_empty_key_switches_nothing_on() -> None:
    assert make_llm(_settings(GEMINI_API_KEY="", ANTHROPIC_API_KEY="")) is None


def test_anthropic_wins_when_both_keys_are_present_unless_told_otherwise() -> None:
    both = {"GEMINI_API_KEY": "test-key-not-real", "ANTHROPIC_API_KEY": "test-key-not-real"}
    assert isinstance(make_llm(_settings(**both)), ClaudeLLM)
    assert isinstance(make_llm(_settings(**both, llm_provider="gemini")), OpenAICompatibleLLM)
    assert make_llm(_settings(**both, llm_provider="none")) is None


def test_a_provider_chosen_without_what_it_needs_fails_loudly() -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        make_llm(_settings(llm_provider="gemini"))
    with pytest.raises(ValueError, match="COSMOS_LLM_BASE_URL"):
        make_llm(_settings(llm_provider="openai_compatible", llm_model="some-model"))

    custom = make_llm(
        _settings(
            llm_provider="openai_compatible",
            llm_base_url="https://llm.test/v1/",
            llm_api_key="test-key-not-real",
            llm_model="some-model",
        )
    )
    assert isinstance(custom, OpenAICompatibleLLM) and custom.model == "some-model"


def test_keys_do_not_appear_when_settings_are_printed() -> None:
    assert "test-key-not-real" not in repr(_settings(GEMINI_API_KEY="test-key-not-real"))


def _by_model(outcomes: dict[str, int], asked: list[str]) -> Handler:
    """Answer each model with the status given for it, recording which were asked."""

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        asked.append(model)
        status = outcomes[model]
        if status == 200:
            return httpx.Response(200, json={**_reply('{"text": "ok"}'), "model": model})
        return httpx.Response(status, json={"error": {"message": "no"}})

    return handler


def test_an_overloaded_model_falls_back_to_the_next_and_is_rested() -> None:
    asked: list[str] = []
    llm = _llm(
        _by_model({"asked-model": 503, "second": 503, "third": 200}, asked),
        fallback_models=("second", "third"),
    )

    first = llm.complete(system="s", prompt="p", shape=Verdict)
    assert first.model == "third"  # the model that actually answered
    assert asked == ["asked-model", "second", "third"]

    # The two that failed are passed over, so nobody waits on them again.
    asked.clear()
    assert llm.complete(system="s", prompt="p", shape=Verdict).model == "third"
    assert asked == ["third"]
    assert llm.model == "asked-model"


def test_a_rested_model_is_tried_again_after_its_cool_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []
    outcomes = {"asked-model": 503, "second": 200}
    llm = _llm(_by_model(outcomes, asked), fallback_models=("second",))
    llm.complete(system="s", prompt="p", shape=Verdict)

    outcomes["asked-model"] = 200
    clock = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: clock + 61)
    asked.clear()
    assert llm.complete(system="s", prompt="p", shape=Verdict).model == "asked-model"
    assert asked == ["asked-model"]


def test_when_every_model_is_down_the_call_fails_and_all_are_still_tried_next_time() -> None:
    asked: list[str] = []
    llm = _llm(_by_model({"asked-model": 503, "second": 429}, asked), fallback_models=("second",))
    for _ in range(2):
        asked.clear()
        with pytest.raises(LLMUnavailable):
            llm.complete(system="s", prompt="p", shape=Verdict)
        assert asked == ["asked-model", "second"]


def test_rejected_credentials_do_not_fall_back() -> None:
    asked: list[str] = []
    llm = _llm(_by_model({"asked-model": 401, "second": 200}, asked), fallback_models=("second",))
    with pytest.raises(LLMUnavailable):
        llm.complete(system="s", prompt="p", shape=Verdict)
    assert asked == ["asked-model"]


def test_a_malformed_request_does_not_fall_back() -> None:
    asked: list[str] = []
    llm = _llm(_by_model({"asked-model": 400, "second": 200}, asked), fallback_models=("second",))
    with pytest.raises(LLMError):
        llm.complete(system="s", prompt="p", shape=Verdict)
    assert asked == ["asked-model"]


def test_gemini_comes_with_fallback_models_unless_told_otherwise() -> None:
    asked: list[str] = []
    transport = httpx.MockTransport(
        _by_model({"gemini-3.8-flash": 503, "gemini-3.5-flash": 200}, asked)
    )
    llm = make_llm(_settings(GEMINI_API_KEY="test-key-not-real"))
    assert isinstance(llm, OpenAICompatibleLLM)
    llm._client = llm._client.with_options(http_client=httpx.Client(transport=transport))
    assert llm.complete(system="s", prompt="p", shape=Verdict).model == "gemini-3.5-flash"

    alone = make_llm(_settings(GEMINI_API_KEY="test-key-not-real", llm_fallback_models=()))
    assert isinstance(alone, OpenAICompatibleLLM) and alone._models == ("gemini-3.8-flash",)
