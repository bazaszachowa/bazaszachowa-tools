#ifndef MYSQL_CONNECTION_H
#define MYSQL_CONNECTION_H

#include <mysql/mysql.h>

#include <array>
#include <cstddef>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>

namespace mysql {

using mysql_is_null_t = std::remove_pointer_t<decltype(MYSQL_BIND{}.is_null)>;

struct ConnectionDeleter {
  void operator()(MYSQL *connection) const noexcept {
    if (connection) {
      mysql_close(connection);
    }
  }
};

struct ResultDeleter {
  void operator()(MYSQL_RES *result) const noexcept {
    if (result) {
      mysql_free_result(result);
    }
  }
};

struct StatementDeleter {
  void operator()(MYSQL_STMT *statement) const noexcept {
    if (statement) {
      mysql_stmt_close(statement);
    }
  }
};

using ConnectionPtr = std::unique_ptr<MYSQL, ConnectionDeleter>;
using ResultPtr = std::unique_ptr<MYSQL_RES, ResultDeleter>;
using StatementPtr = std::unique_ptr<MYSQL_STMT, StatementDeleter>;

class Result {
public:
  explicit Result(MYSQL_RES *result) noexcept : result_(result) {}

  Result(const Result &) = delete;
  Result &operator=(const Result &) = delete;

  Result(Result &&) noexcept = default;
  Result &operator=(Result &&) noexcept = default;

  ~Result() = default;

  MYSQL_ROW fetchRow() noexcept { return mysql_fetch_row(result_.get()); }

  unsigned long *fetchLengths() noexcept {
    return mysql_fetch_lengths(result_.get());
  }

  unsigned int fieldCount() const noexcept {
    return mysql_num_fields(result_.get());
  }

  explicit operator bool() const noexcept { return static_cast<bool>(result_); }

private:
  ResultPtr result_;
};

template <std::size_t N> class BoundResult {
public:
  static constexpr std::size_t BUFFER_SIZE = 4096;

  BoundResult() {
    for (std::size_t i = 0; i < N; ++i) {
      buffers_[i] = std::make_unique<char[]>(BUFFER_SIZE);

      lengths_[i] = 0;
      is_null_[i] = static_cast<mysql_is_null_t>(0);

      bind_[i].buffer_type = MYSQL_TYPE_STRING;
      bind_[i].buffer = buffers_[i].get();
      bind_[i].buffer_length = BUFFER_SIZE;
      bind_[i].length = &lengths_[i];
      bind_[i].is_null = &is_null_[i];
    }
  }

  BoundResult(const BoundResult &) = delete;
  BoundResult &operator=(const BoundResult &) = delete;

  BoundResult(BoundResult &&) = delete;
  BoundResult &operator=(BoundResult &&) = delete;

  ~BoundResult() = default;

  MYSQL_BIND *data() noexcept { return bind_.data(); }

  const MYSQL_BIND *data() const noexcept { return bind_.data(); }

  std::string get(std::size_t index) const {
    if (is_null_[index]) {
      return {};
    }

    return std::string(buffers_[index].get(), lengths_[index]);
  }

private:
  std::array<MYSQL_BIND, N> bind_{};
  std::array<std::unique_ptr<char[]>, N> buffers_{};
  std::array<unsigned long, N> lengths_{};
  std::array<mysql_is_null_t, N> is_null_{};
};

class Statement {
public:
  explicit Statement(MYSQL_STMT *statement) noexcept : statement_(statement) {}

  Statement(const Statement &) = delete;
  Statement &operator=(const Statement &) = delete;

  Statement(Statement &&) noexcept = default;
  Statement &operator=(Statement &&) noexcept = default;

  ~Statement() = default;

  void prepare(std::string_view sql) {
    if (mysql_stmt_prepare(statement_.get(), sql.data(), sql.size())) {
      throwError("MySQL statement prepare failed");
    }
  }

  void bindParam(MYSQL_BIND *params) {
    if (mysql_stmt_bind_param(statement_.get(), params)) {
      throwError("MySQL statement bind param failed");
    }
  }

  void execute() {
    if (mysql_stmt_execute(statement_.get())) {
      throwError("MySQL statement execute failed");
    }
  }

  Result resultMetadata() {
    MYSQL_RES *result = mysql_stmt_result_metadata(statement_.get());

    if (!result) {
      throwError("MySQL statement result metadata failed");
    }

    return Result(result);
  }

  void bindResult(MYSQL_BIND *result) {
    if (mysql_stmt_bind_result(statement_.get(), result)) {
      throwError("MySQL statement bind result failed");
    }
  }

  int fetch() { return mysql_stmt_fetch(statement_.get()); }

  void freeResult() {
    if (mysql_stmt_free_result(statement_.get())) {
      throwError("MySQL statement free result failed");
    }
  }

private:
  StatementPtr statement_;

  [[noreturn]]
  void throwError(std::string_view prefix) const {
    throw std::runtime_error(std::string(prefix) + ": " +
                             mysql_stmt_error(statement_.get()));
  }
};

class Connection {
public:
  Connection(const char *host, const char *user, const char *password,
             const char *database, unsigned int port = 0) {

    ConnectionPtr connection(mysql_init(nullptr));

    if (!connection) {
      throw std::runtime_error("mysql_init() failed");
    }

    if (!mysql_real_connect(connection.get(), host, user, password, database,
                            port, nullptr, 0)) {
      throw std::runtime_error("MySQL connection failed: " +
                               std::string(mysql_error(connection.get())));
    }

    connection_ = std::move(connection);
  }

  Connection(const Connection &) = delete;
  Connection &operator=(const Connection &) = delete;

  Connection(Connection &&) noexcept = default;
  Connection &operator=(Connection &&) noexcept = default;

  ~Connection() = default;

  void query(std::string_view sql) {
    std::string query_string(sql);

    if (mysql_query(connection_.get(), query_string.c_str())) {
      throwError("MySQL query failed");
    }
  }

  Result storeResult() {
    MYSQL_RES *result = mysql_store_result(connection_.get());

    if (!result) {
      throwError("mysql_store_result() failed");
    }

    return Result(result);
  }

  Result queryResult(std::string_view sql) {
    query(sql);
    return storeResult();
  }

  Statement statement(std::string_view sql) {
    MYSQL_STMT *raw_statement = mysql_stmt_init(connection_.get());

    if (!raw_statement) {
      throwError("mysql_stmt_init() failed");
    }

    Statement statement(raw_statement);
    statement.prepare(sql);

    return statement;
  }

private:
  ConnectionPtr connection_;

  [[noreturn]]
  void throwError(std::string_view prefix) const {
    throw std::runtime_error(std::string(prefix) + ": " +
                             mysql_error(connection_.get()));
  }
};

} // namespace mysql

#endif // !MYSQL_CONNECTION_H
