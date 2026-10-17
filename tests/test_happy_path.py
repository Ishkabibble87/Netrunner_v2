"""A growing game path through real content, application setup, and commands.

Each named step checks its checkpoint before the next step runs. Extend this
scenario as gameplay grows; HTTP transport and CLI interaction belong elsewhere.
"""

from collections import Counter
from pathlib import Path
import unittest
from uuid import uuid4

from application.game_setup import create_ai_game
from application.sessions import GameSession, SessionStore
from content.cards.loader import load_nsg_card_definitions
from content.decks.deck_importer import load_nrdb_deck
from content.opponents.loader import load_opponent_profiles
from game.commands import ChooseMulligan, DrawCard, GainCredit, PurgeVirusCounters
from game.engine import legal_actions_for
from game.models import CardZone, DeckSelection, GameState, MulliganDecision, PlayerRole, TurnPhase


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GameProgressTests(unittest.TestCase):
    """Follow one Corp player from deck selection into a game against the AI."""

    def test_game_path(self):
        self.cards = load_nsg_card_definitions(
            PROJECT_ROOT / "content/cards/system-gateway/cards.json"
        )
        self.sessions = SessionStore()
        self.addCleanup(self.sessions.clear)

        deck = self.select_and_parse_deck()
        session = self.select_opponent_and_start_game(deck)
        state = self.mulligan_while_opponent_keeps(session)
        self.assert_corp_first_turn_started(state)
        self.take_basic_actions(session)

    def select_and_parse_deck(self) -> DeckSelection:
        deck = load_nrdb_deck(
            PROJECT_ROOT / "content/decks/system-gateway/starter-corp.txt", self.cards
        )
        self.assertEqual(
            self.cards[deck.identity_definition_id].title,
            "The Syndicate: Profit over Principle",
        )
        self.assertEqual(sum(deck.card_counts.values()), 34)
        self.assertEqual(
            {self.cards[card_id].title: count for card_id, count in deck.card_counts.items()},
            {
                "Offworld Office": 3, "Send a Message": 2, "Superconducting Hub": 2,
                "Nico Campaign": 2, "Urtica Cipher": 2, "Regolith Mining License": 2,
                "Manegarm Skunkworks": 1, "Seamless Launch": 2, "Government Subsidy": 2,
                "Hedge Fund": 3, "Brân 1.0": 2, "Palisade": 3, "Diviner": 2,
                "Whitespace": 2, "Karunā": 2, "Tithe": 2,
            },
        )
        return deck

    def select_opponent_and_start_game(self, deck: DeckSelection) -> GameSession:
        opponent = load_opponent_profiles(self.cards)["system-gateway-training"]
        player_id = uuid4()
        game = create_ai_game(
            player_id=player_id,
            decklist=deck,
            opponent_profile=opponent,
            card_definitions=self.cards,
        )
        session = self.sessions.create(game, opponent.profile_id, [player_id])
        state = session.state
        self.assertEqual(session.opponent_profile_id, opponent.profile_id)
        self.assertEqual(state.corp.player_id, player_id)
        self.assertEqual(state.corp.deck_selection, deck)
        self.assertEqual(state.runner.deck_selection, opponent.deck_selections[PlayerRole.RUNNER])
        self.assertIsNone(state.turn)
        self.assertIsNone(state.winner)
        self.assertEqual(state.setup.pending_player, PlayerRole.CORP)
        self.assertEqual(state.setup.decisions, {})
        for player in (state.corp, state.runner):
            with self.subTest(opening_player=player.role):
                self.assertEqual(player.credits, 5)
                self.assertEqual(player.clicks_remaining, 0)
                self.assertEqual(len(self.cards_in_zone(state, player.player_id, CardZone.HAND)), 5)
                self.assertEqual(
                    len(self.cards_in_zone(state, player.player_id, CardZone.DECK)),
                    sum(player.deck_selection.card_counts.values()) - 5,
                )
                self.assertEqual(
                    Counter(card.definition_id for card in state.card_instances.values()
                            if card.owner_id == player.player_id and card.location.zone != CardZone.IDENTITY),
                    Counter(player.deck_selection.card_counts),
                )
        self.assertEqual(
            legal_actions_for(state, player_id),
            (ChooseMulligan(MulliganDecision.KEEP), ChooseMulligan(MulliganDecision.MULLIGAN)),
        )
        self.assertEqual(legal_actions_for(state, state.runner.player_id), ())
        return session

    def mulligan_while_opponent_keeps(self, session: GameSession) -> GameState:
        before = session.state
        runner_hand = self.cards_in_zone(before, before.runner.player_id, CardZone.HAND)
        token = session.player_tokens[before.corp.player_id]
        updated, player_id = self.sessions.submit(
            before.game_id, token, ChooseMulligan(MulliganDecision.MULLIGAN)
        )
        state = updated.state
        self.assertEqual(player_id, before.corp.player_id)
        self.assertIsNone(state.setup.pending_player)
        self.assertEqual(state.setup.decisions, {
            PlayerRole.CORP: MulliganDecision.MULLIGAN,
            PlayerRole.RUNNER: MulliganDecision.KEEP,
        })
        self.assertEqual(self.cards_in_zone(state, state.runner.player_id, CardZone.HAND), runner_hand)
        # Mulligans reuse physical copies; drawing the same cards again is legal.
        self.assertEqual(state.card_instances.keys(), before.card_instances.keys())
        self.assertEqual(state.runner, before.runner)
        return state

    def assert_corp_first_turn_started(self, state: GameState) -> None:
        self.assertIsNotNone(state.turn)
        self.assertEqual(state.turn.number, 1)
        self.assertEqual(state.turn.active_player, PlayerRole.CORP)
        self.assertEqual(state.turn.phase, TurnPhase.ACTION)
        self.assertIsNone(state.winner)
        self.assertEqual(state.corp.credits, 5)
        self.assertEqual(state.corp.clicks_remaining, 3)
        self.assertEqual(len(self.cards_in_zone(state, state.corp.player_id, CardZone.HAND)), 6)
        self.assertEqual(len(self.cards_in_zone(state, state.corp.player_id, CardZone.DECK)), 28)
        self.assertEqual(
            legal_actions_for(state, state.corp.player_id),
            (GainCredit(), DrawCard(), PurgeVirusCounters()),
        )
        self.assertEqual(legal_actions_for(state, state.runner.player_id), ())

    def take_basic_actions(self, session: GameSession) -> None:
        before = session.state
        player_id = before.corp.player_id
        token = session.player_tokens[player_id]
        hand = self.cards_in_zone(before, player_id, CardZone.HAND)
        deck = self.cards_in_zone(before, player_id, CardZone.DECK)
        top_card = next(card.instance_id for card in deck.values() if card.location.deck_position == 1)

        session, _ = self.sessions.submit(before.game_id, token, DrawCard())
        drawn = session.state
        self.assertEqual(set(self.cards_in_zone(drawn, player_id, CardZone.HAND)), set(hand) | {top_card})
        self.assertEqual(set(self.cards_in_zone(drawn, player_id, CardZone.DECK)), set(deck) - {top_card})
        self.assertEqual(drawn.corp.clicks_remaining, 2)
        self.assertEqual(drawn.corp.credits, 5)
        self.assertEqual(legal_actions_for(drawn, player_id), (GainCredit(), DrawCard()))

        session, _ = self.sessions.submit(before.game_id, token, GainCredit())
        gained = session.state
        self.assertEqual(gained.corp.credits, 6)
        self.assertEqual(gained.corp.clicks_remaining, 1)
        self.assertEqual(self.cards_in_zone(gained, player_id, CardZone.HAND),
                         self.cards_in_zone(drawn, player_id, CardZone.HAND))
        self.assertEqual(self.cards_in_zone(gained, player_id, CardZone.DECK),
                         self.cards_in_zone(drawn, player_id, CardZone.DECK))
        self.assertEqual(gained.runner, before.runner)
        self.assertEqual(gained.turn, before.turn)

    @staticmethod
    def cards_in_zone(state, player_id, zone):
        """Select physical cards without relying on shuffled order or random IDs."""
        return {
            card.instance_id: card for card in state.card_instances.values()
            if card.location.player_id == player_id and card.location.zone is zone
        }


if __name__ == "__main__":
    unittest.main()
