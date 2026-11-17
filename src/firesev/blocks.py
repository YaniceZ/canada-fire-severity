"""Spatial block cross-validation.

Neighbouring pixels look alike. If training and test pixels are drawn at random,
most test pixels have a near-identical training pixel next to them and the
accuracy comes out too high. Splitting by block keeps whole areas out of training.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np


def assign_blocks(x: np.ndarray, y: np.ndarray, block_size: float) -> np.ndarray:
    """Give each point the id of the square block it falls in.

    `x` and `y` are projected coordinates in metres; `block_size` is the block edge.
    """
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    col = np.floor(np.asarray(x) / block_size).astype("int64")
    row = np.floor(np.asarray(y) / block_size).astype("int64")
    _, ids = np.unique(np.stack([row, col], axis=1), axis=0, return_inverse=True)
    return ids.reshape(-1)


def block_kfold(block_ids: np.ndarray, k: int = 5, seed: int = 0) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield (train_index, test_index) pairs where no block is on both sides."""
    blocks = np.unique(block_ids)
    if len(blocks) < k:
        raise ValueError(f"need at least {k} blocks for {k} folds, got {len(blocks)}")
    shuffled = np.random.default_rng(seed).permutation(blocks)
    for held_out in np.array_split(shuffled, k):
        test = np.isin(block_ids, held_out)
        yield np.flatnonzero(~test), np.flatnonzero(test)
