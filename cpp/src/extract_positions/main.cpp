
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

#include "CompletionGuard.hpp"
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

void fetchGames(const string &table, unsigned int workerId,
                const vector<MysqlGameId> &missingIds, size_t begin, size_t end,
                GameQueue &output, atomic<unsigned int> &activeProducers) {
  CompletionGuard completion(activeProducers);

  MySQLGameReader reader(table);

  uint64_t fetched = 0;
  uint64_t enqueued = 0;
  uint64_t batches = 0;

  for (size_t offset = begin; offset < end; offset += GAME_BATCH_SIZE) {
    const size_t count = min<size_t>(GAME_BATCH_SIZE, end - offset);

    vector<MysqlGameId> ids(missingIds.begin() + offset,
                            missingIds.begin() + offset + count);

    auto games = reader.fetchGamesByIds(ids);

    if (games.size() != ids.size()) {
      throw runtime_error("MySQL worker " + to_string(workerId) +
                          ": requested " + to_string(ids.size()) +
                          " games, fetched " + to_string(games.size()));
    }

    const size_t batchSize = games.size();

    if (!output.enqueue(std::move(games))) {
      throw runtime_error("Could not enqueue MySQL batch");
    }

    fetched += batchSize;
    enqueued += batchSize;
    ++batches;
  }

  cout << "MySQL worker " << workerId << " finished: fetched=" << fetched
       << ", enqueued=" << enqueued << ", batches=" << batches << '\n';
}

void processGames(unsigned int workerId, GameQueue &input,
                  ProcessedQueue &output, atomic<unsigned int> &activeProducers,
                  atomic<unsigned int> &activeProcessors) {
  CompletionGuard completion(activeProcessors);

  uint64_t processedCount = 0;
  uint64_t batchesReceived = 0;
  uint64_t batchesEnqueued = 0;

  while (true) {
    vector<Game> batch;

    if (input.try_dequeue(batch)) {
      vector<ProcessedGame> processedBatch;
      processedBatch.reserve(batch.size());

      transform(batch.begin(), batch.end(), back_inserter(processedBatch),
                processGame);

      if (processedBatch.size() != batch.size()) {
        throw runtime_error("Processor produced an incorrect batch size");
      }

      processedCount += processedBatch.size();
      ++batchesReceived;

      if (!output.enqueue(std::move(processedBatch))) {
        throw runtime_error("Could not enqueue processed batch");
      }

      ++batchesEnqueued;
      continue;
    }

    if (activeProducers.load(std::memory_order_acquire) == 0) {
      break;
    }

    this_thread::yield();
  }

  cout << "Processor " << workerId
       << " finished: received batches=" << batchesReceived
       << ", enqueued batches=" << batchesEnqueued
       << ", processed games=" << processedCount << '\n';
}

void writeGames(const string &databasePath, ProcessedQueue &input,
                atomic<unsigned int> &activeProcessors) {
  SQLiteDatabase database(databasePath);

  uint64_t insertedBatches = 0;
  uint64_t processedGames = 0;

  while (true) {
    vector<ProcessedGame> batch;

    if (input.try_dequeue(batch)) {
      processedGames += batch.size();

      database.insertBatch(batch);
      ++insertedBatches;

      cout << "SQLite writer committed batch " << insertedBatches << '\n';
      continue;
    }

    if (activeProcessors.load(std::memory_order_acquire) == 0) {
      break;
    }

    this_thread::yield();
  }

  cout << "SQLite writer finished: games=" << processedGames
       << ", batches=" << insertedBatches << '\n';
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

    // -------------------------------------------------
    // Synchronize MySQL IDs with SQLite.
    // -------------------------------------------------

    MySQLGameReader reader(table);
    const uint64_t totalGames = reader.countGames();

    cout << "Total MySQL games: " << totalGames << '\n';

    vector<MysqlGameId> missingIds;

    {
      SQLiteDatabase database(databasePath);

      database.prepareGameIdSync();

      uint64_t collected = 0;
      MysqlGameId lastId = 0;

      constexpr uint32_t ID_BATCH_SIZE = 10000;

      while (true) {
        auto ids = reader.fetchGameIds(lastId, ID_BATCH_SIZE);

        if (ids.empty()) {
          break;
        }

        database.insertMysqlGameIds(ids);

        collected += ids.size();
        lastId = ids.back();

        if (collected % 100000 == 0 || ids.size() < ID_BATCH_SIZE) {
          cout << "Collected MySQL IDs: " << collected << " / " << totalGames
               << '\n';
        }
      }

      // Remove SQLite games that no longer exist in MySQL.
      database.removeGamesMissingFromMysql();

      // Find games that need importing.
      missingIds = database.findMissingMysqlGames();
    }

    cout << "Games to import: " << missingIds.size() << '\n';

    if (missingIds.empty()) {
      cout << "SQLite is already synchronized.\n";
      return 0;
    }

    // -------------------------------------------------
    // Start the pipeline.
    // -------------------------------------------------

    const size_t gamesPerThread =
        (missingIds.size() + N_THREADS - 1) / N_THREADS;

    const unsigned int producerCount =
        static_cast<unsigned int>(min<size_t>(N_THREADS, missingIds.size()));

    atomic<unsigned int> activeProducers{producerCount};
    atomic<unsigned int> activeProcessors{processingThreads};

    auto writer = async(launch::async, writeGames, databasePath,
                        ref(processedQueue), ref(activeProcessors));

    vector<future<void>> processors;
    processors.reserve(processingThreads);

    for (unsigned int i = 0; i < processingThreads; ++i) {
      processors.push_back(async(launch::async, processGames, i, ref(gameQueue),
                                 ref(processedQueue), ref(activeProducers),
                                 ref(activeProcessors)));
    }

    vector<future<void>> producers;
    producers.reserve(producerCount);

    for (unsigned int i = 0; i < producerCount; ++i) {
      const size_t begin = i * gamesPerThread;
      const size_t end = min(begin + gamesPerThread, missingIds.size());

      producers.push_back(async(launch::async, fetchGames, table, i,
                                cref(missingIds), begin, end, ref(gameQueue),
                                ref(activeProducers)));
    }

    exception_ptr firstError;

    // -------------------------------------------------
    // Wait for MySQL producers.
    // -------------------------------------------------

    for (auto &producer : producers) {
      try {
        producer.get();
      } catch (...) {
        if (!firstError) {
          firstError = current_exception();
        }
      }
    }

    // -------------------------------------------------
    // Wait for processors.
    // They exit after all producers finish and the queue
    // has been drained.
    // -------------------------------------------------

    for (auto &processor : processors) {
      try {
        processor.get();
      } catch (...) {
        if (!firstError) {
          firstError = current_exception();
        }
      }
    }

    // -------------------------------------------------
    // Wait for SQLite writer.
    // It exits after all processors finish and its queue
    // has been drained.
    // -------------------------------------------------

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
