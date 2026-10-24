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
                            self.assertIn("CARDS IN HAND · 3\n", board)
                            self.assertGreater(board.index("CARDS IN HAND"), board.index("SCORE AREA"))
                        else:
                            self.assertTrue(board.startswith("YOUR OPENING HAND\n"))
                        self.assertEqual(board.count("Hedge Fund · Operation\n"), 2)
                        self.assertIn("Palisade · Ice\n", board)


class TurnContextRenderingTests(unittest.TestCase):
    def test_opponent_turn_remains_visible_while_you_have_paid_priority(self):
        for role in ("corp", "runner"):
            other = "runner" if role == "corp" else "corp"
            for compact in (False, True):
                with self.subTest(role=role, compact=compact):
                    state = {
                        "turn": {"number": 2, "active_player": other, "phase": "action"},
                        "you": {"role": role, "clicks_remaining": 0},
                        "opponent": {"role": other, "clicks_remaining": 3},
                        "decision": {"type": "paid_ability_window", "player": role},
                    }
                    renderer = GameRenderer({})
                    renderer.set_context(state, role, [MenuLevel("Game", "root")], None, None)
                    board = "".join(text for _, text in renderer._board_fragments(compact=compact))
                    menu = "".join(text for _, text in renderer._menu_fragments())
                    turn = f"TURN 2 · {other.upper()} (opponent) · action phase"
                    self.assertTrue(board.startswith(turn))
                    self.assertIn(turn, menu)
                    self.assertIn("3 clicks", board)
                    self.assertIn("0 clicks", board)
                    self.assertIn("pass priority to let them continue", menu)
                    state["turn"]["number"] = 3
                    state["turn"]["active_player"] = role
                    state["you"]["clicks_remaining"] = 3
                    renderer.set_context(state, role, [MenuLevel("Game", "root")], None, None)
                    menu = "".join(text for _, text in renderer._menu_fragments())
                    self.assertIn(f"TURN 3 · {role.upper()} (you) · action phase", menu)
                    self.assertNotIn("pass priority to let them continue", menu)


class OpponentActionRenderingTests(unittest.TestCase):
    def test_latest_move_stays_visible_after_passes_and_your_own_actions(self):
        for role in ("corp", "runner"):
            other = "runner" if role == "corp" else "corp"
            with self.subTest(role=role):
                state = {"history": [
                    {"kind": "command", "actor": other, "turn_number": 1,
                     "detail": {"text": "Gain Credit"}},
                    {"kind": "command", "actor": other, "turn_number": 2,
                     "detail": {"text": "Play Card", "title": "Visible card"}},
                    {"kind": "command", "actor": other, "detail": {"text": "Pass Priority"}},
                    {"kind": "command", "actor": role, "detail": {"text": "Draw Card"}},
                    {"kind": "resource", "actor": other, "detail": {"text": "Credits changed"}},
                ]}
                renderer = GameRenderer({})
                renderer.set_context(state, role, [MenuLevel("Game", "root")], None, None)
                menu = "".join(text for _, text in renderer._menu_fragments())
                expected = "Last opponent action · turn 2: Visible card: Play Card"
                self.assertIn("ACTIONS\n" + expected, menu)
                state["history"].append({"kind": "command", "actor": other, "turn_number": 4,
                                         "detail": {"text": "Install Card: hq"}})
                # Hidden installed cards have no title in this viewer's history.
                self.assertEqual(renderer._last_opponent_action(), "Last opponent action · turn 4: Install Card: hq")

    def test_empty_history_and_setup_passes_have_no_opponent_move(self):
        renderer = GameRenderer({})
        for history in ([], [
            {"kind": "command", "actor": "runner", "detail": {"text": "Choose Mulligan: keep"}},
            {"kind": "command", "actor": "runner", "detail": {"text": "Pass Priority"}},
        ]):
            renderer.set_context({"history": history}, "corp", [MenuLevel("Game", "root")], None, None)
            self.assertEqual(renderer._last_opponent_action(), "Last opponent action: none yet.")
