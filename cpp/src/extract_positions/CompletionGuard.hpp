#include <atomic>

struct CompletionGuard {
  std::atomic<unsigned int> &active;

  explicit CompletionGuard(std::atomic<unsigned int> &count) : active(count) {}

  ~CompletionGuard() { active.fetch_sub(1, std::memory_order::seq_cst); }
};
