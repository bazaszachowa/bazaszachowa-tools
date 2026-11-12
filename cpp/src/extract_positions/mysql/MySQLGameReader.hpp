#pragma once

#include "../../MysqlConnection.hpp"
#include "../types/Types.hpp"

#include <cstdint>
#include <string>
#include <vector>

namespace chessdb {

class MySQLGameReader {
public:
  explicit MySQLGameReader(const std::string &sourceTable);

  std::uint64_t countGames();

  std::vector<MysqlGameId> fetchGameIds(MysqlGameId lastId,
                                        std::uint32_t limit);
  std::vector<Game> fetchGamesByIds(const std::vector<MysqlGameId> &ids);

private:
  std::string sourceTable_;
  mysql::Connection conn_;
};

} // namespace chessdb
