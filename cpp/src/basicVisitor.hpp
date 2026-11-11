#ifndef BASIC_VISITOR_H
#define BASIC_VISITOR_H

#include <chess-library/include/chess.hpp>
#include <iostream>
#include <string_view>

using namespace chess;
using namespace std;

bool validateDate(string_view date) {
  if (date.size() < 4) {
    return false;
  }

  for (size_t i = 0; i < 4; ++i) {
    if (!std::isdigit(date[i])) {
      return false;
    }
  }

  return true;
}

class BasicVisitor : public pgn::Visitor {
public:
  ~BasicVisitor() = default;

  void startPgn() override { board.setFen(constants::STARTPOS); }

  void header(std::string_view key, std::string_view value) override {
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
    } else if (key == "ECO") {
      eco = value.substr(0, 3);
    } else if (key == "Result") {
      result = value;
    } else if (key == "Date") {
      parseDateValue(value);
    } else if (key == "EventDate" &&
               (!validateDate(value) || !year.has_value() ||
                year.value() == 1899)) {
      parseDateValue(value);
    } else if (key == "UCIDate" && (!validateDate(value) || !year.has_value() ||
                                    year.value() == 1899)) {
      parseDateValue(value);
    }
  }

  void startMoves() override {
    // Intentionally left empty: No action required at the start of moves.
  }

  void move(std::string_view move, std::string_view comment) override {
    // Intentionally left empty: No action required per move.
  }

private:
  Board board;
  string event = "?";
  string site = "?";
  optional<int> year;
  optional<int> month;
  optional<int> day;
  string round = "?";
  string eco = "?";
  string white = "N, N";
  string black = "N, N";
  optional<int> whiteElo;
  optional<int> blackElo;
  string result = "?";

  void parseDateValue(string_view value) {
    size_t first_pos = value.find('.');
    if (first_pos == std::string_view::npos) {
      return;
    }

    try {
      year = stoi(string(value.substr(0, first_pos)));
    } catch (const std::invalid_argument &) {
      return;
    }

    size_t second_pos = value.find('.', first_pos + 1);
    if (second_pos == std::string_view::npos) {
      return;
    }

    try {
      month =
          stoi(string(value.substr(first_pos + 1, second_pos - first_pos - 1)));
    } catch (const std::invalid_argument &) {
      return;
    }

    size_t third_pos = value.find('.', second_pos + 1);
    if (third_pos != std::string_view::npos) {
      try {
        day = stoi(
            string(value.substr(second_pos + 1, third_pos - second_pos - 1)));
      } catch (const std::invalid_argument &) {
        // Ignore invalid day
      }
    }
  }
};

#endif
