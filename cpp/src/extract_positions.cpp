#include <algorithm>
#include <array>
#include <chess-library/include/chess.hpp>
#include <cstddef>
#include <cstdint>
#include <future>
#include <iostream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "MysqlConnection.hpp"
#include "mysql_settings.hpp"

using namespace std;

constexpr size_t MAX_MOVES = 20;
constexpr size_t MAX_HALF_MOVES = MAX_MOVES * 2;
constexpr size_t GAME_BATCH_SIZE = 1000;
constexpr size_t HASH_BATCH_SIZE = 500;

const unsigned int detected_threads = thread::hardware_concurrency();
const unsigned int N_THREADS = max(2u, detected_threads) - 1;

const array<string, 64> SQUARES = {
    "a1", "b1", "c1", "d1", "e1", "f1", "g1", "h1", "a2", "b2", "c2",
    "d2", "e2", "f2", "g2", "h2", "a3", "b3", "c3", "d3", "e3", "f3",
    "g3", "h3", "a4", "b4", "c4", "d4", "e4", "f4", "g4", "h4", "a5",
    "b5", "c5", "d5", "e5", "f5", "g5", "h5", "a6", "b6", "c6", "d6",
    "e6", "f6", "g6", "h6", "a7", "b7", "c7", "d7", "e7", "f7", "g7",
    "h7", "a8", "b8", "c8", "d8", "e8", "f8", "g8", "h8"};

const array<string, 7> PIECES = {"p", "n", "b", "r", "q", "k", ""};

struct IdAndMove {
  string id;
  string movesBlob;
};

using GameHashes = unordered_map<uint64_t, int>;
using BatchHashes = unordered_map<uint64_t, vector<int>>;

GameHashes processGame(const IdAndMove &game) {
  const int gameId = stoi(game.id);

  GameHashes result;
  result.reserve(MAX_HALF_MOVES);

  chess::Board board;
  const string &movesData = game.movesBlob;

  const size_t scannedMoves = min(MAX_HALF_MOVES * 2, movesData.size());

  for (size_t i = 0; i + 1 < scannedMoves; i += 2) {
    byte byte1 = static_cast<byte>(movesData[i]);
    byte byte2 = static_cast<byte>(movesData[i + 1]);

    uint16_t packed =
        (static_cast<uint16_t>(byte1) << 8) | static_cast<uint16_t>(byte2);

    int from = (packed >> 10) & 0x3f;
    int to = (packed >> 4) & 0x3f;
    int promotion = packed & 0x07;

    string uci = SQUARES[from] + SQUARES[to];

    if (!PIECES[promotion].empty()) {
      uci += PIECES[promotion];
    }

    chess::Move move = chess::uci::uciToMove(board, uci);

    board.makeMove(move);

    result.emplace(board.zobrist(), gameId);
  }

  return result;
}

void processBatch(const vector<IdAndMove> &games, const string &table,
                  mysql::Connection &conn) {
  BatchHashes hashes;
  hashes.reserve(games.size() * MAX_HALF_MOVES);

  vector<future<GameHashes>> futures;
  futures.reserve(games.size());

  transform(
      games.begin(), games.end(), back_inserter(futures),
      [](const auto &game) { return async(launch::async, processGame, game); });

  for (auto &future : futures) {
    auto gameHashes = future.get();

    for (const auto &[hash, gameId] : gameHashes) {
      hashes[hash].push_back(gameId);
    }
  }

  if (!hashes.empty()) {
    vector<pair<uint64_t, vector<int>>> hashList;
    hashList.reserve(hashes.size());

    for (auto &[hash, gameIds] : hashes) {
      hashList.emplace_back(hash, std::move(gameIds));
    }

    for (size_t offset = 0; offset < hashList.size();
         offset += HASH_BATCH_SIZE) {
      size_t end = min(offset + HASH_BATCH_SIZE, hashList.size());

      string insertSql = "INSERT INTO `positions` "
                         "(`zobrist`, `games_table`, `reverse_index`) "
                         "VALUES ";

      vector<uint64_t> insertHashes;
      vector<string> reverseIndexes;

      insertHashes.reserve(end - offset);
      reverseIndexes.reserve(end - offset);

      bool first = true;

      for (size_t i = offset; i < end; ++i) {
        uint64_t hash = hashList[i].first;

        string reverseIndex;

        for (int gameId : hashList[i].second) {
          if (gameId < 0) {
            throw invalid_argument("Invalid negative game ID");
          }

          uint32_t id = static_cast<uint32_t>(gameId);

          reverseIndex.append(reinterpret_cast<const char *>(&id), sizeof(id));
        }

        if (!first) {
          insertSql += ",";
        }

        insertSql += "(?, ?, ?)";

        insertHashes.push_back(hash);
        reverseIndexes.push_back(std::move(reverseIndex));

        first = false;
      }

      insertSql +=
          " ON DUPLICATE KEY UPDATE "
          "`reverse_index` = CONCAT(`reverse_index`, VALUES(`reverse_index`))";

      vector<MYSQL_BIND> insertParams(insertHashes.size() * 3);

      for (size_t i = 0; i < insertHashes.size(); ++i) {
        auto &hashParam = insertParams[i * 3];
        auto &tableParam = insertParams[i * 3 + 1];
        auto &reverseParam = insertParams[i * 3 + 2];

        hashParam.buffer_type = MYSQL_TYPE_LONGLONG;
        hashParam.buffer = &insertHashes[i];

        tableParam.buffer_type = MYSQL_TYPE_STRING;
        tableParam.buffer = const_cast<char *>(table.data());
        tableParam.buffer_length = static_cast<unsigned long>(table.size());

        reverseParam.buffer_type = MYSQL_TYPE_BLOB;
        reverseParam.buffer = reverseIndexes[i].data();
        reverseParam.buffer_length =
            static_cast<unsigned long>(reverseIndexes[i].size());
      }

      auto insertStmt = conn.statement(insertSql);
      insertStmt.bindParam(insertParams.data());
      insertStmt.execute();
    }
  }

  string updateSql = "UPDATE `" + table + "` SET `scanned` = 1 WHERE `id` IN (";

  vector<int> gameIds;
  gameIds.reserve(games.size());

  bool first = true;

  for (const auto &game : games) {
    if (!first) {
      updateSql += ",";
    }

    updateSql += "?";
    gameIds.push_back(stoi(game.id));

    first = false;
  }

  updateSql += ")";

  auto updateStmt = conn.statement(updateSql);

  vector<MYSQL_BIND> updateParams(gameIds.size());

  for (size_t i = 0; i < gameIds.size(); ++i) {
    updateParams[i].buffer_type = MYSQL_TYPE_LONG;
    updateParams[i].buffer = &gameIds[i];
  }

  updateStmt.bindParam(updateParams.data());
  updateStmt.execute();
}

int main(int argc, const char *argv[]) {
  unordered_set<string> allowed_tables{"all_games", "poland_games"};

  string table = "all_games";

  if (argc > 1) {
    table = argv[1];
  }

  if (!allowed_tables.contains(table)) {
    cout << "Invalid table name: " << table << '\n';
    return 1;
  }

  cout << "Using table: " << table << '\n';

  try {
    mysql::Connection conn(mysql_host, mysql_user, mysql_password, database);

    while (true) {
      string query = "SELECT `id`, `moves_blob` "
                     "FROM `" +
                     table +
                     "` "
                     "WHERE `scanned` = 0 "
                     "ORDER BY `id` "
                     "LIMIT " +
                     to_string(GAME_BATCH_SIZE);

      auto stmt = conn.statement(query);

      stmt.execute();

      auto metadata = stmt.resultMetadata();

      mysql::BoundResult<2> result;
      stmt.bindResult(result.data());

      vector<IdAndMove> games;
      games.reserve(GAME_BATCH_SIZE);

      while (stmt.fetch() == 0) {
        IdAndMove game;

        game.id = result.get(0);
        game.movesBlob = result.get(1);

        games.emplace_back(std::move(game));
      }

      stmt.freeResult();

      if (games.empty()) {
        break;
      }

      processBatch(games, table, conn);
    }

    cout << "All games processed.\n";
  } catch (const exception &e) {
    cerr << "MySQL error: " << e.what() << '\n';

    return 1;
  }

  return 0;
}
