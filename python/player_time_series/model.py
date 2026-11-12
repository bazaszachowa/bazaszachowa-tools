
import random

import numpy as np
import tensorflow as tf
import keras
from keras import layers


PADDING_VALUE = -999.0


def encode_yearly_data(data):
    """
    [count, points] -> [log1p(count), scoring_rate]
    """
    encoded = []

    for count, points in data:
        rate = points / count if count else 0.0

        encoded.append([
            np.log1p(count),
            rate,
        ])

    return np.asarray(encoded, dtype=np.float32)


def encode_target(count, points):
    rate = points / count if count else 0.0

    return np.asarray([
        np.log1p(count),
        rate,
    ], dtype=np.float32)


def decode_prediction(prediction):
    count = max(0.0, np.expm1(prediction[0]))
    rate = np.clip(prediction[1], 0.0, 1.0)

    return [
        float(count),
        float(count * rate),
    ]


def build_model():
    inputs = keras.Input(shape=(None, 2))

    x = layers.Masking(mask_value=PADDING_VALUE)(inputs)
    x = layers.LSTM(64)(x)
    x = layers.Dense(32, activation="relu")(x)

    count = layers.Dense(1, activation="softplus")(x)
    rate = layers.Dense(1, activation="sigmoid")(x)

    outputs = layers.Concatenate()([count, rate])

    model = keras.Model(inputs, outputs)

    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=0.001),
        loss="mse",
        metrics=["mae"],
    )

    return model


def make_samples(series, keys, min_history=1):
    """
    Generates:
        previous years -> next year's [count, points]

    Samples are yielded one at a time to avoid storing all
    encoded sequences in memory.
    """
    for key in keys:
        yearly_data = series[key]
        values = [value for _, value in yearly_data]

        for target_index in range(min_history, len(values)):
            history = encode_yearly_data(values[:target_index])
            target = encode_target(*values[target_index])

            yield history, target


def make_dataset(series, keys, min_history, batch_size, shuffle):
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

    return dataset.prefetch(tf.data.AUTOTUNE)


def count_samples(series, keys, min_history):
    return sum(
        max(0, len(series[key]) - min_history)
        for key in keys
    )


def train_model(
    series,
    min_history=1,
    epochs=30,
    batch_size=128,
    validation_fraction=0.2,
    seed=42,
):
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
            "Not enough training or validation samples. "
            "Try lowering min_history."
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

    model = build_model()

    model.fit(
        train_dataset,
        validation_data=validation_dataset,
        epochs=epochs,
        callbacks=[
            keras.callbacks.EarlyStopping(
                monitor="val_loss",
                patience=5,
                restore_best_weights=True,
            )
        ],
    )

    return model


def predict_next_year(model, yearly_data):
    history = encode_yearly_data(
        [value for _, value in yearly_data]
    )

    X = history[np.newaxis, :, :]

    prediction = model.predict(X, verbose=0)[0]

    return decode_prediction(prediction)
