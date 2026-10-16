"""Process-local game sessions; run the API with a single worker."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from secrets import compare_digest, token_urlsafe
from threading import Lock
from time import monotonic
from uuid import UUID, uuid4

from game.commands import ChooseMulligan
from game.models import GameState, MulliganDecision, PlayerRole
from game.setup import choose_mulligan


def advance_ai_setup(state: GameState, ai_player_ids: set[UUID]) -> GameState:
    """The initial scripted opponent keeps every opening hand when prompted."""
    while state.setup.pending_player is not None:
        player = state.corp if state.setup.pending_player is PlayerRole.CORP else state.runner
        if player.player_id not in ai_player_ids:
            break
        state = choose_mulligan(state, player.player_id, ChooseMulligan(MulliganDecision.KEEP))
    return state


class SessionNotFound(Exception):
    """The session does not exist or has expired."""


class SessionAccessDenied(Exception):
    """The supplied token does not belong to a human player in this session."""


@dataclass
class GameSession:
    """Mutable session record holding the current immutable game snapshot.

    Connection IDs identify individual sockets and map to their human players.
    ``expires_at`` uses the store's clock; None disables expiry while connected.
    """

    state: GameState
    opponent_profile_id: str
    player_tokens: dict[UUID, str]
    expires_at: float | None
    ai_player_ids: set[UUID] = field(default_factory=set)
    revision: int = 0
    connections: dict[UUID, UUID] = field(default_factory=dict)


class SessionStore:
    """Coordinate authenticated decisions and connection lifetimes in one process.

    New sessions expire unless a socket connects before the initial deadline.
    Any connected socket keeps the session alive; losing the last socket starts
    the reconnect deadline. Authentication enforces expiry between cleanup runs.
    Explicitly leaving revokes a player's token, and the last player leaving
    removes the session immediately.

    The lock protects store operations, including replacing the game snapshot
    and advancing its revision after decisions. Callers receive the mutable
    GameSession itself; subsequent access to it is outside the store's lock.
    """

    def __init__(
        self,
        *,
        connect_timeout_seconds: float = 60,
        reconnect_timeout_seconds: float = 30,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if connect_timeout_seconds <= 0 or reconnect_timeout_seconds <= 0:
            raise ValueError("Session timeouts must be positive")
        self.connect_timeout_seconds = connect_timeout_seconds
        self.reconnect_timeout_seconds = reconnect_timeout_seconds
        self._clock = clock
        self._sessions: dict[UUID, GameSession] = {}
        # HTTP handlers run in worker threads; sockets and cleanup run on the event loop.
        self._lock = Lock()

    def create(
        self,
        state: GameState,
        opponent_profile_id: str,
        human_player_ids: Iterable[UUID],
    ) -> GameSession:
        humans = set(human_player_ids)
        if not humans or not humans <= {state.corp.player_id, state.runner.player_id}:
            raise ValueError("A session needs human players belonging to its game")
        with self._lock:
            if state.game_id in self._sessions:
                raise ValueError("A session already exists for this game")
            ai_player_ids = {state.corp.player_id, state.runner.player_id} - humans
            session = GameSession(
                state=advance_ai_setup(state, ai_player_ids),
                opponent_profile_id=opponent_profile_id,
                player_tokens={player_id: token_urlsafe(32) for player_id in humans},
                expires_at=self._clock() + self.connect_timeout_seconds,
                ai_player_ids=ai_player_ids,
            )
            self._sessions[state.game_id] = session
            return session

    def _authenticated(self, game_id: UUID, token: str) -> tuple[GameSession, UUID]:
        """Caller holds the lock; expired sessions cannot be revived between sweeps."""
        session = self._sessions.get(game_id)
        if session is None:
            raise SessionNotFound("Game session not found")
        if session.expires_at is not None and self._clock() >= session.expires_at:
            del self._sessions[game_id]
            raise SessionNotFound("Game session has expired")
        for player_id, expected_token in session.player_tokens.items():
            if token.isascii() and compare_digest(token, expected_token):
                return session, player_id
        raise SessionAccessDenied("Invalid player session token")

    def get(self, game_id: UUID, token: str) -> tuple[GameSession, UUID]:
        with self._lock:
            return self._authenticated(game_id, token)

    def submit(
        self, game_id: UUID, token: str, command: ChooseMulligan,
    ) -> tuple[GameSession, UUID]:
        """Commit a legal human decision and any immediately following AI choice."""
        with self._lock:
            session, player_id = self._authenticated(game_id, token)
            state = choose_mulligan(session.state, player_id, command)
            session.state = advance_ai_setup(state, session.ai_player_ids)
            session.revision += 1
            return session, player_id

    def connect(self, game_id: UUID, token: str) -> tuple[GameSession, UUID, UUID]:
        with self._lock:
            session, player_id = self._authenticated(game_id, token)
            connection_id = uuid4()
            session.connections[connection_id] = player_id
            session.expires_at = None
            return session, player_id, connection_id

    def disconnect(self, game_id: UUID, connection_id: UUID) -> None:
        with self._lock:
            session = self._sessions.get(game_id)
            if session is None or connection_id not in session.connections:
                return
            del session.connections[connection_id]
            if not session.connections:
                session.expires_at = self._clock() + self.reconnect_timeout_seconds

    def leave(self, game_id: UUID, token: str) -> set[UUID]:
        """Revoke this player's token and return their sockets for the API to close."""
        with self._lock:
            session, player_id = self._authenticated(game_id, token)
            del session.player_tokens[player_id]
            connection_ids = {
                key for key, owner in session.connections.items() if owner == player_id
            }
            for connection_id in connection_ids:
                del session.connections[connection_id]
            if not session.player_tokens:
                del self._sessions[game_id]
            elif not session.connections and session.expires_at is None:
                session.expires_at = self._clock() + self.reconnect_timeout_seconds
            return connection_ids

    def remove_expired(self) -> int:
        with self._lock:
            now = self._clock()
            expired = [
                game_id
                for game_id, session in self._sessions.items()
                if session.expires_at is not None and now >= session.expires_at
            ]
            for game_id in expired:
                del self._sessions[game_id]
            return len(expired)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
