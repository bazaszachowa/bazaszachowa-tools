import random
from collections.abc import Generator

import keras
import numpy as np
import tensorflow as tf
from keras import layers

PADDING_VALUE = -999.0


def encode_yearly_data(data: list[tuple[int, float]]) -> np.ndarray:
    """
    [count, points] -> [log1p(count), scoring_rate]
    """
    encoded = []

    for count, points in data:
        rate = points / count if count else 0.0

        encoded.append(
            [
                np.log1p(count),
                rate,
            ]
        )

    return np.asarray(encoded, dtype=np.float32)


def encode_target(count: int, points: float) -> np.ndarray:
    rate = points / count if count else 0.0

    return np.asarray(
        [
            np.log1p(count),
            rate,
        ],
        dtype=np.float32,
    )


def decode_prediction(prediction: np.ndarray) -> list[float]:
    count = max(0.0, np.expm1(prediction[0]))
    rate = np.clip(prediction[1], 0.0, 1.0)

    return [
        float(count),
        float(count * rate),
    ]


def build_model() -> keras.Model:
    inputs = keras.Input(shape=(None, 2))

    x = layers.Masking(mask_value=PADDING_VALUE)(inputs)
    x = layers.GRU(32, dropout=0.1)(x)
    x = layers.Dense(32, activation="relu")(x)
    x = layers.Dropout(0.2)(x)
    count = layers.Dense(1, activation="softplus", name="count")(x)
    rate = layers.Dense(1, activation="sigmoid", name="rate")(x)

    outputs = layers.Concatenate()([count, rate])

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


def make_samples(
    series: dict[tuple[int, str], list[tuple[int, list[float]]]],
    keys: list[tuple[int, str]],
    min_history: int = 1,
) -> Generator[tuple[np.ndarray, np.ndarray], None, None]:

    for key in keys:
        yearly_data = series[key]

        encoded = encode_yearly_data(
            [(int(value[0]), float(value[1])) for _, value in yearly_data]
        )

        for target_index in range(min_history, len(encoded)):
            history = encoded[:target_index]
            target = encoded[target_index]

            if not np.all(np.isfinite(history)):
                raise ValueError(f"Non-finite history for {key}")

            if not np.all(np.isfinite(target)):
                raise ValueError(f"Non-finite target for {key}")

            if np.any(history[:, 0] < 0):
                raise ValueError(f"Negative game count for {key}")

            if np.any(history[:, 1] < 0) or np.any(history[:, 1] > 1):
                raise ValueError(f"Invalid scoring rate in history for {key}")

            if target[0] < 0:
                raise ValueError(f"Negative target game count for {key}")

            if target[1] < 0 or target[1] > 1:
                raise ValueError(f"Invalid target scoring rate for {key}")

            yield history, target


def make_dataset(
    series: dict[tuple[int, str], list[tuple[int, list[float]]]],
    keys: list[tuple[int, str]],
    min_history: int,
    batch_size: int,
    shuffle: bool,
) -> tf.data.Dataset:
    output_signature = (
        tf.TensorSpec(shape=(None, 2), dtype=tf.float32),
        tf.TensorSpec(shape=(2,), dtype=tf.float32),
    )

    dataset = tf.data.Dataset.from_generator(
        lambda: make_samples(series, keys, min_history),
        output_signature=output_signature,
    )

    if shuffle:
        dataset = dataset.shuffle(
            buffer_size=10000,
            reshuffle_each_iteration=True,
        )
        dataset = dataset.repeat()

    dataset = dataset.padded_batch(
        batch_size,
        padded_shapes=(
            [None, 2],
            [2],
        ),
        padding_values=(
            tf.constant(PADDING_VALUE, dtype=tf.float32),
            tf.constant(0.0, dtype=tf.float32),
        ),
        drop_remainder=False,
    )

    dataset = dataset.prefetch(tf.data.AUTOTUNE)

    if shuffle:
        dataset = dataset.apply(
            tf.data.experimental.prefetch_to_device(
                "/GPU:0",
                buffer_size=2,
            )
        )

    return dataset


def count_samples(
    series: dict[tuple[int, str], list[tuple[int, list[float]]]],
    keys: list[tuple[int, str]],
    min_history: int,
) -> int:
    return sum(max(0, len(series[key]) - min_history) for key in keys)


def train_model(
    series: dict[tuple[int, str], list[tuple[int, list[float]]]],
    min_history: int = 1,
    epochs: int = 30,
    batch_size: int = 128,
    validation_fraction: float = 0.2,
    seed: int = 42,
) -> keras.Model:
    if not series:
        raise ValueError("No player series provided")

    keys = list(series.keys())
    random.Random(seed).shuffle(keys)

    if len(keys) < 2:
        raise ValueError("At least two player-role series are required")

    split_index = max(
        1,
        min(
            len(keys) - 1,
            int(len(keys) * (1.0 - validation_fraction)),
        ),
    )

    train_keys = keys[:split_index]
    validation_keys = keys[split_index:]

    train_count = count_samples(series, train_keys, min_history)
    validation_count = count_samples(
        series,
        validation_keys,
        min_history,
    )

    if train_count < 1 or validation_count < 1:
        raise ValueError(
            "Not enough training or validation samples. Try lowering min_history."
        )

    print(f"Training samples: {train_count}")
    print(f"Validation samples: {validation_count}")

    train_dataset = make_dataset(
        series,
        train_keys,
        min_history,
        batch_size,
        shuffle=True,
    )

    validation_dataset = make_dataset(
        series,
        validation_keys,
        min_history,
        batch_size,
        shuffle=False,
    )
    print("Train dataset cardinality:", train_dataset.cardinality().numpy())
    print("Validation dataset cardinality:", validation_dataset.cardinality().numpy())

    model = build_model()

    samples_per_epoch = int(train_count * 0.25)

    steps_per_epoch = (samples_per_epoch + batch_size - 1) // batch_size

    model.fit(
        train_dataset,
        validation_data=validation_dataset,
        epochs=epochs,
        shuffle=False,
        steps_per_epoch=steps_per_epoch,
        callbacks=[
            keras.callbacks.TerminateOnNaN(),
            keras.callbacks.ModelCheckpoint(
                "best_model.keras",
                monitor="val_loss",
                save_best_only=True,
                verbose=1,
            ),
            keras.callbacks.ReduceLROnPlateau(
                monitor="val_loss",
                factor=0.5,
                patience=3,
                min_delta=0.0001,
                min_lr=1e-6,
                verbose=1,
            ),
            keras.callbacks.EarlyStopping(
                monitor="val_loss",
                patience=10,
                min_delta=0.0001,
                restore_best_weights=True,
                verbose=1,
            ),
        ],
    )

    return model


def predict_next_year(
    model: keras.Model, yearly_data: list[tuple[int, list[float]]]
) -> list[float]:
    history = encode_yearly_data(
        [(int(value[0]), float(value[1])) for _, value in yearly_data]
    )

    X = history[np.newaxis, :, :]

    prediction = model.predict(X, verbose=0)[0]

    return decode_prediction(prediction)
