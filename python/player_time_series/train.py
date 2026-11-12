
import sqlite3

from player_time_series import (
    load_player_series,
    train_model,
    predict_next_year,
)


DATABASE = "all_games.sqlite"


def main():
    with sqlite3.connect(DATABASE) as connection:
        series = load_player_series(connection)

    print(f"Player-role series: {len(series)}")

    model = train_model(
        series,
        min_history=2,
        epochs=30,
    )

    model.save("player_time_series.keras")

    # Example: predict next year for a player's white appearances.
    player_id = 12345
    key = (player_id, "white")

    if key in series:
        prediction = predict_next_year(model, series[key])
        print("Predicted [games, points]:", prediction)


if __name__ == "__main__":
    main()
