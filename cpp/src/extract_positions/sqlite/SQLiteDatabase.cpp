
#include "SQLiteDatabase.hpp"

#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace chessdb {

namespace {

class Statement {
public:
  Statement(sqlite3 *db, const char *sql) {
    if (sqlite3_prepare_v2(db, sql, -1, &stmt_, nullptr) != SQLITE_OK) {
      throw std::runtime_error(sqlite3_errmsg(db));
    }
  }

  ~Statement() { sqlite3_finalize(stmt_); }

  Statement(const Statement &) = delete;
  Statement &operator=(const Statement &) = delete;

  sqlite3_stmt *get() const { return stmt_; }

  void reset() {
    sqlite3_reset(stmt_);
    sqlite3_clear_bindings(stmt_);
  }

private:
  sqlite3_stmt *stmt_ = nullptr;
};

void checkSQLite(int result, sqlite3 *db) {
  if (result != SQLITE_OK && result != SQLITE_DONE) {
    throw std::runtime_error(sqlite3_errmsg(db));
  }
}

void appendUint32(std::vector<std::uint8_t> &bytes, std::uint32_t value) {
  // Little-endian encoding.
  bytes.push_back(static_cast<std::uint8_t>(value));
  bytes.push_back(static_cast<std::uint8_t>(value >> 8));
  bytes.push_back(static_cast<std::uint8_t>(value >> 16));
  bytes.push_back(static_cast<std::uint8_t>(value >> 24));
}

} // namespace

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

void SQLiteDatabase::insertBatch(const std::vector<ProcessedGame> &games) {
  execute("BEGIN IMMEDIATE TRANSACTION");

  try {
    // FIX: games has no explicit "id" column.
    Statement findGame(db_, "SELECT 1 FROM games "
                            "WHERE games_table = ? AND mysql_game_id = ?");

    Statement insertGame(db_, "INSERT INTO games "
                              "(games_table, mysql_game_id, moves_blob) "
                              "VALUES (?, ?, ?)");

    Statement findPosition(db_, "SELECT reverse_index FROM positions "
                                "WHERE zobrist = ? AND games_table = ?");

    Statement insertPosition(db_, "INSERT INTO positions "
                                  "(zobrist, games_table, reverse_index) "
                                  "VALUES (?, ?, ?)");

    Statement updatePosition(db_, "UPDATE positions SET reverse_index = ? "
                                  "WHERE zobrist = ? AND games_table = ?");

    for (const auto &processed : games) {
      const Game &game = processed.game;
      const int table = static_cast<int>(game.gamesTable);

      // Skip games already imported.
      sqlite3_bind_int(findGame.get(), 1, table);
      sqlite3_bind_int64(findGame.get(), 2,
                         static_cast<sqlite3_int64>(game.mysqlId));

      int rc = sqlite3_step(findGame.get());

      if (rc == SQLITE_ROW) {
        findGame.reset();
        continue;
      }

      if (rc != SQLITE_DONE) {
        checkSQLite(rc, db_);
      }

      findGame.reset();

      // Insert game.
      sqlite3_bind_int(insertGame.get(), 1, table);
      sqlite3_bind_int64(insertGame.get(), 2,
                         static_cast<sqlite3_int64>(game.mysqlId));

      sqlite3_bind_blob(insertGame.get(), 3,
                        game.moves.empty() ? nullptr : game.moves.data(),
                        static_cast<int>(game.moves.size()), SQLITE_TRANSIENT);

      checkSQLite(sqlite3_step(insertGame.get()), db_);

      // Use SQLite's implicit rowid for the reverse index.
      const auto sqliteGameId =
          static_cast<std::uint32_t>(sqlite3_last_insert_rowid(db_));

      insertGame.reset();

      // Insert/update each position.
      for (const PositionHash hash : processed.positions) {
        sqlite3_bind_int64(findPosition.get(), 1,
                           static_cast<sqlite3_int64>(hash));

        sqlite3_bind_int(findPosition.get(), 2, table);

        rc = sqlite3_step(findPosition.get());

        std::vector<std::uint8_t> reverseIndex;

        if (rc == SQLITE_ROW) {
          const auto *data = static_cast<const std::uint8_t *>(
              sqlite3_column_blob(findPosition.get(), 0));

          const int size = sqlite3_column_bytes(findPosition.get(), 0);

          if (size < 0 || size % 4 != 0) {
            throw std::runtime_error("Invalid position reverse_index");
          }

          if (size > 0) {
            reverseIndex.assign(data, data + size);
          }

          findPosition.reset();

          // Avoid duplicate game IDs.
          bool exists = false;

          for (std::size_t i = 0; i < reverseIndex.size(); i += 4) {
            const std::uint32_t id =
                static_cast<std::uint32_t>(reverseIndex[i]) |
                (static_cast<std::uint32_t>(reverseIndex[i + 1]) << 8) |
                (static_cast<std::uint32_t>(reverseIndex[i + 2]) << 16) |
                (static_cast<std::uint32_t>(reverseIndex[i + 3]) << 24);

            if (id == sqliteGameId) {
              exists = true;
              break;
            }
          }

          if (exists) {
            continue;
          }

          appendUint32(reverseIndex, sqliteGameId);

          sqlite3_bind_blob(updatePosition.get(), 1, reverseIndex.data(),
                            static_cast<int>(reverseIndex.size()),
                            SQLITE_TRANSIENT);

          sqlite3_bind_int64(updatePosition.get(), 2,
                             static_cast<sqlite3_int64>(hash));

          sqlite3_bind_int(updatePosition.get(), 3, table);

          checkSQLite(sqlite3_step(updatePosition.get()), db_);
          updatePosition.reset();

        } else if (rc == SQLITE_DONE) {
          findPosition.reset();

          appendUint32(reverseIndex, sqliteGameId);

          sqlite3_bind_int64(insertPosition.get(), 1,
                             static_cast<sqlite3_int64>(hash));

          sqlite3_bind_int(insertPosition.get(), 2, table);

          sqlite3_bind_blob(insertPosition.get(), 3, reverseIndex.data(),
                            static_cast<int>(reverseIndex.size()),
                            SQLITE_TRANSIENT);

          checkSQLite(sqlite3_step(insertPosition.get()), db_);
          insertPosition.reset();

        } else {
          checkSQLite(rc, db_);
        }
      }
    }

    execute("COMMIT");

  } catch (...) {
    try {
      execute("ROLLBACK");
    } catch (...) {
      // Preserve the original exception.
    }

    throw;
  }
}

} // namespace chessdb
