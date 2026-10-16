"""Verify deck uploads use the server parser and remain usable for setup."""

import unittest
from itertools import product
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app


ROOT = Path(__file__).resolve().parents[1]


class DeckImportApiTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(TestClient(create_app()))

    def test_starter_flow_through_mulligans_and_first_corp_turn(self):
        for side, decision in product(("corp", "runner"), ("keep", "mulligan")):
            with self.subTest(side=side, decision=decision):
                text = (ROOT / f"content/decks/system-gateway/starter-{side}.txt").read_text(
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
                created = response.json()
                view = created["initial_state"]
                self.assertEqual(view["viewer_role"], side)
                self.assertEqual(len(view["you"]["hand"]), 5)
                self.assertNotIn("hand", view["opponent"])
                self.assertIsNone(view["turn"])
                self.assertEqual(view["setup"]["pending_player"], side)
                self.assertEqual(view["setup"]["decisions"], {"corp": "keep"} if side == "runner" else {})
                headers = {"Authorization": f"Bearer {created['player_token']}"}
                path = f"/games/{view['game_id']}"
                old_hand = {card["instance_id"] for card in view["you"]["hand"]}
                # Cover both choices on both sides, including snapshots and resume.
                with self.client.websocket_connect(f"{path}/events", headers=headers) as socket:
                    self.assertEqual(socket.receive_json()["state"], view)
                    response = self.client.post(
                        f"{path}/commands", headers=headers,
                        json={"type": "choose_mulligan", "decision": decision},
                    )
                    self.assertEqual(response.status_code, 200)
                    snapshot = response.json()
                    self.assertEqual(socket.receive_json(), snapshot)
                    self.assertEqual(snapshot["revision"], 1)
                    ready = snapshot["state"]
                    self.assertEqual(ready["setup"]["decisions"][side], decision)
                    self.assertIsNone(ready["setup"]["pending_player"])
                    self.assertEqual(ready["turn"], {
                        "number": 1, "active_player": "corp", "phase": "action",
                    })
                    self.assertIsNone(ready["winner"])
                    self.assertNotIn("hand", ready["opponent"])
                    corp = ready["you"] if side == "corp" else ready["opponent"]
                    runner = ready["opponent"] if side == "corp" else ready["you"]
                    self.assertEqual(corp["clicks_remaining"], 3)
                    self.assertEqual(runner["clicks_remaining"], 0)
                    self.assertEqual(corp["credits"], 5)
                    self.assertEqual(runner["credits"], 5)
                    self.assertEqual(len(corp["hand"]) if side == "corp" else corp["hand_count"], 6)
                    self.assertEqual(len(runner["hand"]) if side == "runner" else runner["hand_count"], 5)
                    self.assertEqual(ready["you"]["deck_count"], view["you"]["deck_count"] - (side == "corp"))
                    if decision == "keep":
                        self.assertTrue(old_hand <= {card["instance_id"] for card in ready["you"]["hand"]})
                    repeated = self.client.post(
                        f"{path}/commands", headers=headers,
                        json={"type": "choose_mulligan", "decision": decision},
                    )
                    self.assertEqual(repeated.status_code, 409)
                    self.assertEqual(self.client.get(f"{path}/state", headers=headers).json(), ready)
                with self.client.websocket_connect(f"{path}/events", headers=headers) as socket:
                    self.assertEqual(socket.receive_json(), snapshot)

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
