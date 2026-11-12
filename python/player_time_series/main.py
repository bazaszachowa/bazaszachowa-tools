
import argparse
import sqlite3
from pathlib import Path

from .dataset import load_player_series
from .model import train_model, predict_next_year


def main():
    parser = argparse.ArgumentParser(
        description="Train a chess player yearly time-series model."
    )
    parser.add_argument(
        "--database",
        default="all_games.sqlite",
        help="SQLite database path",
    )
    parser.add_argument(
        "--output",
        default="player_time_series.keras",
        help="Output Keras model path",
    )
    parser.add_argument(
        "--min-history",
        type=int,
        default=1,
        help="Minimum previous years required for a training sample",
    )
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--player-id", type=int)
    parser.add_argument(
        "--role",
        choices=("white", "black"),
        default="white",
        help="Role used when predicting for --player-id",
    )

    args = parser.parse_args()

    if args.min_history < 1:
        parser.error("--min-history must be at least 1")

    with sqlite3.connect(args.database) as connection:
        series = load_player_series(connection)

    print(f"Loaded {len(series)} player-role time series")

    if not series:
        raise RuntimeError("No player data found in the database")

    model = train_model(
        series,
        min_history=args.min_history,
        epochs=args.epochs,
        batch_size=args.batch_size,
    )

    model.save(args.output)
    print(f"Model saved to: {args.output}")

    if args.player_id is not None:
        key = (args.player_id, args.role)

        if key not in series:
            print(f"No {args.role} games found for player {args.player_id}")
            return

        history = series[key]
        prediction = predict_next_year(model, history)

        last_year = history[-1][0]

        print(f"\nPlayer: {args.player_id}")
        print(f"Role: {args.role}")
        print(f"History: {history}")
        print(f"Predicted year: {last_year + 1}")
        print(
            "Predicted [games, points]: "
            f"[{prediction[0]:.2f}, {prediction[1]:.2f}]"
        )


if __name__ == "__main__":
    main()
