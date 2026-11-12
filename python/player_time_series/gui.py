
import threading
import urllib.parse
import tkinter as tk
from tkinter import ttk, messagebox

import requests
import chess

from collections import defaultdict


API = {
    "search_player": (
        "https://api.bazaszachowa.smallhost.pl/players/"
    ),
    "search_games": (
        "https://api.bazaszachowa.smallhost.pl/games/opening/"
    ),
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


def decode_date_code(date_code):
    """
    Database date encoding:

        0-364   standard zero-based day of year
        365     February 29
        366-377 month known, day unknown
        378     month and day unknown

    Returns (month, day), with None for unknown values.
    """
    date_code = int(date_code)

    if 0 <= date_code <= 364:
        import datetime

        date = datetime.date(2001, 1, 1) + datetime.timedelta(
            days=date_code
        )
        return date.month, date.day

    if date_code == 365:
        return 2, 29

    if 366 <= date_code <= 377:
        return date_code - 365, None

    return None, None


def parse_result(result):
    """
    Result encoding:

        0   Black wins
        1   Draw
        2   White wins
        255 Unknown

    Also accepts PGN result strings.
    """
    if result in (0, "0", "0-1"):
        return "black"

    if result in (1, "1", "1/2-1/2"):
        return "draw"

    if result in (2, "2", "1-0"):
        return "white"

    return "unknown"


def make_move(move_data):
    """
    API move format:
        {"from": "e2", "to": "e4"}
        {"from": "e7", "to": "e8", "promotion": "q"}

    Also accepts UCI strings.
    """
    if isinstance(move_data, str):
        return chess.Move.from_uci(move_data)

    uci = move_data["from"] + move_data["to"]

    promotion = move_data.get("promotion")
    if promotion:
        uci += promotion.lower()

    return chess.Move.from_uci(uci)


def create_node():
    return {
        "children": {},
        "games": 0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "unknown": 0,
        "year_counts": defaultdict(int),
        "san": None,
        "uci": None,
    }


class ChessOpeningApp:
    def __init__(self, master):
        self.master = master
        self.master.title("Chess Opening Explorer")
        self.master.geometry("1250x800")

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Bazaszachowa-Opening-Explorer/1.0"
        })

        self.games = []
        self.root_node = create_node()
        self.current_node = self.root_node

        self.board = chess.Board()
        self.selected_square = None
        self.current_path = []

        self.tree_paths = {}
        self.search_after_id = None

        self.player_var = tk.StringVar()
        self.color_var = tk.StringVar(value="white")
        self.status_var = tk.StringVar(value="Search for a player.")

        self.create_widgets()
        self.draw_board()

    # ---------------------------------------------------------
    # GUI
    # ---------------------------------------------------------

    def create_widgets(self):
        top = ttk.Frame(self.master, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(
            top,
            text="Player:",
        ).pack(side=tk.LEFT, padx=5)

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

        ttk.Label(
            top,
            text="Color:",
        ).pack(side=tk.LEFT, padx=5)

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
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Left side: chessboard and navigation.

        left = ttk.Frame(main, padding=5)
        main.add(left, weight=1)

        self.canvas = tk.Canvas(
            left,
            width=BOARD_SIZE,
            height=BOARD_SIZE,
            highlightthickness=0,
        )
        self.canvas.pack(pady=5)
        self.canvas.bind("<Button-1>", self.on_board_click)

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
            text="Reset board",
            command=self.reset_board,
        ).pack(side=tk.LEFT, padx=3)

        self.position_label = ttk.Label(
            left,
            text="Starting position",
            font=("Arial", 11, "bold"),
        )
        self.position_label.pack(pady=5)

        # Right side: opening tree and statistics.

        right = ttk.Frame(main, padding=5)
        main.add(right, weight=2)

        ttk.Label(
            right,
            text="Opening tree",
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W, pady=5)

        columns = (
            "games",
            "wins",
            "draws",
            "losses",
            "unknown",
            "score",
        )

        self.tree = ttk.Treeview(
            right,
            columns=columns,
            show="tree headings",
            height=25,
        )

        self.tree.heading("#0", text="Move")
        self.tree.heading("games", text="Games")
        self.tree.heading("wins", text="Wins")
        self.tree.heading("draws", text="Draws")
        self.tree.heading("losses", text="Losses")
        self.tree.heading("unknown", text="?")
        self.tree.heading("score", text="Score")

        self.tree.column("#0", width=230)
        self.tree.column("games", width=65, anchor=tk.CENTER)
        self.tree.column("wins", width=55, anchor=tk.CENTER)
        self.tree.column("draws", width=55, anchor=tk.CENTER)
        self.tree.column("losses", width=55, anchor=tk.CENTER)
        self.tree.column("unknown", width=55, anchor=tk.CENTER)
        self.tree.column("score", width=70, anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(
            right,
            orient=tk.VERTICAL,
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind(
            "<<TreeviewSelect>>",
            self.on_tree_select,
        )

        # Bottom status.

        ttk.Label(
            self.master,
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor=tk.W,
        ).pack(fill=tk.X, side=tk.BOTTOM)

    # ---------------------------------------------------------
    # Player search
    # ---------------------------------------------------------

    def on_player_keyrelease(self, event):
        if event.keysym in (
            "Up", "Down", "Left", "Right",
            "Return", "Escape", "Tab",
        ):
            return

        if self.search_after_id:
            self.master.after_cancel(self.search_after_id)

        name = self.player_var.get().strip()

        if len(name) < 4:
            self.player_entry["values"] = []
            return

        self.search_after_id = self.master.after(
            400,
            lambda: self.search_players(name),
        )

    def search_players(self, name):
        def worker():
            try:
                url = API["search_player"] + urllib.parse.quote(name)
                response = self.session.get(url, timeout=20)
                response.raise_for_status()

                data = response.json()

                if not isinstance(data, list):
                    data = []

                self.master.after(
                    0,
                    lambda: self.update_player_suggestions(data),
                )

            except Exception as error:
                error_message = str(error)

                self.master.after(
                    0,
                    lambda msg=error_message: self.status_var.set(
                        f"Player search failed: {msg}"
                    ),
                )
        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    def update_player_suggestions(self, players):
        self.player_entry["values"] = players
        self.player_entry.event_generate("<Down>")

    # ---------------------------------------------------------
    # Fetch games
    # ---------------------------------------------------------

    def submit_player(self):
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

        def worker():
            try:
                url = (
                    API["search_games"]
                    + urllib.parse.quote(name)
                    + "/"
                    + urllib.parse.quote(color)
                )

                response = self.session.get(url, timeout=120)
                response.raise_for_status()

                games = response.json()

                if not isinstance(games, list):
                    raise ValueError("Unexpected API response.")

                self.master.after(
                    0,
                    lambda: self.on_games_loaded(games, name, color),
                )

            except Exception as error:
                self.master.after(
                    0,
                    lambda: self.on_games_error(error),
                )

        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    def on_games_error(self, error):
        self.submit_button.config(state=tk.NORMAL)

        self.status_var.set("Failed to load games.")

        messagebox.showerror(
            "API error",
            str(error),
        )

    def on_games_loaded(self, games, name, color):
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

        self.draw_board()
        self.populate_tree()

        self.status_var.set(
            f"{name} ({color}): {len(games)} games loaded."
        )

    # ---------------------------------------------------------
    # Date and result handling
    # ---------------------------------------------------------

    @staticmethod
    def get_game_year(game):
        return game.get("Year", game.get("year"))

    @staticmethod
    def get_game_date(game):
        """
        Accept either a DateCode field or separate Month/Day fields.
        """
        date_code = game.get(
            "DateCode",
            game.get("date_code"),
        )

        if date_code is not None:
            return decode_date_code(date_code)

        month = game.get("Month", game.get("month"))
        day = game.get("Day", game.get("day"))

        return month, day

    @staticmethod
    def get_player_result(result, color):
        outcome = parse_result(result)

        if outcome == "unknown":
            return "unknown"

        if outcome == "draw":
            return "draw"

        if outcome == color:
            return "win"

        return "loss"

    # ---------------------------------------------------------
    # Opening tree
    # ---------------------------------------------------------

    def update_node_stats(self, node, result, year):
        node["games"] += 1

        if result == "win":
            node["wins"] += 1
        elif result == "draw":
            node["draws"] += 1
        elif result == "loss":
            node["losses"] += 1
        else:
            node["unknown"] += 1

        if year is not None:
            node["year_counts"][int(year)] += 1

    def build_opening_tree(self, games, color):
        self.root_node = create_node()
        self.current_node = self.root_node

        for game in games:
            try:
                moves = game.get("moves", [])

                if not moves:
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
                    move = make_move(move_data)

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

            except (ValueError, KeyError, TypeError):
                continue

    # ---------------------------------------------------------
    # Populate tree widget
    # ---------------------------------------------------------

    def populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.tree_paths.clear()

        root_stats = self.get_stats_text(self.root_node)

        root_id = self.tree.insert(
            "",
            tk.END,
            text="Starting position",
            values=root_stats,
            open=True,
        )

        self.tree_paths[root_id] = []

        self.insert_children(
            root_id,
            self.root_node,
            [],
        )

        self.tree.selection_set(root_id)

    def insert_children(self, parent_id, node, path):
        children = sorted(
            node["children"].values(),
            key=lambda child: child["games"],
            reverse=True,
        )

        for child in children:
            child_path = path + [child["uci"]]

            item_id = self.tree.insert(
                parent_id,
                tk.END,
                text=child["san"],
                values=self.get_stats_text(child),
                open=False,
            )

            self.tree_paths[item_id] = child_path

            self.insert_children(
                item_id,
                child,
                child_path,
            )

    @staticmethod
    def get_stats_text(node):
        games = node["games"]
        wins = node["wins"]
        draws = node["draws"]
        losses = node["losses"]
        unknown = node["unknown"]

        decided = wins + draws + losses

        score = (
            (wins + draws * 0.5) / decided * 100
            if decided else 0.0
        )

        return (
            games,
            wins,
            draws,
            losses,
            unknown,
            f"{score:.1f}%",
        )

    # ---------------------------------------------------------
    # Tree navigation
    # ---------------------------------------------------------

    def on_tree_select(self, event):
        selection = self.tree.selection()

        if not selection:
            return

        item_id = selection[0]
        path = self.tree_paths.get(item_id, [])

        self.set_position(path)

    def set_position(self, path):
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

    def get_node(self, path):
        node = self.root_node

        for uci in path:
            node = node["children"].get(uci)

            if node is None:
                return self.root_node

        return node

    def update_position_label(self):
        if not self.current_path:
            self.position_label.config(
                text="Starting position"
            )
            return

        self.position_label.config(
            text=" ".join(
                chess.Board().variation_san(
                    [
                        chess.Move.from_uci(uci)
                        for uci in self.current_path
                    ]
                )
            )
        )

    def go_start(self):
        self.set_position([])

        root_id = self.tree.get_children()

        if root_id:
            self.tree.selection_set(root_id[0])

    def undo_move(self):
        if not self.current_path:
            return

        self.set_position(self.current_path[:-1])

        self.select_tree_path(self.current_path)

    def reset_board(self):
        self.go_start()

    def select_tree_path(self, path):
        for item_id, item_path in self.tree_paths.items():
            if item_path == path:
                self.tree.selection_set(item_id)
                self.tree.see(item_id)
                break

    # ---------------------------------------------------------
    # Chessboard
    # ---------------------------------------------------------

    def draw_board(self):
        self.canvas.delete("all")

        for row in range(8):
            for col in range(8):
                square = chess.square(col, 7 - row)

                color = (
                    LIGHT_SQUARE
                    if (row + col) % 2 == 0
                    else DARK_SQUARE
                )

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

    def on_board_click(self, event):
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

        # Handle promotion by choosing a queen.
        if (
            self.board.piece_at(self.selected_square)
            and self.board.piece_at(self.selected_square).piece_type
                == chess.PAWN
            and chess.square_rank(square) in (0, 7)
        ):
            move.promotion = chess.QUEEN

        self.selected_square = None

        if move not in self.board.legal_moves:
            self.draw_board()
            return

        uci = move.uci()

        if uci not in self.current_node["children"]:
            self.draw_board()
            return

        new_path = self.current_path + [uci]

        self.set_position(new_path)
        self.select_tree_path(new_path)


def main():
    root = tk.Tk()
    ChessOpeningApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
