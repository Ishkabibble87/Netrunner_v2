"""Coordinate short-lived invitations for two external players in one process.

Host credentials authorize polling and cancellation; joining issues each seat its
own session token. Deck validation and authoritative setup remain in game_setup.
"""

from dataclasses import dataclass
from secrets import compare_digest, token_urlsafe
from threading import Lock
from time import monotonic
from uuid import UUID, uuid4

from application.game_setup import create_player_game, validate_player_deck
from application.sessions import SessionAccessDenied, SessionNotFound
from game.models import DeckSelection


@dataclass
class Lobby:
    code: str
    token: str
    host_id: UUID
    deck: DeckSelection
    expires_at: float
    game_id: UUID | None = None
    host_player_token: str | None = None


class LobbyStore:
    """Serialize joins so exactly one guest can claim an invitation."""

    def __init__(self, sessions, definitions, starter_decks, *, clock=monotonic):
        self.sessions = sessions
        self.definitions = definitions
        self.starter_decks = starter_decks
        self.clock = clock
        self.lobbies: dict[str, Lobby] = {}
        self.lock = Lock()

    def host(self, deck):
        validate_player_deck(deck, self.definitions, self.starter_decks)
        with self.lock:
            code = token_urlsafe(12)
            lobby = Lobby(code, token_urlsafe(32), uuid4(), deck, self.clock() + 600)
            self.lobbies[code] = lobby
            return lobby

    def _get(self, code):
        lobby = self.lobbies.get(code)
        if lobby is None or self.clock() >= lobby.expires_at:
            self.lobbies.pop(code, None)
            raise SessionNotFound("Invitation not found or expired")
        return lobby

    def _authorize(self, lobby, token):
        if not token.isascii() or not compare_digest(lobby.token, token):
            raise SessionAccessDenied("Invalid lobby token")

    def inspect(self, code, token):
        with self.lock:
            lobby = self._get(code)
            self._authorize(lobby, token)
            return lobby

    def join(self, code, deck):
        with self.lock:
            lobby = self._get(code)
            if lobby.game_id is not None:
                raise ValueError("Invitation has already been claimed")
            guest_id = uuid4()
            state = create_player_game(lobby.host_id, lobby.deck, guest_id, deck,
                                       self.definitions, self.starter_decks)
            session = self.sessions.create(state, "external", [lobby.host_id, guest_id])
            lobby.game_id = state.game_id
            lobby.host_player_token = session.player_tokens[lobby.host_id]
            return session, guest_id

    def cancel(self, code, token):
        with self.lock:
            lobby = self._get(code)
            self._authorize(lobby, token)
            del self.lobbies[code]
            if lobby.game_id is not None:
                return self.sessions.leave(lobby.game_id, lobby.host_player_token)
            return set()

    def remove_expired(self):
        with self.lock:
            self.lobbies = {code: lobby for code, lobby in self.lobbies.items()
                            if self.clock() < lobby.expires_at}
