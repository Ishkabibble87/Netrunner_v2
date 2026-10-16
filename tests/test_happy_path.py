"""One representative API path, extended for each new action or playable card type."""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app


class GameProgressTests(unittest.TestCase):
    """Track core gameplay progress through the same API used by the CLI."""

    def test_player_starts_ai_game_mulligans_and_uses_basic_actions(self):
        with TestClient(create_app()) as client:
            opponents_response = client.get("/opponent-profiles")
            self.assertEqual(opponents_response.status_code, 200)
            opponents = opponents_response.json()
            self.assertTrue(opponents, "The player needs an AI opponent to select")
            opponent = opponents[0]

            decks_response = client.get("/preconstructed-decks")
            self.assertEqual(decks_response.status_code, 200)
            corp_deck = next(
                deck["decklist"]
                for deck in decks_response.json()
                if deck["role"] == "corp"
            )

            created_response = client.post(
                "/games",
                json={
                    "player_id": str(uuid4()),
                    "decklist": corp_deck,
                    "opponent_profile_id": opponent["profile_id"],
                },
            )
            self.assertEqual(created_response.status_code, 201, created_response.text)
            created = created_response.json()
            self.assertEqual(created["opponent_profile_id"], opponent["profile_id"])

            initial_state = created["initial_state"]
            self.assertEqual(initial_state["viewer_role"], "corp")
            self.assertEqual(initial_state["setup"]["pending_player"], "corp")
            self.assertEqual(len(initial_state["you"]["hand"]), 5)
            self.assertEqual(
                [action["decision"] for action in initial_state["legal_actions"]],
                ["keep", "mulligan"],
            )

            game_id = initial_state["game_id"]
            game_path = f"/games/{game_id}"
            headers = {"Authorization": f"Bearer {created['player_token']}"}

            with client.websocket_connect(
                f"{game_path}/events", headers=headers
            ) as events:
                first_snapshot = events.receive_json()
                self.assertEqual(first_snapshot["revision"], 0)
                self.assertEqual(first_snapshot["state"], initial_state)

                mulligan_response = client.post(
                    f"{game_path}/commands",
                    headers=headers,
                    json={"type": "choose_mulligan", "decision": "mulligan"},
                )
                self.assertEqual(mulligan_response.status_code, 200, mulligan_response.text)
                mulligan_state = mulligan_response.json()["state"]
                self.assertIsNone(mulligan_state["setup"]["pending_player"])
                self.assertEqual(
                    mulligan_state["setup"]["decisions"],
                    {"corp": "mulligan", "runner": "keep"},
                )
                self.assertEqual(
                    mulligan_state["turn"],
                    {"number": 1, "active_player": "corp", "phase": "action"},
                )
                self.assertEqual(len(mulligan_state["you"]["hand"]), 6)
                self.assertEqual(
                    mulligan_state["you"]["deck_count"],
                    initial_state["you"]["deck_count"] - 1,
                )
                self.assertEqual(
                    [action["type"] for action in mulligan_state["legal_actions"]],
                    ["gain_credit", "draw_card", "purge_virus_counters"],
                )
                self.assertEqual(events.receive_json()["state"], mulligan_state)

                hand_size = len(mulligan_state["you"]["hand"])
                deck_count = mulligan_state["you"]["deck_count"]
                clicks = mulligan_state["you"]["clicks_remaining"]
                draw_response = client.post(
                    f"{game_path}/commands",
                    headers=headers,
                    json={"type": "draw_card"},
                )
                self.assertEqual(draw_response.status_code, 200, draw_response.text)
                draw_state = draw_response.json()["state"]
                self.assertEqual(len(draw_state["you"]["hand"]), hand_size + 1)
                self.assertEqual(draw_state["you"]["deck_count"], deck_count - 1)
                self.assertEqual(draw_state["you"]["clicks_remaining"], clicks - 1)
                self.assertEqual(events.receive_json()["state"], draw_state)

                credits = draw_state["you"]["credits"]
                gain_response = client.post(
                    f"{game_path}/commands",
                    headers=headers,
                    json={"type": "gain_credit"},
                )
                self.assertEqual(gain_response.status_code, 200, gain_response.text)
                gain_state = gain_response.json()["state"]
                self.assertEqual(gain_state["you"]["credits"], credits + 1)
                self.assertEqual(gain_state["you"]["clicks_remaining"], clicks - 2)
                self.assertEqual(events.receive_json()["state"], gain_state)


if __name__ == "__main__":
    unittest.main()


