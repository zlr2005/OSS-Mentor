"""Randomness controls shared by future data and model commands."""

from __future__ import annotations

import os
import random


def set_global_seed(seed: int) -> None:
    """Seed the stage-0 standard-library RNG and advertise the process seed.

    Future stages must explicitly seed NumPy/PyTorch after adding those pinned
    dependencies. Setting PYTHONHASHSEED here documents the value for child
    processes; deterministic hashing in the current interpreter requires the
    variable to be set before Python starts.
    """

    if seed < 0:
        raise ValueError("seed must be non-negative")
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
