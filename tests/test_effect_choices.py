"""Authenticated modal choices, private targets, costs, and run continuations.

The scenarios isolate rules boundaries beyond the single growing game path.
Every decision crosses the real command and player-view API boundary.
"""

from test_reactions import ReactionScenario

from application.sessions import advance_ai_decisions
from game.models import CardZone, PlayerRole, PriorityWindow, ZoneLocation
from game.turns import hand_cards


class EffectChoiceTests(ReactionScenario):
    def add(self, title, role=PlayerRole.CORP, **kwargs):
        return self.add_card(next(k for k, c in self.cards.items() if c.title == title), role, **kwargs)

    def choose(self, role, option):
        return self.submit(role, {"type": "choose_effect_option", "option_id": option})

    def select(self, role, cards):
        return self.submit(role, {"type": "select_effect_cards", "card_instance_ids": [str(i) for i in cards]})

    def play(self, title, role=PlayerRole.CORP):
        self.turn(role, clicks=20)
        player = self.state.corp if role is PlayerRole.CORP else self.state.runner
        player.credits = 30
        card = self.add(title, role, installed=False)
        self.submit(role, {"type": "play_card", "card_instance_id": str(card)})
        return card

    def eligible(self, role):
        return self.view(role)["legal_actions"][0]["eligible_card_instance_ids"]

    def approach(self, title, *, central=None):
        self.turn(PlayerRole.RUNNER, clicks=20)
        source = self.add(title)
        server = self.state.card_instances[source].location.server.server_id
        if central:
            self.state.card_instances[source].location.server.server_id = central
            self.state.servers.remove(server)
            server = central
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": server})
        self.pass_paid()
        self.submit(PlayerRole.RUNNER, {"type": "continue_run"})
        self.pass_paid()
        self.assertFalse(self.state.run.successful)
        return source

    def resolve_source(self, role, source):
        action = next(a for a in self.reactions(role) if a["card_instance_id"] == str(source))
        return self.submit(role, action)

    def test_wildcat_chooser_and_recipient_are_distinct(self):
        for option in ("gain_credits", "draw_cards"):
            with self.subTest(option=option):
                card = self.play("Wildcat Strike", PlayerRole.RUNNER)
                credits, hand = self.state.runner.credits, len(hand_cards(self.state, self.state.runner))
                corp = self.state.corp.credits
                self.assertEqual(self.view(PlayerRole.RUNNER)["effect_options"], {})
                self.reject(PlayerRole.RUNNER, {"type": "choose_effect_option", "option_id": option})
                self.choose(PlayerRole.CORP, option)
                self.assertEqual(self.state.corp.credits, corp)
                self.assertEqual(self.state.runner.credits, credits + (6 if option == "gain_credits" else 0))
                self.assertEqual(len(hand_cards(self.state, self.state.runner)), hand + (4 if option == "draw_cards" else 0))
                self.assertEqual(self.state.card_instances[card].location.zone, CardZone.DISCARD_PILE)

    def test_planogram_modes_in_either_order_once_each_and_early_finish(self):
        for order in (("draw_cards", "gain_credits"), ("gain_credits", "draw_cards")):
            with self.subTest(order=order):
                self.state.runner.tags = 1
                card = self.play("Predictive Planogram")
                credits, hand = self.state.corp.credits, len(hand_cards(self.state, self.state.corp))
                self.choose(PlayerRole.CORP, order[0])
                self.assertEqual(self.state.card_instances[card].location.zone, CardZone.PLAY_AREA)
                self.reject(PlayerRole.CORP, {"type": "choose_effect_option", "option_id": order[0]})
                self.choose(PlayerRole.CORP, order[1])
                self.assertEqual(self.state.corp.credits, credits + 3)
                self.assertEqual(len(hand_cards(self.state, self.state.corp)), hand + 3)
                self.assertEqual(self.state.card_instances[card].location.zone, CardZone.DISCARD_PILE)
        card = self.play("Predictive Planogram")
        self.choose(PlayerRole.CORP, "gain_credits")
        self.choose(PlayerRole.CORP, "finish")
        self.assertEqual(self.state.card_instances[card].location.zone, CardZone.DISCARD_PILE)
        self.state.runner.tags = 0
        card = self.play("Predictive Planogram")
        self.choose(PlayerRole.CORP, "draw_cards")
        self.assertEqual(self.state.card_instances[card].location.zone, CardZone.DISCARD_PILE)

    def test_hansei_gains_before_private_mandatory_trash_and_handles_empty_hq(self):
        card = self.play("Hansei Review")
        self.assertEqual(self.state.corp.credits, 35)
        eligible = self.eligible(PlayerRole.CORP)
        self.assertNotIn(str(card), eligible)
        self.assertEqual(self.view(PlayerRole.RUNNER)["selection_cards"], [])
        self.assertEqual(self.view(PlayerRole.RUNNER)["legal_actions"], [])
        self.reject(PlayerRole.RUNNER, {"type": "select_effect_cards", "card_instance_ids": [eligible[0]]})
        self.reject(PlayerRole.CORP, {"type": "select_effect_cards", "card_instance_ids": []})
        self.select(PlayerRole.CORP, [eligible[0]])
        self.assertFalse(self.state.card_instances[next(i for i in self.state.card_instances if str(i) == eligible[0])].face_up)
        for item in hand_cards(self.state, self.state.corp):
            item.face_up = False
            item.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=item.owner_id)
        card = self.play("Hansei Review")
        self.assertEqual(self.state.card_instances[card].location.zone, CardZone.DISCARD_PILE)

    def test_retribution_targets_and_host_cleanup(self):
        resource = self.add("Smartware Distributor", PlayerRole.RUNNER)
        host = self.add("Mayfly", PlayerRole.RUNNER)
        hosted = self.add("Botulus", PlayerRole.RUNNER)
        self.state.card_instances[hosted].location.host_card_instance_id = host
        self.state.runner.tags = 1
        card = self.play("Retribution")
        self.assertNotIn(str(resource), self.eligible(PlayerRole.CORP))
        self.reject(PlayerRole.CORP, {"type": "select_effect_cards", "card_instance_ids": [str(resource)]})
        self.select(PlayerRole.CORP, [host])
        self.assertFalse(self.state.card_instances[host].installed)
        self.assertFalse(self.state.card_instances[hosted].installed)
        self.assertEqual(self.state.card_instances[card].location.zone, CardZone.DISCARD_PILE)

    def test_seamless_tracks_installs_and_does_not_trigger_basic_advance(self):
        self.identity("30035")  # Built to Last
        target = self.add("Regolith Mining License")
        new = self.add("Offworld Office", installed=False)
        self.turn(PlayerRole.CORP, clicks=20)
        self.submit(PlayerRole.CORP, {"type": "install_card", "card_instance_id": str(new), "server_id": "new_remote"})
        self.pass_paid()
        self.assertEqual(self.state.card_instances[new].installed_turn, self.state.turn.number)
        event = self.add("Seamless Launch", installed=False)
        before = self.state.corp.credits
        self.submit(PlayerRole.CORP, {"type": "play_card", "card_instance_id": str(event)})
        self.assertNotIn(str(new), self.eligible(PlayerRole.CORP))
        self.assertIn(str(target), self.eligible(PlayerRole.CORP))
        # Seamless permits a Runner card even if the Runner installed it this turn.
        runner_card = self.add("Smartware Distributor", PlayerRole.RUNNER)
        self.state.card_instances[runner_card].installed_turn = self.state.turn.number
        self.assertIn(str(runner_card), self.eligible(PlayerRole.CORP))
        self.reject(PlayerRole.CORP, {"type": "select_effect_cards", "card_instance_ids": [str(new)]})
        self.select(PlayerRole.CORP, [target])
        self.assertEqual(self.state.card_instances[target].counters["advancement"], 2)
        self.assertEqual(self.state.corp.credits, before - self.cards["30040"].play_cost)
        self.assertNotIn("advance:corp", self.state.turn.occurrences)

    def test_above_the_law_score_targets_are_optional_and_resource_only(self):
        for decline in (True, False):
            self.turn(PlayerRole.CORP, clicks=20)
            resource = self.add("Smartware Distributor", PlayerRole.RUNNER)
            hardware = self.add("Carnivore", PlayerRole.RUNNER)
            agenda = self.add("Above the Law")
            self.state.card_instances[agenda].counters["advancement"] = self.cards["30060"].advancement_requirement
            self.state.window = PriorityWindow(kind="paid", player=PlayerRole.CORP)
            self.submit(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": str(agenda)})
            self.resolve_source(PlayerRole.CORP, agenda)
            self.assertNotIn(str(hardware), self.eligible(PlayerRole.CORP))
            self.select(PlayerRole.CORP, [] if decline else [resource])
            self.assertEqual(self.state.card_instances[resource].installed, decline)
            self.assertEqual(self.state.card_instances[agenda].location.zone, CardZone.SCORE_AREA)

    def test_manegarm_alternative_costs_before_success_and_payment_failure(self):
        for option in ("pay_clicks", "pay_credits", "end_run"):
            source = self.approach("Manegarm Skunkworks")
            self.state.runner.credits = 5
            self.resolve_source(PlayerRole.CORP, source)
            before = self.state.runner.clicks_remaining
            self.choose(PlayerRole.RUNNER, option)
            if option == "end_run":
                self.assertIsNone(self.state.run)
                self.assertFalse(self.state.turn.successful_run)
            else:
                self.assertTrue(self.state.run.successful)
                self.assertEqual(self.state.runner.clicks_remaining, before - (2 if option == "pay_clicks" else 0))
                self.assertEqual(self.state.runner.credits, 0 if option == "pay_credits" else 5)
                self.finish_breach()
                self.pass_paid()
        source = self.approach("Manegarm Skunkworks")
        self.state.runner.clicks_remaining = 1
        self.state.runner.credits = 4
        self.resolve_source(PlayerRole.CORP, source)
        self.assertEqual(list(self.view(PlayerRole.RUNNER)["effect_options"]), ["end_run"])
        self.reject(PlayerRole.RUNNER, {"type": "choose_effect_option", "option_id": "pay_credits"})
        self.choose(PlayerRole.RUNNER, "end_run")

    def test_anoetic_payment_is_atomic_and_private(self):
        source = self.approach("Anoetic Void")
        self.state.corp.credits = 2
        self.resolve_source(PlayerRole.CORP, source)
        self.choose(PlayerRole.CORP, "pay")
        self.assertEqual(self.state.corp.credits, 2)
        self.select(PlayerRole.CORP, [])
        self.assertEqual(self.state.window.kind, "choice")
        self.assertEqual(self.state.corp.credits, 2)
        self.choose(PlayerRole.CORP, "pay")
        eligible = self.eligible(PlayerRole.CORP)
        self.assertEqual(self.view(PlayerRole.RUNNER)["selection_cards"], [])
        self.reject(PlayerRole.CORP, {"type": "select_effect_cards", "card_instance_ids": eligible[:1]})
        self.reject(PlayerRole.CORP, {"type": "select_effect_cards", "card_instance_ids": [eligible[0]] * 2})
        self.select(PlayerRole.CORP, eligible[:2])
        self.assertEqual(self.state.corp.credits, 0)
        self.assertIsNone(self.state.run)
        self.assertFalse(self.state.turn.successful_run)
        self.assertEqual(self.state.suspended_windows, [])

    def test_anoetic_unaffordable_and_declined_cost_do_not_end_run(self):
        for credits in (1, 2):
            source = self.approach("Anoetic Void")
            self.state.corp.credits = credits
            self.resolve_source(PlayerRole.CORP, source)
            if credits == 1:
                self.assertEqual(list(self.view(PlayerRole.CORP)["effect_options"]), ["decline"])
                self.reject(PlayerRole.CORP, {"type": "choose_effect_option", "option_id": "pay"})
            self.choose(PlayerRole.CORP, "decline")
            self.assertTrue(self.state.run.successful)
            self.assertEqual(self.state.corp.credits, credits)
            self.finish_breach()
            self.pass_paid()

    def test_carnivore_trashes_accessed_agenda_once_and_decline_preserves_usage(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        carnivore = self.add("Carnivore", PlayerRole.RUNNER)
        agenda = self.add("Offworld Office")
        self.run_to_breach(self.state.card_instances[agenda].location.server.server_id)
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(agenda)})
        self.resolve_source(PlayerRole.RUNNER, carnivore)
        self.choose(PlayerRole.RUNNER, "decline")
        self.assertNotIn("carnivore", self.state.card_instances[carnivore].ability_last_used_turn)
        self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
        self.pass_paid()
        agenda = self.add("Offworld Office")
        self.run_to_breach(self.state.card_instances[agenda].location.server.server_id)
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(agenda)})
        self.resolve_source(PlayerRole.RUNNER, carnivore)
        self.choose(PlayerRole.RUNNER, "pay")
        self.assertNotIn("carnivore", self.state.card_instances[carnivore].ability_last_used_turn)
        selected = self.eligible(PlayerRole.RUNNER)[:2]
        self.reject(PlayerRole.RUNNER, {"type": "select_effect_cards", "card_instance_ids": selected[:1]})
        self.select(PlayerRole.RUNNER, selected)
        self.assertEqual(self.state.card_instances[agenda].location.zone, CardZone.DISCARD_PILE)
        self.assertEqual(self.state.card_instances[carnivore].ability_last_used_turn["carnivore"], self.state.turn.number)
        self.assertIsNone(self.state.run)
        self.pass_paid()
        third = self.add("Offworld Office")
        self.run_to_breach(self.state.card_instances[third].location.server.server_id)
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(third)})
        self.assertFalse(self.reactions(PlayerRole.RUNNER))

    def test_carnivore_waits_for_access_damage_and_cannot_trash_archives(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        carnivore = self.add("Carnivore", PlayerRole.RUNNER)
        target = self.add("Urtica Cipher")
        self.run_to_breach(self.state.card_instances[target].location.server.server_id)
        before = len(hand_cards(self.state, self.state.runner))
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(target)})
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), before - 2)
        self.resolve_source(PlayerRole.RUNNER, carnivore)
        self.choose(PlayerRole.RUNNER, "decline")
        self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
        self.pass_paid()
        discarded = self.add("Hedge Fund", installed=False)
        self.state.card_instances[discarded].face_up = True
        self.state.card_instances[discarded].location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=self.state.corp.player_id)
        self.run_to_breach("archives")
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(discarded)})
        self.assertFalse(self.reactions(PlayerRole.RUNNER))

    def test_ai_resolves_modes_and_target_descriptors(self):
        self.play("Hansei Review")
        state = advance_ai_decisions(self.state, {self.state.corp.player_id}, self.cards)
        self.assertNotEqual(state.window.kind if state.window else None, "target")
        self.assertEqual(state.card_instances[next(c.instance_id for c in state.card_instances.values()
                         if c.definition_id == "30048")].location.zone, CardZone.DISCARD_PILE)

    def test_carnivore_access_trash_preserves_amaze_and_loup_reactions(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        self.runner_identity("30001")
        carnivore = self.add("Carnivore", PlayerRole.RUNNER)
        amaze = self.add("AMAZE Amusements")
        agenda = self.add("Offworld Office")
        self.state.card_instances[agenda].location = self.state.card_instances[amaze].location.model_copy(deep=True)
        self.run_to_breach(self.state.card_instances[amaze].location.server.server_id)
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(amaze)})
        self.resolve_source(PlayerRole.RUNNER, carnivore)
        self.choose(PlayerRole.RUNNER, "pay")
        before = len(hand_cards(self.state, self.state.runner))
        self.select(PlayerRole.RUNNER, self.eligible(PlayerRole.RUNNER)[:2])
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), before - 2)
        self.assertEqual(self.state.run.phase, "access")
        self.resolve_source(PlayerRole.RUNNER, self.state.runner.identity_card_instance_id)
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), before - 1)
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(agenda)})
        self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
        self.resolve_source(PlayerRole.CORP, amaze)
        self.assertEqual(self.state.runner.tags, 2)
        self.assertIsNone(self.state.run)
        self.assertEqual(self.state.suspended_windows, [])

    def test_carnivore_rd_trash_reindexes_deck_and_requires_full_grip_cost(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        carnivore = self.add("Carnivore", PlayerRole.RUNNER)
        top = min((c for c in self.state.card_instances.values() if c.owner_id == self.state.corp.player_id
                   and c.location.zone is CardZone.DECK), key=lambda c: c.location.deck_position)
        self.run_to_breach("rd")
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": "rd"})
        self.resolve_source(PlayerRole.RUNNER, carnivore)
        self.choose(PlayerRole.RUNNER, "pay")
        self.select(PlayerRole.RUNNER, self.eligible(PlayerRole.RUNNER)[:2])
        self.assertEqual(self.state.card_instances[top.instance_id].location.zone, CardZone.DISCARD_PILE)
        self.assertEqual(min(c.location.deck_position for c in self.state.card_instances.values()
                         if c.owner_id == self.state.corp.player_id and c.location.zone is CardZone.DECK), 1)
        self.assertIsNone(self.state.run)

    def test_run_end_reactions_finish_before_approach_parent_cleanup(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        self.runner_identity("30010")  # Zahya's optional run-end payout
        source = self.approach("Manegarm Skunkworks", central="hq")
        self.resolve_source(PlayerRole.CORP, source)
        self.choose(PlayerRole.RUNNER, "end_run")
        self.assertEqual(self.state.run.phase, "ended")
        self.assertFalse(self.state.run.successful)
        self.resolve_source(PlayerRole.RUNNER, self.state.runner.identity_card_instance_id)
        self.assertIsNone(self.state.run)
        self.assertEqual(self.state.suspended_windows, [])
