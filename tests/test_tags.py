"""Authenticated tag decisions, nested reactions, persistence, and run timing.

These scenarios cover boundaries that complement the growing core game path.
Fixtures arrange a timing point; all player decisions use the real HTTP API.
"""

from test_reactions import ReactionScenario

from application.sessions import advance_ai_decisions
from game.card_movement import trash_installed_card
from game.effects import resolve_effect
from game.models import CardZone, PlayerRole, ServerArea, ServerLocation, ZoneLocation
from game.tags import give_tags
from game.turns import advance_turn, hand_cards


class TagTests(ReactionScenario):
    def add(self, title, role=PlayerRole.CORP, **kwargs):
        return self.add_card(next(k for k, c in self.cards.items() if c.title == title), role, **kwargs)

    def choose(self, role, option):
        return self.submit(role, {"type": "choose_effect_option", "option_id": option})

    def settle(self):
        for _ in range(40):
            if not self.state.window:
                return
            role = self.state.window.player
            if self.state.window.kind == "choice":
                self.choose(role, "gain_credits")
            elif self.state.window.kind == "reaction":
                self.submit(role, self.reactions(role)[0])
            else:
                self.submit(role, {"type": "pass_priority"})
        self.fail("Windows did not settle")

    def ice(self, title, *, rezzed=True):
        self.turn(PlayerRole.RUNNER, clicks=20)
        target = self.add(title)
        card = self.state.card_instances[target]
        card.location = ZoneLocation(zone=CardZone.PLAY_AREA, server=ServerLocation(
            server_id="hq", area=ServerArea.ICE, ice_distance_from_server=0))
        card.rezzed = rezzed
        card.face_up = rezzed
        self.state.corp.credits = 50
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        return target

    def public_trail(self, *, credits=8):
        self.turn(PlayerRole.CORP, clicks=20)
        self.state.corp.credits = 30
        self.state.runner.credits = credits
        self.state.runner_last_turn_successful = True
        target = self.add("Public Trail", installed=False)
        self.submit(PlayerRole.CORP, {"type": "play_card", "card_instance_id": str(target)})
        return target

    def test_public_trail_payment_boundary_and_private_choice(self):
        target = self.public_trail()
        self.assertEqual(self.state.card_instances[target].location.zone, CardZone.PLAY_AREA)
        view = self.view(PlayerRole.RUNNER)
        self.assertEqual(view["decision"], {"type": "choose_effect", "player": "runner", "cards_to_discard": None})
        self.assertEqual({a["option_id"] for a in view["legal_actions"]}, {"pay", "take_tag"})
        self.assertEqual(self.view(PlayerRole.CORP)["effect_options"], {})
        self.reject(PlayerRole.CORP, {"type": "choose_effect_option", "option_id": "pay"})
        self.reject(PlayerRole.RUNNER, {"type": "choose_effect_option", "option_id": "bogus"})
        self.reject(PlayerRole.RUNNER, {"type": "gain_credit"})
        self.choose(PlayerRole.RUNNER, "pay")
        self.assertEqual(self.state.runner.credits, 0)
        self.assertEqual(self.state.runner.tags, 0)
        self.assertEqual(self.state.card_instances[target].location.zone, CardZone.DISCARD_PILE)

    def test_public_trail_cannot_pay_partial_cost_and_can_decline_affordable_cost(self):
        self.public_trail(credits=7)
        self.assertEqual([a["option_id"] for a in self.view(PlayerRole.RUNNER)["legal_actions"]], ["take_tag"])
        self.reject(PlayerRole.RUNNER, {"type": "choose_effect_option", "option_id": "pay"})
        self.choose(PlayerRole.RUNNER, "take_tag")
        self.assertEqual((self.state.runner.tags, self.state.runner.credits), (1, 7))
        self.settle()
        self.public_trail(credits=8)
        self.choose(PlayerRole.RUNNER, "take_tag")
        self.assertEqual((self.state.runner.tags, self.state.runner.credits), (2, 8))

    def test_reality_plus_nested_operation_cleanup_and_first_packet_each_turn(self):
        self.identity("30051")
        target = self.public_trail()
        before = self.state.corp.credits
        self.choose(PlayerRole.RUNNER, "take_tag")
        self.assertEqual(self.state.window.kind, "choice")
        self.assertEqual(self.state.window.player, PlayerRole.CORP)
        self.assertEqual(self.state.card_instances[target].location.zone, CardZone.PLAY_AREA)
        self.choose(PlayerRole.CORP, "gain_credits")
        self.assertEqual(self.state.corp.credits, before + 2)
        self.assertEqual(self.state.card_instances[target].location.zone, CardZone.DISCARD_PILE)
        self.settle()
        give_tags(self.state, 2, self.cards)
        self.assertEqual(self.state.runner.tags, 3)
        self.assertIsNone(self.state.window)
        self.turn(PlayerRole.RUNNER)
        hand = len(hand_cards(self.state, self.state.corp))
        give_tags(self.state, 2, self.cards)
        self.choose(PlayerRole.CORP, "draw_cards")
        self.assertEqual(len(hand_cards(self.state, self.state.corp)), hand + 2)
        self.assertEqual(self.state.runner.tags, 5)

    def test_first_occurrence_recorded_without_identity_and_orbital_uses_shared_tags(self):
        self.turn(PlayerRole.CORP)
        give_tags(self.state, 1, self.cards)
        self.identity("30051")
        give_tags(self.state, 1, self.cards)
        self.assertIsNone(self.state.window)
        self.turn(PlayerRole.CORP)
        self.state.runner.tags = 0
        instruction = self.cards[next(k for k, c in self.cards.items() if c.title == "Orbital Superiority")].score_instructions[0]
        resolve_effect(self.state, self.state.corp, instruction, definitions=self.cards)
        self.assertEqual(self.state.runner.tags, 1)
        self.choose(PlayerRole.CORP, "gain_credits")

    def test_headline_stolen_and_scored_give_tags_with_corp_reality_reward(self):
        self.identity("30051")
        target = self.add("Tomorrowʼs Headline")
        self.turn(PlayerRole.RUNNER, clicks=20)
        self.run_to_breach(self.state.card_instances[target].location.server.server_id)
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(target)})
        self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
        self.assertEqual(self.state.runner.tags, 1)
        self.assertEqual(self.state.window.player, PlayerRole.CORP)
        self.choose(PlayerRole.CORP, "gain_credits")
        self.settle()
        self.assertIsNone(self.state.run)
        self.turn(PlayerRole.CORP)
        target = self.add("Tomorrowʼs Headline")
        self.state.card_instances[target].counters["advancement"] = 3
        from game.models import PriorityWindow
        self.state.window = PriorityWindow(kind="paid", player=PlayerRole.CORP)
        self.submit(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": str(target)})
        self.settle()
        self.assertEqual(self.state.runner.tags, 2)

    def test_funhouse_encounter_end_run_and_take_tag_then_pay_subroutine(self):
        self.ice("Funhouse")
        self.pass_paid()
        self.choose(PlayerRole.RUNNER, "end_run")
        self.assertIsNone(self.state.run)
        self.assertEqual(self.state.runner.tags, 0)
        # A separate encounter on the same board, with no replay of the choice.
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        self.pass_paid()
        self.state.runner.credits = 4
        self.choose(PlayerRole.RUNNER, "take_tag")
        self.pass_paid()
        self.choose(PlayerRole.RUNNER, "pay")
        self.assertEqual((self.state.runner.tags, self.state.runner.credits), (1, 0))
        self.assertEqual(self.state.run.phase, "movement")
        self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        self.assertIsNone(self.state.run)

    def test_funhouse_nested_reality_and_run_credits_pay_subroutine(self):
        self.identity("30051")
        self.ice("Funhouse")
        self.pass_paid()
        self.state.runner.credits = 1
        self.state.run.bad_publicity_credits = 3
        self.choose(PlayerRole.RUNNER, "take_tag")
        self.choose(PlayerRole.CORP, "gain_credits")
        self.pass_paid()
        self.choose(PlayerRole.RUNNER, "pay")
        self.assertEqual((self.state.runner.credits, self.state.run.bad_publicity_credits), (0, 0))
        self.assertEqual(self.state.run.phase, "movement")
        self.assertEqual(self.state.run.next_subroutine, 1)

    def test_breaking_funhouse_subroutine_does_not_skip_its_encounter_ability(self):
        target = self.ice("Funhouse")
        botulus = self.add("Botulus", PlayerRole.RUNNER)
        self.state.card_instances[botulus].location = ZoneLocation(zone=CardZone.PLAY_AREA, host_card_instance_id=target)
        self.state.card_instances[botulus].counters["virus"] = 1
        self.pass_paid()
        self.choose(PlayerRole.RUNNER, "take_tag")
        self.submit(PlayerRole.RUNNER, {"type": "use_card_ability", "card_instance_id": str(botulus),
                    "ability_id": "break_host", "subroutine_indices": [0]})
        self.pass_paid()
        self.assertEqual(self.state.runner.tags, 1)
        self.assertEqual(self.state.run.phase, "movement")

    def test_ping_rez_and_pharos_tag_before_end_run(self):
        self.identity("30051")
        target = self.ice("Ping", rezzed=False)
        while self.state.run.phase != "approach":
            self.submit(self.state.window.player, {"type": "pass_priority"})
        self.submit(PlayerRole.CORP, {"type": "rez_card", "card_instance_id": str(target)})
        self.assertEqual(self.state.runner.tags, 1)
        self.choose(PlayerRole.CORP, "gain_credits")
        self.settle()
        self.assertIsNone(self.state.run)
        trash_installed_card(self.state, self.state.card_instances[target])
        self.ice("Pharos")
        self.pass_paid()
        self.assertIsNotNone(self.state.run)
        self.choose(PlayerRole.CORP, "gain_credits")
        self.assertIsNone(self.state.run)
        self.assertEqual(self.state.runner.tags, 2)

    def test_amaze_persists_only_rezzed_access_trash_and_matches_server(self):
        for rezzed in (True, False):
            with self.subTest(rezzed=rezzed):
                self.turn(PlayerRole.RUNNER, clicks=20)
                self.state.runner.credits = 30
                target = self.add("AMAZE Amusements")
                card = self.state.card_instances[target]
                card.rezzed = card.face_up = rezzed
                server = card.location.server.server_id
                agenda = self.add("Offworld Office")
                self.state.card_instances[agenda].location = card.location.model_copy(deep=True)
                before = self.state.runner.tags
                self.run_to_breach(server)
                self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(target)})
                self.submit(PlayerRole.RUNNER, {"type": "finish_access", "trash": True})
                self.settle()
                self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(agenda)})
                self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
                self.settle()
                self.assertEqual(self.state.runner.tags, before + (2 if rezzed else 0))
                self.assertIsNone(self.state.run)

    def test_amaze_multiple_copies_no_agenda_and_other_server(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        first = self.add("AMAZE Amusements")
        second = self.add("AMAZE Amusements")
        wrong_server = self.add("AMAZE Amusements")
        agenda = self.add("Offworld Office")
        for target in (second, agenda):
            self.state.card_instances[target].location = self.state.card_instances[first].location.model_copy(deep=True)
        self.run_to_breach(self.state.card_instances[first].location.server.server_id)
        self.finish_breach()
        self.settle()
        self.assertEqual(self.state.runner.tags, 4)

        self.assertTrue(self.state.card_instances[wrong_server].installed)
        self.run_to_breach(self.state.card_instances[first].location.server.server_id)
        self.finish_breach()
        self.settle()
        self.assertEqual(self.state.runner.tags, 4)

    def test_persistent_amaze_retains_context_through_nested_run_end_reactions(self):
        self.identity("30051")
        self.turn(PlayerRole.RUNNER, clicks=20)
        self.state.runner.credits = 20
        target = self.add("AMAZE Amusements")
        agenda = self.add("Offworld Office")
        self.state.card_instances[agenda].location = self.state.card_instances[target].location.model_copy(deep=True)
        self.run_to_breach(self.state.card_instances[target].location.server.server_id)
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(target)})
        self.submit(PlayerRole.RUNNER, {"type": "finish_access", "trash": True})
        self.settle()
        self.state.run.bad_publicity_credits = 8
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(agenda)})
        self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
        self.assertEqual(self.state.run.phase, "ended")
        self.assertEqual(self.state.run.bad_publicity_credits, 0)
        self.submit(PlayerRole.CORP, self.reactions(PlayerRole.CORP)[0])
        self.assertEqual(self.state.runner.tags, 2)
        self.assertIsNotNone(self.state.run)
        self.choose(PlayerRole.CORP, "gain_credits")
        self.assertIsNone(self.state.run)
        self.settle()
        self.run_to_breach("hq")
        self.finish_breach()
        self.settle()
        self.assertEqual(self.state.runner.tags, 2)

    def test_tagged_basic_actions_and_rejected_resource_targets_are_atomic(self):
        resource = self.add("Smartware Distributor", PlayerRole.RUNNER)
        program = self.add("Mayfly", PlayerRole.RUNNER)
        self.turn(PlayerRole.CORP)
        self.state.corp.credits = 2
        self.reject(PlayerRole.CORP, {"type": "trash_resource", "card_instance_id": str(resource)})
        self.state.runner.tags = 1
        self.reject(PlayerRole.CORP, {"type": "trash_resource", "card_instance_id": str(program)})
        self.submit(PlayerRole.CORP, {"type": "trash_resource", "card_instance_id": str(resource)})
        self.assertFalse(self.state.card_instances[resource].installed)
        self.assertEqual((self.state.corp.credits, self.state.corp.clicks_remaining), (0, 3))
        self.turn(PlayerRole.RUNNER)
        self.state.runner.credits = 2
        self.submit(PlayerRole.RUNNER, {"type": "remove_tag"})
        self.assertEqual((self.state.runner.tags, self.state.runner.credits), (0, 0))

    def test_public_trail_last_runner_turn_history_and_ai_choice(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        self.run_to_breach("hq")
        self.finish_breach()
        self.settle()
        self.state.runner.clicks_remaining = 0
        self.state.turn.step = "next_turn"
        advance_turn(self.state, self.cards)
        self.assertTrue(self.state.runner_last_turn_successful)
        self.turn(PlayerRole.RUNNER)
        self.state.runner.clicks_remaining = 0
        self.state.turn.step = "next_turn"
        advance_turn(self.state, self.cards)
        self.assertFalse(self.state.runner_last_turn_successful)
        self.turn(PlayerRole.CORP)
        target = self.add("Public Trail", installed=False)
        self.state.corp.credits = 10
        self.state.runner_last_turn_successful = False
        self.reject(PlayerRole.CORP, {"type": "play_card", "card_instance_id": str(target)})
        self.state.runner_last_turn_successful = True
        self.state.runner.credits = 0
        self.identity("30051")
        self.submit(PlayerRole.CORP, {"type": "play_card", "card_instance_id": str(target)})
        advanced = advance_ai_decisions(self.state, {self.state.runner.player_id, self.state.corp.player_id}, self.cards)
        self.assertIsNone(advanced.window)
        self.assertEqual(advanced.runner.tags, 1)

    def test_catalog_hides_executable_instructions(self):
        response = self.client.get("/card-catalog")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('"play_requirement"', response.text)
