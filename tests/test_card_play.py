"""Cover card-specific boundaries and HTTP/WebSocket card-play publication."""

import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from application.game_setup import create_ai_game
from application.sessions import SessionStore
from game.commands import PlayCard
from game.engine import apply_command, legal_actions_for
from game.models import CardZone, PlayerRole, ZoneLocation
from game.setup import IllegalCommand


class CardPlayTests(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore()
        self.app = create_app(session_store=self.store)
        self.cards = self.app.state.card_definitions
        self.client = self.enterContext(TestClient(self.app))
        profile = self.app.state.opponent_profiles["system-gateway-training"]
        class OpeningOrder:
            def shuffle(self, cards):
                rank = {"30075": 0, "30064": 1, "30030": 0, "30020": 1, "30021": 2}
                cards.sort(key=lambda card_id: rank.get(card_id, 3))
        with patch("game.setup.random.SystemRandom", return_value=OpeningOrder()):
            state = create_ai_game(player_id=uuid4(), decklist=profile.deck_selections[PlayerRole.CORP],
                                   opponent_profile=profile, card_definitions=self.cards)
        self.session = self.store.create(state, profile.profile_id, [state.corp.player_id, state.runner.player_id])
        self.path = f"/games/{state.game_id}"
        self.headers = {p.role: {"Authorization": f"Bearer {self.session.player_tokens[p.player_id]}"}
                        for p in (state.corp, state.runner)}
        for role in PlayerRole:
            response = self.client.post(f"{self.path}/commands", headers=self.headers[role],
                                        json={"type": "choose_mulligan", "decision": "keep"})
            self.assertEqual(response.status_code, 200, response.text)

    def instance(self, state, definition_id):
        return next(card for card in state.card_instances.values() if card.definition_id == definition_id
                    and card.location.zone is CardZone.HAND)

    def runner_state(self):
        state = self.session.state.model_copy(deep=True)
        state.turn.active_player = PlayerRole.RUNNER
        state.runner.clicks_remaining = 4
        # Find specific physical copies even when their opening hand was full of Sure Gambles.
        for code in ("30020", "30021"):
            card = next(c for c in state.card_instances.values() if c.definition_id == code)
            if card.location.zone is CardZone.DECK:
                card.location = ZoneLocation(zone=CardZone.HAND, player_id=state.runner.player_id)
        deck = sorted((c for c in state.card_instances.values() if c.owner_id == state.runner.player_id
                       and c.location.zone is CardZone.DECK), key=lambda c:c.location.deck_position)
        for position, card in enumerate(deck, 1):
            card.location = ZoneLocation(zone=CardZone.DECK, player_id=state.runner.player_id, deck_position=position)
        return state

    def test_card_command_is_private_until_play_and_broadcasts_public_discard(self):
        state = self.session.state
        card = self.instance(state, "30075")
        with self.client.websocket_connect(f"{self.path}/events", headers=self.headers[PlayerRole.CORP]) as corp_socket:
            with self.client.websocket_connect(f"{self.path}/events", headers=self.headers[PlayerRole.RUNNER]) as runner_socket:
                corp = corp_socket.receive_json()
                runner = runner_socket.receive_json()
                command = {"type": "play_card", "card_instance_id": str(card.instance_id), "server_id": None}
                self.assertIn(command, corp["state"]["legal_actions"])
                self.assertNotIn(str(card.instance_id), str(runner))
                response = self.client.post(f"{self.path}/commands", headers=self.headers[PlayerRole.CORP], json=command)
                self.assertEqual(response.status_code, 200, response.text)
                corp = corp_socket.receive_json()
                runner = runner_socket.receive_json()
                self.assertEqual(response.json(), corp)
                self.assertEqual(corp["revision"], runner["revision"])
                self.assertEqual(corp["state"]["you"]["credits"], 9)
                self.assertEqual(corp["state"]["you"]["clicks_remaining"], 2)
                self.assertEqual(runner["state"]["opponent"]["discard_pile"][0]["instance_id"], str(card.instance_id))
                self.assertEqual(runner["state"]["legal_actions"], [])
                revision = self.session.revision
                rejected = self.client.post(f"{self.path}/commands", headers=self.headers[PlayerRole.CORP], json=command)
                self.assertEqual(rejected.status_code, 409)
                self.assertEqual(self.session.revision, revision)

    def test_affordability_and_ownership_rejections_preserve_state(self):
        state = self.session.state
        hedge = self.instance(state, "30075")
        government = self.instance(state, "30064")
        cases = [(state, state.corp.player_id, government.instance_id),
                 (state, state.runner.player_id, hedge.instance_id),
                 (state, state.corp.player_id, state.corp.identity_card_instance_id),
                 (state, state.corp.player_id, uuid4())]
        poor = state.model_copy(deep=True)
        poor.corp.credits = 4
        cases.append((poor, poor.corp.player_id, hedge.instance_id))
        unsupported = next(c for c in state.card_instances.values()
                           if c.owner_id == state.corp.player_id and c.definition_id == "30040")
        changed = state.model_copy(deep=True)
        changed.card_instances[unsupported.instance_id].definition_id = "30012"  # Tread Lightly remains unsupported.
        changed.card_instances[unsupported.instance_id].location = ZoneLocation(zone=CardZone.HAND, player_id=state.corp.player_id)
        remaining = sorted((c for c in changed.card_instances.values()
                            if c.owner_id == state.corp.player_id and c.location.zone is CardZone.DECK),
                           key=lambda c:c.location.deck_position)
        for position, card in enumerate(remaining, 1):
            card.location = ZoneLocation(zone=CardZone.DECK, player_id=state.corp.player_id, deck_position=position)
        changed = type(state).model_validate(changed.model_dump())
        # The rejection occurs before any copied state is committed.
        cases.append((changed, state.corp.player_id, unsupported.instance_id))
        for before, player_id, instance_id in cases:
            snapshot = before.model_dump()
            with self.assertRaises(IllegalCommand):
                apply_command(before, player_id, PlayCard(instance_id), self.cards)
            self.assertEqual(before.model_dump(), snapshot)
            self.assertNotIn(PlayCard(instance_id), legal_actions_for(before, player_id, self.cards))

    def test_runner_credit_cards_and_conditional_click_loss(self):
        for code, clicks, expected_credits, expected_clicks in (
                ("30030", 4, 9, 3), ("30020", 4, 9, 2), ("30020", 1, 9, 0)):
            with self.subTest(card=code, clicks=clicks):
                state = self.runner_state()
                state.runner.clicks_remaining = clicks
                card = self.instance(state, code)
                result = apply_command(state, state.runner.player_id, PlayCard(card.instance_id), self.cards)
                self.assertEqual(result.runner.credits, expected_credits)
                self.assertEqual(result.runner.clicks_remaining, expected_clicks)
                self.assertEqual(result.card_instances[card.instance_id].location.zone, CardZone.DISCARD_PILE)
                self.assertTrue(result.card_instances[card.instance_id].face_up)

    def test_vrcation_resolves_draw_before_discard_decision_and_allows_short_stack(self):
        for clicks, available in ((4, 20), (1, 20), (1, 2), (1, 0)):
            with self.subTest(clicks=clicks, available=available):
                state = self.runner_state()
                state.runner.clicks_remaining = clicks
                card = self.instance(state, "30021")
                deck = sorted((c for c in state.card_instances.values() if c.owner_id == state.runner.player_id
                               and c.location.zone is CardZone.DECK), key=lambda c:c.location.deck_position)
                for other in deck[available:]:
                    other.face_up = True
                    other.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=state.runner.player_id)
                before_hand = sum(c.location.zone is CardZone.HAND and c.owner_id == state.runner.player_id
                                  for c in state.card_instances.values())
                result = apply_command(state, state.runner.player_id, PlayCard(card.instance_id), self.cards)
                after_hand = sum(c.location.zone is CardZone.HAND and c.owner_id == state.runner.player_id
                                 for c in result.card_instances.values())
                self.assertEqual(after_hand, before_hand - 1 + min(4, len(deck), available))
                self.assertEqual(result.runner.credits, 4)
                self.assertEqual(result.runner.clicks_remaining, max(0, clicks - 2))
                self.assertIsNone(result.winner)
                if clicks == 1 and after_hand > 5:
                    self.assertEqual(result.turn.phase.value, "discard")
                elif clicks == 1:
                    self.assertEqual(result.turn.active_player, PlayerRole.CORP)
                self.assertEqual(result.card_instances[card.instance_id].location.zone, CardZone.DISCARD_PILE)
