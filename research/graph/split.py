from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from research.graph.builder import TemporalSamples


@dataclass(frozen=True)
class TemporalSplit:
    train: TemporalSamples
    validation: TemporalSamples
    test: TemporalSamples


def temporal_split(samples: TemporalSamples) -> TemporalSplit:
    def positions(start: int, end: int) -> np.ndarray:
        return np.flatnonzero(
            (samples.anchors >= start) & (samples.anchors <= end)
        ).astype(np.int64)

    train = positions(12, 16)
    validation = positions(17, 18)
    test = positions(19, 21)
    if train.size == 0:
        raise ValueError("temporal split requires at least one training anchor")
    return TemporalSplit(
        train=samples.subset(train),
        validation=samples.subset(validation),
        test=samples.subset(test),
    )
