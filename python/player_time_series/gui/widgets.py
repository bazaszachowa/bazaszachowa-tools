"""Tkinter widget construction and chessboard rendering."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

import chess

from .opening_types import (
    BOARD_SIZE,
    CHESS_PIECES,
    DARK_SQUARE,
    LIGHT_SQUARE,
    SELECTED_SQUARE,
    SQUARE_SIZE,
)

if TYPE_CHECKING:
    from .app import ChessOpeningApp


def create_widgets(app: "ChessOpeningApp") -> None:
    top = ttk.Frame(app.master, padding=10)
    top.pack(fill=tk.X)

    ttk.Label(top, text="Player:").pack(side=tk.LEFT, padx=5)

    app.player_entry = ttk.Combobox(
        top,
        textvariable=app.player_var,
        width=35,
    )
    app.player_entry.pack(side=tk.LEFT, padx=5)
    app.player_entry.bind(
        "<KeyRelease>",
        app.on_player_keyrelease,
    )

    ttk.Label(top, text="Color:").pack(side=tk.LEFT, padx=5)

    ttk.Radiobutton(
        top,
        text="White",
        variable=app.color_var,
        value="white",
    ).pack(side=tk.LEFT)

    ttk.Radiobutton(
        top,
        text="Black",
        variable=app.color_var,
        value="black",
    ).pack(side=tk.LEFT)

    app.submit_button = ttk.Button(
        top,
        text="Search games",
        command=app.submit_player,
    )
    app.submit_button.pack(side=tk.LEFT, padx=10)

    main = ttk.PanedWindow(
        app.master,
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

    app.canvas = tk.Canvas(
        left,
        width=BOARD_SIZE,
        height=BOARD_SIZE,
        highlightthickness=0,
    )
    app.canvas.pack(pady=5)
    app.canvas.bind(
        "<Button-1>",
        app.on_board_click,
    )

    navigation = ttk.Frame(left)
    navigation.pack(fill=tk.X, pady=5)

    ttk.Button(
        navigation,
        text="Start",
        command=app.go_start,
    ).pack(side=tk.LEFT, padx=3)

    ttk.Button(
        navigation,
        text="Undo",
        command=app.undo_move,
    ).pack(side=tk.LEFT, padx=3)

    ttk.Button(
        navigation,
        text="Play move",
        command=app.play_selected_move,
    ).pack(side=tk.LEFT, padx=3)

    ttk.Label(
        left,
        textvariable=app.position_var,
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

    app.tree = ttk.Treeview(
        right,
        columns=columns,
        show="tree headings",
        height=17,
    )

    app.tree.heading("#0", text="Move")
    app.tree.heading("games", text="Games")
    app.tree.heading("wins", text="Wins")
    app.tree.heading("draws", text="Draws")
    app.tree.heading("losses", text="Losses")
    app.tree.heading("unknown", text="?")
    app.tree.heading("score", text="Score")
    app.tree.heading("expected_score", text="Expected %")
    app.tree.heading(
        "forecast_games",
        text="Predicted games",
    )
    app.tree.heading(
        "forecast_points",
        text="Predicted points",
    )

    app.tree.column("#0", width=100)
    app.tree.column("games", width=65, anchor=tk.CENTER)
    app.tree.column("wins", width=55, anchor=tk.CENTER)
    app.tree.column("draws", width=55, anchor=tk.CENTER)
    app.tree.column("losses", width=55, anchor=tk.CENTER)
    app.tree.column("unknown", width=55, anchor=tk.CENTER)
    app.tree.column("score", width=70, anchor=tk.CENTER)
    app.tree.column("expected_score", width=100, anchor=tk.CENTER)
    app.tree.column("forecast_games", width=100, anchor=tk.CENTER)
    app.tree.column("forecast_points", width=105, anchor=tk.CENTER)

    scrollbar = ttk.Scrollbar(
        right,
        orient=tk.VERTICAL,
        command=app.tree.yview,
    )
    app.tree.configure(yscrollcommand=scrollbar.set)

    app.tree.pack(
        side=tk.LEFT,
        fill=tk.BOTH,
        expand=True,
    )
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    app.tree.bind(
        "<<TreeviewSelect>>",
        app.on_tree_select,
    )
    app.tree.bind(
        "<Double-1>",
        lambda event: app.play_selected_move(),
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
        textvariable=app.details_var,
        justify=tk.LEFT,
        font=("Consolas", 10),
    ).pack(anchor=tk.W)

    ttk.Label(
        app.master,
        textvariable=app.status_var,
        relief=tk.SUNKEN,
        anchor=tk.W,
    ).pack(fill=tk.X, side=tk.BOTTOM)


def draw_board(app: "ChessOpeningApp") -> None:
    app.canvas.delete("all")

    for row in range(8):
        for col in range(8):
            square = chess.square(col, 7 - row)

            color = LIGHT_SQUARE if (row + col) % 2 == 0 else DARK_SQUARE

            if square == app.selected_square:
                color = SELECTED_SQUARE

            app.canvas.create_rectangle(
                col * SQUARE_SIZE,
                row * SQUARE_SIZE,
                (col + 1) * SQUARE_SIZE,
                (row + 1) * SQUARE_SIZE,
                fill=color,
                outline=color,
            )

            piece = app.board.piece_at(square)

            if piece:
                app.canvas.create_text(
                    col * SQUARE_SIZE + SQUARE_SIZE // 2,
                    row * SQUARE_SIZE + SQUARE_SIZE // 2,
                    text=CHESS_PIECES[piece.symbol()],
                    font=("DejaVu Sans", 36),
                )
