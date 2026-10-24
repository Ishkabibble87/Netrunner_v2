"""Grouped installs must preserve the API's exact legal card and target choices."""

import unittest

from clients.cli.src.anr_cli.action_menu import build_action_choices
from clients.cli.src.anr_cli.game_rendering import GameRenderer, MenuLevel


class InstallMenuTests(unittest.TestCase):
    def test_installs_are_grouped_by_type_instance_and_authorized_destination(self):
        for card_type, title in (("ice", "Karunā"), ("upgrade", "Manegarm Skunkworks")):
            with self.subTest(card_type=card_type):
                self.assert_grouped_installs(card_type, title)

    def assert_grouped_installs(self, card_type, title):
        hand = [
            {"instance_id": "ice-1", "definition_id": "card", "title": title, "card_type": card_type},
            {"instance_id": "ice-2", "definition_id": "card", "title": title, "card_type": card_type},
            {"instance_id": "asset", "definition_id": "asset", "title": "Nico Campaign", "card_type": "asset"},
        ]
        actions = [
            {"type": "install_card", "card_instance_id": "ice-1", "server_id": "hq"},
            {"type": "install_card", "card_instance_id": "ice-1", "server_id": "rd"},
            {"type": "install_card", "card_instance_id": "ice-2", "server_id": "archives"},
            {"type": "install_card", "card_instance_id": "ice-2", "server_id": "new_remote"},
        ]
        asset_action = {"type": "install_card", "card_instance_id": "asset", "server_id": "new_remote"}
        state = {"you": {"hand": hand}, "legal_actions": [actions[0], asset_action, *actions[1:]]}
        choices = build_action_choices(state, {}, submit_action_available=True)
        self.assertEqual(len(choices), 2)
        self.assertEqual(choices[0].label, f"Install {card_type}")
        self.assertEqual(choices[1].value, asset_action)
        cards = choices[0].value.choices
        self.assertEqual([card.label for card in cards], [title, title])
        targets = [choice for card in cards for choice in card.value.choices]
        self.assertEqual([target.value for target in targets], actions)
        self.assertEqual([target.label for target in targets], [
            "HQ · 1 click", "R&D · 1 click", "Archives · 1 click", "new remote · 1 click",
        ])
        self.assertTrue(all(target.kind == "action" for target in targets))
        self.assertEqual(build_action_choices(state, {}, submit_action_available=False), [])

    def test_no_install_menu_without_legal_installs(self):
        state = {"you": {"hand": [{"instance_id": "ice", "card_type": "ice"}]},
                 "legal_actions": [{"type": "gain_credit"}]}
        choices = build_action_choices(state, {}, submit_action_available=True)
        self.assertEqual([choice.label for choice in choices], ["Gain 1 credit"])

    def test_ice_and_upgrades_have_separate_menus(self):
        hand = [
            {"instance_id": kind, "definition_id": kind, "title": kind, "card_type": kind}
            for kind in ("ice", "upgrade")
        ]
        actions = [
            {"type": "install_card", "card_instance_id": kind, "server_id": "hq"}
            for kind in ("ice", "upgrade")
        ]
        choices = build_action_choices({"you": {"hand": hand}, "legal_actions": actions}, {},
                                       submit_action_available=True)
        self.assertEqual([choice.label for choice in choices], ["Install ice", "Install upgrade"])
        for choice, action in zip(choices, actions):
            self.assertEqual(choice.value.choices[0].value.choices[0].value, action)


class EndTurnMenuTests(unittest.TestCase):
    def test_end_turn_labels_only_the_active_players_final_paid_windows(self):
        action = {"type": "pass_priority"}
        for role in ("corp", "runner"):
            other = "runner" if role == "corp" else "corp"
            for phase, clicks, active, decision, run, label in (
                ("action", 0, role, "paid_ability_window", None, "End turn · pass priority"),
                ("discard", 0, role, "paid_ability_window", None, "End turn · pass priority"),
                ("action", 1, role, "paid_ability_window", None, "Pass priority"),
                ("action", 0, other, "paid_ability_window", None, "Pass priority"),
                ("action", 0, role, "resolve_reaction", None, "Pass priority"),
                ("action", 0, role, "paid_ability_window", {"phase": "encounter"}, "Pass priority"),
            ):
                with self.subTest(role=role, phase=phase, clicks=clicks, active=active, decision=decision, run=run):
                    state = {
                        "viewer_role": role,
                        "you": {"role": role, "clicks_remaining": clicks},
                        "turn": {"number": 1, "phase": phase, "active_player": active},
                        "decision": {"type": decision, "player": role},
                        "run": run,
                        "legal_actions": [action],
                    }
                    choices = build_action_choices(state, {}, submit_action_available=True)
                    self.assertEqual(choices[0].label, label)
                    self.assertEqual(choices[0].value, action)
                    renderer = GameRenderer({})
                    renderer.set_context(state, role, [MenuLevel("Game", "root", choices)], None, None)
                    menu = "".join(text for _, text in renderer._menu_fragments())
                    if label.startswith("End turn"):
                        self.assertIn("No clicks remaining", menu)
                        self.assertIn("Required discards and opponent responses follow.", menu)
                    else:
                        self.assertNotIn("No clicks remaining", menu)
