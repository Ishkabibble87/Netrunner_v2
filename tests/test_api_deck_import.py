"""Verify deck uploads use the server parser and remain usable for setup."""

import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app


ROOT = Path(__file__).resolve().parents[1]


class DeckImportApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app())
        self.addCleanup(self.client.close)

    def test_starter_uploads_can_create_games(self):
        for side in ("corp", "runner"):
            with self.subTest(side=side):
                text = (ROOT / f"src/content/decks/system-gateway/starter-{side}.txt").read_text(
                    encoding="utf-8"
                )
                response = self.client.post("/decks/import", json={"text": text})
                self.assertEqual(response.status_code, 200)
                decklist = response.json()
                self.assertEqual(set(decklist), {"identity_definition_id", "card_counts"})
                response = self.client.post(
                    "/games",
                    json={
                        "player_id": str(uuid4()),
                        "decklist": decklist,
                        "opponent_profile_id": "system-gateway-training",
                    },
                )
                self.assertEqual(response.status_code, 201)
                view = response.json()["initial_state"]
                self.assertEqual(view["viewer_role"], side)
                self.assertEqual(len(view["you"]["hand"]), 5)
                self.assertNotIn("hand", view["opponent"])

    def test_bad_exports_return_parser_errors(self):
        for text, expected in (
            ("", "Expected a deck name"),
            (
                "Deck\nThe Catalyst: Convention Breaker (System Gateway)\n1x Missing (System Gateway)",
                "unknown or unsupported",
            ),
            (
                "Deck\nThe Catalyst: Convention Breaker (System Gateway)\n1x Hedge Fund (System Gateway)",
                "opposite side",
            ),
        ):
            with self.subTest(text=text):
                response = self.client.post("/decks/import", json={"text": text})
                self.assertEqual(response.status_code, 422)
                self.assertIn(expected, response.json()["detail"])

    def test_import_does_not_apply_deck_legality_or_setup_checks(self):
        response = self.client.post(
            "/decks/import",
            json={"text": "Deck\nThe Catalyst: Convention Breaker (System Gateway)\n1x Sure Gamble (System Gateway)"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["card_counts"], {"30030": 1})


if __name__ == "__main__":
    unittest.main()
