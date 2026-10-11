#include <algorithm>
#include <array>
#include <atomic>
#include <format>
#include <iostream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "MysqlConnection.hpp"
#include "mysql_settings.hpp"

using namespace std;

constexpr int BATCH_SIZE = 1000;

const int detected_threads = thread::hardware_concurrency();
const int N_THREADS = max(2, detected_threads) - 1;

struct EcoLine {
  int id;
  string uci;
};

void processBatch(mysql::Connection &conn, const string &table,
                  const vector<EcoLine> &eco_lines, int start_id, int end_id,
                  atomic<int> &total_updated) {
  try {
    string select_query = "SELECT id, moves_blob FROM `" + table +
                          "` "
                          "WHERE id >= ? "
                          "AND id < ? "
                          "AND ecoID IS NULL "
                          "ORDER BY id ASC "
                          "LIMIT ?";

    auto select_stmt = conn.statement(select_query);

    array<MYSQL_BIND, 3> params{};

    int start = start_id;
    int end = end_id;
    int limit = BATCH_SIZE;

    params[0].buffer_type = MYSQL_TYPE_LONG;
    params[0].buffer = &start;

    params[1].buffer_type = MYSQL_TYPE_LONG;
    params[1].buffer = &end;

    params[2].buffer_type = MYSQL_TYPE_LONG;
    params[2].buffer = &limit;

    select_stmt.bindParam(params.data());
    select_stmt.execute();

    auto metadata = select_stmt.resultMetadata();

    mysql::BoundResult<2> result;
    select_stmt.bindResult(result.data());

    vector<pair<int, int>> updates;
    updates.reserve(BATCH_SIZE);

    while (select_stmt.fetch() == 0) {
      int game_id = stoi(result.get(0));
      string moves_blob = result.get(1);

      auto it =
          find_if(eco_lines.begin(), eco_lines.end(), [&](const auto &eco) {
            return moves_blob.rfind(eco.uci, 0) == 0;
          });

      if (it != eco_lines.end()) {
        updates.emplace_back(it->id, game_id);
      }
    }

    select_stmt.freeResult();

    if (updates.empty()) {
      return;
    }

    string update_query = "UPDATE `" + table + "` SET ecoID = ? WHERE id = ?";

    auto update_stmt = conn.statement(update_query);

    array<MYSQL_BIND, 2> update_params{};

    int eco_id = 0;
    int game_id = 0;

    update_params[0].buffer_type = MYSQL_TYPE_LONG;
    update_params[0].buffer = &eco_id;

    update_params[1].buffer_type = MYSQL_TYPE_LONG;
    update_params[1].buffer = &game_id;

    update_stmt.bindParam(update_params.data());

    for (const auto &[new_eco_id, new_game_id] : updates) {
      eco_id = new_eco_id;
      game_id = new_game_id;

      update_stmt.execute();
    }

    total_updated += static_cast<int>(updates.size());

  } catch (const exception &e) {
    cerr << "Batch error: " << e.what() << '\n';
  }
}

void classify_worker(const string &table, const vector<EcoLine> &eco_lines,
                     int start_id, int end_id, atomic<int> &total_updated) {
  try {
    mysql::Connection conn(mysql_host, mysql_user, mysql_password, database);

    int last_id = start_id;

    while (last_id < end_id) {
      processBatch(conn, table, eco_lines, last_id, end_id, total_updated);
      last_id += BATCH_SIZE;

      if (last_id + BATCH_SIZE > end_id) {
        last_id = end_id;
      }
    }

  } catch (const exception &e) {
    cerr << "Connection error: " << e.what() << '\n';
  }
}

int main(int argc, const char *argv[]) {
  string table = "all_games";

  if (argc > 1) {
    table = argv[1];
  }

  cout << "Using table: " << table << '\n';

  try {
    mysql::Connection conn(mysql_host, mysql_user, mysql_password, database);

    auto res = conn.queryResult("SELECT id, uci FROM eco "
                                "WHERE uci IS NOT NULL "
                                "ORDER BY LENGTH(uci) DESC");

    vector<EcoLine> eco_lines;
    MYSQL_ROW row;

    while ((row = res.fetchRow())) {
      if (!row[0] || !row[1]) {
        continue;
      }

      eco_lines.emplace_back(atoi(row[0]), row[1]);
    }

    string minmax_query =
        "SELECT MIN(id), MAX(id) FROM `" + table + "` WHERE ecoID IS NULL";

    auto minmax = conn.queryResult(minmax_query);

    int min_id = 0;
    int max_id = 0;

    if ((row = minmax.fetchRow())) {
      if (row[0]) {
        min_id = atoi(row[0]);
      }
      if (row[1]) {
        max_id = atoi(row[1]);
      }
    }

    int total_range = max_id - min_id + 1;
    int chunk_size = (total_range + N_THREADS - 1) / N_THREADS;

    vector<thread> threads;
    atomic total_updated = 0;

    for (int i = 0; i < N_THREADS; ++i) {
      int start_id = min_id + i * chunk_size;
      int end_id = min(start_id + chunk_size - 1, max_id);
      threads.emplace_back(classify_worker, cref(table), cref(eco_lines),
                           start_id, end_id + 1, ref(total_updated));
    }

    for (auto &t : threads) {
      if (t.joinable()) {
        t.join();
      }
    }

    cout << "Done. Updated: " << total_updated << '\n';
  } catch (const exception &e) {
    cerr << "Error: " << e.what() << '\n';
    return 1;
  }

  return 0;
}
