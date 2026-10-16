"""Keep a game's WebSocket open while the synchronous inspector is running."""

import json
from threading import Event, Lock, Thread
from time import monotonic
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.sync.client import ClientConnection, connect


class SessionConnectionError(Exception):
    """A game session could not be connected or resumed."""


class SessionConnection:
    """Keep a session connected for the lifetime of a synchronous ``with`` block.

    Entering opens the socket and validates its first snapshot before starting
    a background listener; initial connection failures raise SessionConnectionError.
    The listener updates status/error and retries interrupted connections, but
    a normal server close ends the session. Exiting stops retries and closes
    the socket.

    ``initial_state`` is replaced on reconnect. Later messages are currently
    consumed without applying updates to the inspector's state.
    """

    def __init__(
        self,
        api_url: str,
        game_id: str,
        player_token: str,
        *,
        reconnect_timeout_seconds: float = 30,
        retry_interval_seconds: float = 1,
    ) -> None:
        url = urlsplit(api_url)
        if url.scheme not in {"http", "https"}:
            raise SessionConnectionError("The API URL must use HTTP or HTTPS")
        self.url = urlunsplit((
            "wss" if url.scheme == "https" else "ws",
            url.netloc,
            f"{url.path.rstrip('/')}/games/{game_id}/events",
            "",
            "",
        ))
        self.game_id = game_id
        self.player_token = player_token
        self.reconnect_timeout_seconds = reconnect_timeout_seconds
        self.retry_interval_seconds = retry_interval_seconds
        self.initial_state: dict[str, Any] = {}
        self.status = "Connecting"
        self.error: str | None = None
        self._stop = Event()
        self._lock = Lock()
        self._socket: ClientConnection | None = None
        self._thread: Thread | None = None

    def _open(self) -> tuple[ClientConnection, dict[str, Any]]:
        socket = connect(
            self.url,
            additional_headers={"Authorization": f"Bearer {self.player_token}"},
            open_timeout=3,
            close_timeout=1,
            ping_interval=10,
            ping_timeout=10,
        )
        try:
            message = json.loads(socket.recv(timeout=3))
            if (
                not isinstance(message, dict)
                or message.get("type") != "state"
                or not isinstance(message.get("state"), dict)
                or message["state"].get("game_id") != self.game_id
            ):
                raise ValueError("The API returned an unexpected session snapshot")
        except (OSError, TimeoutError, WebSocketException, ValueError):
            socket.close()
            raise
        return socket, message["state"]

    def __enter__(self) -> "SessionConnection":
        try:
            self._socket, self.initial_state = self._open()
        except (OSError, TimeoutError, WebSocketException, ValueError) as error:
            raise SessionConnectionError(f"Could not connect to the game session: {error}") from error
        self.status = "Connected"
        self._thread = Thread(target=self._listen, name="game-session", daemon=True)
        self._thread.start()
        return self

    def _listen(self) -> None:
        while not self._stop.is_set():
            try:
                self._socket.recv()
            except (OSError, WebSocketException) as error:
                if self._stop.is_set():
                    return
                if isinstance(error, ConnectionClosed) and error.rcvd is not None and error.rcvd.code == 1000:
                    self.error = "The game session was closed"
                    self.status = "Disconnected: session closed"
                    return
                if not self._reconnect():
                    return

    def _reconnect(self) -> bool:
        """Retry until stopped or the reconnect window elapses.

        HTTP 400/401/403/404 responses and invalid snapshots end retries early;
        transport failures and other rejected handshakes remain retryable.
        The deadline is checked between attempts, so an in-flight open may
        finish after the window has elapsed.
        """
        self.status = "Disconnected: reconnecting"
        deadline = monotonic() + self.reconnect_timeout_seconds
        while not self._stop.is_set():
            remaining = deadline - monotonic()
            if remaining <= 0 or self._stop.wait(min(self.retry_interval_seconds, remaining)):
                break
            try:
                socket, state = self._open()
            except InvalidStatus as error:
                if error.response.status_code in {400, 401, 403, 404}:
                    break
            except ValueError:
                break
            except (OSError, TimeoutError, WebSocketException):
                continue
            else:
                with self._lock:
                    if self._stop.is_set():
                        socket.close()
                        return False
                    self._socket = socket
                    self.initial_state = state
                self.status = "Connected"
                return True
        if not self._stop.is_set():
            self.error = "Could not reconnect to the game session"
            self.status = "Disconnected: session unavailable"
        return False

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            socket = self._socket
        if socket is not None:
            socket.close()
        if self._thread is not None:
            self._thread.join(timeout=7)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
