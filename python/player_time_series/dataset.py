"""Load yearly player statistics from SQLite."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Literal

type Representation = Literal["absolute", "percentage"]
type SeriesKey = tuple[int, str]
type YearValue = tuple[int, list[float]]
type PlayerSeries = dict[SeriesKey, list[YearValue]]


_QUERY = """
WITH player_games AS (
    SELECT
        white_id AS player_id,
        'white' AS role,
        year,
        CASE result
            WHEN 2 THEN 1.0
            WHEN 1 THEN 0.5
            ELSE 0.0
        END AS points
    FROM games

    UNION ALL

    SELECT
        black_id AS player_id,
        'black' AS role,
        year,
        CASE result
            WHEN 0 THEN 1.0
            WHEN 1 THEN 0.5
            ELSE 0.0
        END AS points
    FROM games
), player_years AS (
    SELECT
        player_id,
        role,
        year,
        COUNT(*) AS game_count,
        SUM(points) AS points
    FROM player_games
    GROUP BY player_id, role, year
)
SELECT
    player_id,
    role,
    year,
    game_count,
    points,
    SUM(game_count) OVER (PARTITION BY player_id, role) AS career_games,
    SUM(points) OVER (PARTITION BY player_id, role) AS career_points
FROM player_years
ORDER BY player_id, role, year
"""


def load_player_series(
    connection: sqlite3.Connection,
    representation: Representation = "absolute",
) -> PlayerSeries:
    """Return one chronological series per (player_id, role).

    ``absolute`` values are [games in year, points in year].
    ``percentage`` values are [share of career games, share of career points].
    Both percentage features are fractions in the [0, 1] range.
    """
    if representation not in ("absolute", "percentage"):
        raise ValueError(f"Unknown representation: {representation}")

    grouped: defaultdict[SeriesKey, dict[int, list[float]]] = defaultdict(dict)

    for row in connection.execute(_QUERY):
        (
            player_id,
            role,
            year,
            game_count,
            points,
            career_games,
            career_points,
        ) = row

        if representation == "absolute":
            values = [float(game_count), float(points)]
        else:
            game_share = (
                float(game_count) / float(career_games) if career_games else 0.0
            )
            point_share = float(points) / float(career_points) if career_points else 0.0
            values = [game_share, point_share]

        grouped[(int(player_id), str(role))][int(year)] = values

    # Preserve the existing behavior: missing calendar years are explicit zero rows.
    result: PlayerSeries = {}
    for key, yearly_data in grouped.items():
        first_year = min(yearly_data)
        last_year = max(yearly_data)
        for year in range(first_year, last_year + 1):
            yearly_data.setdefault(year, [0.0, 0.0])
        result[key] = sorted(yearly_data.items())

    return result
