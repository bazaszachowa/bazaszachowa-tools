cmake_minimum_required(VERSION 3.10)

project(chess_db LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(SOURCE_DIR src)
set(CMAKE_INTERPROCEDURAL_OPTIMIZATION TRUE)

if(MSVC)
    add_compile_options(/W3)
else()
    add_compile_options(-Wall -Wextra -pthread)
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
    message(STATUS "Configuring for FreeBSD")

    include_directories(
        /usr/local/include
        /usr/local/include/mysql
    )

    link_directories(
        /usr/local/lib/mysql
        /usr/local/lib
    )

    set(MYSQL_LIB mysqlclient)

elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(STATUS "Configuring for Linux")

    if(EXISTS "/etc/alpine-release")
        message(STATUS "Configuring for Alpine")

        include_directories(
            /usr/include
            /usr/include/mysql
        )

        link_directories(
            /usr/lib
        )

        set(MYSQL_LIB mysqlclient)

    else()
        include_directories(
            /usr/include
            /usr/include/mysql
        )

        link_directories(
            /usr/lib/x86_64-linux-gnu
        )

        set(MYSQL_LIB mysqlclient)
    endif()
else()
    message(FATAL_ERROR "Unsupported OS")
endif()

include_directories(
    ${CMAKE_CURRENT_SOURCE_DIR}
)

find_package(SQLite3 REQUIRED)

add_executable(pgn2sql ${SOURCE_DIR}/pgn2sql.cpp)
add_executable(lichess_classify ${SOURCE_DIR}/lichess_classify.cpp)
add_executable(remote2pgn ${SOURCE_DIR}/remote2pgn.cpp)
add_executable(prepare_pgn ${SOURCE_DIR}/prepare_pgn.cpp)
add_executable(extract_positions ${SOURCE_DIR}/extract_positions/main.cpp)

target_link_libraries(lichess_classify PRIVATE ${MYSQL_LIB} pthread)
target_link_libraries(remote2pgn PRIVATE ${MYSQL_LIB} pthread)
# target_link_libraries(extract_positions PRIVATE ${MYSQL_LIB} pthread)
target_link_libraries(extract_positions PRIVATE ${MYSQL_LIB} SQLite::SQLite3)

target_sources(extract_positions PRIVATE
    ${SOURCE_DIR}/extract_positions/main.cpp
    ${SOURCE_DIR}/extract_positions/sqlite/SQLiteDatabase.cpp
    ${SOURCE_DIR}/extract_positions/mysql/MySQLGameReader.cpp
)
