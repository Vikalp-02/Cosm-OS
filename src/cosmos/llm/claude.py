"""Claude, through the official Anthropic SDK."""

from __future__ import annotations

import logging
from typing import Final

import anthropic
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

DEFAULT_MODEL: Final = "claude-opus-5-5"
# Thinking counts against this, so it is a ceiling with room to spare and not a
# target. Replies here are a few hundred tokens.
MAX_TOKENS: Final = 16_000
# If the model declines a request on policy grounds, the API reruns it on the
# fallback Anthropic recommends for that kind of refusal, within the same call.
FALLBACK_BETA: Final = "server-side-fallback-2026-07-01"


class ClaudeLLM:
    def __init__(
        self, *, model: str = DEFAULT_MODEL, timeout_seconds: float, api_key: str | None = None
    ) -> None:
        self._model = model
        # With no key given, the SDK resolves credentials from the environment.
        # It retries rate limits, overloads and dropped connections itself.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=2)

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self, *, system: str, prompt: str, shape: type[Shape], effort: Effort = "medium"
    ) -> Completion[Shape]:
        try:
            response = self._client.beta.messages.parse(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=shape,
                output_config={"effort": effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.BadRequestError as error:
            # Our request was malformed. Retrying will not help and it should be noticed.
            log.error("the model rejected a request as invalid: %s", error.message)
            raise LLMError("the request was rejected as invalid") from error
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as error:
            log.error("the model provider rejected our credentials")
            raise LLMUnavailable("credentials were rejected") from error
        except anthropic.RateLimitError as error:
            raise LLMUnavailable("rate limited") from error
        except anthropic.APIConnectionError as error:  # includes timeouts
            raise LLMUnavailable("could not reach the provider") from error
        except anthropic.APIStatusError as error:
            raise LLMUnavailable(f"provider error {error.status_code}") from error
        except pydantic.ValidationError as error:
            raise LLMInvalidOutput("the reply did not fit the requested shape") from error

        if response.stop_reason == "refusal":
            raise LLMRefused("the request was declined")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise LLMInvalidOutput(f"reply unusable (stopped on {response.stop_reason})")
        return Completion(
            value=response.parsed_output,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
