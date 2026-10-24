"""Grouped installs must preserve the API's exact legal card and target choices."""

import unittest

from clients.cli.src.anr_cli.action_menu import build_action_choices


class InstallIceMenuTests(unittest.TestCase):
    def test_ice_is_grouped_by_instance_then_authorized_destination(self):
        hand = [
            {"instance_id": "ice-1", "definition_id": "ice", "title": "Karunā", "card_type": "ice"},
            {"instance_id": "ice-2", "definition_id": "ice", "title": "Karunā", "card_type": "ice"},
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
        self.assertEqual(choices[0].label, "Install ice")
        self.assertEqual(choices[1].value, asset_action)
        cards = choices[0].value.choices
        self.assertEqual([card.label for card in cards], ["Karunā", "Karunā"])
        targets = [choice for card in cards for choice in card.value.choices]
        self.assertEqual([target.value for target in targets], actions)
        self.assertEqual([target.label for target in targets], [
            "HQ · 1 click", "R&D · 1 click", "Archives · 1 click", "new remote · 1 click",
        ])
        self.assertTrue(all(target.kind == "action" for target in targets))
        self.assertEqual(build_action_choices(state, {}, submit_action_available=False), [])

    def test_no_install_ice_menu_without_legal_ice_installs(self):
        state = {"you": {"hand": [{"instance_id": "ice", "card_type": "ice"}]},
                 "legal_actions": [{"type": "gain_credit"}]}
        choices = build_action_choices(state, {}, submit_action_available=True)
        self.assertEqual([choice.label for choice in choices], ["Gain 1 credit"])
