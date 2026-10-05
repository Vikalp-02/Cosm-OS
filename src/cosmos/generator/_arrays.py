from __future__ import annotations

from typing import TypeAlias

import numpy as np
import numpy.typing as npt

FloatArray: TypeAlias = npt.NDArray[np.float64]
Float32Array: TypeAlias = npt.NDArray[np.float32]
IntArray: TypeAlias = npt.NDArray[np.int64]
BoolArray: TypeAlias = npt.NDArray[np.bool_]
