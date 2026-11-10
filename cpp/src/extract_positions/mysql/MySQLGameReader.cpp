#include "MySQLGameReader.hpp"

#include "../../mysql_settings.hpp"

#include <limits>
#include <stdexcept>
#include <string>
#include <utility>

namespace chessdb {

namespace {

constexpr bool isAllowedTable(const std::string &table) {
  return table == "all_games" || table == "poland_games";
}

} // namespace

MySQLGameReader::MySQLGameReader(const std::string &sourceTable)
    : sourceTable_(sourceTable),
      conn_(mysql_host, mysql_user, mysql_password, database) {

  if (!isAllowedTable(sourceTable_)) {
    throw std::invalid_argument("Invalid MySQL table: " + sourceTable_);
  }
}

std::uint64_t MySQLGameReader::countGames() {
  const std::string sql = "SELECT COUNT(*) FROM `" + sourceTable_ + "`";

  auto result = conn_.queryResult(sql);
  MYSQL_ROW row = result.fetchRow();

  if (!row || !row[0]) {
    throw std::runtime_error("Could not count MySQL games");
  }

  return std::stoull(row[0]);
}

std::vector<Game> MySQLGameReader::fetchBatch(std::uint64_t offset,
                                              std::uint32_t limit) {
  const std::string sql = "SELECT `id`, `moves_blob` "
                          "FROM `" +
                          sourceTable_ +
                          "` "
                          "ORDER BY `id` "
                          "LIMIT " +
                          std::to_string(limit) + " OFFSET " +
                          std::to_string(offset);

  auto stmt = conn_.statement(sql);
  stmt.execute();

  auto metadata = stmt.resultMetadata();

  mysql::BoundResult<2> result;
  stmt.bindResult(result.data());

  std::vector<Game> games;
  games.reserve(limit);

  while (stmt.fetch() == 0) {
    const std::string idString = result.get(0);
    const std::string movesString = result.get(1);

    const auto parsedId = std::stoull(idString);

    if (parsedId > std::numeric_limits<MysqlGameId>::max()) {
      throw std::runtime_error("MySQL game ID exceeds uint32_t");
    }

    Game game;
    game.gamesTable = sourceTable_;
    game.mysqlId = static_cast<MysqlGameId>(parsedId);

    game.moves.reserve(movesString.size());

    for (unsigned char byte : movesString) {
      game.moves.push_back(static_cast<std::uint8_t>(byte));
    }

    games.push_back(std::move(game));
  }

  stmt.freeResult();

  return games;
}

} // namespace chessdb
