"""Cover ordering and physical-card integrity that private API views cannot expose."""

import random
import unittest
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from content.cards.loader import load_nsg_card_definitions
from content.opponents.loader import load_opponent_profiles
from game.commands import ChooseMulligan
from game.models import CardZone, PlayerRole, MulliganDecision
from game.setup import IllegalCommand, choose_mulligan, create_game_state


class OpeningHandRulesTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        catalog = load_nsg_card_definitions(root / "content/cards/system-gateway/cards.json")
        decks = load_opponent_profiles(catalog)["system-gateway-training"].deck_selections
        self.state = create_game_state(
            game_id=None, corp_player_id=uuid4(), runner_player_id=uuid4(),
            corp_deck=decks[PlayerRole.CORP], runner_deck=decks[PlayerRole.RUNNER],
            card_definitions=catalog, rng=random.Random(0),
            allowed_starter_only_identity_ids=frozenset(
                deck.identity_definition_id for deck in decks.values()
            ),
        )

    def cards(self, state, player_id, zone):
        return {card.instance_id for card in state.card_instances.values()
                if card.location.player_id == player_id and card.location.zone is zone}

    def test_corp_first_redraw_reuses_copies_and_second_hand_is_final(self):
        state = self.state
        before = state.model_dump()
        with self.assertRaises(IllegalCommand):
            choose_mulligan(state, state.runner.player_id, ChooseMulligan(MulliganDecision.KEEP))
        self.assertEqual(state.model_dump(), before)

        # Reverse the whole shuffled deck to make replacement cards predictable.
        shuffle = Mock(side_effect=lambda cards: cards.reverse())
        source = Mock(shuffle=shuffle)
        corp_id = state.corp.player_id
        first_hand = self.cards(state, corp_id, CardZone.HAND)
        state = choose_mulligan(state, corp_id, ChooseMulligan(MulliganDecision.MULLIGAN), rng=source)
        self.assertEqual(shuffle.call_count, 1)
        self.assertEqual(len(shuffle.call_args.args[0]), sum(state.corp.deck_selection.card_counts.values()))
        self.assertEqual(len(self.cards(state, corp_id, CardZone.HAND)), 5)
        self.assertTrue(first_hand.isdisjoint(self.cards(state, corp_id, CardZone.HAND)))
        self.assertTrue(first_hand <= self.cards(state, corp_id, CardZone.DECK))
        self.assertEqual(state.setup.pending_player, PlayerRole.RUNNER)
        self.assertIsNone(state.turn)
        with self.assertRaises(IllegalCommand):
            choose_mulligan(state, corp_id, ChooseMulligan(MulliganDecision.MULLIGAN))

        top = next(card.instance_id for card in state.card_instances.values()
                   if card.location.player_id == corp_id and card.location.deck_position == 1)
        state = choose_mulligan(state, state.runner.player_id, ChooseMulligan(MulliganDecision.MULLIGAN), rng=source)
        self.assertIn(top, self.cards(state, corp_id, CardZone.HAND))
        self.assertEqual(len(self.cards(state, corp_id, CardZone.HAND)), 6)
        self.assertEqual(len(self.cards(state, state.runner.player_id, CardZone.HAND)), 5)
        self.assertEqual(set(state.card_instances), set(self.state.card_instances))
        self.assertEqual(state.corp.identity_card_instance_id, self.state.corp.identity_card_instance_id)
        self.assertEqual(state.runner.identity_card_instance_id, self.state.runner.identity_card_instance_id)
        self.assertEqual(self.state.model_dump(), before)
        for player_id in (corp_id, state.runner.player_id):
            with self.assertRaises(IllegalCommand):
                choose_mulligan(state, player_id, ChooseMulligan(MulliganDecision.MULLIGAN))

    def test_empty_rd_loses_on_mandatory_draw(self):
        state = self.state.model_copy(deep=True)
        state.card_instances = {
            key: card for key, card in state.card_instances.items()
            if not (card.location.player_id == state.corp.player_id and card.location.zone is CardZone.DECK)
        }
        state = choose_mulligan(state, state.corp.player_id, ChooseMulligan(MulliganDecision.KEEP))
        state = choose_mulligan(state, state.runner.player_id, ChooseMulligan(MulliganDecision.KEEP))
        self.assertEqual(state.winner, PlayerRole.RUNNER)
        self.assertEqual(state.turn.phase.value, "draw")
