#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace chessdb {

using MysqlGameId = std::uint32_t;
using SQLiteGameId = std::uint32_t;
using PlayerId = std::uint16_t;
using PositionHash = std::uint64_t;
using MovesBlob = std::vector<std::uint8_t>;

struct GamesTable {
  enum Value : std::uint8_t { AllGames = 0, PolandGames = 1 };

  Value value;

  GamesTable(Value v) : value(v) {}

  GamesTable(const std::string &table) {
    if (table == "all_games")
      value = AllGames;
    else if (table == "poland_games")
      value = PolandGames;
    else
      throw std::invalid_argument("Invalid games table: " + table);
  }

  operator Value() const { return value; }

  operator const char *() const {
    return value == AllGames ? "all_games" : "poland_games";
  }
};

enum class GameResult : std::uint8_t {
  BlackWin = 0,
  Draw = 1,
  WhiteWin = 2,
  Unknown = 255
};

using DateCode = std::uint16_t;

constexpr DateCode UNKNOWN_DATE = 378;
constexpr std::uint32_t GAME_BATCH_SIZE = 1000;

struct Game {
  GamesTable gamesTable{GamesTable::AllGames};
  MysqlGameId mysqlId{};
  std::uint16_t year{};
  DateCode dateCode{UNKNOWN_DATE};

  PlayerId whiteId{};
  PlayerId blackId{};

  GameResult result{GameResult::Unknown};
  MovesBlob moves;
};

struct ProcessedGame {
  Game game;
  std::vector<PositionHash> positions;
};

} // namespace chessdb
