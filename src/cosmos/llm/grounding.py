"""Check that generated text states no figure it was not given.

The model is handed its facts as text with every figure already formatted, and
told to copy figures exactly. That makes the check simple and strict: every
number in the reply must appear among the numbers in the facts. A rounded or
recomputed figure fails, as it should. In a product about money, a number
nobody calculated is the worst thing the text could contain.
"""

from __future__ import annotations

import re
from typing import Final

_NUMBER: Final = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """Every number written in `text`, without digit grouping: "₹1,23,456" gives "123456"."""
    return {match.replace(",", "").rstrip(".") for match in _NUMBER.findall(text)}


def ungrounded_numbers(text: str, facts: str) -> list[str]:
    """Numbers that appear in `text` and nowhere in `facts`, in a stable order."""
    return sorted(numbers_in(text) - numbers_in(facts))
