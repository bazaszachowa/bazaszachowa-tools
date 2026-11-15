"""Pure opening-tree construction and statistics helpers."""

from dataclasses import dataclass

import chess

from .opening_types import (
    MAX_PLIES,
    Game,
    OpeningNode,
    create_node,
    make_move,
    parse_result,
)


@dataclass(slots=True)
class OpeningTreeData:
    root: OpeningNode
    first_year: int | None
    latest_year: int | None

    @property
    def next_year(self) -> int | None:
        return self.latest_year + 1 if self.latest_year is not None else None


def get_game_year(game: Game) -> int | None:
    value = game.get("Year", game.get("year"))
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def get_player_result(result: int | str | None, color: str) -> str:
    outcome = parse_result(result if result is not None else "")
    if outcome == "unknown":
        return "unknown"
    if outcome == "draw":
        return "draw"
    return "win" if outcome == color else "loss"


def update_node_stats(node: OpeningNode, result: str, year: int | None) -> None:
    node["games"] += 1

    if result == "win":
        node["wins"] += 1
        points = 1.0
    elif result == "draw":
        node["draws"] += 1
        points = 0.5
    elif result == "loss":
        node["losses"] += 1
        points = 0.0
    else:
        node["unknown"] += 1
        points = 0.0

    if year is not None:
        yearly = node["yearly"][year]
        yearly[0] += 1
        yearly[1] += points


def build_opening_tree(games: list[Game], color: str) -> OpeningTreeData:
    root = create_node()
    years = [year for game in games if (year := get_game_year(game)) is not None]
    first_year = min(years) if years else None
    latest_year = max(years) if years else None

    for game in games:
        moves = game.get("moves") or []
        result = get_player_result(
            game.get("Result", game.get("result")),
            color,
        )
        year = get_game_year(game)

        # Count every game at the root, including games without moves.
        update_node_stats(root, result, year)

        if not isinstance(moves, list) or not moves:
            continue

        board = chess.Board()
        node = root

        for move_data in moves[:MAX_PLIES]:
            try:
                move = make_move(move_data)
            except (ValueError, KeyError, TypeError):
                break

            if move not in board.legal_moves:
                break

            san = board.san(move)
            uci = move.uci()

            if uci not in node["children"]:
                child = create_node()
                child["san"] = san
                child["uci"] = uci
                node["children"][uci] = child

            node = node["children"][uci]
            board.push(move)
            update_node_stats(node, result, year)

    return OpeningTreeData(root, first_year, latest_year)


def get_time_series(
    node: OpeningNode,
    first_year: int | None,
    latest_year: int | None,
) -> list[tuple[int, int, float]]:
    if first_year is None or latest_year is None:
        return []

    result: list[tuple[int, int, float]] = []
    for year in range(first_year, latest_year + 1):
        games, points = node["yearly"].get(year, [0, 0.0])
        result.append((year, int(games), float(points)))
    return result


def get_stats_text(
    node: OpeningNode,
    forecast_games: float,
    forecast_points: float,
) -> tuple[int, int, int, int, int, str, str, str, str]:
    games = node["games"]
    wins = node["wins"]
    draws = node["draws"]
    losses = node["losses"]
    unknown = node["unknown"]

    decided = wins + draws + losses
    score = (wins + 0.5 * draws) / decided * 100 if decided else 0.0
    expected_score = (
        f"{forecast_points / forecast_games * 100:.1f}" if forecast_games else "0.0"
    )

    return (
        games,
        wins,
        draws,
        losses,
        unknown,
        f"{score:.1f}%",
        expected_score,
        f"{forecast_games:.1f}",
        f"{forecast_points:.1f}",
    )
