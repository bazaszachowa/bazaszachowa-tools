"""Load yearly player statistics from SQLite."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
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
)
SELECT
    p.player_id,
    p.role,
    p.year,
    p.game_count,
    p.points,
    a.total_game_count
FROM position_player_years p
JOIN all_player_years a
    ON a.player_id = p.player_id
    AND a.role = p.role
    AND a.year = p.year
ORDER BY p.player_id, p.role, p.year
"""


def load_player_series(
    connection: sqlite3.Connection,
    representation: Representation = "absolute",
    position_game_ids: Sequence[int] | None = None,
) -> PlayerSeries:
    """Return one chronological series per (player_id, role).

    ``absolute`` values:
        [games with position in year, points scored with position in year]

    ``percentage`` values:
        [share of player's yearly games containing position,
         score percentage in games containing position]

    ``position_game_ids`` contains IDs of games in which the position
    occurred. If None, all games are treated as position games.
    """
    if representation not in ("absolute", "percentage"):
        raise ValueError(f"Unknown representation: {representation}")

    if position_game_ids is None:
        position_filter = "1 = 1"
        parameters: tuple[int, ...] = ()
    else:
        ids = tuple(position_game_ids)
        if not ids:
            position_filter = "0 = 1"
            parameters = ()
        else:
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

        if representation == "absolute":
            values = [
                float(game_count),
                float(points),
            ]
        else:
            game_share = (
                float(game_count) / float(total_game_count) if total_game_count else 0.0
            )
            point_score = float(points) / float(game_count) if game_count else 0.0
            values = [game_share, point_score]

        grouped[(int(player_id), str(role))][int(year)] = values

    result: PlayerSeries = {}

    for key, yearly_data in grouped.items():
        first_year = min(yearly_data)
        last_year = max(yearly_data)

        for year in range(first_year, last_year + 1):
            yearly_data.setdefault(year, [0.0, 0.0])

        result[key] = sorted(yearly_data.items())

    return result
