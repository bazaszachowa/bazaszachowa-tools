
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
  execute("PRAGMA temp_store = MEMORY;");
  execute("PRAGMA cache_size = -64000;");
  execute("PRAGMA mmap_size = 268435456;");

  execute(R"(
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    )");

  sqlite3_stmt *stmt = nullptr;

  int rc = sqlite3_prepare_v2(db_, "PRAGMA user_version;", -1, &stmt, nullptr);

  if (rc != SQLITE_OK) {
    throw std::runtime_error(sqlite3_errmsg(db_));
  }

  rc = sqlite3_step(stmt);

  if (rc != SQLITE_ROW) {
    sqlite3_finalize(stmt);
    throw std::runtime_error("Could not read SQLite schema version");
  }

  const int version = sqlite3_column_int(stmt, 0);
  sqlite3_finalize(stmt);

  if (version > 2) {
    throw std::runtime_error("SQLite database version is newer than supported");
  }

  // New database.
  if (version == 0) {
    execute(R"(
            BEGIN;

            CREATE TABLE games (
                games_table INTEGER NOT NULL CHECK (games_table IN (0, 1)),
                mysql_game_id INTEGER NOT NULL,
                moves_blob BLOB NOT NULL,

                PRIMARY KEY (games_table, mysql_game_id)
            );

            CREATE TABLE positions (
                zobrist INTEGER NOT NULL,
                games_table INTEGER NOT NULL CHECK (games_table IN (0, 1)),
                reverse_index BLOB NOT NULL,

                PRIMARY KEY (zobrist, games_table),

                CHECK (length(reverse_index) % 4 = 0)
            );

            INSERT INTO schema_migrations (version) VALUES (2);

            PRAGMA user_version = 2;

            COMMIT;
        )");
  }

  // Migration from version 1: TEXT table names -> INTEGER IDs.
  else if (version == 1) {
    execute(R"(
            BEGIN;

            CREATE TABLE games_v2 (
                games_table INTEGER NOT NULL CHECK (games_table IN (0, 1)),
                mysql_game_id INTEGER NOT NULL,
                moves_blob BLOB NOT NULL,

                PRIMARY KEY (games_table, mysql_game_id)
            );

            INSERT INTO games_v2 (games_table, mysql_game_id, moves_blob)
            SELECT
                CASE games_table
                    WHEN 'all_games' THEN 0
                    WHEN 'poland_games' THEN 1
                END,
                mysql_game_id,
                moves_blob
            FROM games;

            CREATE TABLE positions_v2 (
                zobrist INTEGER NOT NULL,
                games_table INTEGER NOT NULL CHECK (games_table IN (0, 1)),
                reverse_index BLOB NOT NULL,

                PRIMARY KEY (zobrist, games_table),

                CHECK (length(reverse_index) % 4 = 0)
            );

            INSERT INTO positions_v2 (zobrist, games_table, reverse_index)
            SELECT
                zobrist,
                CASE games_table
                    WHEN 'all_games' THEN 0
                    WHEN 'poland_games' THEN 1
                END,
                reverse_index
            FROM positions;

            DROP TABLE games;
            DROP TABLE positions;

            ALTER TABLE games_v2 RENAME TO games;
            ALTER TABLE positions_v2 RENAME TO positions;

            INSERT OR IGNORE INTO schema_migrations (version) VALUES (2);

            PRAGMA user_version = 2;

            COMMIT;
        )");
  }

  execute("PRAGMA optimize;");
}

bool SQLiteDatabase::containsGame(GamesTable table, MysqlGameId gameId) {
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
    throw std::runtime_error(sqlite3_errmsg(db_));
  }

  struct StatementGuard {
    sqlite3_stmt *stmt;
    ~StatementGuard() { sqlite3_finalize(stmt); }
  } guard{stmt};

  sqlite3_bind_int(stmt, 1, static_cast<int>(table));
  sqlite3_bind_int64(stmt, 2, static_cast<sqlite3_int64>(gameId));

  rc = sqlite3_step(stmt);

  if (rc == SQLITE_ROW) {
    return true;
  }

  if (rc == SQLITE_DONE) {
    return false;
  }

  throw std::runtime_error(sqlite3_errmsg(db_));
}

} // namespace chessdb
