"""Task 10 through authenticated commands: run costs, effects and bioroid breaks.

These fixtures isolate thresholds and suspended encounters; the growing happy
path covers acquiring and using the cards through ordinary game actions.
"""

from test_reactions import ReactionScenario

from game.models import CardZone, PlayerRole, ServerArea, ServerLocation, ZoneLocation


class RunModifierTests(ReactionScenario):
    def setUp(self):
        super().setUp()
        self.turn(PlayerRole.RUNNER, clicks=20)
        self.state.runner.credits = self.state.corp.credits = 30

    def add(self, title, role=PlayerRole.CORP, **kwargs):
        return self.add_card(next(k for k, c in self.cards.items() if c.title == title), role, **kwargs)

    def ice(self, title, *, rezzed=True, position=0):
        instance_id = self.add(title)
        self.state.servers.pop()
        card = self.state.card_instances[instance_id]
        card.location = ZoneLocation(zone=CardZone.PLAY_AREA,
            server=ServerLocation(server_id="hq", area=ServerArea.ICE, ice_distance_from_server=position))
        card.rezzed = card.face_up = rezzed
        return instance_id

    def ability(self, ice, *targets):
        return {"type": "use_card_ability", "card_instance_id": str(ice),
                "ability_id": "bioroid_break", "subroutine_indices": list(targets)}

    def encounter(self, *, command=None):
        self.submit(PlayerRole.RUNNER, command or {"type": "run_server", "server_id": "hq"})
        for _ in range(20):
            if self.state.run.phase == "encounter" and self.state.window and self.state.window.kind == "paid":
                return
            self.submit(self.state.window.player, {"type": "pass_priority"})
        self.fail("Encounter paid window not reached")

    def test_tread_lightly_cost_validation_payment_privacy_and_expiry(self):
        ice = self.ice("Palisade", rezzed=False)
        asset = self.add("Regolith Mining License")
        card = self.state.card_instances[asset]
        card.rezzed = card.face_up = False
        event = self.add("Tread Lightly", PlayerRole.RUNNER, installed=False)
        self.submit(PlayerRole.RUNNER, {"type": "play_card", "card_instance_id": str(event), "server_id": "hq"})
        while self.state.run.phase != "approach":
            self.submit(self.state.window.player, {"type": "pass_priority"})
        printed = self.cards[self.state.card_instances[ice].definition_id].rez_cost
        corp = self.view(PlayerRole.CORP)
        entry = next(c for c in corp["installed_cards"] if c["instance_id"] == str(ice))
        self.assertEqual(entry["card"]["effective_rez_cost"], printed + 3)
        hidden = next(c for c in self.view(PlayerRole.RUNNER)["installed_cards"] if c["instance_id"] == str(ice))
        self.assertIsNone(hidden["card"])
        asset_view = next(c for c in corp["installed_cards"] if c["instance_id"] == str(asset))
        self.assertEqual(asset_view["card"]["effective_rez_cost"], self.cards[card.definition_id].rez_cost)
        command = {"type": "rez_card", "card_instance_id": str(ice)}
        self.state.corp.credits = printed + 2
        self.assertNotIn(command, self.view(PlayerRole.CORP)["legal_actions"])
        self.reject(PlayerRole.CORP, command)
        self.state.corp.credits = printed + 3
        self.submit(PlayerRole.CORP, command)
        self.assertEqual(self.state.corp.credits, 0)
        self.pass_paid()  # Palisade ends the event run.
        self.assertIsNone(self.state.run)
        self.assertEqual(self.state.card_instances[event].location.zone, CardZone.DISCARD_PILE)
        self.state.card_instances[ice].rezzed = self.state.card_instances[ice].face_up = False
        self.state.corp.credits = printed
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        while self.state.run.phase != "approach":
            self.submit(self.state.window.player, {"type": "pass_priority"})
        self.submit(PlayerRole.CORP, command)
        self.assertEqual(self.state.corp.credits, 0)

    def test_whitespace_loss_threshold_and_independently_broken_subroutines(self):
        ice = self.ice("Whitespace")
        # A breaker keeps the encounter window open to choose broken routines.
        self.add("Cleaver", PlayerRole.RUNNER)
        for credits, broken, ends in [(9, [], True), (10, [], False), (6, [0], True),
                                      (7, [0], False), (1, [1], False), (0, [], True)]:
            with self.subTest(credits=credits, broken=broken):
                self.turn(PlayerRole.RUNNER, clicks=20)
                self.state.runner.credits = credits
                self.encounter()
                self.state.run.broken_subroutine_indices = broken.copy()
                self.state.run.bad_publicity_credits = 5
                self.pass_paid()
                self.assertEqual(self.state.runner.credits, credits if 0 in broken else max(0, credits - 3))
                if ends:
                    self.assertIsNone(self.state.run)
                else:
                    self.assertEqual(self.state.run.bad_publicity_credits, 5)
                    self.assertEqual(self.state.run.phase, "movement")
                    self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        self.assertTrue(self.state.card_instances[ice].installed)

    def test_whitespace_does_not_lose_credits_hosted_on_overclock(self):
        self.ice("Whitespace")
        self.add("Cleaver", PlayerRole.RUNNER)
        event = self.add("Overclock", PlayerRole.RUNNER, installed=False)
        self.encounter(command={"type": "play_card", "card_instance_id": str(event), "server_id": "hq"})
        before = self.state.runner.credits
        hosted = self.state.card_instances[event].counters["credit"]
        self.state.run.broken_subroutine_indices = [1]
        self.pass_paid()
        self.assertEqual(self.state.runner.credits, before - 3)
        self.assertEqual(self.state.card_instances[event].counters["credit"], hosted)

    def test_ballista_corp_choice_targets_and_no_program_fallback(self):
        self.ice("Ballista")
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        self.pass_paid()
        self.assertEqual(self.state.window.kind, "choice")
        self.assertEqual(self.state.window.player, PlayerRole.CORP)
        self.assertEqual(list(self.view(PlayerRole.CORP)["effect_options"]), ["end_run"])
        self.reject(PlayerRole.CORP, {"type": "choose_effect_option", "option_id": "trash_program"})
        self.submit(PlayerRole.CORP, {"type": "choose_effect_option", "option_id": "end_run"})
        self.assertIsNone(self.state.run)
        program = self.add("Cleaver", PlayerRole.RUNNER)
        resource = self.add("Telework Contract", PlayerRole.RUNNER)
        self.encounter()
        self.pass_paid()
        self.assertEqual(set(self.view(PlayerRole.CORP)["effect_options"]), {"trash_program", "end_run"})
        self.reject(PlayerRole.RUNNER, {"type": "choose_effect_option", "option_id": "end_run"})
        self.submit(PlayerRole.CORP, {"type": "choose_effect_option", "option_id": "trash_program"})
        self.reject(PlayerRole.CORP, {"type": "select_effect_cards", "card_instance_ids": [str(resource)]})
        self.reject(PlayerRole.CORP, {"type": "select_effect_cards", "card_instance_ids": []})
        self.submit(PlayerRole.CORP, {"type": "select_effect_cards", "card_instance_ids": [str(program)]})
        self.assertFalse(self.state.card_instances[program].installed)
        self.assertTrue(self.state.card_instances[resource].installed)
        self.assertEqual(self.state.run.phase, "movement")
        self.assertEqual(self.state.suspended_windows, [])
        self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        replacement = self.add("Cleaver", PlayerRole.RUNNER)
        self.encounter()
        self.pass_paid()
        self.submit(PlayerRole.CORP, {"type": "choose_effect_option", "option_id": "end_run"})
        self.assertIsNone(self.state.run)
        self.assertTrue(self.state.card_instances[replacement].installed)

    def test_bioroid_without_breaker_uses_runner_clicks_and_validates_atomically(self):
        bran = self.ice("Brân 1.0")
        other = self.ice("Ansel 1.0", position=1)
        # Encounter the outer Ansel; another copy's ability cannot target it.
        self.encounter()
        self.assertEqual(self.state.window.player, PlayerRole.RUNNER)
        self.reject(PlayerRole.CORP, self.ability(other, 0))
        self.reject(PlayerRole.RUNNER, self.ability(bran, 0))
        self.reject(PlayerRole.RUNNER, self.ability(other))
        self.reject(PlayerRole.RUNNER, self.ability(other, 0, 1))
        self.reject(PlayerRole.RUNNER, self.ability(other, 3))
        clicks, credits = self.state.runner.clicks_remaining, self.state.runner.credits
        with self.client.websocket_connect(self.path + "/events", headers=self.headers[PlayerRole.CORP]) as corp:
            corp.receive_json()
            self.submit(PlayerRole.RUNNER, self.ability(other, 0))
            self.assertTrue(corp.receive_json()["state"]["run"]["subroutines"][0]["broken"])
        self.assertEqual(self.state.runner.clicks_remaining, clicks - 1)
        self.assertEqual(self.state.runner.credits, credits)
        self.reject(PlayerRole.RUNNER, self.ability(other, 0))
        self.state.runner.clicks_remaining = 0
        self.reject(PlayerRole.RUNNER, self.ability(other, 1))
        self.assertFalse(any(a.get("ability_id") == "bioroid_break" for a in self.view(PlayerRole.RUNNER)["legal_actions"]))
        self.state.runner.clicks_remaining = 3
        self.submit(PlayerRole.RUNNER, self.ability(other, 1))
        self.submit(PlayerRole.RUNNER, self.ability(other, 2))
        self.pass_paid()
        self.assertEqual(self.state.run.phase, "movement")
        self.assertEqual(self.state.run.broken_subroutine_indices, [])
        self.reject(PlayerRole.RUNNER, self.ability(other, 1))
        self.submit(PlayerRole.RUNNER, {"type": "continue_run"})
        self.assertEqual(self.state.run.current_ice_id, bran)
        self.assertEqual(self.state.run.phase, "encounter")
        self.assertEqual(len([a for a in self.view(PlayerRole.RUNNER)["legal_actions"] if a.get("ability_id") == "bioroid_break"]), 3)

    def test_tread_lightly_applies_to_bran_installed_inward_ice(self):
        bran = self.ice("Brân 1.0")
        inner = self.add("Palisade", installed=False)
        event = self.add("Tread Lightly", PlayerRole.RUNNER, installed=False)
        self.encounter(command={"type": "play_card", "card_instance_id": str(event), "server_id": "hq"})
        self.submit(PlayerRole.RUNNER, self.ability(bran, 1))
        self.submit(PlayerRole.RUNNER, self.ability(bran, 2))
        self.pass_paid()
        self.submit(PlayerRole.CORP, {"type": "install_card", "card_instance_id": str(inner), "server_id": "hq"})
        self.submit(PlayerRole.RUNNER, {"type": "continue_run"})
        while self.state.run.phase != "approach":
            self.submit(self.state.window.player, {"type": "pass_priority"})
        printed = self.cards[self.state.card_instances[inner].definition_id].rez_cost
        self.state.corp.credits = printed + 3
        self.submit(PlayerRole.CORP, {"type": "rez_card", "card_instance_id": str(inner)})
        self.assertEqual(self.state.corp.credits, 0)
