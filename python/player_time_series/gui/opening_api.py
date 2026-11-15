import urllib.parse
from typing import Any, cast

import requests

from .opening_types import Game


class ChessApiClient:
    """Small HTTP client for the player and opening-game API."""

    PLAYER_SEARCH_URL = "https://api.bazaszachowa.smallhost.pl/players/"
    GAMES_URL = "https://api.bazaszachowa.smallhost.pl/games/opening/"

    @staticmethod
    def _get_json(url: str, timeout: int) -> Any:
        # Each request owns its Session; calls from GUI worker threads
        # therefore do not share mutable requests.Session state.
        with requests.Session() as session:
            session.headers.update({"User-Agent": "Bazaszachowa-Opening-Explorer/1.0"})
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()

    def search_players(self, name: str) -> list[str]:
        url = self.PLAYER_SEARCH_URL + urllib.parse.quote(name)
        data = self._get_json(url, timeout=20)
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, str)]

    def fetch_games(self, name: str, color: str) -> list[Game]:
        url = (
            self.GAMES_URL + urllib.parse.quote(name) + "/" + urllib.parse.quote(color)
        )
        data = self._get_json(url, timeout=120)
        if not isinstance(data, list):
            raise ValueError("Unexpected API response: expected a list.")
        if any(not isinstance(item, dict) for item in data):
            raise ValueError("Unexpected API response: each game must be an object.")

        # Field-level validation is kept in the domain layer so this client
        # remains responsible only for HTTP and top-level response shape.
        return cast(list[Game], data)
