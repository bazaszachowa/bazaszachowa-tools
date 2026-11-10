
#include "SQLiteDatabase.hpp"

#include <stdexcept>
#include <string>

namespace chessdb {

SQLiteDatabase::SQLiteDatabase(const std::string &filename, bool readOnly) {
  const int flags = readOnly ? SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX
                             : SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE |
                                   SQLITE_OPEN_FULLMUTEX;

  const int rc = sqlite3_open_v2(filename.c_str(), &db_, flags, nullptr);

  if (rc != SQLITE_OK) {
    std::string error =
        db_ ? sqlite3_errmsg(db_) : "Could not allocate SQLite connection";

    if (db_) {
      sqlite3_close(db_);
      db_ = nullptr;
    }

    throw std::runtime_error("SQLite open failed: " + error);
  }

  sqlite3_busy_timeout(db_, 5000);

  if (!readOnly) {
    try {
      initialize();
    } catch (...) {
      sqlite3_close(db_);
      db_ = nullptr;
      throw;
    }
  }
}

SQLiteDatabase::~SQLiteDatabase() {
  if (db_) {
    sqlite3_close(db_);
  }
}

void SQLiteDatabase::execute(const char *sql) {
  char *errorMessage = nullptr;

  const int rc = sqlite3_exec(db_, sql, nullptr, nullptr, &errorMessage);

  if (rc != SQLITE_OK) {
    std::string error = errorMessage ? errorMessage : sqlite3_errmsg(db_);

    sqlite3_free(errorMessage);

    throw std::runtime_error("SQLite error: " + error);
  }
}

void SQLiteDatabase::initialize() {
  execute("PRAGMA journal_mode = WAL;");
  execute("PRAGMA synchronous = NORMAL;");
  execute("PRAGMA foreign_keys = ON;");

  execute(R"(
        CREATE TABLE IF NOT EXISTS games (
            games_table TEXT NOT NULL,
            mysql_game_id INTEGER NOT NULL,
            moves_blob BLOB NOT NULL,

            PRIMARY KEY (games_table, mysql_game_id)
        );
    )");

  execute(R"(
        CREATE TABLE IF NOT EXISTS positions (
            zobrist INTEGER NOT NULL,
            games_table TEXT NOT NULL,
            reverse_index BLOB NOT NULL,

            PRIMARY KEY (zobrist, games_table),

            CHECK (length(reverse_index) % 4 = 0)
        );
    )");

  execute(R"(
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    )");

  execute(R"(
        INSERT OR IGNORE INTO schema_migrations (version)
        VALUES (1);
    )");

  execute("PRAGMA user_version = 1;");
}

bool SQLiteDatabase::containsGame(const std::string &sourceTable,
                                  std::uint32_t gameId) {
  constexpr const char *sql = R"(
        SELECT 1
        FROM games
        WHERE games_table = ?
          AND mysql_game_id = ?
        LIMIT 1;
    )";

  sqlite3_stmt *stmt = nullptr;

  int rc = sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr);

  if (rc != SQLITE_OK) {
    throw std::runtime_error("SQLite prepare failed: " +
                             std::string(sqlite3_errmsg(db_)));
  }

  struct StatementGuard {
    sqlite3_stmt *stmt;
    ~StatementGuard() { sqlite3_finalize(stmt); }
  } guard{stmt};

  rc =
      sqlite3_bind_text(stmt, 1, sourceTable.c_str(),
                        static_cast<int>(sourceTable.size()), SQLITE_TRANSIENT);

  if (rc != SQLITE_OK) {
    throw std::runtime_error("SQLite bind failed");
  }

  rc = sqlite3_bind_int64(stmt, 2, static_cast<sqlite3_int64>(gameId));

  if (rc != SQLITE_OK) {
    throw std::runtime_error("SQLite bind failed");
  }

  rc = sqlite3_step(stmt);

  if (rc == SQLITE_ROW) {
    return true;
  }

  if (rc == SQLITE_DONE) {
    return false;
  }

  throw std::runtime_error("SQLite query failed: " +
                           std::string(sqlite3_errmsg(db_)));
}

} // namespace chessdb
