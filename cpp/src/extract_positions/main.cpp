#include "sqlite/SQLiteDatabase.hpp"

#include <iostream>

using namespace chessdb;

int main() {
  try {
    SQLiteDatabase writerDb("chess.sqlite");
    SQLiteDatabase readerDb("chess.sqlite", true);

    std::cout << "SQLite initialized successfully\n";
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }

  return 0;
}
