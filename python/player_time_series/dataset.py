"""Load yearly player statistics from SQLite."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Literal

type Representation = Literal["absolute", "percentage"]
type SeriesKey = tuple[int, str]
type YearValue = tuple[int, list[float]]
type PlayerSeries = dict[SeriesKey, list[YearValue]]


_QUERY = """
WITH all_player_games AS (
    SELECT
        white_id AS player_id,
        'white' AS role,
        year
    FROM games

    UNION ALL

    SELECT
        black_id AS player_id,
        'black' AS role,
        year
    FROM games
),
position_games AS (
    SELECT *
    FROM games
    WHERE {position_filter}
),
position_player_games AS (
    SELECT
        white_id AS player_id,
        'white' AS role,
        year,
        CASE result
            WHEN 2 THEN 1.0
            WHEN 1 THEN 0.5
            ELSE 0.0
        END AS points
    FROM position_games

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
    FROM position_games
),
all_player_years AS (
    SELECT
        player_id,
        role,
        year,
        COUNT(*) AS total_game_count
    FROM all_player_games
    GROUP BY player_id, role, year
),
position_player_years AS (
    SELECT
        player_id,
        role,
        year,
        COUNT(*) AS game_count,
        SUM(points) AS points
    FROM position_player_games
    GROUP BY player_id, role, year
),
position_players AS (
    SELECT DISTINCT
        player_id,
        role
    FROM position_player_years
)
SELECT
    a.player_id,
    a.role,
    a.year,
    COALESCE(p.game_count, 0) AS game_count,
    COALESCE(p.points, 0.0) AS points,
    a.total_game_count
FROM all_player_years a
JOIN position_players k
    ON k.player_id = a.player_id
    AND k.role = a.role
LEFT JOIN position_player_years p
    ON p.player_id = a.player_id
    AND p.role = a.role
    AND p.year = a.year
ORDER BY a.player_id, a.role, a.year
"""


def load_player_series(
    connection: sqlite3.Connection,
    representation: Representation = "absolute",
    position_game_ids: Sequence[int] | None = None,
    current_year: int | None = None,
) -> PlayerSeries:
    """Return chronological series for players who played the position.

    Absolute:
        [games_with_position, points]

    Percentage:
        [game_share, point_score, has_games]

    Missing years are filled with zeros.
    """

    if representation not in ("absolute", "percentage"):
        raise ValueError(f"Unknown representation: {representation}")

    if current_year is None:
        current_year = date.today().year

    if current_year < 1:
        raise ValueError("current_year must be positive")

    if position_game_ids is None:
        position_filter = "1 = 1"
        parameters: tuple[int, ...] = ()
    else:
        ids = tuple(position_game_ids)

        if not ids:
            return {}

        placeholders = ", ".join("?" for _ in ids)
        position_filter = f"mysql_game_id IN ({placeholders})"
        parameters = ids

    query = _QUERY.format(position_filter=position_filter)

    grouped: defaultdict[SeriesKey, dict[int, list[float]]] = defaultdict(dict)

    for row in connection.execute(query, parameters):
        (
            player_id,
            role,
            year,
            game_count,
            points,
            total_game_count,
        ) = row

        game_count = int(game_count)
        total_game_count = int(total_game_count)
        points = float(points)

        if representation == "absolute":
            values = [
                float(game_count),
                points,
            ]
        else:
            game_share = game_count / total_game_count if total_game_count > 0 else 0.0

            point_score = points / game_count if game_count > 0 else 0.0

            has_games = 1.0 if total_game_count > 0 else 0.0

            values = [
                game_share,
                point_score,
                has_games,
            ]

        key = (int(player_id), str(role))
        grouped[key][int(year)] = values

    result: PlayerSeries = {}

    for key, yearly_data in grouped.items():
        first_year = min(yearly_data)
        last_year = max(current_year, max(yearly_data))

        for year in range(first_year, last_year + 1):
            if year not in yearly_data:
                if representation == "percentage":
                    yearly_data[year] = [0.0, 0.0, 0.0]
                else:
                    yearly_data[year] = [0.0, 0.0]

        result[key] = sorted(yearly_data.items())

    return result
