from collections import defaultdict
from typing import Literal, NotRequired, TypedDict

import chess

BOARD_SIZE = 400
SQUARE_SIZE = BOARD_SIZE // 8
MAX_PLIES = 20

CHESS_PIECES = {
    "p": "♟",
    "r": "♜",
    "n": "♞",
    "b": "♝",
    "q": "♛",
    "k": "♚",
    "P": "♙",
    "R": "♖",
    "N": "♘",
    "B": "♗",
    "Q": "♕",
    "K": "♔",
}

LIGHT_SQUARE = "#f0d9b5"
DARK_SQUARE = "#b58863"
SELECTED_SQUARE = "#f6f669"

UciMove = TypedDict(
    "UciMove",
    {
        "from": str,
        "to": str,
        "promotion": NotRequired[str | None],
    },
)


class Game(TypedDict, total=False):
    """Game fields returned by the API (some metadata may be absent)."""

    id: int
    moves: list[UciMove]
    Event: str | None
    Site: str | None
    Year: int
    year: int
    Month: int | None
    Day: int | None
    Round: str | None
    White: str
    Black: str
    Result: int | Literal["1-0", "0-1", "1/2-1/2", "*"]
    result: int | Literal["1-0", "0-1", "1/2-1/2", "*"]
    WhiteElo: int | None
    BlackElo: int | None
    ECO: str | None


YearlyStats = list[int | float]


class OpeningNode(TypedDict):
    children: dict[str, "OpeningNode"]
    games: int
    wins: int
    draws: int
    losses: int
    unknown: int
    yearly: dict[int, YearlyStats]
    san: str
    uci: str


def parse_result(result: int | str) -> str:
    """Return the winner: white, black, draw or unknown."""
    if result in (0, "0", "0-1"):
        return "black"
    if result in (1, "1", "1/2-1/2"):
        return "draw"
    if result in (2, "2", "1-0"):
        return "white"
    return "unknown"


def make_move(move_data: UciMove) -> chess.Move:
    """
    Supported formats:
        {"from": "e2", "to": "e4"}
        {"from": "e7", "to": "e8", "promotion": "q"}
    """

    uci = move_data["from"] + move_data["to"]

    promotion = move_data.get("promotion")
    if promotion:
        uci += promotion.lower()

    return chess.Move.from_uci(uci)


def create_node() -> OpeningNode:
    return {
        "children": {},
        "games": 0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "unknown": 0,
        "yearly": defaultdict[int, YearlyStats](lambda: [0, 0.0]),
        "san": "",
        "uci": "",
    }
