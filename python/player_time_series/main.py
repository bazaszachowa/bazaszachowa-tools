"""Train a player time-series model from a SQLite database."""

from __future__ import annotations

import argparse
import sqlite3

from .dataset import Representation, load_player_series
from .model import TimeSeriesTrainer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="all_games.sqlite")
    parser.add_argument(
        "--mode",
        choices=("absolute", "percentage"),
        default="absolute",
        help="absolute=[log games, scoring rate], percentage=[career game share, career point share]",
    )
    parser.add_argument("--min-history", type=int, default=2)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--samples-fraction", type=float, default=0.25)
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mode: Representation = args.mode

    with sqlite3.connect(args.database) as connection:
        series = load_player_series(connection, representation=mode)

    print(f"Player-role series: {len(series)}")

    trainer = TimeSeriesTrainer(
        mode=mode,
        batch_size=args.batch_size,
        samples_fraction=args.samples_fraction,
    )
    model = trainer.fit(
        series,
        min_history=args.min_history,
        epochs=args.epochs,
        checkpoint_path=f"best_{mode}.keras",
    )

    output = args.output or f"player_time_series_{mode}.keras"
    model.save(output)
    print(f"Saved model: {output}")


if __name__ == "__main__":
    main()
