import argparse
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import chess
import keras

from ..model import predict_next_year
from .opening_api import ChessApiClient
from .opening_tree import build_opening_tree, get_stats_text, get_time_series
from .opening_types import (
    SQUARE_SIZE,
    Game,
    OpeningNode,
    create_node,
)
from .widgets import create_widgets, draw_board, update_columns_visibility


class ChessOpeningApp:
    def __init__(
        self,
        master: tk.Tk,
        models: dict[str, keras.Model],
    ) -> None:
        self.master = master
        self.models = models

        self.mode_var = tk.StringVar(
            value="absolute" if "absolute" in models else next(iter(models))
        )

        self.master.title("Chess Opening Explorer")
        self.master.geometry("1450x850")
        self.master.minsize(1100, 700)

        self.player_entry: ttk.Combobox
        self.submit_button: ttk.Button
        self.canvas: tk.Canvas
        self.tree: ttk.Treeview
        self.mode_selector: ttk.Combobox

        self.api = ChessApiClient()

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

        create_widgets(self)
        draw_board(self)
        self.populate_tree()

    # ---------------------------------------------------------
    # GUI
    # ---------------------------------------------------------

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
                players = self.api.search_players(name)
            except Exception as error:
                message = str(error)
                self.master.after(0, self.on_player_search_error, message)
                return

            self.master.after(
                0,
                self.update_player_suggestions,
                players,
                name,
                generation,
            )

        threading.Thread(target=worker, daemon=True).start()

    def on_player_search_error(self, error: str) -> None:
        self.status_var.set(f"Player search failed: {error}")

    def update_player_suggestions(
        self,
        players: list[str],
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
                games = self.api.fetch_games(name, color)
            except Exception as error:
                message = str(error)
                self.master.after(0, self.on_games_error, message)
                return

            self.master.after(0, self.on_games_loaded, games, name, color)

        threading.Thread(target=worker, daemon=True).start()

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

        tree_data = build_opening_tree(games, color)
        self.root_node = tree_data.root
        self.first_year = tree_data.first_year
        self.latest_year = tree_data.latest_year
        self.next_year = tree_data.next_year

        self.board = chess.Board()
        self.current_path = []
        self.current_node = self.root_node
        self.selected_square = None

        draw_board(self)
        self.update_position_label()
        self.populate_tree()

        self.status_var.set(
            f"{name} ({color}): {len(games)} games loaded. "
            f"Forecast year: {self.next_year or 'unknown'}."
        )

    # ---------------------------------------------------------
    # Result and year handling
    # ---------------------------------------------------------

    # ---------------------------------------------------------
    # Opening tree and yearly time series
    # ---------------------------------------------------------

    # ---------------------------------------------------------
    # Forecasting
    # ---------------------------------------------------------

    def forecast_move(self, node: OpeningNode) -> tuple[float, float]:
        """Predict next year's position share and score, or absolute statistics."""
        if self.first_year is None or self.latest_year is None:
            return 0.0, 0.0

        mode = self.mode_var.get()

        position_series = get_time_series(
            node,
            self.first_year,
            self.latest_year,
        )

        if not position_series:
            return 0.0, 0.0

        model = self.models[mode]

        if mode == "absolute":
            yearly_data = [
                (year, [float(games), float(points)])
                for year, games, points in position_series
            ]

        elif mode == "percentage":
            total_series = get_time_series(
                self.root_node,
                self.first_year,
                self.latest_year,
            )

            total_games_by_year = {year: games for year, games, _ in total_series}

            position_by_year = {
                year: (games, points) for year, games, points in position_series
            }

            yearly_data = []

            # Use every calendar year, including years without games.
            for year, total_games in total_games_by_year.items():
                games, points = position_by_year.get(
                    year,
                    (0, 0.0),
                )

                game_share = games / total_games if total_games > 0 else 0.0

                point_score = points / games if games > 0 else 0.0

                has_games = 1.0 if total_games > 0 else 0.0

                yearly_data.append(
                    (
                        year,
                        [
                            float(game_share),
                            float(point_score),
                            has_games,
                        ],
                    )
                )

        else:
            raise ValueError(f"Unknown prediction mode: {mode}")

        predicted_first, predicted_second = predict_next_year(
            model,
            yearly_data,
            mode=mode,
        )

        predicted_first = max(0.0, float(predicted_first))
        predicted_second = max(0.0, float(predicted_second))

        if mode == "percentage":
            predicted_first = min(predicted_first, 1.0) * 100.0
            predicted_second = min(predicted_second, 1.0) * 100.0
        else:
            predicted_second = min(predicted_second, predicted_first)

        return predicted_first, predicted_second

    # ---------------------------------------------------------
    # Display only immediate next moves
    # ---------------------------------------------------------

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
                values=get_stats_text(
                    child,
                    forecast_games,
                    forecast_points,
                ),
            )

            self.tree_moves[item_id] = child["uci"]

        if self.mode_var.get() == "percentage":
            self.tree.heading("forecast_games", text="Position games (%)")
            self.tree.heading("forecast_points", text="Position score (%)")
        else:
            self.tree.heading("forecast_games", text="Predicted games")
            self.tree.heading("forecast_points", text="Predicted points")

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
        series = get_time_series(node, self.first_year, self.latest_year)
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

        if self.mode_var.get() == "percentage":
            lines.extend(
                [
                    "",
                    f"Forecast for {self.next_year or 'next year'}:",
                    f"Position game share: {predicted_games:.1f}%",
                    f"Position score: {predicted_points:.1f}%",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    f"Forecast for {self.next_year or 'next year'}:",
                    f"Games:  {predicted_games:.1f}",
                    f"Points: {predicted_points:.1f}",
                ]
            )

        if self.mode_var.get() == "absolute" and predicted_games > 0:
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

        draw_board(self)
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

            draw_board(self)
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
            draw_board(self)
            return

        uci = move.uci()

        # Only allow moves present in the downloaded games.
        if uci not in self.current_node["children"]:
            draw_board(self)
            return

        self.set_position(self.current_path + [uci])

    def on_mode_changed(self, event: tk.Event | None = None) -> None:
        update_columns_visibility(self)
        self.populate_tree()


def main() -> None:
    parser = argparse.ArgumentParser(description="Chess Opening Explorer")
    parser.add_argument(
        "--absolute-model",
        help="Path to the absolute model (.keras)",
    )
    parser.add_argument(
        "--percentage-model",
        help="Path to the percentage model (.keras)",
    )

    args = parser.parse_args()

    model_paths = {
        "absolute": args.absolute_model,
        "percentage": args.percentage_model,
    }

    models: dict[str, keras.Model] = {}

    try:
        for mode, path in model_paths.items():
            if path:
                models[mode] = keras.models.load_model(path)

        if not models:
            parser.error("Provide at least one model path.")
    except Exception as error:
        print(f"Could not load model: {error}")
        return

    root = tk.Tk()
    ChessOpeningApp(root, models)
    root.mainloop()


if __name__ == "__main__":
    main()
