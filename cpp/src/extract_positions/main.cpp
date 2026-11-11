
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

#include <concurrentqueue/blockingconcurrentqueue.h>

#include "mysql/MySQLGameReader.hpp"
#include "processing/GameProcessor.hpp"
#include "sqlite/SQLiteDatabase.hpp"
#include "types/Types.hpp"

using namespace std;
using namespace chessdb;

using GameQueue = moodycamel::BlockingConcurrentQueue<vector<Game>>;
using ProcessedQueue =
    moodycamel::BlockingConcurrentQueue<vector<ProcessedGame>>;

const unsigned int detectedThreads = thread::hardware_concurrency();
const unsigned int N_THREADS = max(2u, detectedThreads) - 1;

void fetchGames(const string &table, unsigned int workerId, uint64_t offset,
                uint64_t count, GameQueue &output) {
  uint64_t fetched = 0;

  MySQLGameReader reader(table);

  while (fetched < count) {
    const uint32_t limit =
        static_cast<uint32_t>(min<uint64_t>(GAME_BATCH_SIZE, count - fetched));

    auto games = reader.fetchBatch(offset + fetched, limit);

    if (games.empty()) {
      break;
    }

    const size_t batchSize = games.size();

    if (!output.enqueue(std::move(games))) {
      throw runtime_error("Could not enqueue MySQL batch");
    }

    fetched += batchSize;

    cout << "MySQL worker " << workerId << ": fetched " << fetched << " / "
         << count << '\n';
  }

  cout << "MySQL worker " << workerId << " finished. Fetched " << fetched
       << " games.\n";
}

void processGames(unsigned int workerId, GameQueue &input,
                  ProcessedQueue &output) {
  uint64_t processedCount = 0;

  while (true) {
    vector<Game> batch;
    input.wait_dequeue(batch);

    // Empty batch signals that no more games will arrive.
    if (batch.empty()) {
      break;
    }

    vector<ProcessedGame> processedBatch;
    processedBatch.reserve(batch.size());

    for (auto &game : batch) {
      processedBatch.push_back(processGame(std::move(game)));
    }

    processedCount += processedBatch.size();

    if (!output.enqueue(std::move(processedBatch))) {
      throw runtime_error("Could not enqueue processed batch");
    }
  }

  cout << "Processor " << workerId << " finished. Processed " << processedCount
       << " games.\n";
}

void writeGames(const string &databasePath, ProcessedQueue &input) {
  SQLiteDatabase database(databasePath);

  uint64_t insertedBatches = 0;
  uint64_t processedGames = 0;

  while (true) {
    vector<ProcessedGame> batch;
    input.wait_dequeue(batch);

    // Empty batch signals that processing is finished.
    if (batch.empty()) {
      break;
    }

    processedGames += batch.size();

    cout << "SQLite writer received batch: " << batch.size() << " games\n";

    database.insertBatch(batch);

    ++insertedBatches;

    cout << "SQLite writer committed batch " << insertedBatches << '\n';
  }

  cout << "SQLite writer finished. Processed " << processedGames << " games in "
       << insertedBatches << " batches.\n";
}

int main(int argc, const char *argv[]) {
  const unordered_set<string> allowedTables{"all_games", "poland_games"};

  const string table = argc > 1 ? argv[1] : "all_games";
  const string databasePath = argc > 2 ? argv[2] : "chess.sqlite";

  if (!allowedTables.contains(table)) {
    cerr << "Invalid table name: " << table << '\n';
    return 1;
  }

  GameQueue gameQueue;
  ProcessedQueue processedQueue;

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

    // Signal that no more MySQL batches will arrive.
    for (unsigned int i = 0; i < processingThreads; ++i) {
      gameQueue.enqueue(vector<Game>{});
    }

    // Wait for processing workers.
    for (auto &processor : processors) {
      try {
        processor.get();
      } catch (...) {
        if (!firstError) {
          firstError = current_exception();
        }
      }
    }

    // Signal that processing is finished.
    processedQueue.enqueue(vector<ProcessedGame>{});

    // Wait for SQLite writer.
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
    cerr << "Import failed: " << e.what() << '\n';
    return 1;
  }

  return 0;
}
