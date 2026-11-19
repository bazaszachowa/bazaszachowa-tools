"""Shared chronological GRU training pipeline."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator

import keras
import numpy as np
import tensorflow as tf
from keras import layers

from .dataset import PlayerSeries, YearValue
from .strategies import (
    PredictionStrategy,
    get_feature_strategy,
)

PADDING_VALUE = -999.0

type Sample = tuple[np.ndarray, np.ndarray]
type SampleFactory = Callable[[], Iterator[Sample]]


def build_model(strategy: PredictionStrategy) -> keras.Model:
    """Build a GRU model for the selected representation."""

    inputs = keras.Input(
        shape=(None, strategy.input_size),
        name="yearly_history",
    )

    x = layers.Masking(
        mask_value=PADDING_VALUE,
    )(inputs)

    x = layers.GRU(
        32,
        dropout=0.1,
        name="gru",
    )(x)

    x = layers.Dense(
        32,
        activation="relu",
        name="dense",
    )(x)

    x = layers.Dropout(0.2)(x)

    outputs = strategy.make_output(x)

    model = keras.Model(inputs, outputs)

    model.compile(
        optimizer=keras.optimizers.Adam(
            learning_rate=0.0001,
            clipnorm=1.0,
        ),
        loss="mse",
        metrics=["mae"],
    )

    return model


def make_training_samples(
    series: PlayerSeries,
    keys: list[tuple[int, str]],
    min_history: int,
    max_target_year: int,
    strategy: PredictionStrategy,
) -> Iterator[Sample]:
    """Generate prefix samples with targets before validation year."""

    for key in keys:
        yearly_data = series[key]

        for target_index in range(min_history, len(yearly_data)):
            target_year, target_values = yearly_data[target_index]

            if target_year > max_target_year:
                break

            history = yearly_data[:target_index]

            encoded_history = strategy.encode_input_series(history)

            encoded_target = strategy.encode_target(target_values)

            if not np.all(np.isfinite(encoded_history)) or not np.all(
                np.isfinite(encoded_target)
            ):
                raise ValueError(f"Non-finite values for {key}")

            yield encoded_history, encoded_target


def make_forecast_samples(
    series: PlayerSeries,
    keys: list[tuple[int, str]],
    target_year: int,
    min_history: int,
    strategy: PredictionStrategy,
) -> Iterator[Sample]:
    """Generate samples predicting one actual calendar year."""

    for key in keys:
        yearly_data = series[key]

        target_index = next(
            (
                index
                for index, (year, _) in enumerate(yearly_data)
                if year == target_year
            ),
            None,
        )

        if target_index is None or target_index < min_history:
            continue

        history = yearly_data[:target_index]
        target_values = yearly_data[target_index][1]

        encoded_history = strategy.encode_input_series(history)

        encoded_target = strategy.encode_target(target_values)

        if not np.all(np.isfinite(encoded_history)) or not np.all(
            np.isfinite(encoded_target)
        ):
            raise ValueError(f"Non-finite validation values for {key}")

        yield encoded_history, encoded_target


def count_training_samples(
    series: PlayerSeries,
    keys: list[tuple[int, str]],
    min_history: int,
    max_target_year: int,
) -> int:
    count = 0

    for key in keys:
        yearly_data = series[key]

        for target_index in range(min_history, len(yearly_data)):
            target_year = yearly_data[target_index][0]

            if target_year > max_target_year:
                break

            count += 1

    return count


def count_forecast_samples(
    series: PlayerSeries,
    keys: list[tuple[int, str]],
    target_year: int,
    min_history: int,
) -> int:
    count = 0

    for key in keys:
        yearly_data = series[key]

        target_index = next(
            (
                index
                for index, (year, _) in enumerate(yearly_data)
                if year == target_year
            ),
            None,
        )

        if target_index is not None and target_index >= min_history:
            count += 1

    return count


def make_dataset(
    sample_factory: SampleFactory,
    sample_count: int,
    batch_size: int,
    shuffle: bool,
) -> tf.data.Dataset:
    dataset = tf.data.Dataset.from_generator(
        sample_factory,
        output_signature=(
            tf.TensorSpec(
                shape=(None, None),
                dtype=tf.float32,
            ),
            tf.TensorSpec(
                shape=(None,),
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
            [None, None],
            [None],
        ),
        padding_values=(
            tf.constant(PADDING_VALUE, dtype=tf.float32),
            tf.constant(0.0, dtype=tf.float32),
        ),
        drop_remainder=False,
    )

    return dataset.prefetch(tf.data.AUTOTUNE)


class TimeSeriesTrainer:
    """Train and evaluate yearly GRU models chronologically."""

    def __init__(
        self,
        mode: str = "absolute",
        batch_size: int = 128,
        seed: int = 42,
        samples_fraction: float = 0.25,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")

        if not 0.0 < samples_fraction <= 1.0:
            raise ValueError("samples_fraction must be in (0, 1]")

        self.strategy = get_feature_strategy(mode)
        self.batch_size = batch_size
        self.seed = seed
        self.samples_fraction = samples_fraction

    def fit(
        self,
        series: PlayerSeries,
        validation_year: int,
        min_history: int = 2,
        epochs: int = 30,
        checkpoint_path: str | None = None,
    ) -> keras.Model:
        """Train on historical prefixes and validate on one real year."""

        if not series:
            raise ValueError("No player series provided")

        if min_history < 1:
            raise ValueError("min_history must be at least 1")

        if validation_year < 1:
            raise ValueError("validation_year must be positive")

        keys = list(series)

        train_end_year = validation_year - 1

        train_count = count_training_samples(
            series,
            keys,
            min_history,
            train_end_year,
        )

        validation_count = count_forecast_samples(
            series,
            keys,
            validation_year,
            min_history,
        )

        if train_count < 1:
            raise ValueError("No training samples available")

        if validation_count < 1:
            raise ValueError(f"No validation samples for {validation_year}")

        print(f"Mode: {self.strategy.name}")
        print(f"Training target years: <= {train_end_year}")
        print(f"Validation target year: {validation_year}")
        print(f"Training samples: {train_count}")
        print(f"Validation samples: {validation_count}")

        def train_factory() -> Iterator[Sample]:
            return make_training_samples(
                series,
                keys,
                min_history,
                train_end_year,
                self.strategy,
            )

        def validation_factory() -> Iterator[Sample]:
            return make_forecast_samples(
                series,
                keys,
                validation_year,
                min_history,
                self.strategy,
            )

        train_dataset = make_dataset(
            train_factory,
            train_count,
            self.batch_size,
            shuffle=True,
        )

        validation_dataset = make_dataset(
            validation_factory,
            validation_count,
            self.batch_size,
            shuffle=False,
        )

        samples_per_epoch = max(
            1,
            int(train_count * self.samples_fraction),
        )

        steps_per_epoch = math.ceil(samples_per_epoch / self.batch_size)

        callbacks: list[keras.callbacks.Callback] = [
            keras.callbacks.TerminateOnNaN(),
            keras.callbacks.ReduceLROnPlateau(
                monitor="val_loss",
                factor=0.5,
                patience=5,
                min_delta=0.0001,
                min_lr=1e-6,
                verbose=1,
            ),
            keras.callbacks.EarlyStopping(
                monitor="val_loss",
                patience=12,
                min_delta=0.0001,
                restore_best_weights=False,
                verbose=1,
            ),
        ]

        if checkpoint_path:
            callbacks.append(
                keras.callbacks.ModelCheckpoint(
                    checkpoint_path,
                    monitor="val_loss",
                    save_best_only=True,
                )
            )

        keras.utils.set_random_seed(self.seed)

        model = build_model(self.strategy)

        model.fit(
            train_dataset,
            validation_data=validation_dataset,
            epochs=epochs,
            steps_per_epoch=steps_per_epoch,
            callbacks=callbacks,
            verbose=1,
        )

        return model

    def predict(
        self,
        model: keras.Model,
        yearly_data: list[YearValue],
    ) -> list[float]:
        if not yearly_data:
            return [0.0, 0.0]

        history = self.strategy.encode_input_series(yearly_data)

        prediction = model.predict(
            history[np.newaxis, :, :],
            verbose=0,
        )[0]

        return self.strategy.decode(prediction)

    def evaluate_year(
        self,
        model: keras.Model,
        series: PlayerSeries,
        target_year: int,
        min_history: int = 2,
    ) -> dict[str, float]:
        """Evaluate predictions against actual values for one year."""

        keys = list(series)

        samples = list(
            make_forecast_samples(
                series,
                keys,
                target_year,
                min_history,
                self.strategy,
            )
        )

        if not samples:
            raise ValueError(f"No samples available for {target_year}")

        absolute_errors: list[np.ndarray] = []
        squared_errors: list[np.ndarray] = []

        for history, encoded_target in samples:
            prediction = model.predict(
                history[np.newaxis, :, :],
                verbose=0,
            )[0]

            predicted_values = np.asarray(
                self.strategy.decode(prediction),
                dtype=np.float64,
            )

            actual_values = np.asarray(
                self.strategy.decode(encoded_target),
                dtype=np.float64,
            )

            errors = predicted_values - actual_values

            absolute_errors.append(np.abs(errors))
            squared_errors.append(np.square(errors))

        mae = np.mean(absolute_errors, axis=0)
        mse = np.mean(squared_errors, axis=0)

        return {
            "samples": float(len(samples)),
            "mae_first": float(mae[0]),
            "mae_second": float(mae[1]),
            "mse_first": float(mse[0]),
            "mse_second": float(mse[1]),
        }


def train_model(
    series: PlayerSeries,
    validation_year: int,
    min_history: int = 2,
    epochs: int = 30,
    batch_size: int = 128,
    seed: int = 42,
    mode: str = "absolute",
    samples_fraction: float = 0.25,
) -> keras.Model:
    """Compatibility function for model training."""

    trainer = TimeSeriesTrainer(
        mode=mode,
        batch_size=batch_size,
        seed=seed,
        samples_fraction=samples_fraction,
    )

    return trainer.fit(
        series,
        validation_year=validation_year,
        min_history=min_history,
        epochs=epochs,
    )


def predict_next_year(
    model: keras.Model,
    yearly_data: list[YearValue],
    mode: str = "absolute",
) -> list[float]:
    """Predict the next year using the selected representation."""

    strategy = get_feature_strategy(mode)

    if not yearly_data:
        return [0.0, 0.0]

    history = strategy.encode_input_series(yearly_data)

    prediction = model.predict(
        history[np.newaxis, :, :],
        verbose=0,
    )[0]

    return strategy.decode(prediction)
