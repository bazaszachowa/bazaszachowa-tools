
#pragma once

#include <sqlite3.h>

#include <cstdint>
#include <string>

namespace chessdb {

class SQLiteDatabase {
public:
  explicit SQLiteDatabase(const std::string &filename, bool readOnly = false);
  ~SQLiteDatabase();

  SQLiteDatabase(const SQLiteDatabase &) = delete;
  SQLiteDatabase &operator=(const SQLiteDatabase &) = delete;

  bool containsGame(const std::string &sourceTable, std::uint32_t gameId);

  sqlite3 *handle() const noexcept { return db_; }

private:
  sqlite3 *db_ = nullptr;

  void initialize();
  void execute(const char *sql);
};

} // namespace chessdb
