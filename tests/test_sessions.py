"""Exercise expiry and player presence without waiting for real timeouts."""

import unittest
from pathlib import Path
from uuid import uuid4

from application.game_setup import create_ai_game
from application.sessions import SessionAccessDenied, SessionNotFound, SessionStore
from content.cards.loader import load_nsg_card_definitions
from content.opponents.loader import load_opponent_profiles
from game.models import PlayerRole


class SessionStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.catalog = load_nsg_card_definitions(root / "src/content/cards/system-gateway/cards.json")
        cls.profile = load_opponent_profiles(cls.catalog)["system-gateway-training"]

    def setUp(self):
        self.now = 100.0
        self.store = SessionStore(clock=lambda: self.now)
        self.player_id = uuid4()
        self.state = create_ai_game(
            player_id=self.player_id,
            decklist=self.profile.deck_selections[PlayerRole.RUNNER],
            opponent_profile=self.profile,
            card_definitions=self.catalog,
        )
        self.session = self.store.create(self.state, self.profile.profile_id, [self.player_id])
        self.game_id = self.state.game_id
        self.token = self.session.player_tokens[self.player_id]

    def test_unconnected_session_expires_at_deadline_without_waiting_for_sweep(self):
        self.now += 59
        self.store.get(self.game_id, self.token)
        self.now += 1
        with self.assertRaises(SessionNotFound):
            self.store.connect(self.game_id, self.token)

    def test_sweep_removes_unconnected_sessions_and_shutdown_clears_connected_ones(self):
        self.now += 60
        self.assertEqual(self.store.remove_expired(), 1)
        self.assertEqual(self.store.remove_expired(), 0)
        with self.assertRaises(SessionNotFound):
            self.store.get(self.game_id, self.token)

        self.session = self.store.create(self.state, self.profile.profile_id, [self.player_id])
        token = self.session.player_tokens[self.player_id]
        self.store.connect(self.game_id, token)
        self.store.clear()
        with self.assertRaises(SessionNotFound):
            self.store.get(self.game_id, token)

    def test_active_inspection_does_not_expire_and_disconnect_gets_thirty_seconds(self):
        _, _, connection_id = self.store.connect(self.game_id, self.token)
        self.now += 3600
        self.assertEqual(self.store.remove_expired(), 0)
        self.store.get(self.game_id, self.token)
        self.store.disconnect(self.game_id, connection_id)
        self.now += 29
        self.store.get(self.game_id, self.token)
        self.now += 1
        self.assertEqual(self.store.remove_expired(), 1)

    def test_reconnect_and_late_old_disconnect_do_not_expire_new_connection(self):
        _, _, old = self.store.connect(self.game_id, self.token)
        self.store.disconnect(self.game_id, old)
        self.now += 29
        _, _, new = self.store.connect(self.game_id, self.token)
        self.store.disconnect(self.game_id, old)
        self.now += 100
        self.assertEqual(self.store.remove_expired(), 0)
        self.store.disconnect(self.game_id, new)
        self.now += 30
        self.assertEqual(self.store.remove_expired(), 1)

    def test_multiple_connections_only_start_expiry_after_last_disconnect(self):
        _, _, first = self.store.connect(self.game_id, self.token)
        _, _, second = self.store.connect(self.game_id, self.token)
        self.store.disconnect(self.game_id, first)
        self.now += 100
        self.assertEqual(self.store.remove_expired(), 0)
        self.store.disconnect(self.game_id, second)
        self.now += 20
        self.store.disconnect(self.game_id, first)
        self.now += 10
        self.assertEqual(self.store.remove_expired(), 1)

    def test_explicit_leave_revokes_all_connections_and_deletes_ai_game(self):
        _, _, first = self.store.connect(self.game_id, self.token)
        _, _, second = self.store.connect(self.game_id, self.token)
        self.assertEqual(self.store.leave(self.game_id, self.token), {first, second})
        self.store.disconnect(self.game_id, first)
        with self.assertRaises(SessionNotFound):
            self.store.get(self.game_id, self.token)

    def test_other_human_keeps_game_alive_after_disconnect_or_leave(self):
        self.store.clear()
        session = self.store.create(
            self.state, self.profile.profile_id,
            [self.state.corp.player_id, self.state.runner.player_id],
        )
        first_token = session.player_tokens[self.state.corp.player_id]
        second_token = session.player_tokens[self.state.runner.player_id]
        _, _, first = self.store.connect(self.game_id, first_token)
        self.store.connect(self.game_id, second_token)
        self.store.disconnect(self.game_id, first)
        self.now += 100
        self.assertEqual(self.store.remove_expired(), 0)
        self.store.leave(self.game_id, first_token)
        self.store.get(self.game_id, second_token)
        with self.assertRaises(SessionAccessDenied):
            self.store.connect(self.game_id, first_token)
        self.store.leave(self.game_id, second_token)
        with self.assertRaises(SessionNotFound):
            self.store.get(self.game_id, second_token)

    def test_wrong_token_and_ai_player_id_cannot_access_session(self):
        self.assertNotIn(self.state.corp.player_id, self.session.player_tokens)
        for token in (str(self.state.corp.player_id), "wrong", "é"):
            with self.subTest(token=token), self.assertRaises(SessionAccessDenied):
                self.store.connect(self.game_id, token)


if __name__ == "__main__":
    unittest.main()
