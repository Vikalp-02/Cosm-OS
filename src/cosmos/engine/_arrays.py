from __future__ import annotations

import warnings
from typing import TypeAlias

import numpy as np
import numpy.typing as npt
from numpy.lib.stride_tricks import sliding_window_view

FloatArray: TypeAlias = npt.NDArray[np.float64]
IntArray: TypeAlias = npt.NDArray[np.int64]
BoolArray: TypeAlias = npt.NDArray[np.bool_]


def trailing_median(values: FloatArray, window: int, min_valid: int) -> FloatArray:
    """Along the last axis, the median of the `window` entries before each one.

    The entry itself is left out, and so is anything missing. Where fewer than
    `min_valid` entries are available the result is NaN.
    """
    lead = np.full((*values.shape[:-1], window), np.nan)
    before = sliding_window_view(np.concatenate([lead, values], axis=-1), window, axis=-1)
    before = before[..., : values.shape[-1], :]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # windows that are entirely missing
        median: FloatArray = np.nanmedian(before, axis=-1)
    median[np.isfinite(before).sum(axis=-1) < min_valid] = np.nan
    return median


def runs(flags: BoolArray) -> list[tuple[int, int]]:
    """First and last index of every unbroken stretch of True."""
    edges = np.flatnonzero(np.diff(np.concatenate(([False], flags, [False])).astype(np.int8)))
    return [
        (int(start), int(stop) - 1) for start, stop in zip(edges[::2], edges[1::2], strict=True)
    ]
