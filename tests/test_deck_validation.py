"""Cover the deck-construction constraints that govern game setup."""

import unittest

from content.cards.models import CardDefinition, CardSide, CardType
from game.deck_validation import (
    DeckValidationError,
    _corp_agenda_point_range,
    validate_deck,
)
from game.models import DeckSelection, PlayerRole


def card(
    definition_id: str,
    title: str,
    *,
    role: PlayerRole = PlayerRole.RUNNER,
    faction: str = "anarch",
    card_type: CardType = CardType.EVENT,
    deck_limit: int | None = None,
    influence_cost: int | None = 0,
    agenda_points: int | None = None,
    minimum_deck_size: int | None = None,
    influence_limit: int | None = None,
) -> CardDefinition:
    """Build compact definitions for isolated rule cases."""

    return CardDefinition(
        definition_id=definition_id,
        title=title,
        side=CardSide(role.value),
        card_type=card_type,
        faction_code=faction,
        deck_limit=deck_limit,
        influence_cost=influence_cost,
        agenda_points=agenda_points,
        minimum_deck_size=minimum_deck_size,
        influence_limit=influence_limit,
    )


class DeckValidationTests(unittest.TestCase):
    def setUp(self):
        self.identity = card(
            "runner-id",
            "Runner Identity",
            card_type=CardType.IDENTITY,
            minimum_deck_size=40,
            influence_limit=15,
        )
        self.filler = card("filler", "Filler", deck_limit=40)
        self.cards = {self.identity.definition_id: self.identity, "filler": self.filler}

    def selection(self, counts, identity_id="runner-id"):
        return DeckSelection(identity_definition_id=identity_id, card_counts=counts)

    def test_identity_minimum_deck_size(self):
        with self.assertRaisesRegex(DeckValidationError, "at least 40 cards"):
            validate_deck(self.selection({"filler": 39}), PlayerRole.RUNNER, self.cards)

    def test_copy_limit_counts_same_name_across_definition_ids(self):
        first = card("copy-a", "Shared Name")
        second = card("copy-b", "Shared Name")
        self.cards.update({"copy-a": first, "copy-b": second})
        selection = self.selection({"copy-a": 2, "copy-b": 2, "filler": 36})
        with self.assertRaisesRegex(DeckValidationError, "3 copies.*by name"):
            validate_deck(selection, PlayerRole.RUNNER, self.cards)

    def test_influence_cost_is_counted_for_each_out_of_faction_copy(self):
        expensive = card(
            "expensive", "Out of Faction", faction="shaper", deck_limit=8,
            influence_cost=2,
        )
        self.cards["expensive"] = expensive
        selection = self.selection({"expensive": 8, "filler": 32})
        with self.assertRaisesRegex(DeckValidationError, "spends 16 influence"):
            validate_deck(selection, PlayerRole.RUNNER, self.cards)

    def test_out_of_faction_card_requires_an_influence_cost(self):
        no_cost = card("no-cost", "No Cost", faction="shaper", influence_cost=None)
        self.cards["no-cost"] = no_cost
        selection = self.selection({"no-cost": 1, "filler": 39})
        with self.assertRaisesRegex(DeckValidationError, "has no influence cost"):
            validate_deck(selection, PlayerRole.RUNNER, self.cards)

    def test_starter_only_identity_requires_explicit_starter_deck_allowance(self):
        starter = card(
            "starter-id",
            "The Catalyst: Convention Breaker",
            card_type=CardType.IDENTITY,
            minimum_deck_size=30,
            influence_limit=None,
        )
        self.cards["starter-id"] = starter
        selection = self.selection({"filler": 30}, identity_id="starter-id")
        with self.assertRaisesRegex(DeckValidationError, "not legal under full deck"):
            validate_deck(selection, PlayerRole.RUNNER, self.cards)
        validate_deck(
            selection,
            PlayerRole.RUNNER,
            self.cards,
            allowed_starter_only_identity_ids=frozenset({"starter-id"}),
        )

    def test_corp_agenda_point_bands_follow_rule_1_4_6(self):
        self.assertEqual(_corp_agenda_point_range(40), (18, 19))
        self.assertEqual(_corp_agenda_point_range(45), (20, 21))
        self.assertEqual(_corp_agenda_point_range(50), (22, 23))
        self.assertEqual(_corp_agenda_point_range(55), (24, 25))
        self.assertEqual(_corp_agenda_point_range(66), (28, 29))

    def test_corp_deck_must_meet_its_agenda_point_band(self):
        corp_identity = card(
            "corp-id",
            "Corp Identity",
            role=PlayerRole.CORP,
            faction="haas-bioroid",
            card_type=CardType.IDENTITY,
            minimum_deck_size=40,
            influence_limit=15,
        )
        corp_cards = {"corp-id": corp_identity}
        counts = {"filler": 34}
        for index in range(1, 4):
            agenda = card(
                f"agenda-{index}",
                f"Agenda {index}",
                role=PlayerRole.CORP,
                faction="haas-bioroid",
                card_type=CardType.AGENDA,
                deck_limit=3,
                influence_cost=None,
                agenda_points=2,
            )
            corp_cards[agenda.definition_id] = agenda
            counts[agenda.definition_id] = 2
        corp_cards["filler"] = card(
            "filler",
            "Filler",
            role=PlayerRole.CORP,
            faction="haas-bioroid",
            deck_limit=40,
            influence_cost=None,
        )
        with self.assertRaisesRegex(DeckValidationError, "must contain 18 or 19"):
            validate_deck(
                DeckSelection(identity_definition_id="corp-id", card_counts=counts),
                PlayerRole.CORP,
                corp_cards,
            )


if __name__ == "__main__":
    unittest.main()
