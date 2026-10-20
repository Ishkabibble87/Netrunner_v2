"""Exercise hosted virus lifecycle, reaction timing, and counter costs via HTTP."""

from uuid import uuid4

from test_reactions import ReactionScenario

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
from game.reactions import emit_fact
from game.runs import begin_breach, enter_movement


class VirusTests(ReactionScenario):
    """Cover rule boundaries beyond the growing complete-game scenario."""

    def ready(self):
        self.turn(PlayerRole.RUNNER, clicks=30)
        self.state.runner.credits = 100
        self.state.runner.memory_limit = 20

    def ice(self, *, rezzed=False, server="hq"):
        instance_id = self.add_card("30072", PlayerRole.CORP)
        card = self.state.card_instances[instance_id]
        card.location = ZoneLocation(zone=CardZone.PLAY_AREA, server=ServerLocation(
            server_id=server, area=ServerArea.ICE, ice_distance_from_server=0))
        card.rezzed = rezzed
        card.face_up = rezzed
        return instance_id

    def install(self, code, host=None):
        self.pass_paid()
        instance_id = self.add_card(code, PlayerRole.RUNNER, installed=False)
        self.submit(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(instance_id),
                                      "host_card_instance_id": str(host) if host else None})
        return instance_id

    def resolve_all(self):
        for _ in range(50):
            if self.state.window is None:
                return
            if self.state.window.kind == "paid":
                self.pass_paid()
            else:
                self.submit(self.state.window.player, self.reactions(self.state.window.player)[0])
        self.fail("Reaction stack did not settle")

    def use(self, instance_id, ability_id, indices=()):
        return {"type": "use_card_ability", "card_instance_id": str(instance_id),
                "ability_id": ability_id, "subroutine_indices": list(indices)}

    def test_host_install_validation_visibility_memory_and_corp_replacement(self):
        self.ready()
        host = self.ice()
        virus = self.add_card("30004", PlayerRole.RUNNER, installed=False)
        command = {"type": "install_card", "card_instance_id": str(virus)}
        self.reject(PlayerRole.RUNNER, command)
        self.reject(PlayerRole.RUNNER, dict(command, host_card_instance_id=str(uuid4())))
        root = self.add_card("30071", PlayerRole.CORP)
        self.reject(PlayerRole.RUNNER, dict(command, host_card_instance_id=str(root)))
        self.state.runner.memory_limit = 0
        self.reject(PlayerRole.RUNNER, dict(command, host_card_instance_id=str(host)))
        self.state.runner.memory_limit = 4
        view = self.view(PlayerRole.RUNNER)
        self.assertIsNone(next(c for c in view["installed_cards"] if c["instance_id"] == str(host))["card"])
        options = [a for a in view["legal_actions"] if a["type"] == "install_card"
                   and a["card_instance_id"] == str(virus)]
        self.assertEqual({a["host_card_instance_id"] for a in options}, {str(host)})
        self.submit(PlayerRole.RUNNER, options[0])
        self.assertEqual(self.state.card_instances[virus].counters, {"virus": 1})
        installed = next(c for c in self.view(PlayerRole.RUNNER)["installed_cards"] if c["instance_id"] == str(virus))
        self.assertEqual(installed["host_card_instance_id"], str(host))
        self.assertEqual(self.view(PlayerRole.RUNNER)["you"]["memory_used"], 1)
        self.turn(PlayerRole.CORP)
        self.state.corp.credits = 20
        replacement = self.add_card("30072", PlayerRole.CORP, installed=False)
        self.submit(PlayerRole.CORP, {"type": "install_card", "card_instance_id": str(replacement),
                                    "server_id": "hq", "trash_card_instance_ids": [str(host)]})
        self.assertFalse(self.state.card_instances[virus].installed)
        self.assertEqual(self.state.card_instances[virus].counters, {})
        self.assertIsNone(self.state.card_instances[virus].location.host_card_instance_id)
        self.assertFalse(self.state.card_instances[host].face_up)

    def test_cookbook_order_matters_for_tranquilizer_and_can_decline(self):
        self.ready()
        self.add_card("30009", PlayerRole.RUNNER)
        self.add_card("30009", PlayerRole.RUNNER)
        host = self.ice(rezzed=True)
        virus = self.install("30017", host)
        choices = self.reactions(PlayerRole.RUNNER)
        cookbook = [a for a in choices if a["card_instance_id"] != str(virus)]
        for action in cookbook:
            self.submit(PlayerRole.RUNNER, action)
        self.assertTrue(self.state.card_instances[host].rezzed)
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertEqual(self.state.card_instances[virus].counters["virus"], 3)
        self.assertFalse(self.state.card_instances[host].rezzed)
        self.resolve_all()
        # If its own reaction resolves first, later Cookbook placements do not derez.
        self.state.card_instances[host].face_up = True
        self.state.card_instances[host].rezzed = True
        second = self.install("30017", host)
        self.submit(PlayerRole.RUNNER, next(a for a in self.reactions(PlayerRole.RUNNER)
                                           if a["card_instance_id"] == str(second)))
        self.resolve_all()
        self.assertEqual(self.state.card_instances[second].counters["virus"], 3)
        self.assertTrue(self.state.card_instances[host].rezzed)
        third = self.install("30007")
        self.submit(PlayerRole.RUNNER, next(a for a in self.reactions(PlayerRole.RUNNER)
                                           if a["card_instance_id"] == str(third)))
        self.submit(PlayerRole.RUNNER, {"type": "pass_priority"})
        self.assertEqual(self.state.card_instances[third].counters["virus"], 1)

    def test_turn_counters_purge_preserves_other_counters_and_derez_is_not_a_lock(self):
        self.ready()
        host = self.ice(rezzed=True)
        virus = self.install("30017", host)
        self.resolve_all()
        fermenter = self.install("30007")
        self.resolve_all()
        self.state.card_instances[fermenter].counters["credit"] = 2
        for expected in (2, 3):
            self.turn(PlayerRole.RUNNER)
            emit_fact(self.state, self.cards, RuleFact(type="turn_begin", player=PlayerRole.RUNNER))
            self.resolve_all()
            self.assertEqual(self.state.card_instances[virus].counters["virus"], expected)
        self.assertFalse(self.state.card_instances[host].rezzed)
        self.assertFalse(self.state.card_instances[host].face_up)
        self.state.run = RunState(server_id="hq", phase="approach", current_ice_id=host)
        self.state.window = PriorityWindow(kind="paid", player=PlayerRole.CORP, run_continuation="encounter")
        self.state.corp.credits = 20
        self.submit(PlayerRole.CORP, {"type": "rez_card", "card_instance_id": str(host)})
        self.assertTrue(self.state.card_instances[host].rezzed)
        self.assertTrue(self.state.card_instances[host].face_up)
        self.state.run = None
        self.turn(PlayerRole.CORP)
        self.submit(PlayerRole.CORP, {"type": "purge_virus_counters"})
        self.assertEqual(self.state.card_instances[fermenter].counters, {"credit": 2})
        self.assertTrue(self.state.card_instances[virus].installed)
        self.assertTrue(self.state.card_instances[host].rezzed)
        self.turn(PlayerRole.RUNNER)
        emit_fact(self.state, self.cards, RuleFact(type="turn_begin", player=PlayerRole.RUNNER))
        self.resolve_all()
        self.assertEqual(self.state.card_instances[virus].counters["virus"], 1)

    def test_fermenter_trash_cost_preserves_payout_and_zero_is_legal(self):
        self.ready()
        for count in (4, 0):
            virus = self.install("30007")
            self.resolve_all()
            self.state.card_instances[virus].counters["virus"] = count
            credits, clicks = self.state.runner.credits, self.state.runner.clicks_remaining
            self.submit(PlayerRole.RUNNER, self.use(virus, "cash_out"))
            self.assertEqual(self.state.runner.credits, credits + 2 * count)
            self.assertEqual(self.state.runner.clicks_remaining, clicks - 1)
            self.assertFalse(self.state.card_instances[virus].installed)
            self.assertEqual(self.state.card_instances[virus].counters, {})
            self.resolve_all()
            self.reject(PlayerRole.RUNNER, self.use(virus, "cash_out"))

    def test_counter_paid_abilities_enforce_host_costs_and_encounter_duration(self):
        self.ready()
        host = self.ice(rezzed=True)
        other = self.ice(rezzed=True, server="rd")
        botulus = self.install("30004", host)
        self.resolve_all()
        leech = self.install("30008")
        self.resolve_all()
        self.state.card_instances[leech].counters["virus"] = 8
        self.reject(PlayerRole.RUNNER, self.use(leech, "lower_strength"))
        self.state.run = RunState(server_id="rd", phase="encounter", current_ice_id=other)
        self.state.window = PriorityWindow(kind="paid", player=PlayerRole.RUNNER, run_continuation="resolve_subroutines")
        self.reject(PlayerRole.RUNNER, self.use(botulus, "break_host", (0,)))
        self.state.run.current_ice_id = host
        self.state.run.server_id = "hq"
        self.reject(PlayerRole.RUNNER, self.use(botulus, "break_host", (99,)))
        self.submit(PlayerRole.RUNNER, self.use(botulus, "break_host", (0,)))
        self.assertEqual(self.state.run.broken_subroutine_indices, [0])
        self.assertEqual(self.state.card_instances[botulus].counters["virus"], 0)
        self.reject(PlayerRole.RUNNER, self.use(botulus, "break_host", (0,)))
        for _ in range(8):
            self.submit(PlayerRole.RUNNER, self.use(leech, "lower_strength"))
        ice_view = next(c for c in self.view(PlayerRole.RUNNER)["installed_cards"] if c["instance_id"] == str(host))
        self.assertEqual(ice_view["card"]["strength"], self.cards["30072"].strength - 8)
        self.reject(PlayerRole.RUNNER, self.use(leech, "lower_strength"))
        enter_movement(self.state, self.cards)
        self.assertEqual(self.state.run.strength_boosts, {})
        ice_view = next(c for c in self.view(PlayerRole.RUNNER)["installed_cards"] if c["instance_id"] == str(host))
        self.assertEqual(ice_view["card"]["strength"], self.cards["30072"].strength)

    def test_conduit_reads_at_breach_and_counters_arrive_after_successful_run(self):
        self.ready()
        conduit = self.install("30024")
        self.resolve_all()
        leech = self.install("30008")
        self.resolve_all()
        self.add_card("30008", PlayerRole.RUNNER)  # Two mandatory reactions retain the ordering window.
        self.run_to_breach("rd", command=dict(self.use(conduit, "run_rd"), server_id="rd"))
        self.assertEqual(self.state.run.phase, "success")  # Leech reacts before breach.
        self.state.card_instances[conduit].counters["virus"] = 2
        self.resolve_all()
        self.assertEqual(self.state.run.access_limit, 3)
        self.assertEqual(self.state.card_instances[leech].counters["virus"], 1)
        self.finish_breach()
        self.assertEqual(self.state.run.phase, "ended")
        self.assertEqual(self.state.card_instances[conduit].counters["virus"], 2)
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertIsNone(self.state.run)
        self.assertEqual(self.state.card_instances[conduit].counters["virus"], 3)
        self.resolve_all()
        # Ordinary R&D runs do not get its bonus, but do offer its end reaction.
        self.run_to_breach("rd")
        self.resolve_all()
        self.assertEqual(self.state.run.access_limit, 1)
        self.finish_breach()
        self.submit(PlayerRole.RUNNER, {"type": "pass_priority"})
        self.assertEqual(self.state.card_instances[conduit].counters["virus"], 3)
        self.resolve_all()
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "rd"})
        self.pass_paid()
        self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        self.assertIsNone(self.state.run)
        self.assertEqual(self.state.card_instances[conduit].counters["virus"], 3)

    def test_conduit_bonus_requires_originating_copy_and_leech_ignores_remotes(self):
        self.ready()
        first = self.install("30024")
        self.resolve_all()
        second = self.install("30024")
        self.resolve_all()
        self.state.card_instances[first].counters["virus"] = 2
        self.state.card_instances[second].counters["virus"] = 5
        self.state.run = RunState(server_id="rd", source_card_id=first, source_ability_id="run_rd",
                                 source_location=self.state.card_instances[first].location.model_copy(deep=True),
                                 additional_accesses_per_virus=True)
        trash_installed_card(self.state, self.state.card_instances[first])
        begin_breach(self.state, self.cards)
        self.assertEqual(self.state.run.access_limit, 1)
        self.state.run = None
        leech = self.add_card("30008", PlayerRole.RUNNER)
        remote = self.add_card("30071", PlayerRole.CORP)
        self.state.run = RunState(server_id=self.state.card_instances[remote].location.server.server_id, successful=True)
        emit_fact(self.state, self.cards, RuleFact(type="run_success", player=PlayerRole.RUNNER, server_id=self.state.run.server_id))
        self.assertEqual(self.state.card_instances[leech].counters, {})
