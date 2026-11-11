#include <algorithm>
#include <format>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <regex>
#include <sstream>
#include <stdexcept>

#include <chess-library/include/chess.hpp>

using namespace chess;
using namespace std;

bool validateDate(string_view date) {
  return date.size() >= 4 &&
         std::all_of(date.begin(), date.begin() + 4,
                     [](unsigned char c) { return std::isdigit(c); });
}

class MyVisitor : public pgn::Visitor {
public:
  MyVisitor(ofstream &outFile, const string &tableName)
      : outFile(outFile), tableName(tableName) {
    cout << endl;
  }

  ~MyVisitor() = default;

  void startPgn() override {
    board.setFen(constants::STARTPOS);
    movesStream.str("");
    movesStream.clear();
  }

  void header(string_view key, string_view value) override {
    if (key == "Event") {
      event = value;
    } else if (key == "Site") {
      site = value;
    } else if (key == "Round") {
      round = value;
    } else if (key == "White") {
      white = value;
    } else if (key == "Black") {
      black = value;
    } else if (key == "WhiteElo") {
      whiteElo = stoi(string(value));
    } else if (key == "BlackElo") {
      blackElo = stoi(string(value));
    } else if (key == "Result") {
      result = value;
    } else if (key == "Date") {
      parseDateValue(value);
    } else if (key == "EventDate" && validateDate(value) &&
               (!year.has_value() || year.value() == 1899)) {
      parseDateValue(value);
    } else if (key == "UTCDate" && validateDate(value) &&
               (!year.has_value() || year.value() == 1899)) {
      parseDateValue(value);
    }
  }

  void startMoves() override {
    // Intentionally left empty: No action required at the start of moves.
  }

  void move(string_view move, string_view comment) override {
    try {
      const Move m = uci::parseSan(board, move);

      int src = m.from().index();
      int dest = m.to().index();

      if (m.typeOf() == Move::CASTLING) {
        switch (dest) {
        case Square(Square::underlying::SQ_A1).index():
        case Square(Square::underlying::SQ_A8).index():
          dest += 2;
          break;
        case Square(Square::underlying::SQ_H1).index():
        case Square(Square::underlying::SQ_H8).index():
          dest--;
          break;
        }
      }

      PieceType piece = PieceType::NONE;

      if (move.find("=") != string::npos) {
        piece = m.promotionType();
      }

      uint16_t packed = (static_cast<uint16_t>(src) << 10) |
                        (static_cast<uint16_t>(dest) << 4) |
                        (static_cast<uint16_t>(piece) & 0x07);

      movesStream << static_cast<char>((packed >> 8) & 0xFF)
                  << static_cast<char>(packed & 0xFF);
      board.makeMove(m);
    } catch (const std::invalid_argument &e) {
      cerr << "Error in move processing: " << e.what() << endl;
    } catch (const std::out_of_range &e) {
      cerr << "Error in move processing: " << e.what() << endl;
    }
  }

  void endPgn() override {
    outFile << format(
        "INSERT IGNORE INTO {} (moves_blob, Event, Site, Year, Month, Day, "
        "Round, White, Black, Result, WhiteElo, BlackElo) VALUES(",
        tableName);

    outFile << "0x";
    for (unsigned char c : movesStream.str()) {
      outFile << format("{:02x}", static_cast<int>(c));
    }

    outFile << format(
        ", \"{}\", \"{}\", {}, {}, {}, \"{}\", \"{}\", \"{}\", \"{}\", {}, "
        "{});\n",
        event, site, year.has_value() ? to_string(year.value()) : "NULL",
        month.has_value() ? to_string(month.value()) : "NULL",
        day.has_value() ? to_string(day.value()) : "NULL", round, white, black,
        result, whiteElo.has_value() ? to_string(whiteElo.value()) : "NULL",
        blackElo.has_value() ? to_string(blackElo.value()) : "NULL");

    cout << "\rProcessed " << ++counter << " games";
  }

private:
  ofstream &outFile;
  string tableName;
  Board board;
  string event = "?";
  string site = "?";
  optional<int> year;
  optional<int> month;
  optional<int> day;
  string round = "?";
  string white = "N, N";
  string black = "N, N";
  optional<int> whiteElo;
  optional<int> blackElo;
  string result = "*";
  ostringstream movesStream;
  int counter = 0;

  void parseDateValue(string_view value) {
    regex dateRegex(R"((\d+)(?:[^\d]+(\d+)(?:[^\d]+(\d+))?)?)");
    smatch matches;
    string valueStr(value);

    if (regex_search(valueStr, matches, dateRegex)) {
      try {
        year = stoi(matches[1].str());
        if (matches.size() > 2 && !matches[2].str().empty()) {
          month = stoi(matches[2].str());
        }

        if (matches.size() > 3 && !matches[3].str().empty()) {
          day = stoi(matches[3].str());
        }
      } catch (const std::invalid_argument &e) {
        cerr << "Invalid date format: " << e.what() << endl;
      } catch (const std::out_of_range &e) {
        cerr << "Date out of range: " << e.what() << endl;
      }
    }
  }
};

int main(int argc, const char *argv[]) {
  string input_file;
  string target_table;

  if (argc > 1) {
    input_file = argv[1];
  } else {
    cout << "Podaj nazwę pliku PGN: ";
    cin >> input_file;
    cout << endl;
  }

  if (argc > 2) {
    target_table = argv[2];
  } else {
    cout << "Podaj nazwę tabeli: ";
    cin >> target_table;
    cout << endl;
  }

  ofstream outFile("insert.sql");
  auto vis = make_unique<MyVisitor>(outFile, target_table);

  ifstream file_stream(input_file);

  try {
    pgn::StreamParser parser(file_stream);

    const auto err = parser.readGames(*vis);

    if (err) {
      cerr << "PGN parse error: " << err.message() << '\n';
      return 1;
    }
  } catch (const std::exception &e) {
    cerr << "Exception: " << e.what() << '\n';
    return 1;
  }

  cout << endl;
  return 0;
}
