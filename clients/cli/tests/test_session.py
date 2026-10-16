"""Exercise CLI presence and reconnects using real WebSocket connections."""

import json
import unittest
from threading import Event, Thread
from time import monotonic
from unittest.mock import MagicMock, patch

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from anr_cli.main import play_ai, request_json
from anr_cli.session import SessionConnection, SessionConnectionError


class SessionConnectionTests(unittest.TestCase):
    def start_server(self, handler):
        server = serve(handler, "127.0.0.1", 0, close_timeout=1)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def shutdown():
            server.shutdown()
            thread.join(timeout=3)

        self.addCleanup(shutdown)
        return f"http://127.0.0.1:{server.socket.getsockname()[1]}"

    def send_snapshot(self, socket, game_id="game"):
        socket.send(json.dumps({"type": "state", "revision": 0, "state": {"game_id": game_id}}))

    def wait_for_close(self, socket):
        try:
            socket.recv()
        except ConnectionClosed:
            pass

    def wait_until(self, predicate):
        deadline = monotonic() + 2
        while not predicate() and monotonic() < deadline:
            Event().wait(0.01)
        self.assertTrue(predicate())

    def test_inspection_keeps_connection_open_and_exit_closes_it(self):
        closed = Event()
        headers = []

        def handler(socket):
            headers.append(socket.request.headers.get("Authorization"))
            self.send_snapshot(socket)
            self.wait_for_close(socket)
            closed.set()

        url = self.start_server(handler)
        with SessionConnection(url, "game", "private-token") as connection:
            self.assertEqual(connection.initial_state, {"game_id": "game"})
            self.assertEqual(connection.status, "Connected")
            self.assertFalse(closed.is_set())
            self.assertEqual(headers, ["Bearer private-token"])
        self.assertTrue(closed.wait(timeout=2))
        self.assertFalse(connection._thread.is_alive())
        self.assertIsNone(connection.error)

    def test_live_snapshots_refresh_state_and_old_http_responses_are_ignored(self):
        updated = Event()

        def handler(socket):
            self.send_snapshot(socket)
            socket.send(json.dumps({
                "type": "state", "revision": 1,
                "state": {"game_id": "game", "setup": {"pending_player": None}},
            }))
            updated.set()
            self.wait_for_close(socket)

        url = self.start_server(handler)
        with SessionConnection(url, "game", "private-token") as connection:
            self.assertTrue(updated.wait(timeout=2))
            self.wait_until(lambda: "setup" in connection.latest_state)
            current = connection.latest_state
            connection.accept_snapshot({"type": "state", "revision": 0, "state": {"game_id": "game"}})
            self.assertEqual(connection.latest_state, current)

    def test_transient_disconnect_reconnects_without_leaving_inspector(self):
        attempts = []
        resumed = Event()

        def handler(socket):
            attempts.append(socket.request.headers.get("Authorization"))
            self.send_snapshot(socket)
            if len(attempts) == 1:
                socket.close(code=1012, reason="Temporary interruption")
            else:
                resumed.set()
                self.wait_for_close(socket)

        url = self.start_server(handler)
        with SessionConnection(
            url, "game", "private-token", retry_interval_seconds=0.01,
        ) as connection:
            self.assertTrue(resumed.wait(timeout=2))
            self.wait_until(lambda: connection.status == "Connected")
            self.assertEqual(attempts, ["Bearer private-token"] * 2)
            self.assertIsNone(connection.error)

    def test_reconnect_stops_when_window_expires(self):
        attempts = []

        def handler(socket):
            attempts.append(True)
            if len(attempts) == 1:
                self.send_snapshot(socket)
            socket.close(code=1012, reason="Session unavailable")

        url = self.start_server(handler)
        with SessionConnection(
            url, "game", "private-token", reconnect_timeout_seconds=0.1,
            retry_interval_seconds=0.01,
        ) as connection:
            self.wait_until(lambda: connection.error is not None)
            self.assertEqual(connection.status, "Disconnected: session unavailable")
            self.assertGreaterEqual(len(attempts), 2)

    def test_invalid_snapshot_closes_connection_and_reports_error(self):
        closed = Event()

        def handler(socket):
            self.send_snapshot(socket, game_id="wrong-game")
            self.wait_for_close(socket)
            closed.set()

        url = self.start_server(handler)
        with self.assertRaises(SessionConnectionError):
            with SessionConnection(url, "game", "private-token"):
                self.fail("An invalid snapshot should not enter the inspector")
        self.assertTrue(closed.wait(timeout=2))


class CliSessionLifecycleTests(unittest.TestCase):
    def test_exit_and_errors_both_notify_api_that_player_left(self):
        for screen_error in (None, RuntimeError("Inspector failed")):
            with self.subTest(error=screen_error):
                connection = MagicMock()
                connection.initial_state = {"game_id": "game"}
                connection.status = "Connected"
                connection.error = None
                created = {
                    "initial_state": connection.initial_state,
                    "player_token": "private-token",
                    "reconnect_timeout_seconds": 30,
                }
                with (
                    patch("anr_cli.main.request_json", side_effect=[
                        [], [{"profile_id": "training", "name": "Training"}], created, None,
                    ]) as request,
                    patch("anr_cli.main.select_deck", return_value={}),
                    patch("anr_cli.main.choose", return_value=0),
                    patch("anr_cli.main.SessionConnection") as session,
                    patch("anr_cli.main.GameScreen") as screen,
                ):
                    session.return_value.__enter__.return_value = connection
                    screen.return_value.run.side_effect = screen_error
                    if screen_error is None:
                        play_ai("http://api")
                    else:
                        with self.assertRaises(RuntimeError):
                            play_ai("http://api")
                    session.return_value.__exit__.assert_called_once()
                    request.assert_called_with(
                        "http://api", "/games/game/leave", {}, player_token="private-token",
                    )

    def test_leave_accepts_empty_204_response_and_sends_private_token(self):
        with patch("anr_cli.main.urlopen") as open_url:
            response = open_url.return_value.__enter__.return_value
            response.status = 204
            self.assertIsNone(request_json(
                "http://api", "/games/game/leave", {}, player_token="private-token",
            ))
            response.read.assert_not_called()
            request = open_url.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer private-token")


if __name__ == "__main__":
    unittest.main()
