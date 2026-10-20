"""Conditional prices and delayed breaker effects through authenticated HTTP."""

from test_reactions import ReactionScenario

from content.cards.models import EndRun
from game.card_movement import trash_installed_card
from game.models import CardZone, PlayerRole, ServerArea, ServerLocation, ZoneLocation


class ConditionalBreakerTests(ReactionScenario):
    """Check the timing and cost boundaries that need separate coverage."""

    def ready(self):
        self.turn(PlayerRole.RUNNER, clicks=30)
        self.state.runner.credits = 100
        self.state.runner.memory_limit = 20

    def settle(self):
        for _ in range(50):
            if not self.state.window:
                return
            if self.state.window.kind == "paid":
                self.pass_paid()
            else:
                self.submit(self.state.window.player, self.reactions(self.state.window.player)[0])
        self.fail("Windows did not settle")

    def install(self, code):
        self.settle()
        instance_id = self.add_card(code, PlayerRole.RUNNER, installed=False)
        before = self.state.runner.credits
        self.submit(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(instance_id)})
        self.settle()
        return instance_id, before - self.state.runner.credits

    def use(self, instance_id, ability, targets=()):
        return {"type": "use_card_ability", "card_instance_id": str(instance_id),
                "ability_id": ability, "subroutine_indices": list(targets)}

    def encounter(self, subtype="Barrier", subroutines=1):
        code = "30072"
        self.cards[code] = self.cards[code].model_copy(update={
            "subtypes": (subtype,), "strength": 1,
            "subroutines": tuple(EndRun() for _ in range(subroutines))})
        ice = self.add_card(code, PlayerRole.CORP)
        self.state.card_instances[ice].location = ZoneLocation(zone=CardZone.PLAY_AREA,
            server=ServerLocation(server_id="hq", area=ServerArea.ICE, ice_distance_from_server=0))
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        for _ in range(20):
            if self.state.run.phase == "encounter":
                return ice
            self.submit(self.state.window.player, {"type": "pass_priority"})
        self.fail("Encounter did not begin")

    def successful_run(self):
        self.run_to_breach("archives")
        self.settle()
        self.assertIsNone(self.state.run)
        self.assertGreater(self.state.turn.occurrences["run_success:runner"], 0)

    def test_carmen_discount_uses_turn_history_stacks_and_resets(self):
        self.ready()
        _, cost = self.install("30015")
        self.assertEqual(cost, 5)
        self.successful_run()
        self.install("30022")
        self.turn(PlayerRole.RUNNER, clicks=30)
        self.successful_run()
        carmen = self.add_card("30015", PlayerRole.RUNNER, installed=False)
        self.state.runner.credits = 1
        command = {"type": "install_card", "card_instance_id": str(carmen)}
        self.reject(PlayerRole.RUNNER, command)
        self.state.runner.credits = 2
        hand = self.view(PlayerRole.RUNNER)["you"]["hand"]
        self.assertEqual(next(c for c in hand if c["instance_id"] == str(carmen))["effective_install_cost"], 2)
        self.submit(PlayerRole.RUNNER, command)
        self.assertEqual(self.state.runner.credits, 0)
        self.settle()
        self.state.runner.credits = 100
        _, cost = self.install("30015")
        self.assertEqual(cost, 3)
        self.turn(PlayerRole.RUNNER, clicks=30)
        _, cost = self.install("30015")
        self.assertEqual(cost, 4)

    def test_carmen_strength_and_sentry_restriction(self):
        self.ready()
        carmen, _ = self.install("30015")
        ice = self.encounter("Sentry")
        before = self.state.runner.credits
        self.submit(PlayerRole.RUNNER, self.use(carmen, "boost_strength"))
        self.assertEqual(self.state.run.strength_boosts[carmen], 3)
        self.assertEqual(self.state.runner.credits, before - 2)
        self.submit(PlayerRole.RUNNER, self.use(carmen, "break_sentry", (0,)))
        self.cards["30072"] = self.cards["30072"].model_copy(update={"subtypes": ("Barrier",)})
        self.state.run.broken_subroutine_indices = []
        self.reject(PlayerRole.RUNNER, self.use(carmen, "break_sentry", (0,)))
        self.assertTrue(self.state.card_instances[ice].rezzed)

    def test_marjanah_dynamic_cost_validation_payment_display_and_run_funds(self):
        self.ready()
        marjanah, _ = self.install("30016")
        self.encounter(subroutines=3)
        self.state.runner.credits = 1
        command = self.use(marjanah, "break_barrier", (0,))
        self.reject(PlayerRole.RUNNER, command)
        self.state.run.bad_publicity_credits = 1
        self.submit(PlayerRole.RUNNER, command)
        self.assertEqual(self.state.runner.credits, 0)
        self.assertEqual(self.state.run.bad_publicity_credits, 0)
        self.settle()  # An unbroken subroutine ends this unsuccessful run.
        self.assertNotIn("run_success:runner", self.state.turn.occurrences)
        self.successful_run()
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        while self.state.run.phase != "encounter":
            self.submit(self.state.window.player, {"type": "pass_priority"})
        self.reject(PlayerRole.RUNNER, command)
        self.state.run.bad_publicity_credits = 1
        self.submit(PlayerRole.RUNNER, command)
        self.assertEqual(self.state.runner.credits, 0)
        rig = self.view(PlayerRole.RUNNER)["installed_cards"]
        card = next(c["card"] for c in rig if c["instance_id"] == str(marjanah))
        self.assertIn("1 credit", card["ability_labels"]["break_barrier"])
        self.settle()
        self.turn(PlayerRole.RUNNER, clicks=30)
        self.state.runner.credits = 1
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        while self.state.run.phase != "encounter":
            self.submit(self.state.window.player, {"type": "pass_priority"})
        self.reject(PlayerRole.RUNNER, command)

    def test_mayfly_boost_only_survives_run_end(self):
        self.ready()
        mayfly, _ = self.install("30032")
        self.encounter()
        self.submit(PlayerRole.RUNNER, self.use(mayfly, "boost_strength"))
        self.settle()
        self.assertIsNone(self.state.run)
        self.assertTrue(self.state.card_instances[mayfly].installed)

    def test_mayfly_any_subtype_strength_and_successful_run_cleanup(self):
        for subtype in ("Barrier", "Sentry", "Code Gate"):
            with self.subTest(subtype=subtype):
                self.ready()
                mayfly, _ = self.install("30032")
                unused, _ = self.install("30032")
                self.encounter(subtype)
                self.cards["30072"] = self.cards["30072"].model_copy(update={"strength": 2})
                self.reject(PlayerRole.RUNNER, self.use(mayfly, "break_subroutine", (0,)))
                self.submit(PlayerRole.RUNNER, self.use(mayfly, "boost_strength"))
                self.submit(PlayerRole.RUNNER, self.use(mayfly, "break_subroutine", (0,)))
                self.reject(PlayerRole.RUNNER, self.use(mayfly, "break_subroutine", (0,)))
                self.settle()
                self.submit(PlayerRole.RUNNER, {"type": "continue_run"})
                self.settle()
                self.assertTrue(self.state.run.successful)
                self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": "hq"})
                self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
                if self.state.window and self.state.window.player is PlayerRole.CORP:
                    self.submit(PlayerRole.CORP, {"type": "pass_priority"})  # Decline Send a Message.
                self.assertEqual(self.state.run.phase, "ended")
                reactions = self.reactions(PlayerRole.RUNNER)
                self.assertTrue(any(a["card_instance_id"] == str(mayfly) for a in reactions))
                self.settle()
                self.assertIsNone(self.state.run)
                self.assertFalse(self.state.card_instances[mayfly].installed)
                self.assertTrue(self.state.card_instances[unused].installed)
                for c in list(self.state.card_instances.values()):
                    if c.installed:
                        trash_installed_card(self.state, c)

    def test_mayfly_multiple_uses_and_copies_trash_on_unsuccessful_end(self):
        self.ready()
        first, _ = self.install("30032")
        second, _ = self.install("30032")
        self.encounter(subroutines=4)
        for card, index in ((first, 0), (first, 1), (second, 2)):
            self.submit(PlayerRole.RUNNER, self.use(card, "break_subroutine", (index,)))
        self.settle()
        self.assertIsNone(self.state.run)
        for card in (first, second):
            self.assertFalse(self.state.card_instances[card].installed)
            self.assertEqual(self.state.card_instances[card].location.zone, CardZone.DISCARD_PILE)

    def test_mayfly_jack_out_and_old_installation_target(self):
        self.ready()
        mayfly, _ = self.install("30032")
        self.encounter()
        self.submit(PlayerRole.RUNNER, self.use(mayfly, "break_subroutine", (0,)))
        self.settle()
        self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        self.settle()
        self.assertFalse(self.state.card_instances[mayfly].installed)
        self.pass_paid()
        second, _ = self.install("30032")
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        while self.state.run.phase != "encounter":
            self.submit(self.state.window.player, {"type": "pass_priority"})
        self.submit(PlayerRole.RUNNER, self.use(second, "break_subroutine", (0,)))
        trash_installed_card(self.state, self.state.card_instances[second])
        # A later installation of the same physical card is a new rules object.
        self.state.card_instances[second] = self.state.card_instances[second].model_copy(update={
            "installed": True, "controller_id": self.state.runner.player_id,
            "location": ZoneLocation(zone=CardZone.PLAY_AREA)})
        self.settle()
        self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        self.settle()
        self.assertTrue(self.state.card_instances[second].installed)
