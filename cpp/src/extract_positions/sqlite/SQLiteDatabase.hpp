#pragma once

#include <sqlite3.h>

#include "../types/Types.hpp"

#include <string>
#include <unordered_set>
#include <vector>

namespace chessdb {

class SQLiteDatabase {
public:
  explicit SQLiteDatabase(const std::string &filename, bool readOnly = false);
  ~SQLiteDatabase();

  SQLiteDatabase(const SQLiteDatabase &) = delete;
  SQLiteDatabase &operator=(const SQLiteDatabase &) = delete;

  void insertBatch(const std::vector<ProcessedGame> &games);
  std::unordered_set<MysqlGameId>
  findExistingGames(const std::vector<MysqlGameId> &ids);

private:
  sqlite3 *db_ = nullptr;

  void initialize();
  void execute(const char *sql);
};

} // namespace chessdb
