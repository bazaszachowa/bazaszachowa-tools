import argparse
import threading
import tkinter as tk
import urllib.parse
from collections import defaultdict
from tkinter import messagebox, ttk
from typing import Any, Literal, TypedDict

import chess
import keras
import requests

from .model import predict_next_year

API = {
    "search_player": "https://api.bazaszachowa.smallhost.pl/players/",
    "search_games": "https://api.bazaszachowa.smallhost.pl/games/opening/",
}

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
        "promotion": str | None,
    },
)


class Game(TypedDict):
    id: int
    moves: list[UciMove]
    Event: str | None
    Site: str | None
    Year: int
    Month: int | None
    Day: int | None
    Round: str | None
    White: str
    Black: str
    Result: Literal["1-0", "0-1", "1/2-1/2", "*"]
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


class ChessOpeningApp:
    def __init__(self, master: tk.Tk, model: keras.Model) -> None:
        self.master = master
        self.model = model
        self.master.title("Chess Opening Explorer")
        self.master.geometry("1450x850")
        self.master.minsize(1100, 700)

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Bazaszachowa-Opening-Explorer/1.0"})

        self.games: list[Game] = []
        self.board = chess.Board()

        self.root_node: OpeningNode = create_node()
        self.current_node: OpeningNode = self.root_node

        self.current_path: list[str] = []
        self.selected_square: chess.Square | None = None

        self.tree_moves: dict[str, str] = {}
        self.search_after_id: str | None = None
        self.search_generation: int = 0

        self.first_year: int | None = None
        self.latest_year: int | None = None
        self.next_year: int | None = None

        self.player_var = tk.StringVar()
        self.color_var = tk.StringVar(value="white")
        self.status_var = tk.StringVar(value="Search for a player.")

        self.position_var = tk.StringVar(value="Starting position")

        self.details_var = tk.StringVar(
            value="Select a possible next move to see its time series."
        )

        self.create_widgets()
        self.draw_board()
        self.populate_tree()

    # ---------------------------------------------------------
    # GUI
    # ---------------------------------------------------------

    def create_widgets(self) -> None:
        top = ttk.Frame(self.master, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Player:").pack(side=tk.LEFT, padx=5)

        self.player_entry = ttk.Combobox(
            top,
            textvariable=self.player_var,
            width=35,
        )
        self.player_entry.pack(side=tk.LEFT, padx=5)
        self.player_entry.bind(
            "<KeyRelease>",
            self.on_player_keyrelease,
        )

        ttk.Label(top, text="Color:").pack(side=tk.LEFT, padx=5)

        ttk.Radiobutton(
            top,
            text="White",
            variable=self.color_var,
            value="white",
        ).pack(side=tk.LEFT)

        ttk.Radiobutton(
            top,
            text="Black",
            variable=self.color_var,
            value="black",
        ).pack(side=tk.LEFT)

        self.submit_button = ttk.Button(
            top,
            text="Search games",
            command=self.submit_player,
        )
        self.submit_button.pack(side=tk.LEFT, padx=10)

        main = ttk.PanedWindow(
            self.master,
            orient=tk.HORIZONTAL,
        )
        main.pack(
            fill=tk.BOTH,
            expand=True,
            padx=10,
            pady=5,
        )

        # Left panel: board and navigation.

        left = ttk.Frame(main, padding=5)
        main.add(left, weight=1)

        self.canvas = tk.Canvas(
            left,
            width=BOARD_SIZE,
            height=BOARD_SIZE,
            highlightthickness=0,
        )
        self.canvas.pack(pady=5)
        self.canvas.bind(
            "<Button-1>",
            self.on_board_click,
        )

        navigation = ttk.Frame(left)
        navigation.pack(fill=tk.X, pady=5)

        ttk.Button(
            navigation,
            text="Start",
            command=self.go_start,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Button(
            navigation,
            text="Undo",
            command=self.undo_move,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Button(
            navigation,
            text="Play move",
            command=self.play_selected_move,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Label(
            left,
            textvariable=self.position_var,
            font=("Arial", 11, "bold"),
            wraplength=420,
        ).pack(pady=5)

        # Right panel: only immediate next moves.

        right = ttk.Frame(main, padding=5)
        main.add(right, weight=2)

        ttk.Label(
            right,
            text="Possible next moves",
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W, pady=5)

        columns = (
            "games",
            "wins",
            "draws",
            "losses",
            "unknown",
            "score",
            "expected_score",
            "forecast_games",
            "forecast_points",
        )

        self.tree = ttk.Treeview(
            right,
            columns=columns,
            show="tree headings",
            height=17,
        )

        self.tree.heading("#0", text="Move")
        self.tree.heading("games", text="Games")
        self.tree.heading("wins", text="Wins")
        self.tree.heading("draws", text="Draws")
        self.tree.heading("losses", text="Losses")
        self.tree.heading("unknown", text="?")
        self.tree.heading("score", text="Score")
        self.tree.heading("expected_score", text="Expected %")
        self.tree.heading(
            "forecast_games",
            text="Predicted games",
        )
        self.tree.heading(
            "forecast_points",
            text="Predicted points",
        )

        self.tree.column("#0", width=100)
        self.tree.column("games", width=65, anchor=tk.CENTER)
        self.tree.column("wins", width=55, anchor=tk.CENTER)
        self.tree.column("draws", width=55, anchor=tk.CENTER)
        self.tree.column("losses", width=55, anchor=tk.CENTER)
        self.tree.column("unknown", width=55, anchor=tk.CENTER)
        self.tree.column("score", width=70, anchor=tk.CENTER)
        self.tree.column("expected_score", width=100, anchor=tk.CENTER)
        self.tree.column("forecast_games", width=100, anchor=tk.CENTER)
        self.tree.column("forecast_points", width=105, anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(
            right,
            orient=tk.VERTICAL,
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(
            side=tk.LEFT,
            fill=tk.BOTH,
            expand=True,
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind(
            "<<TreeviewSelect>>",
            self.on_tree_select,
        )
        self.tree.bind(
            "<Double-1>",
            lambda event: self.play_selected_move(),
        )

        # Selected move time series.

        details_frame = ttk.LabelFrame(
            right,
            text="Selected move — yearly time series and forecast",
            padding=8,
        )
        details_frame.pack(
            fill=tk.BOTH,
            expand=True,
            pady=10,
        )

        ttk.Label(
            details_frame,
            textvariable=self.details_var,
            justify=tk.LEFT,
            font=("Consolas", 10),
        ).pack(anchor=tk.W)

        ttk.Label(
            self.master,
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor=tk.W,
        ).pack(fill=tk.X, side=tk.BOTTOM)

    # ---------------------------------------------------------
    # Player search
    # ---------------------------------------------------------

    def on_player_keyrelease(self, event: tk.Event) -> None:
        if event.keysym in (
            "Up",
            "Down",
            "Left",
            "Right",
            "Return",
            "Escape",
            "Tab",
        ):
            return

        self.search_generation += 1
        generation = self.search_generation

        if self.search_after_id:
            self.master.after_cancel(self.search_after_id)
            self.search_after_id = None

        name = self.player_var.get().strip()

        if len(name) < 4:
            self.player_entry["values"] = []
            return

        self.search_after_id = self.master.after(
            400,
            lambda: self.search_players(name, generation),
        )

    def search_players(self, name: str, generation: int) -> None:
        def worker() -> None:
            try:
                url = API["search_player"] + urllib.parse.quote(name)

                response = self.session.get(
                    url,
                    timeout=20,
                )
                response.raise_for_status()

                players = response.json()

                if not isinstance(players, list):
                    players = []

                def update() -> None:
                    self.update_player_suggestions(players, name, generation)

                self.master.after(0, update)

            except Exception as error:
                message = str(error)

                self.master.after(
                    0,
                    self.on_games_error,
                    message,
                )

        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    def update_player_suggestions(
        self,
        players: list[dict[str, Any]],
        requested_name: str,
        generation: int,
    ) -> None:
        if generation != self.search_generation:
            return

        if self.player_var.get().strip() != requested_name:
            return

        self.player_entry["values"] = players

        if players:
            self.player_entry.event_generate("<Down>")

    # ---------------------------------------------------------
    # Fetch games
    # ---------------------------------------------------------

    def submit_player(self) -> None:
        name = self.player_var.get().strip()
        color = self.color_var.get()

        if not name:
            messagebox.showwarning(
                "Player required",
                "Enter a player name.",
            )
            return

        self.submit_button.config(state=tk.DISABLED)
        self.status_var.set("Downloading games...")

        def worker() -> None:
            try:
                url = (
                    API["search_games"]
                    + urllib.parse.quote(name)
                    + "/"
                    + urllib.parse.quote(color)
                )

                response = self.session.get(
                    url,
                    timeout=120,
                )
                response.raise_for_status()

                games = response.json()

                if not isinstance(games, list):
                    raise ValueError("Unexpected API response: expected a list.")

                self.master.after(
                    0,
                    lambda: self.on_games_loaded(games, name, color),
                )

            except Exception:

                def update() -> None:
                    self.on_games_loaded(games, name, color)

                self.master.after(0, update)

        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    def on_games_error(self, error: str) -> None:
        self.submit_button.config(state=tk.NORMAL)
        self.status_var.set("Failed to load games.")

        messagebox.showerror(
            "API error",
            str(error),
        )

    def on_games_loaded(
        self,
        games: list[Game],
        name: str,
        color: str,
    ) -> None:
        self.submit_button.config(state=tk.NORMAL)

        self.games = games

        if not games:
            self.status_var.set("No games found.")
            messagebox.showinfo(
                "No games",
                "No games found for this player and color.",
            )
            return

        self.build_opening_tree(games, color)

        self.board = chess.Board()
        self.current_path = []
        self.current_node = self.root_node
        self.selected_square = None

        self.draw_board()
        self.update_position_label()
        self.populate_tree()

        self.status_var.set(
            f"{name} ({color}): {len(games)} games loaded. "
            f"Forecast year: {self.next_year or 'unknown'}."
        )

    # ---------------------------------------------------------
    # Result and year handling
    # ---------------------------------------------------------

    @staticmethod
    def get_game_year(game: Game) -> int | None:
        value = game.get("Year", game.get("year"))

        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def get_player_result(result: int | str, color: str) -> str:
        outcome = parse_result(result)

        if outcome == "unknown":
            return "unknown"

        if outcome == "draw":
            return "draw"

        if outcome == color:
            return "win"

        return "loss"

    # ---------------------------------------------------------
    # Opening tree and yearly time series
    # ---------------------------------------------------------

    def update_node_stats(
        self,
        node: OpeningNode,
        result: str,
        year: int | None,
    ) -> None:
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

    def build_opening_tree(
        self,
        games: list[Game],
        color: str,
    ) -> None:
        self.root_node = create_node()
        self.current_node = self.root_node

        years = [
            year for game in games if (year := self.get_game_year(game)) is not None
        ]

        self.first_year = min(years) if years else None
        self.latest_year = max(years) if years else None
        self.next_year = self.latest_year + 1 if self.latest_year is not None else None

        for game in games:
            moves = game.get("moves", [])

            if not isinstance(moves, list) or not moves:
                continue

            result_value = game.get(
                "Result",
                game.get("result"),
            )

            result = self.get_player_result(
                result_value,
                color,
            )

            year = self.get_game_year(game)

            board = chess.Board()
            node = self.root_node

            self.update_node_stats(node, result, year)

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

                self.update_node_stats(
                    node,
                    result,
                    year,
                )

    # ---------------------------------------------------------
    # Forecasting
    # ---------------------------------------------------------

    def get_time_series(
        self,
        node: OpeningNode,
    ) -> list[tuple[int, int, float]]:

        if self.first_year is None or self.latest_year is None:
            return []

        result: list[tuple[int, int, float]] = []

        for year in range(self.first_year, self.latest_year + 1):
            games, points = node["yearly"].get(year, [0, 0.0])

            result.append((year, int(games), float(points)))

        return result

    def forecast_move(self, node: OpeningNode) -> tuple[float, float]:
        """
        Predict next year's game count and points using the trained Keras model.
        """
        series = self.get_time_series(node)

        if not series:
            return 0.0, 0.0

        # predict_next_year expects [(year, [games, points]), ...].
        yearly_data = [(year, [games, points]) for year, games, points in series]

        predicted_games, predicted_points = predict_next_year(
            self.model,
            yearly_data,
        )

        predicted_games = max(0.0, float(predicted_games))
        predicted_points = max(0.0, float(predicted_points))
        predicted_points = min(predicted_points, predicted_games)

        return predicted_games, predicted_points

    # ---------------------------------------------------------
    # Display only immediate next moves
    # ---------------------------------------------------------

    @staticmethod
    def get_stats_text(
        node: OpeningNode, forecast_games: float, forecast_points: float
    ) -> tuple[int, int, int, int, int, str, str, str, str]:
        games = node["games"]
        wins = node["wins"]
        draws = node["draws"]
        losses = node["losses"]
        unknown = node["unknown"]

        decided = wins + draws + losses

        score = (wins + 0.5 * draws) / decided * 100 if decided else 0.0

        return (
            games,
            wins,
            draws,
            losses,
            unknown,
            f"{score:.1f}%",
            f"{(forecast_points / forecast_games * 100):.1f}"
            if forecast_games
            else "0.0",
            f"{forecast_games:.1f}",
            f"{forecast_points:.1f}",
        )

    def populate_tree(self) -> None:
        """
        Display only the direct children of the current position.
        No recursive tree expansion.
        """
        self.tree.delete(*self.tree.get_children())
        self.tree_moves.clear()

        children = sorted(
            self.current_node["children"].values(),
            key=lambda child: child["games"],
            reverse=True,
        )

        for child in children:
            forecast_games, forecast_points = self.forecast_move(child)

            item_id = self.tree.insert(
                "",
                tk.END,
                text=child["san"],
                values=self.get_stats_text(
                    child,
                    forecast_games,
                    forecast_points,
                ),
            )

            self.tree_moves[item_id] = child["uci"]

        self.tree.heading(
            "forecast_games",
            text="Predicted games",
        )
        self.tree.heading(
            "forecast_points",
            text="Predicted points",
        )

        self.details_var.set(
            "Select a possible next move to see its yearly time series."
        )

    def on_tree_select(self, event: tk.Event) -> None:
        selection = self.tree.selection()

        if not selection:
            return

        item_id = selection[0]
        uci = self.tree_moves.get(item_id)

        if uci is None:
            return

        child = self.current_node["children"][uci]
        self.show_move_details(child)

    def show_move_details(self, node: OpeningNode) -> None:
        series = self.get_time_series(node)
        predicted_games, predicted_points = self.forecast_move(node)

        lines = [
            f"Move: {node['san']} ({node['uci']})",
            "",
            "Year       Games       Points",
            "-----------------------------",
        ]

        if series:
            for year, games, points in series:
                lines.append(f"{year:<10}{games:<12}{points:.1f}")
        else:
            lines.append("No usable year information.")

        lines.extend(
            [
                "",
                f"Forecast for {self.next_year or 'next year'}:",
                f"Games:  {predicted_games:.1f}",
                f"Points: {predicted_points:.1f}",
            ]
        )

        if predicted_games > 0:
            expected_score = predicted_points / predicted_games * 100
            lines.append(f"Expected score: {expected_score:.1f}%")

        lines.extend(
            [
                "",
                "Points: win = 1, draw = 0.5, loss = 0.",
            ]
        )

        self.details_var.set("\n".join(lines))

    # ---------------------------------------------------------
    # Board navigation
    # ---------------------------------------------------------

    def set_position(self, path: list[str]) -> None:
        self.board = chess.Board()
        self.current_path = []

        for uci in path:
            move = chess.Move.from_uci(uci)

            if move not in self.board.legal_moves:
                break

            self.board.push(move)
            self.current_path.append(uci)

        self.selected_square = None
        self.current_node = self.get_node(self.current_path)

        self.draw_board()
        self.update_position_label()
        self.populate_tree()

    def get_node(self, path: list[str]) -> OpeningNode:
        node = self.root_node

        for uci in path:
            child = node["children"].get(uci)

            if child is None:
                return self.root_node

            node = child

        return node

    def update_position_label(self) -> None:
        if not self.current_path:
            self.position_var.set("Starting position")
            return

        moves = [chess.Move.from_uci(uci) for uci in self.current_path]

        self.position_var.set(" ".join(chess.Board().variation_san(moves)))

    def play_selected_move(self) -> None:
        selection = self.tree.selection()

        if not selection:
            return

        uci = self.tree_moves.get(selection[0])

        if uci is None:
            return

        self.set_position(self.current_path + [uci])

    def go_start(self) -> None:
        self.set_position([])

    def undo_move(self) -> None:
        if self.current_path:
            self.set_position(self.current_path[:-1])

    # ---------------------------------------------------------
    # Chessboard drawing and interaction
    # ---------------------------------------------------------

    def draw_board(self) -> None:
        self.canvas.delete("all")

        for row in range(8):
            for col in range(8):
                square = chess.square(col, 7 - row)

                color = LIGHT_SQUARE if (row + col) % 2 == 0 else DARK_SQUARE

                if square == self.selected_square:
                    color = SELECTED_SQUARE

                self.canvas.create_rectangle(
                    col * SQUARE_SIZE,
                    row * SQUARE_SIZE,
                    (col + 1) * SQUARE_SIZE,
                    (row + 1) * SQUARE_SIZE,
                    fill=color,
                    outline=color,
                )

                piece = self.board.piece_at(square)

                if piece:
                    self.canvas.create_text(
                        col * SQUARE_SIZE + SQUARE_SIZE // 2,
                        row * SQUARE_SIZE + SQUARE_SIZE // 2,
                        text=CHESS_PIECES[piece.symbol()],
                        font=("DejaVu Sans", 36),
                    )

    def on_board_click(self, event: tk.Event) -> None:
        col = event.x // SQUARE_SIZE
        row = event.y // SQUARE_SIZE

        if not (0 <= col < 8 and 0 <= row < 8):
            return

        square = chess.square(col, 7 - row)

        if self.selected_square is None:
            piece = self.board.piece_at(square)

            if piece and piece.color == self.board.turn:
                self.selected_square = square

            self.draw_board()
            return

        move = chess.Move(
            self.selected_square,
            square,
        )

        piece = self.board.piece_at(self.selected_square)

        # Automatically promote to queen.
        if (
            piece
            and piece.piece_type == chess.PAWN
            and chess.square_rank(square) in (0, 7)
        ):
            move.promotion = chess.QUEEN

        self.selected_square = None

        if move not in self.board.legal_moves:
            self.draw_board()
            return

        uci = move.uci()

        # Only allow moves present in the downloaded games.
        if uci not in self.current_node["children"]:
            self.draw_board()
            return

        self.set_position(self.current_path + [uci])


def main() -> None:
    parser = argparse.ArgumentParser(description="Chess Opening Explorer")
    parser.add_argument(
        "--model",
        required=True,
        help="Path to the trained Keras model (.keras)",
    )

    args = parser.parse_args()

    try:
        model = keras.models.load_model(args.model)
    except Exception as error:
        print(f"Could not load model: {error}")
        return

    root = tk.Tk()
    ChessOpeningApp(root, model)
    root.mainloop()


if __name__ == "__main__":
    main()
