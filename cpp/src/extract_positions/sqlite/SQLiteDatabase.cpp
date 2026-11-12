#include "SQLiteDatabase.hpp"

#include <bit>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace chessdb {
namespace {

constexpr int SCHEMA_VERSION = 6;

class Statement {
public:
  Statement(sqlite3 *db, const char *sql) : db_(db) {
    const int rc = sqlite3_prepare_v2(db_, sql, -1, &stmt_, nullptr);
    if (rc != SQLITE_OK)
      throw std::runtime_error(sqlite3_errmsg(db_));
  }

  ~Statement() {
    if (stmt_)
      sqlite3_finalize(stmt_);
  }

  Statement(const Statement &) = delete;
  Statement &operator=(const Statement &) = delete;

  sqlite3_stmt *get() const { return stmt_; }

  void reset() {
    check(sqlite3_reset(stmt_));
    check(sqlite3_clear_bindings(stmt_));
  }

private:
  sqlite3 *db_{};
  sqlite3_stmt *stmt_{};

  void check(int rc) {
    if (rc != SQLITE_OK && rc != SQLITE_DONE)
      throw std::runtime_error(sqlite3_errmsg(db_));
  }
};

void checkSQLite(int rc, sqlite3 *db) {
  if (rc != SQLITE_OK && rc != SQLITE_DONE && rc != SQLITE_ROW)
    throw std::runtime_error(sqlite3_errmsg(db));
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

void beginMigration(sqlite3 *db) { executeSql(db, "BEGIN IMMEDIATE;"); }

void finishMigration(sqlite3 *db, int version) {
  Statement migration(db, "INSERT INTO schema_migrations(version) VALUES (?);");

  sqlite3_bind_int(migration.get(), 1, version);
  checkSQLite(sqlite3_step(migration.get()), db);

  executeSql(db, version == 6   ? "PRAGMA user_version = 6; COMMIT;"
                 : version == 5 ? "PRAGMA user_version = 5; COMMIT;"
                 : version == 4 ? "PRAGMA user_version = 4; COMMIT;"
                                : "PRAGMA user_version = 3; COMMIT;");
}

void rollback(sqlite3 *db) noexcept {
  sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
}

std::uint32_t readGameId(const std::uint8_t *bytes) {
  return static_cast<std::uint32_t>(bytes[0]) |
         (static_cast<std::uint32_t>(bytes[1]) << 8) |
         (static_cast<std::uint32_t>(bytes[2]) << 16) |
         (static_cast<std::uint32_t>(bytes[3]) << 24);
}

void insertPositionGame(sqlite3 *db, sqlite3_stmt *stmt, sqlite3_int64 hash,
                        std::uint32_t gameId) {

  sqlite3_bind_int64(stmt, 1, hash);
  sqlite3_bind_int64(stmt, 2, gameId);
  checkSQLite(sqlite3_step(stmt), db);

  sqlite3_reset(stmt);
  sqlite3_clear_bindings(stmt);
}

void createVersion6Schema(sqlite3 *db) {
  beginMigration(db);

  try {
    executeSql(db, R"(
            CREATE TABLE games (
                mysql_game_id INTEGER PRIMARY KEY,
                year          INTEGER NOT NULL,
                date_code     INTEGER NOT NULL DEFAULT 378,
                white_id      INTEGER NOT NULL,
                black_id      INTEGER NOT NULL,
                result        INTEGER NOT NULL
            );

            CREATE TABLE positions (
                game_id INTEGER NOT NULL,
                zobrist INTEGER NOT NULL,
                PRIMARY KEY (game_id, zobrist)
            ) WITHOUT ROWID;
        )");

    finishMigration(db, 6);
  } catch (...) {
    rollback(db);
    throw;
  }
}

// Version 3 -> 4.
//
// Version 3 has:
//   games(mysql_game_id)
//   positions(zobrist, reverse_index)
//
// reverse_index contains packed 4-byte little-endian game IDs.
void migrateVersion3To4(sqlite3 *db) {
  beginMigration(db);

  try {
    executeSql(db, R"(
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
                zobrist INTEGER NOT NULL,
                mysql_game_id INTEGER NOT NULL,
                PRIMARY KEY (zobrist, mysql_game_id)
            ) WITHOUT ROWID;

            INSERT INTO games_v4(mysql_game_id)
            SELECT mysql_game_id FROM games;

            INSERT INTO positions_v4(zobrist)
            SELECT zobrist FROM positions;
        )");

    Statement read(
        db, "SELECT zobrist, reverse_index FROM positions ORDER BY zobrist;");

    Statement insert(db,
                     "INSERT INTO position_games_v4(zobrist, mysql_game_id) "
                     "VALUES (?, ?);");

    while (true) {
      const int rc = sqlite3_step(read.get());

      if (rc == SQLITE_DONE)
        break;

      checkSQLite(rc, db);

      const sqlite3_int64 hash = sqlite3_column_int64(read.get(), 0);

      const auto *blob =
          static_cast<const std::uint8_t *>(sqlite3_column_blob(read.get(), 1));

      const int bytes = sqlite3_column_bytes(read.get(), 1);

      if (bytes % 4 != 0)
        throw std::runtime_error(
            "Invalid reverse_index BLOB in schema version 3");

      for (int offset = 0; offset < bytes; offset += 4) {
        const std::uint32_t gameId = readGameId(blob + offset);
        insertPositionGame(db, insert.get(), hash, gameId);
      }
    }

    executeSql(db, R"(
            DROP TABLE positions;
            DROP TABLE games;

            ALTER TABLE games_v4 RENAME TO games;
            ALTER TABLE positions_v4 RENAME TO positions;
            ALTER TABLE position_games_v4 RENAME TO position_games;
        )");

    finishMigration(db, 4);
  } catch (...) {
    rollback(db);
    throw;
  }
}

// Version 4 -> 5.
//
// Convert position_games rows into packed 4-byte little-endian game ID BLOBs.
void migrateVersion4To5(sqlite3 *db) {
  beginMigration(db);

  try {
    executeSql(db, R"(
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

    {
      Statement read(db,
                     "SELECT p.zobrist, pg.mysql_game_id "
                     "FROM positions p "
                     "LEFT JOIN position_games pg ON pg.zobrist = p.zobrist "
                     "ORDER BY p.zobrist, pg.mysql_game_id;");

      Statement insert(
          db, "INSERT INTO positions_v5(zobrist, game_ids) VALUES (?, ?);");

      bool hasHash = false;
      sqlite3_int64 currentHash = 0;
      std::vector<std::uint8_t> ids;

      auto flush = [&]() {
        if (!hasHash)
          return;

        sqlite3_bind_int64(insert.get(), 1, currentHash);

        const int rc = sqlite3_bind_blob(
            insert.get(), 2, ids.empty() ? nullptr : ids.data(),
            static_cast<int>(ids.size()), SQLITE_TRANSIENT);

        checkSQLite(rc, db);
        checkSQLite(sqlite3_step(insert.get()), db);
        insert.reset();
        ids.clear();
      };

      while (true) {
        const int rc = sqlite3_step(read.get());

        if (rc == SQLITE_DONE)
          break;

        checkSQLite(rc, db);

        const sqlite3_int64 hash = sqlite3_column_int64(read.get(), 0);

        if (!hasHash || hash != currentHash) {
          flush();
          currentHash = hash;
          hasHash = true;
        }

        if (sqlite3_column_type(read.get(), 1) != SQLITE_NULL) {
          const sqlite3_int64 id = sqlite3_column_int64(read.get(), 1);

          if (id < 0 || id > std::numeric_limits<std::uint32_t>::max()) {
            throw std::runtime_error(
                "Invalid game ID during version 4 migration");
          }

          const auto gameId = static_cast<std::uint32_t>(id);

          ids.push_back(static_cast<std::uint8_t>(gameId));
          ids.push_back(static_cast<std::uint8_t>(gameId >> 8));
          ids.push_back(static_cast<std::uint8_t>(gameId >> 16));
          ids.push_back(static_cast<std::uint8_t>(gameId >> 24));
        }
      }

      flush();
    }

    executeSql(db, R"(
            DROP TABLE position_games;
            DROP TABLE positions;
            DROP TABLE games;

            ALTER TABLE games_v5 RENAME TO games;
            ALTER TABLE positions_v5 RENAME TO positions;
        )");

    finishMigration(db, 5);
  } catch (...) {
    rollback(db);
    throw;
  }
}

// Version 5 -> 6.
//
// Unpack each game_ids BLOB into individual (zobrist, game_id) rows.
void migrateVersion5To6(sqlite3 *db) {
  beginMigration(db);

  try {
    executeSql(db, R"(
            CREATE TABLE positions_v6 (
                zobrist INTEGER NOT NULL,
                game_id INTEGER NOT NULL,
                PRIMARY KEY (zobrist, game_id)
            ) WITHOUT ROWID;
        )");

    {
      Statement read(
          db, "SELECT zobrist, game_ids FROM positions ORDER BY zobrist;");

      Statement insert(db,
                       "INSERT OR IGNORE INTO positions_v6(zobrist, game_id) "
                       "VALUES (?, ?);");

      while (true) {
        const int rc = sqlite3_step(read.get());

        if (rc == SQLITE_DONE)
          break;

        checkSQLite(rc, db);

        const sqlite3_int64 hash = sqlite3_column_int64(read.get(), 0);

        const auto *blob = static_cast<const std::uint8_t *>(
            sqlite3_column_blob(read.get(), 1));

        const int bytes = sqlite3_column_bytes(read.get(), 1);

        if (bytes % 4 != 0)
          throw std::runtime_error("Invalid game_ids BLOB in schema version 5");

        for (int offset = 0; offset < bytes; offset += 4) {
          const std::uint32_t gameId = readGameId(blob + offset);

          insertPositionGame(db, insert.get(), hash, gameId);
        }
      }
    }

    executeSql(db, R"(
            DROP TABLE positions;
            ALTER TABLE positions_v6 RENAME TO positions;
        )");

    finishMigration(db, 6);
  } catch (...) {
    rollback(db);
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
  if (db_)
    sqlite3_close(db_);
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

  if (version > SCHEMA_VERSION)
    throw std::runtime_error("SQLite database version is newer than supported");

  // A new database is created directly at the latest schema.
  if (version == 0) {
    createVersion6Schema(db_);
  } else {
    // Upgrade one version at a time.
    while (version < SCHEMA_VERSION) {
      switch (version) {
      case 3:
        migrateVersion3To4(db_);
        version = 4;
        break;

      case 4:
        migrateVersion4To5(db_);
        version = 5;
        break;

      case 5:
        migrateVersion5To6(db_);
        version = 6;
        break;

      default:
        throw std::runtime_error("Unsupported SQLite schema version. "
                                 "Supported existing versions are 3, 4 and 5.");
      }
    }
  }

  Statement optimize(db_, "PRAGMA optimize;");
  checkSQLite(sqlite3_step(optimize.get()), db_);
}

void SQLiteDatabase::insertBatch(const std::vector<ProcessedGame> &games) {

  if (games.empty())
    return;

  execute("BEGIN IMMEDIATE;");

  try {
    Statement insertGame(
        db_, "INSERT INTO games "
             "(mysql_game_id, year, date_code, white_id, black_id, result) "
             "VALUES (?, ?, ?, ?, ?, ?) "
             "ON CONFLICT(mysql_game_id) DO NOTHING;");

    Statement insertPosition(
        db_, "INSERT OR IGNORE INTO positions(zobrist, game_id) "
             "VALUES (?, ?);");

    for (const ProcessedGame &game : games) {
      sqlite3_bind_int64(insertGame.get(), 1, game.mysqlId);
      sqlite3_bind_int(insertGame.get(), 2, game.year);
      sqlite3_bind_int(insertGame.get(), 3, game.dateCode);
      sqlite3_bind_int(insertGame.get(), 4, game.whiteId);
      sqlite3_bind_int(insertGame.get(), 5, game.blackId);
      sqlite3_bind_int(insertGame.get(), 6, static_cast<int>(game.result));

      checkSQLite(sqlite3_step(insertGame.get()), db_);

      const bool inserted = sqlite3_changes(db_) != 0;
      insertGame.reset();

      // Do not duplicate position links for an already imported game.
      if (!inserted)
        continue;

      for (const PositionHash hash : game.positions) {
        const sqlite3_int64 sqliteHash = std::bit_cast<sqlite3_int64>(hash);

        sqlite3_bind_int64(insertPosition.get(), 1, sqliteHash);
        sqlite3_bind_int64(insertPosition.get(), 2, game.mysqlId);

        checkSQLite(sqlite3_step(insertPosition.get()), db_);
        insertPosition.reset();
      }
    }

    execute("COMMIT;");
  } catch (...) {
    rollback(db_);
    throw;
  }
}

void SQLiteDatabase::prepareGameIdSync() {
  execute(R"(
        CREATE TEMP TABLE IF NOT EXISTS mysql_game_ids (
            id INTEGER PRIMARY KEY
        );

        DELETE FROM mysql_game_ids;
    )");
}

void SQLiteDatabase::insertMysqlGameIds(const std::vector<MysqlGameId> &ids) {

  if (ids.empty())
    return;

  execute("BEGIN;");

  try {
    Statement stmt(db_, "INSERT OR IGNORE INTO mysql_game_ids(id) VALUES (?);");

    for (const auto id : ids) {
      sqlite3_bind_int64(stmt.get(), 1, id);
      checkSQLite(sqlite3_step(stmt.get()), db_);
      stmt.reset();
    }

    execute("COMMIT;");
  } catch (...) {
    rollback(db_);
    throw;
  }
}

void SQLiteDatabase::removeGamesMissingFromMysql() {
  execute(R"(
        DELETE FROM positions
        WHERE NOT EXISTS (
            SELECT 1
            FROM mysql_game_ids
            WHERE mysql_game_ids.id = positions.game_id
        );

        DELETE FROM games
        WHERE NOT EXISTS (
            SELECT 1
            FROM mysql_game_ids
            WHERE mysql_game_ids.id = games.mysql_game_id
        );
    )");
}

std::vector<MysqlGameId> SQLiteDatabase::findMissingMysqlGames() {
  std::vector<MysqlGameId> ids;

  Statement stmt(db_, "SELECT id FROM mysql_game_ids "
                      "WHERE NOT EXISTS ("
                      "    SELECT 1 FROM games "
                      "    WHERE games.mysql_game_id = mysql_game_ids.id"
                      ") ORDER BY id;");

  int rc;

  while ((rc = sqlite3_step(stmt.get())) == SQLITE_ROW) {
    const sqlite3_int64 id = sqlite3_column_int64(stmt.get(), 0);

    if (id < 0 || id > std::numeric_limits<MysqlGameId>::max()) {
      throw std::runtime_error("Invalid MySQL game ID in SQLite");
    }

    ids.push_back(static_cast<MysqlGameId>(id));
  }

  if (rc != SQLITE_DONE)
    throw std::runtime_error(sqlite3_errmsg(db_));

  return ids;
}

} // namespace chessdb
