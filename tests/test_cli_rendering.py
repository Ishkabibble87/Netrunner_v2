"""Regression coverage for private opening hands on the CLI board."""

import unittest

from clients.cli.src.anr_cli.game_rendering import GameRenderer, MenuLevel


class OpeningHandRenderingTests(unittest.TestCase):
    def test_opening_hand_is_visible_for_both_roles_and_layouts(self):
        for role in ("corp", "runner"):
            for compact in (False, True):
                for pending in ("corp", "runner", None):
                    with self.subTest(role=role, compact=compact, pending=pending):
                        renderer = GameRenderer({})
                        renderer.set_context(
                            {
                                "setup": {"pending_player": pending},
                                "you": {
                                    "role": role,
                                    "hand": [
                                        {"title": "Hedge Fund", "card_type": "operation"},
                                        {"title": "Hedge Fund", "card_type": "operation"},
                                        {"title": "Palisade", "card_type": "ice"},
                                    ],
                                },
                                "opponent": {"role": "runner" if role == "corp" else "corp"},
                            },
                            role, [MenuLevel("Game", "root")], None, None,
                        )
                        board = "".join(text for _, text in renderer._board_fragments(compact=compact))
                        if pending is None:
                            self.assertNotIn("YOUR OPENING HAND", board)
                            self.assertNotIn("Hedge Fund", board)
                        else:
                            self.assertTrue(board.startswith("YOUR OPENING HAND\n"))
                            self.assertEqual(board.count("Hedge Fund · Operation\n"), 2)
                            self.assertIn("Palisade · Ice\n", board)
