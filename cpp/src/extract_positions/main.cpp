#include <algorithm>
#include <cstdint>
#include <exception>
#include <future>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

#include "mysql/MySQLGameReader.hpp"
#include "types/Types.hpp"

using namespace std;
using namespace chessdb;

const unsigned int detected_threads = thread::hardware_concurrency();
const unsigned int N_THREADS = max(2u, detected_threads) - 1;

void fetchGames(const string &table, unsigned int workerId, uint64_t offset,
                uint64_t count) {
  MySQLGameReader reader(table);

  uint64_t fetched = 0;

  while (fetched < count) {
    const uint32_t limit =
        static_cast<uint32_t>(min<uint64_t>(GAME_BATCH_SIZE, count - fetched));

    auto games = reader.fetchBatch(offset + fetched, limit);

    if (games.empty()) {
      break;
    }

    fetched += games.size();

    cout << "Worker " << workerId << ": fetched " << fetched << " / " << count
         << '\n';
  }

  cout << "Worker " << workerId << " finished. Fetched " << fetched
       << " games.\n";
}

int main(int argc, const char *argv[]) {
  const unordered_set<string> allowedTables{"all_games", "poland_games"};

  string table = "all_games";

  if (argc > 1) {
    table = argv[1];
  }

  if (!allowedTables.contains(table)) {
    cerr << "Invalid table name: " << table << '\n';
    return 1;
  }

  try {
    cout << "Using table: " << table << '\n';
    cout << "Workers: " << N_THREADS << '\n';

    MySQLGameReader countReader(table);
    const uint64_t totalGames = countReader.countGames();

    cout << "Total games: " << totalGames << '\n';

    if (totalGames == 0) {
      cout << "No games to fetch.\n";
      return 0;
    }

    const uint64_t gamesPerThread = (totalGames + N_THREADS - 1) / N_THREADS;

    vector<future<void>> workers;
    workers.reserve(N_THREADS);

    for (unsigned int i = 0; i < N_THREADS; ++i) {
      const uint64_t offset = i * gamesPerThread;

      if (offset >= totalGames) {
        break;
      }

      const uint64_t count = min(gamesPerThread, totalGames - offset);

      cout << "Worker " << i << ": offset = " << offset << ", count = " << count
           << '\n';

      workers.push_back(
          async(launch::async, fetchGames, table, i, offset, count));
    }

    for (auto &worker : workers) {
      worker.get();
    }

    cout << "All games fetched successfully.\n";

  } catch (const exception &e) {
    cerr << "Error: " << e.what() << '\n';
    return 1;
  }

  return 0;
}
