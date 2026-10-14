"""Exercise real exports and reject inputs that would lose or misidentify cards."""

import tempfile
import unittest
from pathlib import Path

from content.decks.deck_importer import DeckImportError, load_nrdb_deck, parse_nrdb_deck
from content.cards.loader import load_nsg_card_definitions


ROOT = Path(__file__).resolve().parents[1]


class DeckImporterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_nsg_card_definitions(
            ROOT / "src/content/cards/system-gateway/cards.json"
        )

    def parse(self, body, identity="The Catalyst: Convention Breaker"):
        return parse_nrdb_deck(
            f"Test deck\n{identity} (System Gateway)\n{body}", self.catalog
        )

    def test_starter_exports_match_every_card_and_identity(self):
        for side, count, identity_id in (("runner", 30, "30076"), ("corp", 34, "30077")):
            with self.subTest(side=side):
                path = ROOT / f"src/content/decks/system-gateway/starter-{side}.txt"
                deck = load_nrdb_deck(path, self.catalog)
                self.assertEqual(deck.identity_definition_id, identity_id)
                self.assertEqual(sum(deck.card_counts.values()), count)
                self.assertNotIn(identity_id, deck.card_counts)
                expected = {}
                for line in path.read_text(encoding="utf-8").splitlines():
                    if "x " in line and line.split("x ", 1)[0].isdigit():
                        quantity, printed = line.split("x ", 1)
                        title = printed.split(" (System Gateway)")[0]
                        card = next(c for c in self.catalog.values() if c.title == title)
                        expected[card.definition_id] = int(quantity)
                self.assertEqual(deck.card_counts, expected)

    def test_repeated_entries_are_combined(self):
        deck = self.parse("1x Sure Gamble (System Gateway)\n2x Sure Gamble (System Gateway)")
        self.assertEqual(deck.card_counts, {"30030": 3})

    def test_blank_lines_bom_and_influence_symbols(self):
        text = "\ufeffDeck\r\n\r\nThe Catalyst: Convention Breaker (System Gateway)\r\n2x Tread Lightly (System Gateway) ••\r\n"
        self.assertEqual(sum(parse_nrdb_deck(text, self.catalog).card_counts.values()), 2)

    def test_unknown_and_malformed_lines_are_not_silently_dropped(self):
        for body, message in (
            ("1x Missing Card (System Gateway)", "unknown or unsupported"),
            ("2 Sure Gamble (System Gateway)", "unrecognized"),
            ("1x Sure Gamble", "set in parentheses"),
            ("0x Sure Gamble (System Gateway)", "quantity must be positive"),
            ("1x Sure Gamble (System Gateway)\nunexpected data", "unrecognized"),
            ("1x Hedge Fund (System Gateway)", "opposite side"),
            ("1x The Catalyst: Convention Breaker (System Gateway)", "identity cannot"),
            ("1x Sure Gamble (System Gateway)\n30 cards (min 30)", "entries total 1"),
            ("Event (0)", "at least one card"),
        ):
            with self.subTest(body=body), self.assertRaisesRegex(DeckImportError, message):
                self.parse(body)

    def test_nonidentity_in_identity_position(self):
        with self.assertRaisesRegex(DeckImportError, "not an identity"):
            self.parse("1x Sure Gamble (System Gateway)", identity="Sure Gamble")

    def test_ambiguous_titles_are_rejected(self):
        catalog = dict(self.catalog)
        catalog["other-printing"] = catalog["30030"].model_copy(
            update={"definition_id": "other-printing"}
        )
        with self.assertRaisesRegex(DeckImportError, "ambiguous"):
            parse_nrdb_deck(
                "Deck\nThe Catalyst: Convention Breaker (System Gateway)\n1x Sure Gamble (System Gateway)",
                catalog,
            )

    def test_empty_file(self):
        with self.assertRaisesRegex(DeckImportError, "Expected a deck name"):
            parse_nrdb_deck("", self.catalog)

    def test_missing_and_invalid_utf8_files_report_path(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "deck.txt"
            with self.assertRaises(DeckImportError) as missing:
                load_nrdb_deck(path, self.catalog)
            self.assertIn(str(path), str(missing.exception))
            path.write_bytes(b"\xff\xfe")
            with self.assertRaises(DeckImportError) as invalid:
                load_nrdb_deck(path, self.catalog)
            self.assertIn(str(path), str(invalid.exception))


if __name__ == "__main__":
    unittest.main()
