"""Any model served through the OpenAI chat-completions protocol.

Gemini, Groq, Cerebras, Mistral and OpenRouter all offer this, so one adapter
covers them. The reply is always validated here against the requested shape:
providers differ in how strictly they hold a model to a JSON schema, and
nothing downstream should have to care.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from typing import Any, Final

import httpx2 as httpx
import openai
import pydantic

from cosmos.llm.provider import (
    Completion,
    Effort,
    LLMError,
    LLMInvalidOutput,
    LLMRefused,
    LLMUnavailable,
    Shape,
)

log = logging.getLogger("cosmos.llm")

GEMINI_BASE_URL: Final = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_DEFAULT_MODEL: Final = "gemini-3.8-flash"
# Tried in order when the model above is overloaded, which the newest models
# often are on the free tier.
GEMINI_FALLBACK_MODELS: Final = ("gemini-3.5-flash", "gemini-3.5-flash-lite")

# How long a model that just failed is passed over before it is tried again.
COOL_DOWN_SECONDS: Final = 60.0


class _CredentialsRejected(LLMUnavailable):
    """No other model on the same key will fare better, so there is nothing to fall back to."""


class OpenAICompatibleLLM:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        fallback_models: Sequence[str] = (),
        send_effort: bool = False,
        max_retries: int = 1,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._models = (model, *(name for name in fallback_models if name != model))
        # Not every provider accepts a reasoning-effort setting, and those that
        # do not reject the request outright.
        self._send_effort = send_effort
        # The SDK retries rate limits, overloads and dropped connections itself.
        self._client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
            http_client=http_client,
        )
        self._resting_until: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self, *, system: str, prompt: str, shape: type[Shape], effort: Effort = "medium"
    ) -> Completion[Shape]:
        """Ask the first model that is up. A model that fails as unavailable is
        rested for a minute, so later calls go straight to one that works."""
        now = time.monotonic()
        with self._lock:
            rested = [name for name in self._models if self._resting_until.get(name, 0.0) <= now]
        # If every model is resting, trying them all beats refusing outright.
        candidates = rested or list(self._models)

        for position, model in enumerate(candidates):
            try:
                return self._ask(model, system, prompt, shape, effort)
            except _CredentialsRejected:
                raise
            except LLMUnavailable as error:
                with self._lock:
                    self._resting_until[model] = time.monotonic() + COOL_DOWN_SECONDS
                if position == len(candidates) - 1:
                    raise
                log.warning(
                    "%s is unavailable (%s); trying %s", model, error, candidates[position + 1]
                )
        raise LLMUnavailable("no model is configured")

    def _ask(
        self, model: str, system: str, prompt: str, shape: type[Shape], effort: Effort
    ) -> Completion[Shape]:
        extra: dict[str, Any] = {"reasoning_effort": effort} if self._send_effort else {}
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": shape.__name__,
                        "schema": inline_references(shape.model_json_schema()),
                        "strict": True,
                    },
                },
                **extra,
            )
        except openai.BadRequestError as error:
            # Our request was malformed. Retrying will not help and it should be noticed.
            log.error("the model rejected a request as invalid: %s", error.message)
            raise LLMError("the request was rejected as invalid") from error
        except (openai.AuthenticationError, openai.PermissionDeniedError) as error:
            log.error("the model provider rejected our credentials")
            raise _CredentialsRejected("credentials were rejected") from error
        except openai.RateLimitError as error:
            raise LLMUnavailable("rate limited") from error
        except openai.APIConnectionError as error:  # includes timeouts
            raise LLMUnavailable("could not reach the provider") from error
        except openai.APIStatusError as error:
            raise LLMUnavailable(f"provider error {error.status_code}") from error

        if not response.choices:
            raise LLMInvalidOutput("the reply was empty")
        choice = response.choices[0]
        if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
            raise LLMRefused("the request was declined")
        if choice.finish_reason == "length" or not choice.message.content:
            raise LLMInvalidOutput(f"reply unusable (stopped on {choice.finish_reason})")
        try:
            value = shape.model_validate_json(choice.message.content)
        except pydantic.ValidationError as error:
            raise LLMInvalidOutput("the reply did not fit the requested shape") from error

        usage = response.usage
        return Completion(
            value=value,
            model=response.model or model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )


def inline_references(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace every `$ref` with the definition it points to, and drop `$defs`.

    Shared definitions are valid JSON Schema, but not every provider's
    structured-output mode resolves them. Written out in place they are plain
    schema that all of them accept.
    """
    definitions: dict[str, Any] = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            return resolve(definitions[name])
        return {key: resolve(value) for key, value in node.items() if key != "$defs"}

    resolved: dict[str, Any] = resolve(schema)
    return resolved
