
#include "SQLiteDatabase.hpp"

#include <bit>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace chessdb {

namespace {

constexpr int SCHEMA_VERSION = 4;

class Statement {
public:
  Statement(sqlite3 *db, const char *sql) : db_(db) {
    const int rc = sqlite3_prepare_v2(db_, sql, -1, &stmt_, nullptr);

    if (rc != SQLITE_OK) {
      throw std::runtime_error(sqlite3_errmsg(db_));
    }
  }

  ~Statement() { sqlite3_finalize(stmt_); }

  Statement(const Statement &) = delete;
  Statement &operator=(const Statement &) = delete;

  sqlite3_stmt *get() const { return stmt_; }

  void reset() {
    checkSQLite(sqlite3_reset(stmt_), db_);
    checkSQLite(sqlite3_clear_bindings(stmt_), db_);
  }

private:
  sqlite3 *db_ = nullptr;
  sqlite3_stmt *stmt_ = nullptr;

  void checkSQLite(int rc, sqlite3 *db) {
    if (rc != SQLITE_OK && rc != SQLITE_DONE) {
      throw std::runtime_error(sqlite3_errmsg(db));
    }
  }
};

void checkSQLite(int rc, sqlite3 *db) {
  if (rc != SQLITE_OK && rc != SQLITE_DONE && rc != SQLITE_ROW) {
    throw std::runtime_error(sqlite3_errmsg(db));
  }
}

void createVersion4Schema(sqlite3 *db) {
  const char *sql = R"(
    BEGIN IMMEDIATE;

    CREATE TABLE games (
      mysql_game_id INTEGER PRIMARY KEY,
      year          INTEGER NOT NULL,
      date_code     INTEGER NOT NULL DEFAULT 378,
      white_id      INTEGER NOT NULL,
      black_id      INTEGER NOT NULL,
      result        INTEGER NOT NULL
    );

    CREATE TABLE positions (
      zobrist INTEGER PRIMARY KEY
    );

    CREATE TABLE position_games (
      zobrist       INTEGER NOT NULL,
      mysql_game_id INTEGER NOT NULL,

      PRIMARY KEY (zobrist, mysql_game_id),

      FOREIGN KEY (zobrist)
        REFERENCES positions(zobrist),

      FOREIGN KEY (mysql_game_id)
        REFERENCES games(mysql_game_id)
    );

    CREATE INDEX idx_position_games_game
      ON position_games(mysql_game_id);

    INSERT INTO schema_migrations(version) VALUES (4);
    PRAGMA user_version = 4;

    COMMIT;
  )";

  char *error = nullptr;
  const int rc = sqlite3_exec(db, sql, nullptr, nullptr, &error);

  if (rc != SQLITE_OK) {
    const std::string message = error ? error : sqlite3_errmsg(db);
    sqlite3_free(error);
    throw std::runtime_error("Schema creation failed: " + message);
  }
}

// Version 3 -> 4.
//
// Version 3 stored SQLite game rowids as 4-byte little-endian
// values inside positions.reverse_index.
//
// Since mysql_game_id was INTEGER PRIMARY KEY, its value was
// also the SQLite rowid. We can restore the original game IDs.
void migrateVersion3To4(sqlite3 *db) {
  const char *createSql = R"(
    BEGIN IMMEDIATE;

    CREATE TABLE games_v4 (
      mysql_game_id INTEGER PRIMARY KEY,
      year          INTEGER NOT NULL DEFAULT 0,
      date_code     INTEGER NOT NULL DEFAULT 378,
      white_id      INTEGER NOT NULL DEFAULT 0,
      black_id      INTEGER NOT NULL DEFAULT 0,
      result        INTEGER NOT NULL DEFAULT 255
    );

    CREATE TABLE positions_v4 (
      zobrist INTEGER PRIMARY KEY
    );

    CREATE TABLE position_games_v4 (
      zobrist       INTEGER NOT NULL,
      mysql_game_id INTEGER NOT NULL,

      PRIMARY KEY (zobrist, mysql_game_id),

      FOREIGN KEY (zobrist)
        REFERENCES positions_v4(zobrist),

      FOREIGN KEY (mysql_game_id)
        REFERENCES games_v4(mysql_game_id)
    );

    INSERT INTO games_v4 (mysql_game_id)
      SELECT mysql_game_id FROM games;

    INSERT INTO positions_v4 (zobrist)
      SELECT zobrist FROM positions;
  )";

  char *error = nullptr;
  int rc = sqlite3_exec(db, createSql, nullptr, nullptr, &error);

  if (rc != SQLITE_OK) {
    const std::string message = error ? error : sqlite3_errmsg(db);
    sqlite3_free(error);
    sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
    throw std::runtime_error("Migration 3 -> 4 failed: " + message);
  }

  try {
    {
      Statement readPositions(db,
                              "SELECT zobrist, reverse_index FROM positions;");

      Statement insertLink(db, "INSERT OR IGNORE INTO position_games_v4 "
                               "(zobrist, mysql_game_id) VALUES (?, ?);");

      while (true) {
        rc = sqlite3_step(readPositions.get());

        if (rc == SQLITE_DONE) {
          break;
        }

        checkSQLite(rc, db);

        const sqlite3_int64 hash = sqlite3_column_int64(readPositions.get(), 0);

        const auto *bytes = static_cast<const std::uint8_t *>(
            sqlite3_column_blob(readPositions.get(), 1));

        const int byteCount = sqlite3_column_bytes(readPositions.get(), 1);

        if (byteCount % 4 != 0) {
          throw std::runtime_error(
              "Invalid reverse_index length during migration");
        }

        if (byteCount > 0 && bytes == nullptr) {
          throw std::runtime_error(
              "Could not read reverse_index during migration");
        }

        for (int i = 0; i < byteCount; i += 4) {
          const std::uint32_t gameId =
              static_cast<std::uint32_t>(bytes[i]) |
              (static_cast<std::uint32_t>(bytes[i + 1]) << 8) |
              (static_cast<std::uint32_t>(bytes[i + 2]) << 16) |
              (static_cast<std::uint32_t>(bytes[i + 3]) << 24);

          sqlite3_bind_int64(insertLink.get(), 1, hash);
          sqlite3_bind_int64(insertLink.get(), 2,
                             static_cast<sqlite3_int64>(gameId));

          checkSQLite(sqlite3_step(insertLink.get()), db);
          insertLink.reset();
        }
      }
    }

    const char *finishSql = R"(
      DROP TABLE positions;
      DROP TABLE games;

      ALTER TABLE games_v4 RENAME TO games;
      ALTER TABLE positions_v4 RENAME TO positions;
      ALTER TABLE position_games_v4 RENAME TO position_games;

      CREATE INDEX idx_position_games_game
        ON position_games(mysql_game_id);

      INSERT INTO schema_migrations(version) VALUES (4);
      PRAGMA user_version = 4;

      COMMIT;
    )";

    error = nullptr;
    rc = sqlite3_exec(db, finishSql, nullptr, nullptr, &error);

    if (rc != SQLITE_OK) {
      const std::string message = error ? error : sqlite3_errmsg(db);
      sqlite3_free(error);
      throw std::runtime_error("Migration 3 -> 4 failed: " + message);
    }

  } catch (...) {
    sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
    throw;
  }
}

} // namespace

SQLiteDatabase::SQLiteDatabase(const std::string &filename, bool readOnly) {

  const int flags = readOnly ? SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX
                             : SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE |
                                   SQLITE_OPEN_FULLMUTEX;

  const int rc = sqlite3_open_v2(filename.c_str(), &db_, flags, nullptr);

  if (rc != SQLITE_OK) {
    const std::string message =
        db_ ? sqlite3_errmsg(db_) : "Could not allocate SQLite connection";

    if (db_) {
      sqlite3_close(db_);
      db_ = nullptr;
    }

    throw std::runtime_error("SQLite open failed: " + message);
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
  char *error = nullptr;
  const int rc = sqlite3_exec(db_, sql, nullptr, nullptr, &error);

  if (rc != SQLITE_OK) {
    const std::string message = error ? error : sqlite3_errmsg(db_);
    sqlite3_free(error);
    throw std::runtime_error("SQLite error: " + message);
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

  int version = 0;

  {
    Statement stmt(db_, "PRAGMA user_version;");
    checkSQLite(sqlite3_step(stmt.get()), db_);
    version = sqlite3_column_int(stmt.get(), 0);
  }

  if (version > SCHEMA_VERSION) {
    throw std::runtime_error("SQLite database version is newer than supported");
  }

  if (version == 0) {
    createVersion4Schema(db_);
    version = 4;
  } else if (version == 3) {
    migrateVersion3To4(db_);
    version = 4;
  } else if (version != 4) {
    throw std::runtime_error("Unsupported SQLite schema version. "
                             "Expected version 3 or 4.");
  }

  if (version != SCHEMA_VERSION) {
    throw std::runtime_error("SQLite schema migration failed");
  }

  execute("PRAGMA optimize;");
}

void SQLiteDatabase::insertBatch(const std::vector<ProcessedGame> &games) {

  if (games.empty()) {
    return;
  }

  execute("BEGIN IMMEDIATE;");

  try {
    Statement insertGame(
        db_, "INSERT INTO games "
             "(mysql_game_id, year, date_code, white_id, black_id, result) "
             "VALUES (?, ?, ?, ?, ?, ?) "
             "ON CONFLICT(mysql_game_id) DO NOTHING;");

    Statement insertPosition(
        db_, "INSERT OR IGNORE INTO positions (zobrist) VALUES (?);");

    Statement insertPositionGame(db_,
                                 "INSERT OR IGNORE INTO position_games "
                                 "(zobrist, mysql_game_id) VALUES (?, ?);");

    for (const ProcessedGame &processed : games) {
      const Game &game = processed.game;

      sqlite3_bind_int64(insertGame.get(), 1,
                         static_cast<sqlite3_int64>(game.mysqlId));

      sqlite3_bind_int(insertGame.get(), 2, static_cast<int>(game.year));

      sqlite3_bind_int(insertGame.get(), 3, static_cast<int>(game.dateCode));

      sqlite3_bind_int(insertGame.get(), 4, static_cast<int>(game.whiteId));

      sqlite3_bind_int(insertGame.get(), 5, static_cast<int>(game.blackId));

      sqlite3_bind_int(insertGame.get(), 6, static_cast<int>(game.result));

      checkSQLite(sqlite3_step(insertGame.get()), db_);

      const bool inserted = sqlite3_changes(db_) != 0;
      insertGame.reset();

      // Game already exists: do not duplicate its position links.
      if (!inserted) {
        continue;
      }

      for (const PositionHash hash : processed.positions) {
        // Preserve all 64 bits of the unsigned Zobrist hash.
        const sqlite3_int64 sqliteHash = std::bit_cast<sqlite3_int64>(hash);

        sqlite3_bind_int64(insertPosition.get(), 1, sqliteHash);

        checkSQLite(sqlite3_step(insertPosition.get()), db_);
        insertPosition.reset();

        sqlite3_bind_int64(insertPositionGame.get(), 1, sqliteHash);

        sqlite3_bind_int64(insertPositionGame.get(), 2,
                           static_cast<sqlite3_int64>(game.mysqlId));

        checkSQLite(sqlite3_step(insertPositionGame.get()), db_);
        insertPositionGame.reset();
      }
    }

    execute("COMMIT;");

  } catch (...) {
    try {
      execute("ROLLBACK;");
    } catch (...) {
      // Preserve the original exception.
    }

    throw;
  }
}

} // namespace chessdb
