
import numpy as np
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


def make_samples(series, min_history=1):
    """
    Creates variable-length sequences:
        previous years -> next year's [count, points]
    """
    samples = []

    for yearly_data in series.values():
        values = [value for _, value in yearly_data]

        for target_index in range(min_history, len(values)):
            history = encode_yearly_data(values[:target_index])
            target = encode_target(*values[target_index])

            samples.append((history, target))

    return samples


def pad_samples(samples):
    max_length = max(len(history) for history, _ in samples)

    X = np.full(
        (len(samples), max_length, 2),
        PADDING_VALUE,
        dtype=np.float32,
    )
    y = np.asarray([target for _, target in samples], dtype=np.float32)

    for index, (history, _) in enumerate(samples):
        X[index, :len(history)] = history

    return X, y

def train_model(series, min_history=1, epochs=30, batch_size=128):
    samples = make_samples(series, min_history)

    if len(samples) < 2:
        raise ValueError("Not enough training samples")

    X, y = pad_samples(samples)

    model = build_model()

    model.fit(
        X,
        y,
        epochs=epochs,
        batch_size=batch_size,
        validation_split=0.2,
        shuffle=True,
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
