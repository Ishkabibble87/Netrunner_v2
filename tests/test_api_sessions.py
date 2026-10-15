"""Verify session presence, private snapshots, and cleanup through the API."""

import unittest
from threading import Event
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app
from application.sessions import SessionNotFound, SessionStore


class SessionApiTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.store = SessionStore(clock=lambda: self.now)
        self.client = self.enterContext(TestClient(create_app(
            session_store=self.store, cleanup_interval_seconds=0.01,
        )))
        deck = self.client.get("/preconstructed-decks").json()[0]["decklist"]
        response = self.client.post("/games", json={
            "player_id": str(uuid4()),
            "decklist": deck,
            "opponent_profile_id": "system-gateway-training",
        })
        self.assertEqual(response.status_code, 201)
        self.created = response.json()
        self.game_id = self.created["initial_state"]["game_id"]
        self.token = self.created["player_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.path = f"/games/{self.game_id}"

    def test_snapshot_and_websocket_only_reveal_own_hand(self):
        response = self.client.get(f"{self.path}/state", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.created["initial_state"])
        self.assertEqual(self.created["reconnect_timeout_seconds"], 30)
        with self.client.websocket_connect(f"{self.path}/events", headers=self.headers) as socket:
            message = socket.receive_json()
            self.assertEqual(message["type"], "state")
            self.assertEqual(message["revision"], 0)
            self.assertEqual(len(message["state"]["you"]["hand"]), 5)
            self.assertNotIn("hand", message["state"]["opponent"])
            self.assertNotIn("player_token", message["state"])
            self.now += 3600
            self.assertEqual(self.store.remove_expired(), 0)

    def test_reconnect_restores_same_game_and_old_disconnect_cannot_remove_it(self):
        with self.client.websocket_connect(f"{self.path}/events", headers=self.headers) as old:
            old.receive_json()
            with self.client.websocket_connect(f"{self.path}/events", headers=self.headers) as new:
                self.assertEqual(new.receive_json()["state"], self.created["initial_state"])
        self.now += 29
        with self.client.websocket_connect(f"{self.path}/events", headers=self.headers) as resumed:
            self.assertEqual(resumed.receive_json()["state"], self.created["initial_state"])
            self.now += 3600
            self.assertEqual(self.client.get(f"{self.path}/state", headers=self.headers).status_code, 200)

    def test_disconnect_expires_after_grace_period(self):
        with self.client.websocket_connect(f"{self.path}/events", headers=self.headers) as socket:
            socket.receive_json()
        self.now += 30
        self.assertEqual(self.client.get(f"{self.path}/state", headers=self.headers).status_code, 404)
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(f"{self.path}/events", headers=self.headers):
                pass

    def test_leave_deletes_immediately_and_closes_existing_connections(self):
        with self.client.websocket_connect(f"{self.path}/events", headers=self.headers) as socket:
            socket.receive_json()
            response = self.client.post(f"{self.path}/leave", headers=self.headers)
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response.content, b"")
            with self.assertRaises(WebSocketDisconnect) as closed:
                socket.receive_json()
            self.assertEqual(closed.exception.code, 1000)
        self.assertEqual(self.client.get(f"{self.path}/state", headers=self.headers).status_code, 404)

    def test_player_token_is_required_for_reads_leave_and_websocket(self):
        for headers, expected in (({}, 401), ({"Authorization": "Bearer wrong"}, 403)):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get(f"{self.path}/state", headers=headers).status_code, expected)
                self.assertEqual(self.client.post(f"{self.path}/leave", headers=headers).status_code, expected)
                with self.assertRaises(WebSocketDisconnect):
                    with self.client.websocket_connect(f"{self.path}/events", headers=headers):
                        pass
        self.assertEqual(self.client.get(f"{self.path}/state", headers=self.headers).status_code, 200)

    def test_periodic_cleanup_removes_game_that_never_connected(self):
        removed = Event()
        sweep = self.store.remove_expired

        def observe_cleanup():
            count = sweep()
            if count:
                removed.set()
            return count

        with patch.object(self.store, "remove_expired", side_effect=observe_cleanup):
            self.now += 60
            self.assertTrue(removed.wait(timeout=2), "The lifespan cleanup task did not expire the session")
        with self.assertRaises(SessionNotFound):
            self.store.get(UUID(self.game_id), self.token)


if __name__ == "__main__":
    unittest.main()
