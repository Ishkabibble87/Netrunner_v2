"""Check transport privacy and exceptional rules beyond the growing game path."""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from application.game_setup import create_ai_game
from application.sessions import SessionStore
from game.commands import GainCredit
from game.engine import apply_command, legal_actions_for, pending_decision
from game.models import CardZone, PlayerRole, TurnPhase, ZoneLocation


class TurnDecisionTests(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore()
        self.app = create_app(session_store=self.store)
        self.client = self.enterContext(TestClient(self.app))
        profile = self.app.state.opponent_profiles["system-gateway-training"]
        state = create_ai_game(player_id=uuid4(), decklist=profile.deck_selections[PlayerRole.CORP],
                               opponent_profile=profile, card_definitions=self.app.state.card_definitions)
        self.session = self.store.create(state, profile.profile_id,
                                         [state.corp.player_id, state.runner.player_id])
        self.path = f"/games/{state.game_id}"
        self.headers = {p.role: {"Authorization": f"Bearer {self.session.player_tokens[p.player_id]}"}
                        for p in (state.corp, state.runner)}
        for role in PlayerRole:
            response = self.submit(role, {"type": "choose_mulligan", "decision": "keep"})
            self.assertEqual(response.status_code, 200, response.text)

    def submit(self, role, command):
        return self.client.post(f"{self.path}/commands", headers=self.headers[role], json=command)

    def test_both_views_follow_discard_and_next_turn_without_private_choices(self):
        with self.client.websocket_connect(f"{self.path}/events", headers=self.headers[PlayerRole.CORP]) as corp_socket:
            with self.client.websocket_connect(f"{self.path}/events", headers=self.headers[PlayerRole.RUNNER]) as runner_socket:
                corp_socket.receive_json()
                runner_socket.receive_json()
                for _ in range(3):
                    response = self.submit(PlayerRole.CORP, {"type": "gain_credit"})
                    self.assertEqual(response.status_code, 200, response.text)
                    corp = corp_socket.receive_json()
                    runner = runner_socket.receive_json()
                    self.assertEqual(response.json(), corp)
                    self.assertEqual(corp["revision"], runner["revision"])
                    self.assertEqual(corp["state"]["decision"], runner["state"]["decision"])
                self.assertEqual(corp["state"]["decision"], {
                    "type": "discard_to_hand_limit", "player": "corp", "cards_to_discard": 1})
                self.assertEqual(runner["state"]["legal_actions"], [])
                selection, = corp["state"]["legal_actions"]
                selected = selection["eligible_card_instance_ids"][0]
                self.assertNotIn(selected, str(runner))
                revision = self.session.revision
                invalid = [(PlayerRole.RUNNER, [selected]),
                           (PlayerRole.CORP, [selected, selected]),
                           (PlayerRole.CORP, [str(uuid4())]),
                           (PlayerRole.CORP, [str(self.session.state.corp.identity_card_instance_id)])]
                before = self.session.state.model_dump()
                for role, ids in invalid:
                    rejected = self.submit(role, {"type": "discard_cards", "card_instance_ids": ids})
                    self.assertEqual(rejected.status_code, 409, rejected.text)
                    self.assertEqual(self.session.revision, revision)
                    self.assertEqual(self.session.state.model_dump(), before)
                response = self.submit(PlayerRole.CORP, {"type": "discard_cards", "card_instance_ids": [selected]})
                self.assertEqual(response.status_code, 200, response.text)
                corp = corp_socket.receive_json()["state"]
                runner = runner_socket.receive_json()["state"]
                self.assertEqual(corp["turn"], {"number": 2, "active_player": "runner", "phase": "action", "cannot_score": False})
                self.assertEqual(corp["legal_actions"], [])
                self.assertEqual(len(runner["you"]["hand"]), 5)
                self.assertEqual(runner["you"]["clicks_remaining"], 4)
                self.assertEqual(runner["opponent"]["discard_count"], 1)
                self.assertEqual(runner["opponent"]["discard_pile"], [])
                self.assertNotIn(selected, str(runner))
                for _ in range(4):
                    response = self.submit(PlayerRole.RUNNER, {"type": "draw_card"})
                    self.assertEqual(response.status_code, 200, response.text)
                    corp = corp_socket.receive_json()["state"]
                    runner = runner_socket.receive_json()["state"]
                selection, = runner["legal_actions"]
                self.assertEqual(selection["required_count"], 4)
                ids = selection["eligible_card_instance_ids"][:4]
                response = self.submit(PlayerRole.RUNNER, {"type": "discard_cards", "card_instance_ids": ids})
                self.assertEqual(response.status_code, 200, response.text)
                corp = corp_socket.receive_json()["state"]
                runner = runner_socket.receive_json()["state"]
                self.assertEqual(corp["turn"]["number"], 3)
                self.assertEqual(len(corp["you"]["hand"]), 6)
                self.assertEqual(corp["you"]["clicks_remaining"], 3)
                self.assertEqual({c["instance_id"] for c in corp["opponent"]["discard_pile"]}, set(ids))
                self.assertEqual(corp["decision"], runner["decision"])

    def test_negative_runner_hand_limit_flatlines_at_discard(self):
        state = self.session.state.model_copy(deep=True)
        state.turn.active_player = PlayerRole.RUNNER
        state.runner.clicks_remaining = 1
        state.runner.core_damage = 6
        result = apply_command(state, state.runner.player_id, GainCredit())
        self.assertEqual(result.winner, PlayerRole.CORP)
        self.assertEqual(result.turn.phase, TurnPhase.DISCARD)
        self.assertEqual(legal_actions_for(result, state.runner.player_id), ())
        self.assertIsNone(pending_decision(result))
        self.assertIsNone(state.winner)

    def test_empty_mandatory_draw_ends_game_after_runner_turn(self):
        state = self.session.state.model_copy(deep=True)
        state.turn.active_player = PlayerRole.RUNNER
        state.runner.clicks_remaining = 1
        for card in state.card_instances.values():
            if card.location.zone is CardZone.DECK and card.owner_id == state.corp.player_id:
                card.face_up = False
                card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=state.corp.player_id)
        result = apply_command(state, state.runner.player_id, GainCredit())
        self.assertEqual(result.winner, PlayerRole.RUNNER)
        self.assertEqual(result.turn.active_player, PlayerRole.CORP)
