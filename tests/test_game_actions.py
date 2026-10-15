"""Rule checks for the first shared basic actions."""

import unittest
from pathlib import Path
from uuid import uuid4

from application.game_setup import create_ai_game
from content.cards.loader import load_nsg_card_definitions
from content.opponents.loader import load_opponent_profiles
from game.commands import BasicAction
from game.engine import (
    GameRuleError,
    begin_initial_turn,
    legal_actions,
    resolve_basic_action,
)
from game.models import (
    CardZone,
    GameEndReason,
    PlayerRole,
    TurnPhase,
    ZoneLocation,
)


class BasicActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.catalog = load_nsg_card_definitions(
            root / "src/content/cards/system-gateway/cards.json"
        )
        cls.profile = load_opponent_profiles(cls.catalog)["system-gateway-training"]

    def setUp(self):
        self.corp_id = uuid4()
        self.state = create_ai_game(
            player_id=self.corp_id,
            decklist=self.profile.deck_selections[PlayerRole.CORP],
            opponent_profile=self.profile,
            card_definitions=self.catalog,
        )

    def test_initial_turn_draws_for_corp_and_offers_shared_actions(self):
        old_hand_size = sum(
            card.location.zone is CardZone.HAND
            and card.location.player_id == self.corp_id
            for card in self.state.card_instances.values()
        )
        begin_initial_turn(self.state)

        self.assertEqual(self.state.turn.phase, TurnPhase.ACTION)
        self.assertEqual(self.state.corp.clicks_remaining, 3)
        self.assertEqual(old_hand_size, 5)
        self.assertEqual(
            sum(
                card.location.zone is CardZone.HAND
                and card.location.player_id == self.corp_id
                for card in self.state.card_instances.values()
            ),
            6,
        )
        self.assertEqual(
            legal_actions(self.state, self.corp_id),
            [BasicAction.GAIN_CREDIT, BasicAction.DRAW_CARD],
        )

    def test_credit_action_spends_one_click_and_adds_one_credit(self):
        begin_initial_turn(self.state)
        resolve_basic_action(self.state, self.corp_id, BasicAction.GAIN_CREDIT)

        self.assertEqual(self.state.corp.credits, 6)
        self.assertEqual(self.state.corp.clicks_remaining, 2)

    def test_draw_action_moves_top_card_to_hand_and_compacts_deck(self):
        begin_initial_turn(self.state)
        hand_before = sum(
            card.location.zone is CardZone.HAND
            and card.location.player_id == self.corp_id
            for card in self.state.card_instances.values()
        )
        deck_before = sum(
            card.location.zone is CardZone.DECK
            and card.location.player_id == self.corp_id
            for card in self.state.card_instances.values()
        )

        resolve_basic_action(self.state, self.corp_id, BasicAction.DRAW_CARD)

        self.assertEqual(
            sum(
                card.location.zone is CardZone.HAND
                and card.location.player_id == self.corp_id
                for card in self.state.card_instances.values()
            ),
            hand_before + 1,
        )
        self.assertEqual(
            sum(
                card.location.zone is CardZone.DECK
                and card.location.player_id == self.corp_id
                for card in self.state.card_instances.values()
            ),
            deck_before - 1,
        )
        self.assertEqual(self.state.corp.clicks_remaining, 2)

    def test_runner_can_take_shared_actions_during_their_action_phase(self):
        begin_initial_turn(self.state)
        self.state.turn.active_player = PlayerRole.RUNNER
        self.state.turn.number += 1
        self.state.runner.clicks_remaining = 4

        resolve_basic_action(self.state, self.state.runner.player_id, BasicAction.GAIN_CREDIT)

        self.assertEqual(self.state.runner.credits, 6)
        self.assertEqual(self.state.runner.clicks_remaining, 3)

    def test_wrong_player_phase_or_exhausted_clicks_are_rejected_without_changes(self):
        begin_initial_turn(self.state)
        original_credits = self.state.corp.credits
        original_clicks = self.state.corp.clicks_remaining

        for player_id in (self.state.runner.player_id, uuid4()):
            with self.subTest(player_id=player_id), self.assertRaises(GameRuleError):
                resolve_basic_action(self.state, player_id, BasicAction.GAIN_CREDIT)
        self.assertEqual((self.state.corp.credits, self.state.corp.clicks_remaining), (original_credits, original_clicks))

        self.state.corp.clicks_remaining = 0
        with self.assertRaisesRegex(GameRuleError, "click"):
            resolve_basic_action(self.state, self.corp_id, BasicAction.GAIN_CREDIT)
        self.assertEqual(self.state.corp.credits, original_credits)

        self.state.corp.clicks_remaining = original_clicks
        self.state.turn.phase = TurnPhase.DISCARD
        with self.assertRaisesRegex(GameRuleError, "action phase"):
            resolve_basic_action(self.state, self.corp_id, BasicAction.GAIN_CREDIT)
        self.assertEqual(self.state.corp.credits, original_credits)

    def test_draw_from_empty_deck_ends_game_after_spending_click(self):
        begin_initial_turn(self.state)
        for card in list(self.state.card_instances.values()):
            if card.location.zone is CardZone.DECK and card.location.player_id == self.corp_id:
                card.face_up = True
                card.location = ZoneLocation(zone=CardZone.REMOVED_FROM_GAME)

        resolve_basic_action(self.state, self.corp_id, BasicAction.DRAW_CARD)

        self.assertEqual(self.state.outcome.winner, PlayerRole.RUNNER)
        self.assertEqual(self.state.outcome.reason, GameEndReason.DECKED_OUT)
        self.assertEqual(self.state.corp.clicks_remaining, 2)
        self.assertEqual(legal_actions(self.state, self.corp_id), [])


if __name__ == "__main__":
    unittest.main()
