"""What the rest of the code asks of a language model, whoever provides it.

Every call returns a validated object of a declared type, never free text to be
parsed afterwards. Nothing outside this package imports a vendor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel

Shape = TypeVar("Shape", bound=BaseModel)
Effort = Literal["low", "medium", "high"]


class LLMError(Exception):
    """The model could not give a usable answer. Callers fall back to rule-based output."""


class LLMUnavailable(LLMError):
    """The service could not be reached, or would not serve the request right now."""


class LLMRefused(LLMError):
    """The provider declined the request on policy grounds."""


class LLMInvalidOutput(LLMError):
    """The reply was cut off or did not fit the requested shape."""


@dataclass(frozen=True, slots=True)
class Completion(Generic[Shape]):
    value: Shape
    model: str  # the model that actually answered, which a fallback can change
    input_tokens: int
    output_tokens: int


class LLM(Protocol):
    @property
    def model(self) -> str: ...

    def complete(
        self, *, system: str, prompt: str, shape: type[Shape], effort: Effort = "medium"
    ) -> Completion[Shape]:
        """Answer `prompt` as an instance of `shape`. Raises an `LLMError` on any failure."""
        ...
