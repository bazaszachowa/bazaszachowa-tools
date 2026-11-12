import sqlite3
from collections import defaultdict


def load_player_series(
    connection: sqlite3.Connection,
) -> dict[tuple[int, str], list[tuple[int, list[float]]]]:
    """
    Returns:
        {
            (player_id, "white"): {
                year: [game_count, points],
                ...
            },
            (player_id, "black"): {...}
        }
    """

    query = """
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
        )
        SELECT
            player_id,
            role,
            year,
            COUNT(*) AS game_count,
            SUM(points) AS points
        FROM player_games
        GROUP BY player_id, role, year
        ORDER BY player_id, role, year
    """

    series: defaultdict[tuple[int, str], dict[int, list[float]]] = defaultdict(dict)

    for player_id, role, year, count, points in connection.execute(query):
        series[(player_id, role)][year] = [
            int(count),
            float(points),
        ]

    # Fill missing calendar years with [0, 0].
    for yearly_data in series.values():
        first_year = min(yearly_data)
        last_year = max(yearly_data)

        for year in range(first_year, last_year + 1):
            yearly_data.setdefault(year, [0, 0.0])

    return {key: sorted(values.items()) for key, values in series.items()}
