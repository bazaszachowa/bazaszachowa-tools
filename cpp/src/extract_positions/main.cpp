
#include <algorithm>
#include <cstdint>
#include <exception>
#include <future>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_set>
#include <utility>
#include <vector>

#include "./pipeline/BlockingQueue.hpp"
#include "mysql/MySQLGameReader.hpp"
#include "processing/GameProcessor.hpp"
#include "sqlite/SQLiteDatabase.hpp"
#include "types/Types.hpp"

using namespace std;
using namespace chessdb;

const unsigned int detectedThreads = thread::hardware_concurrency();
const unsigned int N_THREADS = max(2u, detectedThreads) - 1;

constexpr size_t QUEUE_CAPACITY = 8;

void fetchGames(const string &table, unsigned int workerId, uint64_t offset,
                uint64_t count, BlockingQueue<vector<Game>> &output) {
  try {
    MySQLGameReader reader(table);

    uint64_t fetched = 0;

    while (fetched < count) {
      const uint32_t limit = static_cast<uint32_t>(
          min<uint64_t>(GAME_BATCH_SIZE, count - fetched));

      auto games = reader.fetchBatch(offset + fetched, limit);

      if (games.empty()) {
        break;
      }

      const size_t batchSize = games.size();

      output.push(std::move(games));

      fetched += batchSize;

      cout << "MySQL worker " << workerId << ": fetched " << fetched << " / "
           << count << '\n';
    }

    cout << "MySQL worker " << workerId << " finished. Fetched " << fetched
         << " games.\n";

  } catch (...) {
    output.close();
    throw;
  }
}

void processGames(unsigned int workerId, BlockingQueue<vector<Game>> &input,
                  BlockingQueue<vector<ProcessedGame>> &output) {
  try {
    uint64_t processedCount = 0;

    while (auto batch = input.pop()) {
      vector<ProcessedGame> processedBatch;
      processedBatch.reserve(batch->size());

      for (auto &game : *batch) {
        processedBatch.push_back(processGame(std::move(game)));
      }

      processedCount += processedBatch.size();

      output.push(std::move(processedBatch));
    }

    cout << "Processor " << workerId << " finished. Processed "
         << processedCount << " games.\n";

  } catch (...) {
    input.close();
    output.close();
    throw;
  }
}

void writeGames(const string &databasePath,
                BlockingQueue<vector<ProcessedGame>> &input) {
  try {
    SQLiteDatabase database(databasePath);

    uint64_t insertedBatches = 0;
    uint64_t processedGames = 0;

    while (auto batch = input.pop()) {
      processedGames += batch->size();

      cout << "SQLite writer received batch: " << batch->size() << " games\n";

      database.insertBatch(*batch);

      ++insertedBatches;

      cout << "SQLite writer committed batch " << insertedBatches << '\n';
    }

    cout << "SQLite writer finished. Processed " << processedGames
         << " games in " << insertedBatches << " batches.\n";

  } catch (...) {
    input.close();
    throw;
  }
}

int main(int argc, const char *argv[]) {
  const unordered_set<string> allowedTables{"all_games", "poland_games"};

  const string table = argc > 1 ? argv[1] : "all_games";
  const string databasePath = argc > 2 ? argv[2] : "chess.sqlite";

  if (!allowedTables.contains(table)) {
    cerr << "Invalid table name: " << table << '\n';
    return 1;
  }

  BlockingQueue<vector<Game>> gameQueue(QUEUE_CAPACITY);
  BlockingQueue<vector<ProcessedGame>> processedQueue(QUEUE_CAPACITY);

  try {
    cout << "MySQL table: " << table << '\n';
    cout << "SQLite database: " << databasePath << '\n';
    cout << "MySQL workers: " << N_THREADS << '\n';

    const unsigned int processingThreads = max(N_THREADS - 1, 1u);

    cout << "Processing workers: " << processingThreads << '\n';

    MySQLGameReader countReader(table);
    const uint64_t totalGames = countReader.countGames();

    cout << "Total MySQL games: " << totalGames << '\n';

    if (totalGames == 0) {
      cout << "No games to import.\n";
      return 0;
    }

    // Start the single SQLite writer.
    auto writer =
        async(launch::async, writeGames, databasePath, ref(processedQueue));

    // Start processing workers.
    vector<future<void>> processors;
    processors.reserve(processingThreads);

    for (unsigned int i = 0; i < processingThreads; ++i) {
      processors.push_back(async(launch::async, processGames, i, ref(gameQueue),
                                 ref(processedQueue)));
    }

    // Start MySQL producers.
    vector<future<void>> producers;
    producers.reserve(N_THREADS);

    const uint64_t gamesPerThread = (totalGames + N_THREADS - 1) / N_THREADS;

    for (unsigned int i = 0; i < N_THREADS; ++i) {
      const uint64_t offset = i * gamesPerThread;

      if (offset >= totalGames) {
        break;
      }

      const uint64_t count = min(gamesPerThread, totalGames - offset);

      producers.push_back(async(launch::async, fetchGames, table, i, offset,
                                count, ref(gameQueue)));
    }

    exception_ptr firstError;

    // Wait for all MySQL producers.
    for (auto &producer : producers) {
      try {
        producer.get();
      } catch (...) {
        if (!firstError) {
          firstError = current_exception();
        }
      }
    }

    // Signal: no more MySQL batches will arrive.
    gameQueue.close();

    // Wait for processing workers to drain the queue.
    for (auto &processor : processors) {
      try {
        processor.get();
      } catch (...) {
        if (!firstError) {
          firstError = current_exception();
        }
      }
    }

    // Signal: no more processed batches will arrive.
    processedQueue.close();

    // Wait for SQLite writer to finish draining its queue.
    try {
      writer.get();
    } catch (...) {
      if (!firstError) {
        firstError = current_exception();
      }
    }

    if (firstError) {
      rethrow_exception(firstError);
    }

    cout << "Import pipeline finished successfully.\n";

  } catch (const exception &e) {
    gameQueue.close();
    processedQueue.close();

    cerr << "Import failed: " << e.what() << '\n';
    return 1;
  }

  return 0;
}
