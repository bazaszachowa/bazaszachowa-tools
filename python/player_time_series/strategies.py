from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
from keras import layers

from .dataset import YearValue


class PredictionStrategy(ABC):
    """Defines how yearly values are encoded and predicted."""

    name: str

    @abstractmethod
    def encode(self, values: list[float]) -> np.ndarray:
        raise NotImplementedError

    @abstractmethod
    def decode(self, prediction: np.ndarray) -> list[float]:
        raise NotImplementedError

    @abstractmethod
    def make_output(self, x: layers.Layer) -> layers.Layer:
        raise NotImplementedError

    def encode_series(self, yearly_data: list[YearValue]) -> np.ndarray:
        return np.asarray(
            [self.encode(values) for _, values in yearly_data],
            dtype=np.float32,
        )


class AbsoluteStrategy(PredictionStrategy):
    """Input: [games, points]. Output: [games, points]."""

    name = "absolute"

    def encode(self, values: list[float]) -> np.ndarray:
        count, points = values
        rate = points / count if count else 0.0

        return np.asarray(
            [np.log1p(count), rate],
            dtype=np.float32,
        )

    def decode(self, prediction: np.ndarray) -> list[float]:
        count = max(0.0, float(np.expm1(prediction[0])))
        rate = float(np.clip(prediction[1], 0.0, 1.0))
        return [count, count * rate]

    def make_output(self, x: layers.Layer) -> layers.Layer:
        count = layers.Dense(
            1,
            activation="softplus",
            name="count",
        )(x)

        rate = layers.Dense(
            1,
            activation="sigmoid",
            name="rate",
        )(x)

        return layers.Concatenate()([count, rate])


class PercentageStrategy(PredictionStrategy):
    """Input and output: [career_game_share, career_point_share]."""

    name = "percentage"

    def encode(self, values: list[float]) -> np.ndarray:
        result = np.asarray(values, dtype=np.float32)

        if not np.all(np.isfinite(result)):
            raise ValueError("Percentage values must be finite")

        if np.any(result < 0.0) or np.any(result > 1.0):
            raise ValueError("Percentage values must be in [0, 1]")

        return result

    def decode(self, prediction: np.ndarray) -> list[float]:
        result = np.clip(prediction, 0.0, 1.0)
        return [float(result[0]), float(result[1])]

    def make_output(self, x: layers.Layer) -> layers.Layer:
        return layers.Dense(
            2,
            activation="sigmoid",
            name="percentage",
        )(x)


def get_feature_strategy(mode: str) -> PredictionStrategy:
    if mode == "absolute":
        return AbsoluteStrategy()

    if mode == "percentage":
        return PercentageStrategy()

    raise ValueError(f"Unknown representation: {mode}")
