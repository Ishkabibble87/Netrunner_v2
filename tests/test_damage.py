"""Damage timing, simultaneous trashing and lethal boundaries through the API."""

from unittest.mock import patch

from test_reactions import ReactionScenario

from game.card_movement import trash_installed_card
from game.damage import deal_damage
from game.models import CardZone, PlayerRole, ServerArea, ServerLocation, ZoneLocation
from game.turns import advance_turn, hand_cards


class DamageTests(ReactionScenario):
    """Exercise the boundaries not naturally reached in the growing game path."""

    def code(self, title):
        return next(code for code, card in self.cards.items() if card.title == title)

    def add(self, title, role=PlayerRole.CORP, **kwargs):
        return self.add_card(self.code(title), role, **kwargs)

    def settle(self):
        for _ in range(60):
            if not self.state.window:
                return
            role = self.state.window.player
            self.submit(role, self.reactions(role)[0] if self.state.window.kind == "reaction"
                        else {"type": "pass_priority"})
        self.fail("Windows did not settle")

    def grip_size(self, count, title="Sure Gamble"):
        for card in hand_cards(self.state, self.state.runner):
            card.face_up = True
            card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=self.state.runner.player_id)
        return [self.add(title, PlayerRole.RUNNER, installed=False) for _ in range(count)]

    def encounter(self, title, *, broken=()):
        self.turn(PlayerRole.RUNNER, clicks=20)
        ice_id = self.add(title)
        self.state.card_instances[ice_id].location = ZoneLocation(zone=CardZone.PLAY_AREA,
            server=ServerLocation(server_id="hq", area=ServerArea.ICE, ice_distance_from_server=0))
        botulus = None
        if broken:
            botulus = self.add("Botulus", PlayerRole.RUNNER)
            self.state.card_instances[botulus].location = ZoneLocation(zone=CardZone.PLAY_AREA, host_card_instance_id=ice_id)
            self.state.card_instances[botulus].counters["virus"] = 3
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        if broken:
            for _ in range(20):
                if self.state.run.phase == "encounter" and self.state.window.player is PlayerRole.RUNNER:
                    break
                self.submit(self.state.window.player, {"type": "pass_priority"})
            for index in broken:
                self.submit(PlayerRole.RUNNER, {"type": "use_card_ability", "card_instance_id": str(botulus),
                    "ability_id": "break_host", "subroutine_indices": [index]})
        # Without breakers, the rezzed encounter resolves after automatic passes.
        return ice_id

    def play_neurospike(self):
        self.turn(PlayerRole.CORP, clicks=20)
        self.state.corp.credits = 50
        return self.add("Neurospike", installed=False)

    def test_damage_packet_random_selection_exact_grip_and_zero(self):
        selected = self.grip_size(3)
        with patch("game.damage.SystemRandom") as random:
            random.return_value.sample.side_effect = lambda cards, n: list(reversed(cards))[:n]
            trashed = deal_damage(self.state, 2, "net")
        self.assertEqual([c.instance_id for c in trashed], list(reversed(selected))[:2])
        self.assertTrue(all(self.state.card_instances[i].face_up for i in selected[1:]))
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), 1)
        deal_damage(self.state, 1, "meat")
        self.assertIsNone(self.state.winner)
        self.assertEqual(deal_damage(self.state, 0, "net"), ())
        self.assertIsNone(self.state.winner)

    def test_neurospike_uses_current_turn_total_and_lethal_stops_actions(self):
        self.grip_size(2)
        operation = self.play_neurospike()
        self.state.turn.printed_agenda_points_scored = 3
        view = self.submit(PlayerRole.CORP, {"type": "play_card", "card_instance_id": str(operation)})
        self.assertEqual(view["winner"], "corp")
        self.assertIsNone(view["decision"])
        for role in PlayerRole:
            view = self.view(role)
            self.assertEqual(view["legal_actions"], [])
            self.reject(role, {"type": "gain_credit"})
        self.assertIsNone(self.state.run)
        self.assertIsNone(self.state.window)
        self.assertEqual(self.state.suspended_windows, [])

    def test_neurospike_zero_and_turn_reset(self):
        self.grip_size(4)
        operation = self.play_neurospike()
        self.submit(PlayerRole.CORP, {"type": "play_card", "card_instance_id": str(operation)})
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), 4)
        self.state.turn.printed_agenda_points_scored = 6
        self.settle()
        self.state.corp.clicks_remaining = 1
        self.submit(PlayerRole.CORP, {"type": "gain_credit"})
        self.settle()
        selection = next((a for a in self.view(PlayerRole.CORP)["legal_actions"] if a["type"] == "discard_cards"), None)
        if selection:
            self.submit(PlayerRole.CORP, {"type": "discard_cards", "card_instance_ids":
                selection["eligible_card_instance_ids"][:selection["required_count"]]})
            self.settle()
        self.assertEqual(self.state.turn.active_player, PlayerRole.RUNNER)
        self.assertEqual(self.state.turn.printed_agenda_points_scored, 0)

    def test_urtica_unrezzed_access_precedes_trash_and_scales_with_advancement(self):
        self.grip_size(4)
        self.turn(PlayerRole.RUNNER, clicks=10)
        target = self.add("Urtica Cipher")
        card = self.state.card_instances[target]
        card.rezzed = card.face_up = False
        card.counters["advancement"] = 1
        self.run_to_breach(card.location.server.server_id)
        view = self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(target)})
        self.assertEqual(len(view["you"]["hand"]), 1)
        self.assertEqual(view["run"]["current_card"]["title"], "Urtica Cipher")
        self.assertIsNone(self.state.winner)
        self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
        self.assertTrue(self.state.card_instances[target].installed)

    def test_urtica_in_hq_does_not_fire(self):
        self.grip_size(4)
        self.turn(PlayerRole.RUNNER)
        for card in hand_cards(self.state, self.state.corp):
            card.face_up = False
            card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=self.state.corp.player_id)
        self.add("Urtica Cipher", installed=False)
        self.run_to_breach("hq")
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": "hq"})
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), 4)

    def test_urtica_flatline_ends_access_without_run_end_reactions(self):
        self.grip_size(1)
        self.turn(PlayerRole.RUNNER)
        target = self.add("Urtica Cipher")
        self.run_to_breach(self.state.card_instances[target].location.server.server_id)
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(target)})
        self.assertEqual(self.state.winner, PlayerRole.CORP)
        self.assertIsNone(self.state.run)
        self.assertIsNone(self.state.window)

    def test_clearinghouse_decline_then_trash_preserves_counter_amount(self):
        self.grip_size(5)
        target = self.add("Clearinghouse")
        self.state.card_instances[target].counters["advancement"] = 3
        self.turn(PlayerRole.CORP, step="turn_begin")
        advance_turn(self.state, self.cards)
        decline = dict(self.reactions(PlayerRole.CORP)[0], decline=True)
        self.submit(PlayerRole.CORP, decline)
        self.assertTrue(self.state.card_instances[target].installed)
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), 5)
        self.turn(PlayerRole.CORP, step="turn_begin")
        advance_turn(self.state, self.cards)
        self.submit(PlayerRole.CORP, self.reactions(PlayerRole.CORP)[0])
        self.assertFalse(self.state.card_instances[target].installed)
        self.assertEqual(self.state.card_instances[target].counters, {})
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), 2)

    def test_unrezzed_clearinghouse_does_not_trigger_and_assets_can_advance(self):
        self.turn(PlayerRole.CORP, clicks=10)
        self.state.corp.credits = 20
        for title in ("Clearinghouse", "Urtica Cipher"):
            target = self.add(title)
            self.state.card_instances[target].rezzed = self.state.card_instances[target].face_up = False
            self.settle()
            self.submit(PlayerRole.CORP, {"type": "advance_card", "card_instance_id": str(target)})
            self.assertEqual(self.state.card_instances[target].counters["advancement"], 1)
            self.settle()
        self.turn(PlayerRole.CORP, step="turn_begin")
        advance_turn(self.state, self.cards)
        self.assertFalse(self.reactions(PlayerRole.CORP))

    def test_tithe_damage_then_credit_and_fatal_packet_stops_credit(self):
        self.grip_size(3)
        credits = self.state.corp.credits
        self.encounter("Tithe")
        self.settle()
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), 2)
        self.assertEqual(self.state.corp.credits, credits + 1)
        self.assertEqual(self.state.run.phase, "movement")
        self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        self.settle()
        self.grip_size(0)
        credits = self.state.corp.credits
        self.encounter("Tithe")
        self.settle()
        self.assertEqual(self.state.winner, PlayerRole.CORP)
        self.assertEqual(self.state.corp.credits, credits)

    def test_diviner_odd_cost_ends_run_even_cost_continues(self):
        for title, ends in (("Sure Gamble", True), ("Jailbreak", False)):
            with self.subTest(title=title):
                self.grip_size(2, title)
                self.encounter("Diviner")
                self.settle()
                self.assertEqual(self.state.run is None, ends)
                self.assertEqual(len(hand_cards(self.state, self.state.runner)), 1)
                if self.state.run:
                    self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
                    self.settle()
                # Remove the previous encounter's ice before running HQ again.
                for card in self.state.card_instances.values():
                    if card.installed and self.cards[card.definition_id].title == "Diviner":
                        trash_installed_card(self.state, card)

    def test_karuna_jack_out_or_continue_resumes_without_repeating_damage(self):
        for jack_out in (True, False):
            with self.subTest(jack_out=jack_out):
                self.grip_size(5)
                self.encounter("Karunā")
                self.settle()
                self.assertEqual(len(hand_cards(self.state, self.state.runner)), 3)
                self.assertEqual(self.state.run.phase, "subroutine_choice")
                view = self.view(PlayerRole.RUNNER)
                self.assertEqual({a["type"] for a in view["legal_actions"]}, {"continue_run", "jack_out"})
                self.reject(PlayerRole.RUNNER, {"type": "finish_access"})
                self.submit(PlayerRole.RUNNER, {"type": "jack_out" if jack_out else "continue_run"})
                self.assertEqual(len(hand_cards(self.state, self.state.runner)), 3 if jack_out else 1)
                self.settle()
                if not jack_out:
                    self.assertEqual(self.state.run.phase, "movement")
                    self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
                    self.settle()
                for card in self.state.card_instances.values():
                    if card.installed and self.cards[card.definition_id].title == "Karunā":
                        trash_installed_card(self.state, card)

    def test_orbital_both_branches_and_scored_points_accumulate(self):
        self.grip_size(5)
        self.turn(PlayerRole.CORP, clicks=20)
        for tagged in (False, True):
            self.state.runner.tags = int(tagged)
            target = self.add("Orbital Superiority")
            self.state.card_instances[target].counters["advancement"] = 4
            self.state.turn.step = "before_action"
            advance_turn(self.state, self.cards)
            self.submit(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": str(target)})
            self.settle()
            self.assertEqual(self.state.runner.tags, 1)
            self.assertEqual(len(hand_cards(self.state, self.state.runner)), 1 if tagged else 5)
        self.assertEqual(self.state.turn.printed_agenda_points_scored, 4)
        operation = self.add("Neurospike", installed=False)
        self.state.corp.credits = 20
        self.submit(PlayerRole.CORP, {"type": "play_card", "card_instance_id": str(operation)})
        self.assertEqual(self.state.winner, PlayerRole.CORP)

    def test_broken_karuna_subroutines_skip_damage_and_choice(self):
        self.grip_size(5)
        self.encounter("Karunā", broken=(0,))
        self.settle()
        self.assertEqual(self.state.run.phase, "movement")
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), 3)

    def test_broken_second_karuna_subroutine_does_not_replay_first(self):
        self.grip_size(5)
        self.encounter("Karunā", broken=(1,))
        self.settle()
        self.assertEqual(self.state.run.phase, "subroutine_choice")
        self.submit(PlayerRole.RUNNER, {"type": "continue_run"})
        self.assertEqual(self.state.run.phase, "movement")
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), 3)

    def test_scoring_victory_precedes_orbital_damage(self):
        self.grip_size(0)
        self.state.runner.tags = 1
        for _ in range(3):
            agenda = self.add("Offworld Office")
            trash_installed_card(self.state, self.state.card_instances[agenda])
            self.state.card_instances[agenda].location = ZoneLocation(zone=CardZone.SCORE_AREA,
                player_id=self.state.corp.player_id)
        self.turn(PlayerRole.CORP)
        target = self.add("Orbital Superiority")
        self.state.card_instances[target].counters["advancement"] = 4
        self.state.turn.step = "before_action"
        advance_turn(self.state, self.cards)
        self.submit(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": str(target)})
        self.assertEqual(self.state.winner, PlayerRole.CORP)
        self.assertEqual(self.state.runner.tags, 1)
        self.assertIsNone(self.state.window)

    def test_card_catalog_serializes_all_new_authored_abilities(self):
        response = self.client.get("/card-catalog")
        self.assertEqual(response.status_code, 200, response.text)
        catalog = {c["title"]:c for c in response.json()}
        self.assertIn("Urtica Cipher", catalog)
        self.assertIn("Karunā", catalog)
        self.assertNotIn("advanceable", catalog["Urtica Cipher"])
