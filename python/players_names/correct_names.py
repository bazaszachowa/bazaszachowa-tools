import re
import sys
import traceback
from typing import cast

import mysql.connector

from ..settings import SETTINGS

PlayerRow = tuple[int, str]
mydb = mysql.connector.connect(**SETTINGS["mysql"])


def get_update_sql(table: str, old: int, new: int) -> str:
    return f"""UPDATE {table}_games SET whiteId = {new} WHERE whiteID = {old};
UPDATE {table}_games SET blackID = {new} WHERE blackID = {old};
DELETE FROM {table}_players WHERE id = {old};
"""


if __name__ == "__main__":
    try:
        TABLE = sys.argv[1]
    except IndexError:
        # TABLE = "all"
        TABLE = "poland"

    reinit = True
    # reinit = False

    curs = mydb.cursor()
    if reinit:
        curs.execute(rf"""UPDATE `{TABLE}_games` as g
        INNER JOIN {TABLE}_players as p1
        ON g.WhiteID = p1.id
        INNER JOIN {TABLE}_players as p2
        ON REGEXP_REPLACE(p1.fullname, "\\s*\\(.*\\)\\s*", "") = p2.fullname
        SET g.WhiteID = p2.id
        WHERE p1.fullname LIKE "%(%)%" """)
        curs.execute(rf"""UPDATE `{TABLE}_games` as g
        INNER JOIN {TABLE}_players as p1
        ON g.BlackID = p1.id
        INNER JOIN {TABLE}_players as p2
        ON REGEXP_REPLACE(p1.fullname, "\\s*\\(.*\\)\\s*", "") = p2.fullname
        SET g.BlackID = p2.id
        WHERE p1.fullname LIKE "%(%)%" """)
        mydb.commit()

        queries = f"""DELETE FROM {TABLE}_players;
        INSERT IGNORE INTO {TABLE}_players(id, fullname)
        SELECT pom.id, fullname FROM (SELECT WhiteID as id FROM {TABLE}_games
        UNION DISTINCT
        SELECT BlackID as id FROM {TABLE}_games) as pom
        INNER JOIN players
        on pom.id = players.id
        """

        for query in queries.split(";"):
            curs.execute(query)
        mydb.commit()

    sql = f"""
    SELECT id,fullname as fullname FROM `{TABLE}_players`
    ORDER BY fullname
    """

    curs.execute(sql)
    rows = cast(list[PlayerRow], curs.fetchall())
    with open(f"correct_{TABLE}.sql", "w") as output:
        for i in range(len(rows) - 1):
            try:
                current_id, current_player = rows[i]
                if len(current_player) > 4 and (
                    re.search(r" [A-Za-z]{1,2}\.*$", current_player)
                    or "," not in current_player
                ):
                    current_player = current_player.replace('"', r"\"")
                    current_player = current_player.replace("*", r"\*")
                    current_player = current_player.replace(")", r"\)")
                    current_player = current_player.replace("[", r"\[")
                    current_player = current_player.replace("]", r"\]")
                    current_player = current_player.replace("?", r"\?")
                    current_player = current_player.replace("(", r"\(")
                    current_player = current_player.replace("+", r"\+")
                    current_player = re.sub(r"\.*\s+$", "", current_player)
                    current_player = re.sub(r"\.+\s*$", "", current_player)
                    current_player = re.sub(r"\s+", " ", current_player)

                    next_id, next_player = rows[i + 1]

                    if (
                        re.search(f"^{current_player}", next_player, re.IGNORECASE)
                        and not re.search(" [A-Za-z]{1,2}$", next_player, re.IGNORECASE)
                        and "," in next_player
                        and (
                            current_player.count(",") == next_player.count(",")
                            or next_player.startswith(f"{current_player},")
                        )
                    ):
                        if i < len(rows) - 2:
                            if (
                                not re.search(
                                    f"^{rows[i][1]}", rows[i + 2][1], re.IGNORECASE
                                )
                                and not re.search(
                                    f"^{current_player}", rows[i + 2][1], re.IGNORECASE
                                )
                                and (
                                    re.search(r"\d+$", rows[i][1], re.IGNORECASE)
                                    or "," in next_player
                                )
                            ):
                                output.write(get_update_sql(TABLE, current_id, next_id))
                        else:
                            output.write(get_update_sql(TABLE, current_id, next_id))

                if i > 0:
                    prev_id, prev_player = rows[i - 1]
                    prev_player = prev_player.strip()
                    if (
                        prev_player
                        == re.sub(r" *\(.*\)| *[0-9\W]+$", "", current_player).strip()
                        or prev_player == re.sub(" *,", ",", current_player).strip()
                        or prev_player
                        == re.sub(r" [a-zA-Z]{0,2}\.?$", "", current_player).strip()
                    ) and "," in next_player:
                        output.write(get_update_sql(TABLE, current_id, prev_id))

            except Exception as e:
                print(e)
                print(current_player, "\t\t", rows[i + 1][1])
                traceback.print_exc()
