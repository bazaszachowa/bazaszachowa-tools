"""Shared GRU training pipeline for both yearly-data representations."""

from __future__ import annotations

import math
import random
from collections.abc import Generator, Iterator

import keras
import numpy as np

from .dataset import PlayerSeries, SeriesKey, YearValue
from .strategies import (
    PredictionStrategy,
    get_feature_strategy,
)

PADDING_VALUE = -999.0
type Sample = tuple[np.ndarray, np.ndarray]


def build_model(strategy: PredictionStrategy) -> keras.Model:
    inputs = keras.Input(
        shape=(None, strategy.input_size),
        name="history",
    )

    x = keras.layers.Masking(
        mask_value=PADDING_VALUE,
    )(inputs)

    x = keras.layers.GRU(
        32,
        dropout=0.1,
    )(x)

    x = keras.layers.Dense(
        32,
        activation="relu",
    )(x)

    x = keras.layers.Dropout(0.2)(x)

    outputs = strategy.make_output(x)

    model = keras.Model(inputs, outputs)

    model.compile(
        optimizer=keras.optimizers.Adam(
            learning_rate=1e-4,
            clipnorm=1.0,
        ),
        loss="mse",
        metrics=["mae"],
    )

    return model


def make_samples(
    series: PlayerSeries,
    keys: list[SeriesKey],
    min_history: int,
    strategy: PredictionStrategy,
) -> Generator[Sample, None, None]:
    for key in keys:
        yearly_data = series[key]
        encoded = strategy.encode_input_series(yearly_data)

        for target_index in range(min_history, len(encoded)):
            history = encoded[:target_index]
            target = strategy.encode_target(yearly_data[target_index][1])

            if not np.all(np.isfinite(history)) or not np.all(np.isfinite(target)):
                raise ValueError(f"Non-finite feature values for {key}")

            yield history, target


def count_samples(series: PlayerSeries, keys: list[SeriesKey], min_history: int) -> int:
    return sum(max(0, len(series[key]) - min_history) for key in keys)


class TimeSeriesTrainer:
    """Owns the common split, dataset, model-fit and prediction workflow."""

    def __init__(
        self,
        mode: str = "absolute",
        batch_size: int = 128,
        validation_fraction: float = 0.2,
        seed: int = 42,
        samples_fraction: float = 0.25,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not 0.0 < validation_fraction < 1.0:
            raise ValueError("validation_fraction must be between 0 and 1")
        if not 0.0 < samples_fraction <= 1.0:
            raise ValueError("samples_fraction must be in (0, 1]")

        self.strategy = get_feature_strategy(mode)
        self.batch_size = batch_size
        self.validation_fraction = validation_fraction
        self.seed = seed
        self.samples_fraction = samples_fraction

    def fit(
        self,
        series: PlayerSeries,
        min_history: int = 1,
        epochs: int = 30,
        checkpoint_path: str | None = None,
    ) -> keras.Model:
        if not series:
            raise ValueError("No player series provided")
        if min_history < 1:
            raise ValueError("min_history must be at least 1")

        keys = list(series)
        random.Random(self.seed).shuffle(keys)
        if len(keys) < 2:
            raise ValueError("At least two player-role series are required")

        split_index = max(
            1,
            min(len(keys) - 1, int(len(keys) * (1.0 - self.validation_fraction))),
        )
        train_keys = keys[:split_index]
        validation_keys = keys[split_index:]

        train_count = count_samples(series, train_keys, min_history)
        validation_count = count_samples(series, validation_keys, min_history)
        if train_count < 1 or validation_count < 1:
            raise ValueError(
                "Not enough training or validation samples; lower min_history"
            )

        print(f"Mode: {self.strategy.name}")
        print(f"Training samples: {train_count}")
        print(f"Validation samples: {validation_count}")

        def train_factory() -> Iterator[Sample]:
            return make_samples(
                series,
                train_keys,
                min_history,
                self.strategy,
            )

        def validation_factory() -> Iterator[Sample]:
            return make_samples(
                series,
                validation_keys,
                min_history,
                self.strategy,
            )

        train_dataset = self.strategy.make_dataset(
            train_factory,
            train_count,
            self.batch_size,
            shuffle=True,
            padding_value=PADDING_VALUE,
        )

        validation_dataset = self.strategy.make_dataset(
            validation_factory,
            validation_count,
            self.batch_size,
            shuffle=False,
            padding_value=PADDING_VALUE,
        )

        samples_per_epoch = max(1, int(train_count * self.samples_fraction))
        steps_per_epoch = math.ceil(samples_per_epoch / self.batch_size)
        callbacks: list[keras.callbacks.Callback] = [keras.callbacks.TerminateOnNaN()]

        if checkpoint_path:
            callbacks.append(
                keras.callbacks.ModelCheckpoint(
                    checkpoint_path,
                    monitor="val_loss",
                    save_best_only=True,
                    verbose=1,
                )
            )
        callbacks.extend(
            [
                keras.callbacks.ReduceLROnPlateau(
                    monitor="val_loss",
                    factor=0.5,
                    patience=10,
                    min_delta=0.0001,
                    min_lr=1e-6,
                    verbose=1,
                ),
                keras.callbacks.EarlyStopping(
                    monitor="val_loss",
                    patience=30,
                    min_delta=0.0001,
                    restore_best_weights=False,
                    verbose=1,
                ),
            ]
        )

        model = build_model(self.strategy)
        model.fit(
            train_dataset,
            validation_data=validation_dataset,
            epochs=epochs,
            shuffle=False,
            steps_per_epoch=steps_per_epoch,
            callbacks=callbacks,
        )
        return model

    def predict(self, model: keras.Model, yearly_data: list[YearValue]) -> list[float]:
        if not yearly_data:
            return [0.0, 0.0]
        history = self.strategy.encode_series(yearly_data)
        prediction = model.predict(history[np.newaxis, :, :], verbose=0)[0]
        return self.strategy.decode(prediction)


def train_model(
    series: PlayerSeries,
    min_history: int = 1,
    epochs: int = 30,
    batch_size: int = 128,
    validation_fraction: float = 0.2,
    seed: int = 42,
    mode: str = "absolute",
    samples_fraction: float = 0.25,
) -> keras.Model:
    """Compatibility function; new code can use TimeSeriesTrainer directly."""
    trainer = TimeSeriesTrainer(
        mode=mode,
        batch_size=batch_size,
        validation_fraction=validation_fraction,
        seed=seed,
        samples_fraction=samples_fraction,
    )
    return trainer.fit(series, min_history=min_history, epochs=epochs)


def predict_next_year(
    model: keras.Model,
    yearly_data: list[YearValue],
    mode: str = "absolute",
) -> list[float]:
    """Compatibility helper for inference with the selected representation."""
    strategy = get_feature_strategy(mode)

    if not yearly_data:
        return [0.0, 0.0]

    history = strategy.encode_series(yearly_data)
    prediction = model.predict(
        history[np.newaxis, :, :],
        verbose=0,
    )[0]

    return strategy.decode(prediction)
