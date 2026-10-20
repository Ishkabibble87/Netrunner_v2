"""Exercise modifier lifetimes, first installs, and dynamic breakers via the API."""


from test_reactions import ReactionScenario

from content.cards.models import EndRun, TriggeredAbility
from game.card_movement import trash_installed_card
from game.models import (
    CardZone,
    PlayerRole,
    PriorityWindow,
    RuleFact,
    RunState,
    ServerArea,
    ServerLocation,
    ZoneLocation,
)
from game.reactions import checkpoint, emit_fact
from game.turns import advance_turn, hand_cards


class HardwareModifierTests(ReactionScenario):
    """Boundaries requiring separate coverage beyond the growing game path."""

    def install(self, code):
        self.pass_paid()
        instance_id = self.add_card(code, PlayerRole.RUNNER, installed=False)
        self.submit(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(instance_id)})
        self.pass_paid()
        return instance_id

    def rig_card(self, instance_id, role=PlayerRole.RUNNER):
        return next(c for c in self.view(role)["installed_cards"] if c["instance_id"] == str(instance_id))

    def ready_runner(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        self.state.runner.credits = 100

    def test_memory_stacks_console_replacement_clears_old_source_and_is_atomic(self):
        self.ready_runner()
        penny = self.install("30014")
        self.state.card_instances[penny].counters["credit"] = 12
        self.install("30022")
        self.install("30022")
        self.install("30031")
        self.assertEqual(self.view(PlayerRole.RUNNER)["you"]["memory_limit"], 8)
        carnivore = self.install("30003")
        self.assertFalse(self.state.card_instances[penny].installed)
        self.assertEqual(self.state.card_instances[penny].counters, {})
        self.assertEqual(self.view(PlayerRole.CORP)["opponent"]["memory_limit"], 8)
        self.assertTrue(self.state.card_instances[carnivore].installed)
        self.assertEqual(self.view(PlayerRole.RUNNER)["you"]["maximum_hand_size"], 6)
        pantograph = self.add_card("30023", PlayerRole.RUNNER, installed=False)
        self.state.runner.credits = 1
        self.reject(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(pantograph)})
        self.assertTrue(self.state.card_instances[carnivore].installed)
        self.state.runner.credits = 10
        self.submit(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(pantograph)})
        self.assertFalse(self.state.card_instances[carnivore].installed)
        # Bonus memory really permits more than four MU of programs.
        self.state.runner.credits = 100
        for _ in range(5):
            self.install("30006")
        self.assertEqual(self.view(PlayerRole.RUNNER)["you"]["memory_used"], 5)

    def test_optimizer_discounts_stack_floor_and_only_commit_on_success(self):
        self.ready_runner()
        self.install("30022")
        self.install("30022")
        cleaver = self.add_card("30006", PlayerRole.RUNNER, installed=False)
        self.state.runner.credits = 0
        self.reject(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(cleaver)})
        self.assertNotIn("program_install:runner", self.state.turn.occurrences)
        hand_card = next(c for c in self.view(PlayerRole.RUNNER)["you"]["hand"] if c["instance_id"] == str(cleaver))
        self.assertEqual(hand_card["effective_install_cost"], 1)
        self.state.runner.credits = 10
        self.submit(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(cleaver)})
        self.assertEqual(self.state.runner.credits, 9)
        self.install("30006")
        self.assertEqual(self.state.runner.credits, 6)
        self.turn(PlayerRole.RUNNER, clicks=10)
        self.state.runner.credits = 20
        self.install("30022")
        self.install("30022")
        before = self.state.runner.credits
        self.install("30026")
        self.assertEqual(self.state.runner.credits, before)  # Four discounts, cost floored at zero.
        self.install("30025")
        self.assertEqual(self.state.runner.credits, before - 3)

    def test_program_before_optimizer_consumes_first_occurrence_and_turn_resets(self):
        self.ready_runner()
        self.install("30006")
        self.install("30022")
        before = self.state.runner.credits
        self.install("30026")
        self.assertEqual(self.state.runner.credits, before - 3)
        self.turn(PlayerRole.RUNNER)
        self.install("30025")
        self.assertEqual(self.state.runner.credits, before - 5)

    def test_pennyshaver_success_before_breach_cash_out_all_and_failed_run(self):
        self.ready_runner()
        penny = self.install("30014")
        command = {"type": "use_card_ability", "card_instance_id": str(penny), "ability_id": "cash_out"}
        before = self.state.runner.credits
        self.submit(PlayerRole.RUNNER, command)
        self.assertEqual(self.state.runner.credits, before + 1)
        self.run_to_breach("hq")
        self.assertEqual(self.state.card_instances[penny].counters["credit"], 1)
        self.assertEqual(self.state.run.phase, "breach")
        self.finish_breach()
        self.pass_paid()
        self.state.card_instances[penny].counters["credit"] = 1000
        before = self.state.runner.credits
        self.submit(PlayerRole.RUNNER, command)
        self.assertEqual(self.state.runner.credits, before + 1001)
        self.assertEqual(self.state.card_instances[penny].counters["credit"], 0)
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        self.pass_paid()
        self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        self.assertEqual(self.state.card_instances[penny].counters["credit"], 0)

    def test_hand_limits_identity_scored_agendas_and_optional_hub_draw(self):
        self.identity("30035")
        self.assertEqual(self.view(PlayerRole.CORP)["you"]["maximum_hand_size"], 6)
        # Scoring Hub opens its existing optional reaction after its static bonus applies.
        hub = self.add_card("30070", PlayerRole.CORP)
        self.state.card_instances[hub].counters["advancement"] = 3
        self.state.window = PriorityWindow(kind="paid", player=PlayerRole.CORP)
        before = len(hand_cards(self.state, self.state.corp))
        view = self.submit(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": str(hub)})
        self.assertEqual(view["you"]["maximum_hand_size"], 8)
        reaction = next(a for a in self.reactions(PlayerRole.CORP) if a["card_instance_id"] == str(hub))
        self.submit(PlayerRole.CORP, dict(reaction, decline=True))
        self.assertEqual(len(hand_cards(self.state, self.state.corp)), before)
        second = self.add_card("30070", PlayerRole.CORP)
        self.state.card_instances[second].counters["advancement"] = 3
        self.state.window = PriorityWindow(kind="paid", player=PlayerRole.CORP)
        self.submit(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": str(second)})
        self.assertEqual(self.view(PlayerRole.CORP)["you"]["maximum_hand_size"], 10)
        self.submit(PlayerRole.CORP, next(a for a in self.reactions(PlayerRole.CORP) if a["card_instance_id"] == str(second)))
        self.assertEqual(len(hand_cards(self.state, self.state.corp)), before + 2)
        # An agenda stolen into the Runner's score area supplies no hand bonus.
        self.state.card_instances[hub].location = ZoneLocation(zone=CardZone.SCORE_AREA, player_id=self.state.runner.player_id)
        self.state.card_instances[hub].controller_id = self.state.runner.player_id
        self.assertEqual(self.view(PlayerRole.CORP)["you"]["maximum_hand_size"], 8)
        self.ready_runner()
        self.install("30031")
        diamond = self.install("30031")
        self.state.runner.core_damage = 1
        self.assertEqual(self.view(PlayerRole.RUNNER)["you"]["maximum_hand_size"], 6)
        trash_installed_card(self.state, self.state.card_instances[diamond])
        self.assertEqual(self.view(PlayerRole.RUNNER)["you"]["maximum_hand_size"], 5)
        for _ in range(2):
            self.add_card("30030", PlayerRole.RUNNER, installed=False)
        self.state.runner.clicks_remaining = 0
        self.state.turn.phase = "discard"
        self.state.turn.step = "discard"
        advance_turn(self.state, self.cards)
        view = self.view(PlayerRole.RUNNER)
        self.assertEqual(view["decision"]["cards_to_discard"], 2)
        selection, = view["legal_actions"]
        self.submit(PlayerRole.RUNNER, {"type": "discard_cards", "card_instance_ids": selection["eligible_card_instance_ids"][:2]})

    def test_memory_loss_suspends_effect_and_only_runner_can_resolve(self):
        self.ready_runner()
        diamond = self.install("30031")
        programs = [self.install("30006") for _ in range(5)]
        # Exercise a source loss inside a multi-instruction ability, preserving continuation.
        self.cards["30031"] = self.cards["30031"].model_copy(update={"triggered_abilities": (
            TriggeredAbility(ability_id="test_loss", label="Lose memory then gain credits", trigger="turn_end",
                instructions=({"type": "trash_source"}, {"type": "gain_credits", "amount": 7})),)})
        emit_fact(self.state, self.cards, RuleFact(type="turn_end", player=PlayerRole.RUNNER))
        before = self.state.runner.credits
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertFalse(self.state.card_instances[diamond].installed)
        view = self.view(PlayerRole.RUNNER)
        self.assertEqual(view["decision"]["type"], "trash_for_memory")
        self.assertEqual(self.state.runner.credits, before)
        self.assertEqual(self.view(PlayerRole.CORP)["legal_actions"], [])
        self.reject(PlayerRole.RUNNER, {"type": "gain_credit"})
        self.reject(PlayerRole.CORP, {"type": "trash_programs", "card_instance_ids": [str(programs[0])]})
        self.reject(PlayerRole.RUNNER, {"type": "trash_programs", "card_instance_ids": [str(programs[0])] * 2})
        self.reject(PlayerRole.RUNNER, {"type": "trash_programs", "card_instance_ids": [str(diamond)]})
        clicks = self.state.runner.clicks_remaining
        self.submit(PlayerRole.RUNNER, {"type": "trash_programs", "card_instance_ids": [str(programs[0])]})
        self.assertFalse(self.state.memory_trash_pending)
        self.assertEqual(self.state.runner.credits, before + 7)
        self.assertEqual(self.state.runner.clicks_remaining, clicks)
        self.assertEqual(self.view(PlayerRole.RUNNER)["you"]["memory_used"], 4)

    def test_breaker_count_strength_and_unity_boost_are_live_with_fixed_paid_boosts(self):
        self.ready_runner()
        echelon = self.install("30025")
        unity = self.install("30026")
        self.assertEqual(self.rig_card(echelon)["card"]["strength"], 2)
        # Synthetic one-subroutine encounter isolates interface rules from later ice tasks.
        ice = self.add_card("30072", PlayerRole.CORP)
        self.state.card_instances[ice].location.server = ServerLocation(server_id=1, area=ServerArea.ICE, ice_distance_from_server=0)
        self.cards["30072"] = self.cards["30072"].model_copy(update={
            "subtypes": ("Code Gate",), "strength": 3, "remote_strength_bonus": 0,
            "subroutines": (EndRun(),)})
        self.state.run = RunState(server_id=1, phase="encounter", current_ice_id=ice)
        self.state.window = PriorityWindow(kind="paid", player=PlayerRole.RUNNER, run_continuation="resolve_subroutines")
        break_unity = {"type": "use_card_ability", "card_instance_id": str(unity),
                       "ability_id": "break_code_gate", "subroutine_indices": [0]}
        self.reject(PlayerRole.RUNNER, break_unity)
        self.submit(PlayerRole.RUNNER, {"type": "use_card_ability", "card_instance_id": str(unity), "ability_id": "boost_strength"})
        self.assertEqual(self.rig_card(unity)["card"]["strength"], 3)
        self.submit(PlayerRole.RUNNER, break_unity)
        self.reject(PlayerRole.RUNNER, break_unity)
        self.reject(PlayerRole.RUNNER, {"type": "use_card_ability", "card_instance_id": str(echelon),
                                        "ability_id": "break_sentry", "subroutine_indices": [0]})
        self.cards["30072"] = self.cards["30072"].model_copy(update={"subtypes": ("Sentry",)})
        self.state.run.broken_subroutine_indices = []
        before = self.state.runner.credits
        self.submit(PlayerRole.RUNNER, {"type": "use_card_ability", "card_instance_id": str(echelon), "ability_id": "boost_strength"})
        self.assertEqual(self.rig_card(echelon)["card"]["strength"], 4)
        self.submit(PlayerRole.RUNNER, {"type": "use_card_ability", "card_instance_id": str(echelon),
                                        "ability_id": "break_sentry", "subroutine_indices": [0]})
        self.assertEqual(self.state.runner.credits, before - 4)
        trash_installed_card(self.state, self.state.card_instances[echelon])
        checkpoint(self.state, self.cards)
        self.assertEqual(self.rig_card(unity)["card"]["strength"], 3)  # Paid +2 remains fixed.
        self.submit(PlayerRole.RUNNER, {"type": "use_card_ability", "card_instance_id": str(unity), "ability_id": "boost_strength"})
        self.assertEqual(self.rig_card(unity)["card"]["strength"], 4)  # New boost uses X=1.
        self.pass_paid()
        self.assertEqual(self.state.run.strength_boosts, {})
        self.assertEqual(self.rig_card(unity)["card"]["strength"], 1)
