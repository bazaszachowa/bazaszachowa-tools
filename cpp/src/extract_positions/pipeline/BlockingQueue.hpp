
#pragma once

#include <condition_variable>
#include <cstddef>
#include <deque>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <utility>

namespace chessdb {

template <typename T> class BlockingQueue {
public:
  explicit BlockingQueue(std::size_t capacity) : capacity_(capacity) {
    if (capacity == 0) {
      throw std::invalid_argument("Queue capacity must be positive");
    }
  }

  void push(T value) {
    std::unique_lock lock(mutex_);

    notFull_.wait(lock,
                  [this] { return closed_ || queue_.size() < capacity_; });

    if (closed_) {
      throw std::runtime_error("Push to closed queue");
    }

    queue_.push_back(std::move(value));

    lock.unlock();
    notEmpty_.notify_one();
  }

  std::optional<T> pop() {
    std::unique_lock lock(mutex_);

    notEmpty_.wait(lock, [this] { return closed_ || !queue_.empty(); });

    if (queue_.empty()) {
      return std::nullopt;
    }

    T value = std::move(queue_.front());
    queue_.pop_front();

    lock.unlock();
    notFull_.notify_one();

    return value;
  }

  void close() {
    {
      std::lock_guard lock(mutex_);
      closed_ = true;
    }

    notEmpty_.notify_all();
    notFull_.notify_all();
  }

private:
  const std::size_t capacity_;

  std::deque<T> queue_;
  std::mutex mutex_;
  std::condition_variable notEmpty_;
  std::condition_variable notFull_;

  bool closed_ = false;
};

} // namespace chessdb
