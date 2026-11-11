
#include "GameProcessor.hpp"

#include <algorithm>
#include <array>
#include <string>
#include <string_view>
#include <unordered_set>

#include <chess-library/include/chess.hpp>

namespace chessdb {

namespace {

constexpr std::size_t MAX_HALF_MOVES = 40;

constexpr std::array<std::string_view, 64> SQUARES = {
    "a1", "b1", "c1", "d1", "e1", "f1", "g1", "h1", "a2", "b2", "c2",
    "d2", "e2", "f2", "g2", "h2", "a3", "b3", "c3", "d3", "e3", "f3",
    "g3", "h3", "a4", "b4", "c4", "d4", "e4", "f4", "g4", "h4", "a5",
    "b5", "c5", "d5", "e5", "f5", "g5", "h5", "a6", "b6", "c6", "d6",
    "e6", "f6", "g6", "h6", "a7", "b7", "c7", "d7", "e7", "f7", "g7",
    "h7", "a8", "b8", "c8", "d8", "e8", "f8", "g8", "h8"};

// Index 7 is also empty to safely handle all 3-bit values.
constexpr std::array<std::string_view, 8> PIECES = {"p", "n", "b", "r",
                                                    "q", "k", "",  ""};

} // namespace

ProcessedGame processGame(Game game) {
  ProcessedGame processed;
  processed.game = std::move(game);

  const auto &moves = processed.game.moves;
  const std::size_t scannedBytes = std::min(MAX_HALF_MOVES * 2, moves.size());

  chess::Board board;

  std::unordered_set<PositionHash> uniquePositions;
  uniquePositions.reserve(MAX_HALF_MOVES);

  processed.positions.reserve(MAX_HALF_MOVES);

  for (std::size_t i = 0; i + 1 < scannedBytes; i += 2) {
    const std::uint16_t packed = (static_cast<std::uint16_t>(moves[i]) << 8) |
                                 static_cast<std::uint16_t>(moves[i + 1]);

    const std::uint8_t from = (packed >> 10) & 0x3f;
    const std::uint8_t to = (packed >> 4) & 0x3f;
    const std::uint8_t promotion = packed & 0x07;

    std::string uci;
    uci.reserve(5);

    uci += SQUARES[from];
    uci += SQUARES[to];

    if (!PIECES[promotion].empty()) {
      uci += PIECES[promotion];
    }

    const chess::Move move = chess::uci::uciToMove(board, uci);
    board.makeMove(move);

    const PositionHash hash = board.zobrist();

    if (uniquePositions.insert(hash).second) {
      processed.positions.push_back(hash);
    }
  }

  processed.game.moves.clear();
  processed.game.moves.shrink_to_fit();

  return processed;
}

} // namespace chessdb
