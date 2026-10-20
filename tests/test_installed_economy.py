"""Exercise economy boundaries and publication not covered by the growing path."""

import unittest
from collections import Counter
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from application.game_setup import create_ai_game
from game.models import PlayerRole


class InstalledEconomyTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.cards = self.app.state.card_definitions
        profile = self.app.state.opponent_profiles["system-gateway-training"]
        orders = {}
        for role, titles in {
            PlayerRole.CORP: ["Regolith Mining License", "Regolith Mining License", "Offworld Office", "Palisade", "Nico Campaign"],
            PlayerRole.RUNNER: ["Telework Contract", "Telework Contract"],
        }.items():
            prefix = [next(i for i, c in self.cards.items() if c.title == t) for t in titles]
            remaining = Counter(profile.deck_selections[role].card_counts)
            remaining.subtract(prefix)
            orders[role] = prefix + [i for i in sorted(remaining) for _ in range(remaining[i])]
        game = create_ai_game(player_id=uuid4(), decklist=profile.deck_selections[PlayerRole.CORP],
                              opponent_profile=profile, card_definitions=self.cards, deck_orders=orders)
        self.session = self.app.state.sessions.create(game, profile.profile_id,
                                                      [game.corp.player_id, game.runner.player_id])
        self.client = self.enterContext(TestClient(self.app))
        self.path = f"/games/{game.game_id}"
        self.headers = {p.role.value: {"Authorization": f"Bearer {self.session.player_tokens[p.player_id]}"}
                        for p in (game.corp, game.runner)}
        self.submit("corp", {"type": "choose_mulligan", "decision": "keep"})
        self.submit("runner", {"type": "choose_mulligan", "decision": "keep"})

    def submit(self, role, command):
        response = self.client.post(self.path + "/commands", headers=self.headers[role], json=command)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["state"]

    def view(self, role):
        return self.client.get(self.path + "/state", headers=self.headers[role]).json()

    def reject(self, role, command):
        before, revision = self.session.state.model_dump(), self.session.revision
        response = self.client.post(self.path + "/commands", headers=self.headers[role], json=command)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.session.state.model_dump(), before)
        self.assertEqual(self.session.revision, revision)

    def hand(self, role, title):
        return next(c["instance_id"] for c in self.view(role)["you"]["hand"] if c["title"] == title)

    def pass_windows(self):
        while self.session.state.window:
            self.submit(self.session.state.window.player.value, {"type": "pass_priority"})

    def next_runner_action(self):
        for _ in range(20):
            self.pass_windows()
            if self.session.state.turn.active_player is PlayerRole.RUNNER:
                return
            view = self.view("corp")
            selection = next((a for a in view["legal_actions"] if a["type"] == "discard_cards"), None)
            command = ({"type": "discard_cards", "card_instance_ids": selection["eligible_card_instance_ids"][:selection["required_count"]]}
                       if selection else {"type": "gain_credit"})
            self.submit("corp", command)
        self.fail("Runner action window did not arrive")

    def test_rez_publishes_counters_and_invalid_choices_are_atomic(self):
        regolith = self.hand("corp", "Regolith Mining License")
        rez = {"type": "rez_card", "card_instance_id": regolith}
        ability = {"type": "use_card_ability", "card_instance_id": regolith, "ability_id": "take_credits"}
        self.reject("corp", rez)
        self.submit("corp", {"type": "install_card", "card_instance_id": regolith, "server_id": "new_remote"})
        self.assertIsNone(self.view("runner")["installed_cards"][0]["card"])
        self.reject("runner", rez)
        self.reject("corp", ability)
        self.session.state.corp.credits = 1
        self.reject("corp", rez)
        self.session.state.corp.credits = 5
        with self.client.websocket_connect(self.path + "/events", headers=self.headers["runner"]) as socket:
            socket.receive_json()
            before = self.session.state
            self.submit("corp", rez)
            published = socket.receive_json()["state"]
            card, = published["installed_cards"]
            self.assertEqual(card["card"]["title"], "Regolith Mining License")
            self.assertEqual(card["counters"], {"credit": 15})
            self.assertTrue(card["rezzed"])
            self.assertEqual(self.session.state.corp.credits, 3)
            self.assertEqual(self.session.state.corp.clicks_remaining, before.corp.clicks_remaining)
            self.assertFalse(before.card_instances[UUID(regolith)].rezzed)
        self.reject("corp", rez)
        self.pass_windows()
        self.reject("corp", dict(ability, ability_id="missing"))
        self.reject("runner", ability)
        self.submit("corp", ability)
        self.pass_windows()
        self.submit("corp", ability)
        self.assertEqual(next(c for c in self.view("corp")["installed_cards"] if c["instance_id"] == regolith)["counters"], {"credit": 9})

    def test_telework_copies_have_independent_limits_and_public_rig(self):
        self.next_runner_action()
        first = self.hand("runner", "Telework Contract")
        self.reject("runner", {"type": "install_card", "card_instance_id": first, "server_id": "hq"})
        self.session.state.runner.credits = 0
        self.reject("runner", {"type": "install_card", "card_instance_id": first})
        self.session.state.runner.credits = 5
        self.submit("runner", {"type": "install_card", "card_instance_id": first})
        second = self.hand("runner", "Telework Contract")
        self.submit("runner", {"type": "install_card", "card_instance_id": second})
        rig = self.view("corp")["installed_cards"]
        self.assertEqual(len(rig), 2)
        self.assertTrue(all(c["area"] == "rig" and c["server_id"] is None and c["card"] and not c["rezzed"] for c in rig))
        ability = {"type": "use_card_ability", "card_instance_id": first, "ability_id": "take_credits"}
        self.submit("runner", ability)
        self.reject("runner", ability)
        self.assertIn(dict(ability, card_instance_id=second, subroutine_indices=[], server_id=None), self.view("runner")["legal_actions"])
        self.submit("runner", dict(ability, card_instance_id=second))
        self.assertEqual(self.session.state.runner.credits, 9)
        self.assertTrue(all(c["counters"] == {"credit": 6} for c in self.view("corp")["installed_cards"]))

    def test_hidden_card_type_does_not_change_runner_priority(self):
        views = []
        base = self.session.state.model_copy(deep=True)
        for title in ("Offworld Office", "Regolith Mining License", "Nico Campaign"):
            self.session.state = base.model_copy(deep=True)
            card = self.hand("corp", title)
            self.submit("corp", {"type": "install_card", "card_instance_id": card, "server_id": "new_remote"})
            self.pass_windows()
            self.submit("corp", {"type": "gain_credit"})
            self.pass_windows()
            self.submit("corp", {"type": "gain_credit"})
            # Pass the last action and discard windows, stopping at Runner start.
            while self.session.state.turn.active_player is PlayerRole.CORP:
                self.submit("corp", {"type": "pass_priority"})
            view = self.view("runner")
            views.append((view["decision"], view["legal_actions"], view["installed_cards"][0]["card"]))
        self.assertTrue(all(v == views[0] for v in views))
        self.assertEqual(views[0][0], {"player": "corp", "type": "paid_ability_window", "cards_to_discard": None})

    def test_run_waits_for_rez_and_cannot_score_or_spend_clicks_inside_it(self):
        regolith = self.hand("corp", "Regolith Mining License")
        self.submit("corp", {"type": "install_card", "card_instance_id": regolith, "server_id": "new_remote"})
        self.next_runner_action()
        self.submit("runner", {"type": "run_server", "server_id": 1})
        self.reject("runner", {"type": "continue_run"})
        self.reject("corp", {"type": "score_agenda", "card_instance_id": regolith})
        self.submit("corp", {"type": "rez_card", "card_instance_id": regolith})
        self.reject("corp", {"type": "use_card_ability", "card_instance_id": regolith, "ability_id": "take_credits"})
        self.pass_windows()
        self.submit("runner", {"type": "jack_out"})
        self.assertIsNone(self.session.state.run)
        self.assertEqual(self.session.state.card_instances[UUID(regolith)].counters, {"credit": 15})
