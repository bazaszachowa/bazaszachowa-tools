
#include "SQLiteDatabase.hpp"

#include <algorithm>
#include <bit>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace chessdb {

namespace {

constexpr int SCHEMA_VERSION = 5;

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

void executeSql(sqlite3 *db, const char *sql) {
  char *error = nullptr;
  const int rc = sqlite3_exec(db, sql, nullptr, nullptr, &error);

  if (rc != SQLITE_OK) {
    const std::string message = error ? error : sqlite3_errmsg(db);
    sqlite3_free(error);
    throw std::runtime_error(message);
  }
}

void appendGameId(std::vector<std::uint8_t> &blob, std::uint32_t gameId) {
  blob.push_back(static_cast<std::uint8_t>(gameId));
  blob.push_back(static_cast<std::uint8_t>(gameId >> 8));
  blob.push_back(static_cast<std::uint8_t>(gameId >> 16));
  blob.push_back(static_cast<std::uint8_t>(gameId >> 24));
}

void bindBlob(sqlite3_stmt *stmt, int index,
              const std::vector<std::uint8_t> &blob, sqlite3 *db) {
  if (blob.size() > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
    throw std::runtime_error("game_ids BLOB is too large");
  }

  const int rc =
      sqlite3_bind_blob(stmt, index, blob.data(), static_cast<int>(blob.size()),
                        SQLITE_TRANSIENT);

  checkSQLite(rc, db);
}

void createVersion5Schema(sqlite3 *db) {
  executeSql(db, R"(
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
      zobrist  INTEGER PRIMARY KEY,
      game_ids BLOB NOT NULL
    );

    INSERT INTO schema_migrations(version) VALUES (5);
    PRAGMA user_version = 5;

    COMMIT;
  )");
}

// Version 3 -> 5.
//
// Version 3 already stored the packed game IDs in reverse_index.
// Copy that BLOB directly into game_ids.
void migrateVersion3To5(sqlite3 *db) {
  try {
    executeSql(db, R"(
      BEGIN IMMEDIATE;

      CREATE TABLE games_v5 (
        mysql_game_id INTEGER PRIMARY KEY,
        year          INTEGER NOT NULL DEFAULT 0,
        date_code     INTEGER NOT NULL DEFAULT 378,
        white_id      INTEGER NOT NULL DEFAULT 0,
        black_id      INTEGER NOT NULL DEFAULT 0,
        result        INTEGER NOT NULL DEFAULT 255
      );

      CREATE TABLE positions_v5 (
        zobrist  INTEGER PRIMARY KEY,
        game_ids BLOB NOT NULL
      );

      INSERT INTO games_v5(mysql_game_id)
        SELECT mysql_game_id FROM games;

      INSERT INTO positions_v5(zobrist, game_ids)
        SELECT zobrist, reverse_index FROM positions;

      DROP TABLE positions;
      DROP TABLE games;

      ALTER TABLE games_v5 RENAME TO games;
      ALTER TABLE positions_v5 RENAME TO positions;

      INSERT INTO schema_migrations(version) VALUES (5);
      PRAGMA user_version = 5;

      COMMIT;
    )");
  } catch (...) {
    sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
    throw;
  }
}

// Version 4 -> 5.
//
// Version 4 stored the game IDs in the position_games table.
// Convert those rows into packed 4-byte little-endian BLOBs.
void migrateVersion4To5(sqlite3 *db) {
  try {
    executeSql(db, R"(
      BEGIN IMMEDIATE;

      CREATE TABLE games_v5 (
        mysql_game_id INTEGER PRIMARY KEY,
        year          INTEGER NOT NULL DEFAULT 0,
        date_code     INTEGER NOT NULL DEFAULT 378,
        white_id      INTEGER NOT NULL DEFAULT 0,
        black_id      INTEGER NOT NULL DEFAULT 0,
        result        INTEGER NOT NULL DEFAULT 255
      );

      CREATE TABLE positions_v5 (
        zobrist  INTEGER PRIMARY KEY,
        game_ids BLOB NOT NULL
      );

      INSERT INTO games_v5
        (mysql_game_id, year, date_code, white_id, black_id, result)
      SELECT
        mysql_game_id, year, date_code, white_id, black_id, result
      FROM games;
    )");

    Statement readLinks(db,
                        "SELECT p.zobrist, pg.mysql_game_id "
                        "FROM positions p "
                        "LEFT JOIN position_games pg ON pg.zobrist = p.zobrist "
                        "ORDER BY p.zobrist, pg.mysql_game_id;");

    Statement insertPosition(
        db, "INSERT INTO positions_v5(zobrist, game_ids) VALUES (?, ?);");

    bool hasCurrentHash = false;
    sqlite3_int64 currentHash = 0;
    std::vector<std::uint8_t> gameIds;

    auto flushPosition = [&]() {
      if (!hasCurrentHash) {
        return;
      }

      sqlite3_bind_int64(insertPosition.get(), 1, currentHash);
      bindBlob(insertPosition.get(), 2, gameIds, db);

      checkSQLite(sqlite3_step(insertPosition.get()), db);
      insertPosition.reset();

      gameIds.clear();
    };

    while (true) {
      const int rc = sqlite3_step(readLinks.get());

      if (rc == SQLITE_DONE) {
        break;
      }

      checkSQLite(rc, db);

      const sqlite3_int64 hash = sqlite3_column_int64(readLinks.get(), 0);

      if (!hasCurrentHash || hash != currentHash) {
        flushPosition();
        currentHash = hash;
        hasCurrentHash = true;
      }

      if (sqlite3_column_type(readLinks.get(), 1) != SQLITE_NULL) {
        const sqlite3_int64 id = sqlite3_column_int64(readLinks.get(), 1);

        if (id < 0 || id > std::numeric_limits<std::uint32_t>::max()) {
          throw std::runtime_error("Invalid game ID during migration");
        }

        appendGameId(gameIds, static_cast<std::uint32_t>(id));
      }
    }

    flushPosition();

    executeSql(db, R"(
      DROP TABLE position_games;
      DROP TABLE positions;
      DROP TABLE games;

      ALTER TABLE games_v5 RENAME TO games;
      ALTER TABLE positions_v5 RENAME TO positions;

      INSERT INTO schema_migrations(version) VALUES (5);
      PRAGMA user_version = 5;

      COMMIT;
    )");

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

void SQLiteDatabase::execute(const char *sql) { executeSql(db_, sql); }

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
    createVersion5Schema(db_);
  } else if (version == 3) {
    migrateVersion3To5(db_);
  } else if (version == 4) {
    migrateVersion4To5(db_);
  } else if (version != 5) {
    throw std::runtime_error(
        "Unsupported SQLite schema version. Expected version 3, 4 or 5.");
  }

  Statement optimize(db_, "PRAGMA optimize;");
  checkSQLite(sqlite3_step(optimize.get()), db_);
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

    Statement upsertPosition(
        db_,
        "INSERT INTO positions (zobrist, game_ids) "
        "VALUES (?, ?) "
        "ON CONFLICT(zobrist) DO UPDATE SET "
        "game_ids = CAST(positions.game_ids || excluded.game_ids AS BLOB);");

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

      // Already imported: don't append its IDs again.
      if (!inserted) {
        continue;
      }

      // Avoid duplicate IDs if a position occurs more than once in a game.
      std::vector<PositionHash> uniquePositions = processed.positions;
      std::sort(uniquePositions.begin(), uniquePositions.end());
      uniquePositions.erase(
          std::unique(uniquePositions.begin(), uniquePositions.end()),
          uniquePositions.end());

      for (const PositionHash hash : uniquePositions) {
        const sqlite3_int64 sqliteHash = std::bit_cast<sqlite3_int64>(hash);

        std::vector<std::uint8_t> gameIdBlob;
        appendGameId(gameIdBlob, game.mysqlId);

        sqlite3_bind_int64(upsertPosition.get(), 1, sqliteHash);
        bindBlob(upsertPosition.get(), 2, gameIdBlob, db_);

        checkSQLite(sqlite3_step(upsertPosition.get()), db_);
        upsertPosition.reset();
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
