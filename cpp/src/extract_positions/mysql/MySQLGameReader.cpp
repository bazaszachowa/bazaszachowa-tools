
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

template <typename T>
T parseInteger(const std::string &value, const char *field) {
  try {
    const unsigned long long parsed = std::stoull(value);

    if (parsed > std::numeric_limits<T>::max()) {
      throw std::runtime_error(std::string(field) +
                               " exceeds its allowed range");
    }

    return static_cast<T>(parsed);
  } catch (const std::invalid_argument &) {
    throw std::runtime_error(std::string("Invalid MySQL value for ") + field);
  } catch (const std::out_of_range &) {
    throw std::runtime_error(std::string("MySQL value out of range for ") +
                             field);
  }
}

DateCode encodeDate(const std::string &month, const std::string &day) {
  if (month.empty()) {
    return UNKNOWN_DATE;
  }

  const unsigned int m = parseInteger<unsigned int>(month, "Month");

  if (m < 1 || m > 12) {
    return UNKNOWN_DATE;
  }

  // Month known, day NULL: 366–377.
  if (day.empty()) {
    return static_cast<DateCode>(365 + m);
  }

  const unsigned int d = parseInteger<unsigned int>(day, "Day");

  constexpr unsigned int daysPerMonth[] = {31, 28, 31, 30, 31, 30,
                                           31, 31, 30, 31, 30, 31};

  if (d < 1 || d > daysPerMonth[m - 1] + (m == 2 ? 1 : 0)) {
    return UNKNOWN_DATE;
  }

  // February 29 has its own reserved code.
  if (m == 2 && d == 29) {
    return 365;
  }

  unsigned int dayOfYear = d - 1;

  for (unsigned int i = 1; i < m; ++i) {
    dayOfYear += daysPerMonth[i - 1];
  }

  return static_cast<DateCode>(dayOfYear);
}

GameResult parseResult(const std::string &value) {
  if (value.empty()) {
    return GameResult::Unknown;
  }

  if (value == "0-1") {
    return GameResult::BlackWin;
  }
  if (value == "1-0") {
    return GameResult::WhiteWin;
  }
  if (value == "1/2-1/2") {
    return GameResult::Draw;
  }

  return GameResult::Unknown;
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

  const std::string sql = "SELECT `id`, `moves_blob`, `Year`, `Month`, `Day`, "
                          "`WhiteID`, `BlackID`, `Result` "
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

  mysql::BoundResult<8> result;
  stmt.bindResult(result.data());

  std::vector<Game> games;
  games.reserve(limit);

  while (true) {
    const int rc = stmt.fetch();

    if (rc == MYSQL_NO_DATA) {
      break;
    }

    if (rc == MYSQL_DATA_TRUNCATED) {
      throw std::runtime_error(
          "MySQL result truncated while fetching moves_blob");
    }

    if (rc != 0) {
      throw std::runtime_error("MySQL fetch failed, error code: " +
                               std::to_string(rc));
    }

    const std::string idString = result.get(0);
    const std::string movesString = result.get(1);
    const std::string yearString = result.get(2);
    const std::string monthString = result.get(3);
    const std::string dayString = result.get(4);
    const std::string whiteIdString = result.get(5);
    const std::string blackIdString = result.get(6);
    const std::string resultString = result.get(7);

    Game game;

    game.mysqlId = parseInteger<MysqlGameId>(idString, "id");
    game.year = parseInteger<std::uint16_t>(yearString, "Year");
    game.dateCode = encodeDate(monthString, dayString);
    game.whiteId = parseInteger<PlayerId>(whiteIdString, "WhiteID");
    game.blackId = parseInteger<PlayerId>(blackIdString, "BlackID");
    game.result = parseResult(resultString);
    game.moves.reserve(movesString.size());

    for (unsigned char byte : movesString) {
      game.moves.push_back(static_cast<std::uint8_t>(byte));
    }

    games.push_back(std::move(game));
  }

  stmt.freeResult();

  return games;
}

std::vector<MysqlGameId> MySQLGameReader::fetchGameIds(std::uint64_t offset,
                                                       std::uint32_t limit) {

  const std::string sql = "SELECT `id` FROM `" + sourceTable_ +
                          "` ORDER BY `id` LIMIT " + std::to_string(limit) +
                          " OFFSET " + std::to_string(offset);

  auto stmt = conn_.statement(sql);
  stmt.execute();

  auto metadata = stmt.resultMetadata();

  mysql::BoundResult<1> result;
  stmt.bindResult(result.data());

  std::vector<MysqlGameId> ids;
  ids.reserve(limit);

  while (true) {
    const int rc = stmt.fetch();

    if (rc == MYSQL_NO_DATA) {
      break;
    }

    if (rc == MYSQL_DATA_TRUNCATED) {
      throw std::runtime_error("MySQL result truncated while fetching id");
    }

    if (rc != 0) {
      throw std::runtime_error("MySQL fetch failed, error code: " +
                               std::to_string(rc));
    }

    ids.push_back(parseInteger<MysqlGameId>(result.get(0), "id"));
  }

  stmt.freeResult();
  return ids;
}

std::vector<Game>
MySQLGameReader::fetchGamesByIds(const std::vector<MysqlGameId> &ids) {

  if (ids.empty()) {
    return {};
  }

  std::string sql = "SELECT `id`, `moves_blob`, `Year`, `Month`, `Day`, "
                    "`WhiteID`, `BlackID`, `Result` "
                    "FROM `" +
                    sourceTable_ + "` WHERE `id` IN (";

  for (std::size_t i = 0; i < ids.size(); ++i) {
    if (i != 0) {
      sql += ',';
    }
    sql += std::to_string(ids[i]);
  }

  sql += ") ORDER BY `id`";

  auto stmt = conn_.statement(sql);
  stmt.execute();

  auto metadata = stmt.resultMetadata();

  mysql::BoundResult<8> result;
  stmt.bindResult(result.data());

  std::vector<Game> games;
  games.reserve(ids.size());

  while (true) {
    const int rc = stmt.fetch();

    if (rc == MYSQL_NO_DATA) {
      break;
    }

    if (rc == MYSQL_DATA_TRUNCATED) {
      throw std::runtime_error(
          "MySQL result truncated while fetching moves_blob");
    }

    if (rc != 0) {
      throw std::runtime_error("MySQL fetch failed, error code: " +
                               std::to_string(rc));
    }

    const std::string idString = result.get(0);
    const std::string movesString = result.get(1);
    const std::string yearString = result.get(2);
    const std::string monthString = result.get(3);
    const std::string dayString = result.get(4);
    const std::string whiteIdString = result.get(5);
    const std::string blackIdString = result.get(6);
    const std::string resultString = result.get(7);

    Game game;

    game.mysqlId = parseInteger<MysqlGameId>(idString, "id");
    game.year = parseInteger<std::uint16_t>(yearString, "Year");
    game.dateCode = encodeDate(monthString, dayString);
    game.whiteId = parseInteger<PlayerId>(whiteIdString, "WhiteID");
    game.blackId = parseInteger<PlayerId>(blackIdString, "BlackID");
    game.result = parseResult(resultString);

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
