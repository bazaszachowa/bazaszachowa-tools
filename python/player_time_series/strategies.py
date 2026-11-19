"""Feature strategies for yearly time-series prediction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator

import numpy as np
import tensorflow as tf
from keras import layers

from .dataset import YearValue

type Sample = tuple[np.ndarray, np.ndarray]
type SampleFactory = Callable[[], Iterator[Sample]]


class PredictionStrategy(ABC):
    """Defines input encoding and output decoding."""

    name: str

    @property
    @abstractmethod
    def input_size(self) -> int:
        """Number of features per historical year."""
        ...

    @property
    def target_size(self) -> int:
        """Number of predicted values."""
        return 2

    @abstractmethod
    def encode_input(self, values: list[float]) -> np.ndarray:
        raise NotImplementedError

    @abstractmethod
    def encode_target(self, values: list[float]) -> np.ndarray:
        raise NotImplementedError

    @abstractmethod
    def decode(self, prediction: np.ndarray) -> list[float]:
        raise NotImplementedError

    @abstractmethod
    def make_output(self, x: layers.Layer) -> layers.Layer:
        raise NotImplementedError

    def encode_input_series(
        self,
        yearly_data: list[YearValue],
    ) -> np.ndarray:
        return np.asarray(
            [self.encode_input(values) for _, values in yearly_data],
            dtype=np.float32,
        )

    def encode_series(
        self,
        yearly_data: list[YearValue],
    ) -> np.ndarray:
        """Compatibility alias for input encoding."""
        return self.encode_input_series(yearly_data)

    def make_dataset(
        self,
        sample_factory: SampleFactory,
        sample_count: int,
        batch_size: int,
        shuffle: bool,
        padding_value: float = -999.0,
    ) -> tf.data.Dataset:
        dataset = tf.data.Dataset.from_generator(
            sample_factory,
            output_signature=(
                tf.TensorSpec(
                    shape=(None, self.input_size),
                    dtype=tf.float32,
                ),
                tf.TensorSpec(
                    shape=(self.target_size,),
                    dtype=tf.float32,
                ),
            ),
        )

        if shuffle:
            dataset = dataset.shuffle(
                min(sample_count, 10000),
                reshuffle_each_iteration=True,
            ).repeat()

        dataset = dataset.padded_batch(
            batch_size,
            padded_shapes=(
                [None, self.input_size],
                [self.target_size],
            ),
            padding_values=(
                tf.constant(padding_value, dtype=tf.float32),
                tf.constant(0.0, dtype=tf.float32),
            ),
            drop_remainder=False,
        )

        return dataset.prefetch(tf.data.AUTOTUNE)


class AbsoluteStrategy(PredictionStrategy):
    """Input: [log1p(games), score_rate].

    Output: [log1p(games), score_rate].
    """

    name = "absolute"

    @property
    def input_size(self) -> int:
        return 2

    def encode_input(self, values: list[float]) -> np.ndarray:
        count, points = values

        if count < 0.0 or points < 0.0 or points > count:
            raise ValueError("Invalid absolute values")

        rate = points / count if count > 0.0 else 0.0

        return np.asarray(
            [np.log1p(count), rate],
            dtype=np.float32,
        )

    def encode_target(self, values: list[float]) -> np.ndarray:
        return self.encode_input(values)

    def decode(self, prediction: np.ndarray) -> list[float]:
        count = max(
            0.0,
            float(np.expm1(prediction[0])),
        )

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
    """Input: [game_share, point_score, has_games].

    Output: [game_share, point_score].
    """

    name = "percentage"

    @property
    def input_size(self) -> int:
        return 3

    def encode_input(self, values: list[float]) -> np.ndarray:
        if len(values) != 3:
            raise ValueError("Percentage input must contain three values")

        result = np.asarray(values, dtype=np.float32)

        if not np.all(np.isfinite(result)):
            raise ValueError("Percentage values must be finite")

        if np.any(result < 0.0) or np.any(result > 1.0):
            raise ValueError("Percentage values must be in [0, 1]")

        game_share, point_score, has_games = result

        if has_games == 0.0 and (game_share != 0.0 or point_score != 0.0):
            raise ValueError("Values must be zero when has_games is zero")

        return result

    def encode_target(self, values: list[float]) -> np.ndarray:
        if len(values) != 3:
            raise ValueError("Percentage target must contain three values")

        result = np.asarray(values[:2], dtype=np.float32)

        if not np.all(np.isfinite(result)):
            raise ValueError("Percentage target must be finite")

        if np.any(result < 0.0) or np.any(result > 1.0):
            raise ValueError("Percentage target must be in [0, 1]")

        return result

    def decode(self, prediction: np.ndarray) -> list[float]:
        result = np.clip(prediction, 0.0, 1.0)

        return [
            float(result[0]),
            float(result[1]),
        ]

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
