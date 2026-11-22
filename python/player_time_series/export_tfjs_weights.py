import json
import sys
from pathlib import Path
from typing import Any, cast

import keras
import numpy as np


def to_list(values: np.ndarray) -> list[float]:
    return cast(list[float], values.astype(np.float32).tolist())


def export_model(model_path: Path, output_path: Path) -> None:
    model = keras.models.load_model(model_path)

    gru = model.get_layer("gru")
    dense = model.get_layer("dense")

    gru_kernel, gru_recurrent_kernel, gru_bias = gru.get_weights()
    dense_kernel, dense_bias = dense.get_weights()

    weights: dict[str, Any] = {
        "input_size": 3,
        "gru_units": 32,
        "gru": {
            "kernel": to_list(gru_kernel),
            "recurrent_kernel": to_list(gru_recurrent_kernel),
            "bias": to_list(gru_bias),
        },
        "dense": {
            "kernel": to_list(dense_kernel),
            "bias": to_list(dense_bias),
        },
    }

    if "count" in [layer.name for layer in model.layers]:
        count = model.get_layer("count")
        rate = model.get_layer("rate")

        count_kernel, count_bias = count.get_weights()
        rate_kernel, rate_bias = rate.get_weights()

        weights["output"] = {
            "type": "absolute",
            "count": {
                "kernel": to_list(count_kernel),
                "bias": to_list(count_bias),
            },
            "rate": {
                "kernel": to_list(rate_kernel),
                "bias": to_list(rate_bias),
            },
        }
    else:
        percentage = model.get_layer("percentage")
        output_kernel, output_bias = percentage.get_weights()

        weights["output"] = {
            "type": "percentage",
            "kernel": to_list(output_kernel),
            "bias": to_list(output_bias),
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(weights, separators=(",", ":")),
        encoding="utf-8",
    )

    print(f"Zapisano: {output_path}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Użycie: export_tfjs_weights.py MODEL.keras OUTPUT.json")

    export_model(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
