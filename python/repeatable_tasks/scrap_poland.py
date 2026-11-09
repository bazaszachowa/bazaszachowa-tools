import html
import os
import re
import tempfile
import time
import traceback
import zipfile
from collections.abc import Mapping
from datetime import datetime
from io import StringIO
from typing import Any
from urllib.parse import quote_plus

import chess.pgn
import requests as std_requests
from pymongo import MongoClient

from .. import settings
from ..chess_scrappers.parser import lichess_download, scrap_livechess
from . import PGN_DIR

try:
    from curl_cffi import requests

    session: std_requests.Session | requests.Session[Any] = requests.Session(
        impersonate="chrome"
    )

except ImportError:
    import requests  # type: ignore[no-redef]

    session = requests.Session()

os.makedirs(PGN_DIR, exist_ok=True)

MONGO_URI = (
    f"mongodb://{quote_plus(settings.SETTINGS['mongo']['user'])}:"
    f"{quote_plus(settings.SETTINGS['mongo']['password'])}@"
    f"{settings.SETTINGS['mongo']['host']}:27017/{settings.SETTINGS['mongo']['database']}"
)

MONGO_COLLECTION = "poland_tournaments"

client: MongoClient[Mapping[str, Any]] = MongoClient(MONGO_URI)
db = client[settings.SETTINGS["mongo"]["database"]]
coll = db[MONGO_COLLECTION]

CR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "http://www.cr-pzszach.pl/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl-PL,pl;q=0.9",
    }
)
session.get("https://www.chessmanager.com/")
session.get("https://www.chessmanager.com/pl-pl/tournaments/")
session.get("https://www.chessmanager.com/robots.txt")
session.get("https://www.chessmanager.com/sitemap.xml")
session.get("https://www.chessmanager.com/en-us/tournaments/sitemap.xml")


def download_swsx(tour_id: int | str) -> bytes | None:
    urls = [
        f"http://www.cr-pzszach.pl/ew/ew/swsswd/{tour_id}.swsx",
        f"http://www.cr-pzszach.pl/ew/ew/swsswd/{tour_id}.swdx",
    ]

    for url in urls:
        try:
            res = requests.get(url, headers=CR_HEADERS, timeout=20)
            if res.ok and len(res.content) > 100:
                return res.content

        except std_requests.RequestException as e:
            print("Download error:", url, e)

    return None


def sanitize_xml(file_path: str) -> None:
    try:
        with open(file_path, encoding="utf-8", errors="replace") as file:
            content = file.read()

        content = html.unescape(content)
        content = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]", "", content)

        with open(file_path, "w", encoding="utf-8") as file:
            file.write(content)

    except Exception as e:
        print("XML sanitize error:", file_path, e)
        raise


def extract_tournament_links(xml_file: str) -> set[str]:
    links = set()

    try:
        sanitize_xml(xml_file)

        with open(xml_file, encoding="utf-8", errors="ignore") as file:
            xml_text = file.read()

        for match in re.findall(r'https?://[^\s"\'<>]+', xml_text):
            url = match.rstrip(".,);]")

            if "lichess" in url or "livechesscloud" in url:
                links.add(url)

    except Exception as e:
        print("XML parse error:", e)

    return links


def pzszach_extract_pgns(tour_id: int | str) -> str:
    archive = download_swsx(tour_id)

    if not archive:
        return ""

    pgn = ""

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "tournament.zip")

        with open(zip_path, "wb") as file:
            file.write(archive)

        try:
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(tmp)

        except zipfile.BadZipFile:
            print("Invalid ZIP:", tour_id)
            return ""

        for root, _, files in os.walk(tmp):
            for filename in files:
                if filename.lower().endswith(".pgn"):
                    path = os.path.join(root, filename)

                    with open(path, encoding="utf-8", errors="ignore") as file:
                        pgn += file.read()
                        pgn += "\n"

                elif filename.lower() == "tournament.xml":
                    xml_path = os.path.join(root, filename)

                    for link in extract_tournament_links(xml_path):
                        try:
                            if "lichess" in link:
                                pgn += lichess_download(link)

                            elif "livechesscloud" in link:
                                pgn += scrap_livechess(link)

                        except Exception as e:
                            print(tour_id)
                            print("External PGN error:", link, e)
                            if "lichess" in link or "livechesscloud" in link:
                                raise e

    return pgn


def chessmanager_scrap_pgns(link: str) -> str:
    time.sleep(2)
    res = session.get(
        link,
        timeout=20,
        headers={
            "Referer": "https://www.chessmanager.com/",
        },
    )
    if res.status_code in [403, 404]:
        return ""
    res.raise_for_status()
    html = res.text

    links = set()

    for match in re.findall(r'https?://[^\s"\'<>]+', html):
        match = match.rstrip(".,);]")

        if "lichess" in match or "livechesscloud" in match:
            links.add(match)

    pgn = ""

    for url in links:
        try:
            if "lichess" in url:
                pgn += lichess_download(url)

            elif "livechesscloud" in url:
                pgn += scrap_livechess(url)

        except Exception as e:
            print("Chessmanager PGN error:", url, e)
            if "lichess" in url or "livechesscloud" in url:
                raise e

    return pgn


def scrap_poland() -> None:
    docs = coll.find({"scanned": {"$ne": True}})

    with open(PGN_DIR / "poland_games.pgn", "a", encoding="utf-8") as file:
        for doc in docs:
            tour_id = doc["_id"]
            url = doc["url"]
            start_date = doc["start"]
            end_date = doc["end"]

            try:
                if url:
                    if "chessmanager.com" in url:
                        pgn = chessmanager_scrap_pgns(url)
                    else:
                        pgn = pzszach_extract_pgns(tour_id)

                    if pgn:
                        pgn_io = StringIO(pgn)
                        games = []
                        while True:
                            game = chess.pgn.read_game(pgn_io)
                            if game is None:
                                break

                            games.append(game)
                        pgn = ""
                        for game in games:
                            try:
                                game_date = datetime.strptime(
                                    game.headers["Date"], "%Y.%m.%d"
                                )
                                if game_date.date() < start_date.date():
                                    raise ValueError
                                if game_date.date() > end_date.date():
                                    raise ValueError
                            except Exception:
                                game.headers["Date"] = start_date.strftime("%Y.%m.%d")
                            pgn += str(game).strip() + "\n\n"

                        file.write(pgn)
                        file.flush()

                coll.update_one({"_id": tour_id}, {"$set": {"scanned": True}})

            except Exception as e:
                print("Tournament error:", tour_id, e)
                print(traceback.format_exc())


if __name__ == "__main__":
    scrap_poland()
