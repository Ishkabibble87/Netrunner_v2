"""Task 9 decisions through authenticated commands, including nested installs.

These scenarios stress private zones, partial selections, installation payments,
and run continuation separately from the growing core game path.
"""

from unittest.mock import patch

from test_reactions import ReactionScenario

from game.card_movement import reindex_deck
from game.models import (
    CardZone,
    PlayerRole,
    PriorityWindow,
    ServerArea,
    ServerLocation,
    ZoneLocation,
)
from game.turns import hand_cards


class SearchRecursionTests(ReactionScenario):
    def add(self, title, role=PlayerRole.CORP, *, zone=None, **kwargs):
        instance_id = self.add_card(next(k for k, c in self.cards.items() if c.title == title), role, **kwargs)
        if zone is not None:
            card = self.state.card_instances[instance_id]
            card.face_up = False if role is PlayerRole.CORP else True
            card.location = ZoneLocation(zone=zone, player_id=card.owner_id)
        return instance_id

    def select(self, role, ids=()):
        return self.submit(role, {"type": "select_effect_cards", "card_instance_ids": [str(i) for i in ids]})

    def resolve(self, role, source):
        action = next(a for a in self.reactions(role) if a["card_instance_id"] == str(source))
        return self.submit(role, action)

    def score(self, agenda):
        self.state.card_instances[agenda].counters["advancement"] = self.cards[self.state.card_instances[agenda].definition_id].advancement_requirement
        self.state.window = PriorityWindow(kind="paid", player=PlayerRole.CORP)
        return self.submit(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": str(agenda)})

    def play(self, title, role=PlayerRole.CORP):
        self.turn(role, clicks=20)
        player = self.state.corp if role is PlayerRole.CORP else self.state.runner
        player.credits = 30
        card = self.add(title, role, installed=False)
        self.submit(role, {"type": "play_card", "card_instance_id": str(card)})
        return card

    def test_sprint_private_exact_selection_and_atomic_rejection(self):
        before = len(hand_cards(self.state, self.state.corp))
        card = self.play("Sprint")
        self.assertEqual(len(hand_cards(self.state, self.state.corp)), before + 3)
        view = self.view(PlayerRole.CORP)
        eligible = view["legal_actions"][0]["eligible_card_instance_ids"]
        self.assertNotIn(str(card), eligible)
        self.assertEqual(self.view(PlayerRole.RUNNER)["selection_cards"], [])
        self.reject(PlayerRole.CORP, {"type": "select_effect_cards", "card_instance_ids": eligible[:1]})
        self.reject(PlayerRole.CORP, {"type": "select_effect_cards", "card_instance_ids": [eligible[0]] * 2})
        self.reject(PlayerRole.RUNNER, {"type": "select_effect_cards", "card_instance_ids": eligible[:2]})
        self.select(PlayerRole.CORP, eligible[:2])
        self.assertEqual(self.state.card_instances[card].location.zone, CardZone.DISCARD_PILE)
        self.assertEqual(len(hand_cards(self.state, self.state.corp)), before + 1)
        self.assertEqual(self.state.suspended_windows, [])

    def test_mutual_favor_reveals_only_found_card_and_shuffles_on_failure(self):
        self.play("Mutual Favor", PlayerRole.RUNNER)
        eligible = self.view(PlayerRole.RUNNER)["selection_cards"]
        self.assertTrue(eligible)
        self.assertTrue(all("Icebreaker" in self.cards[c["definition_id"]].subtypes for c in eligible))
        self.assertEqual(self.view(PlayerRole.CORP)["selection_cards"], [])
        found = eligible[0]["instance_id"]
        self.select(PlayerRole.RUNNER, [found])
        public = self.view(PlayerRole.CORP)["revealed_cards"]
        self.assertEqual([c["instance_id"] for c in public], [found])
        self.assertIn(found, [str(c.instance_id) for c in hand_cards(self.state, self.state.runner)])
        self.play("Mutual Favor", PlayerRole.RUNNER)
        with patch("game.card_movement.SystemRandom") as rng:
            self.select(PlayerRole.RUNNER)
        rng.return_value.shuffle.assert_called_once()
        self.assertEqual(self.view(PlayerRole.CORP)["revealed_cards"], [])

    def test_mutual_favor_installs_without_click_and_can_decline_or_be_unaffordable(self):
        for mode in ("install", "decline", "poor"):
            with self.subTest(mode=mode):
                self.play("Mutual Favor", PlayerRole.RUNNER)
                self.state.turn.occurrences["run_success:runner"] = 1
                found = self.view(PlayerRole.RUNNER)["selection_cards"][0]
                self.state.runner.credits = 0 if mode == "poor" else 30
                clicks = self.state.runner.clicks_remaining
                self.select(PlayerRole.RUNNER, [found["instance_id"]])
                if mode != "poor":
                    actions = self.view(PlayerRole.RUNNER)["legal_actions"]
                    self.assertTrue(self.view(PlayerRole.RUNNER)["effect_install"])
                    if mode == "install":
                        install = next(a for a in actions if a["type"] == "install_card")
                        self.submit(PlayerRole.RUNNER, install)
                        self.assertTrue(next(c for c in self.state.card_instances.values() if str(c.instance_id) == found["instance_id"]).installed)
                    else:
                        self.select(PlayerRole.RUNNER)
                if mode != "install":
                    self.assertIn(found["instance_id"], [str(c.instance_id) for c in hand_cards(self.state, self.state.runner)])
                self.assertEqual(self.state.runner.clicks_remaining, clicks)
                self.pass_paid()

    def test_longevity_trash_then_shuffle_up_to_three_including_just_trashed(self):
        self.turn(PlayerRole.CORP, clicks=20)
        agenda = self.add("Longevity Serum")
        targets = [self.add("Hedge Fund", installed=False) for _ in range(3)]
        self.score(agenda)
        self.resolve(PlayerRole.CORP, agenda)
        self.assertTrue(self.view(PlayerRole.CORP)["legal_actions"][0]["up_to"])
        self.select(PlayerRole.CORP, targets)
        self.assertTrue(set(map(str, targets)) <= set(self.view(PlayerRole.CORP)["legal_actions"][0]["eligible_card_instance_ids"]))
        self.select(PlayerRole.CORP, targets[:2])
        self.assertEqual(self.state.card_instances[targets[0]].location.zone, CardZone.DECK)
        self.assertEqual(self.state.card_instances[targets[2]].location.zone, CardZone.DISCARD_PILE)
        self.assertEqual(self.state.suspended_windows, [])

    def test_precision_and_malapert_share_scoring_order_with_original_server(self):
        self.identity("30035")
        self.turn(PlayerRole.CORP, clicks=20)
        other = self.add("Malapert Data Vault")
        matching = self.add("Malapert Data Vault")
        agenda = self.add("Offworld Office")
        self.state.card_instances[agenda].location = self.state.card_instances[matching].location.model_copy(deep=True)
        # Remove the unused remote introduced by the fixture.
        self.state.servers.pop()
        discarded = self.add("Hedge Fund", installed=False, zone=CardZone.DISCARD_PILE)
        self.score(agenda)
        sources = {a["card_instance_id"] for a in self.reactions(PlayerRole.CORP)}
        self.assertIn(str(matching), sources)
        self.assertNotIn(str(other), sources)
        self.resolve(PlayerRole.CORP, self.state.corp.identity_card_instance_id)
        self.select(PlayerRole.CORP, [discarded])
        self.assertEqual(self.state.card_instances[discarded].location.zone, CardZone.HAND)
        self.resolve(PlayerRole.CORP, matching)
        choices = self.view(PlayerRole.CORP)["selection_cards"]
        self.assertTrue(all(c["card_type"] != "agenda" for c in choices))
        self.select(PlayerRole.CORP, [choices[0]["instance_id"]])
        self.assertEqual(len(self.view(PlayerRole.RUNNER)["revealed_cards"]), 1)

    def test_spin_doctor_paid_removal_before_private_selection_and_no_reuse(self):
        self.turn(PlayerRole.CORP, clicks=20)
        spin = self.add("Spin Doctor")
        target = self.add("Hedge Fund", installed=False, zone=CardZone.DISCARD_PILE)
        self.state.window = PriorityWindow(kind="paid", player=PlayerRole.CORP)
        action = {"type": "use_card_ability", "card_instance_id": str(spin), "ability_id": "recycle"}
        clicks = self.state.corp.clicks_remaining
        self.submit(PlayerRole.CORP, action)
        self.assertEqual(self.state.card_instances[spin].location.zone, CardZone.REMOVED_FROM_GAME)
        self.assertEqual(self.state.corp.clicks_remaining, clicks)
        self.assertNotIn(str(spin), self.view(PlayerRole.CORP)["legal_actions"][0]["eligible_card_instance_ids"])
        self.assertEqual(self.view(PlayerRole.RUNNER)["selection_cards"], [])
        self.select(PlayerRole.CORP, [target])
        self.assertEqual(self.state.card_instances[target].location.zone, CardZone.DECK)
        self.reject(PlayerRole.CORP, action)

    def test_spin_doctor_rez_draws_two(self):
        self.turn(PlayerRole.CORP, clicks=20)
        spin = self.add("Spin Doctor")
        self.state.card_instances[spin].rezzed = False
        self.state.card_instances[spin].face_up = False
        self.state.window = PriorityWindow(kind="paid", player=PlayerRole.CORP)
        before = len(hand_cards(self.state, self.state.corp))
        self.submit(PlayerRole.CORP, {"type": "rez_card", "card_instance_id": str(spin)})
        self.assertEqual(len(hand_cards(self.state, self.state.corp)), before + 2)

    def test_pantograph_mandatory_credit_then_install_and_cookbook_reaction(self):
        self.turn(PlayerRole.CORP, clicks=20)
        pantograph = self.add("Pantograph", PlayerRole.RUNNER)
        cookbook = self.add("Cookbook", PlayerRole.RUNNER)
        virus = self.add("Fermenter", PlayerRole.RUNNER, installed=False)
        agenda = self.add("Offworld Office")
        self.state.runner.credits = 20
        self.state.runner.clicks_remaining = 0
        self.score(agenda)
        self.resolve(PlayerRole.CORP, agenda)
        if self.state.window.player is PlayerRole.CORP:
            self.submit(PlayerRole.CORP, {"type": "pass_priority"})
        self.resolve(PlayerRole.RUNNER, pantograph)
        self.assertEqual(self.state.runner.credits, 21)
        self.reject(PlayerRole.CORP, {"type": "install_card", "card_instance_id": str(virus)})
        self.submit(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(virus)})
        self.assertEqual(self.state.runner.clicks_remaining, 0)
        self.resolve(PlayerRole.RUNNER, cookbook)
        self.assertEqual(self.state.card_instances[virus].counters["virus"], 1)
        self.resolve(PlayerRole.RUNNER, virus)
        self.assertEqual(self.state.card_instances[virus].counters["virus"], 2)
        self.assertEqual(self.state.suspended_windows, [])

    def test_bran_installs_archives_ice_inward_for_free_and_encounters_it_next(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        bran = self.add("Brân 1.0")
        self.state.servers.pop()
        self.state.card_instances[bran].location = ZoneLocation(zone=CardZone.PLAY_AREA,
            server=ServerLocation(server_id="hq", area=ServerArea.ICE, ice_distance_from_server=0))
        ice = self.add("Palisade", installed=False, zone=CardZone.DISCARD_PILE)
        self.state.corp.credits = 0
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        self.pass_paid()
        # The two end-the-run routines were broken before the encounter resolves.
        self.state.run.broken_subroutine_indices = [1, 2]
        self.pass_paid()
        self.assertEqual(self.state.window.kind, "install")
        self.submit(PlayerRole.CORP, {"type": "install_card", "card_instance_id": str(ice), "server_id": "hq"})
        self.assertEqual(self.state.corp.credits, 0)
        self.assertEqual(self.state.card_instances[ice].location.server.ice_distance_from_server, 0)
        self.assertEqual(self.state.card_instances[bran].location.server.ice_distance_from_server, 1)
        self.assertEqual(self.state.run.remaining_ice_ids, [ice])
        self.submit(PlayerRole.RUNNER, {"type": "continue_run"})
        self.submit(PlayerRole.CORP, {"type": "pass_priority"})
        self.assertEqual(self.state.run.current_ice_id, ice)

    def test_search_with_no_matching_cards_still_shuffles(self):
        for card in self.state.card_instances.values():
            if card.owner_id == self.state.runner.player_id and card.location.zone is CardZone.DECK:
                if "Icebreaker" in self.cards[card.definition_id].subtypes:
                    card.location = ZoneLocation(zone=CardZone.HAND, player_id=card.owner_id)
        reindex_deck(self.state, self.state.runner.player_id)
        with patch("game.card_movement.SystemRandom") as rng:
            card = self.play("Mutual Favor", PlayerRole.RUNNER)
        rng.return_value.shuffle.assert_called_once()
        self.assertEqual(self.state.card_instances[card].location.zone, CardZone.DISCARD_PILE)

    def test_ansel_installs_from_archives_pays_ice_cost_and_restricts_this_run(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        ansel = self.add("Ansel 1.0")
        self.state.servers.pop()
        self.state.card_instances[ansel].location = ZoneLocation(zone=CardZone.PLAY_AREA,
            server=ServerLocation(server_id="hq", area=ServerArea.ICE, ice_distance_from_server=0))
        ice = self.add("Palisade", installed=False, zone=CardZone.DISCARD_PILE)
        self.state.corp.credits = 1
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        self.pass_paid()
        self.assertEqual(self.state.window.kind, "install")
        clicks = self.state.corp.clicks_remaining
        command = {"type": "install_card", "card_instance_id": str(ice), "server_id": "hq"}
        self.state.corp.credits = 0
        self.reject(PlayerRole.CORP, command)
        self.state.corp.credits = 1
        self.submit(PlayerRole.CORP, command)
        self.assertEqual(self.state.corp.credits, 0)
        self.assertEqual(self.state.corp.clicks_remaining, clicks)
        self.assertTrue(self.state.run.cannot_steal_or_trash)
        self.assertEqual(self.state.card_instances[ice].location.server.ice_distance_from_server, 1)
        self.assertEqual(self.state.run.remaining_ice_ids, [])
        self.submit(PlayerRole.RUNNER, {"type": "continue_run"})
        self.pass_paid()
        agenda = self.add("Offworld Office", installed=False)
        # Use an agenda as the deterministic HQ access.
        with patch("game.runs.SystemRandom") as rng:
            rng.return_value.choice.return_value = self.state.card_instances[agenda]
            self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": "hq"})
        self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
        self.assertEqual(self.state.card_instances[agenda].location.zone, CardZone.HAND)
        self.assertIsNone(self.state.run)

    def test_pantograph_on_steal_install_uses_run_credits_and_console_cleanup(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        pantograph = self.add("Pantograph", PlayerRole.RUNNER)
        replacement = self.add("Carnivore", PlayerRole.RUNNER, installed=False)
        agenda = self.add("Offworld Office")
        self.run_to_breach(self.state.card_instances[agenda].location.server.server_id)
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(agenda)})
        self.state.runner.credits = 0
        self.state.run.bad_publicity_credits = 10
        self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
        self.assertEqual(self.state.window.kind, "install")
        self.submit(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(replacement)})
        self.assertTrue(self.state.card_instances[replacement].installed)
        self.assertFalse(self.state.card_instances[pantograph].installed)
        self.assertEqual(self.state.runner.credits, 1)
        self.assertIsNone(self.state.run)

    def test_effect_program_install_preserves_memory_replacements_and_discounts(self):
        self.turn(PlayerRole.CORP, clicks=20)
        pantograph = self.add("Pantograph", PlayerRole.RUNNER)
        self.add("DZMZ Optimizer", PlayerRole.RUNNER)
        programs = [self.add("Cleaver", PlayerRole.RUNNER) for _ in range(3)]
        # Pantograph and DZMZ add two MU; three Cleavers fill the resulting limit.
        self.state.runner.memory_limit = 1
        target = self.add("Cleaver", PlayerRole.RUNNER, installed=False)
        agenda = self.add("Offworld Office")
        self.state.runner.credits = 20
        self.score(agenda)
        self.resolve(PlayerRole.CORP, agenda)
        if self.state.window.player is PlayerRole.CORP:
            self.submit(PlayerRole.CORP, {"type": "pass_priority"})
        self.resolve(PlayerRole.RUNNER, pantograph)
        self.reject(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(target)})
        self.submit(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(target),
            "trash_card_instance_ids": [str(programs[0])]})
        self.assertFalse(self.state.card_instances[programs[0]].installed)
        self.assertTrue(self.state.card_instances[target].installed)
        self.assertEqual(self.state.runner.credits, 21 - (self.cards[self.state.card_instances[target].definition_id].install_cost - 1))

    def test_ansel_replacing_itself_finishes_encounter_without_running_remaining_subroutines(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        ansel = self.add("Ansel 1.0")
        self.state.servers.pop()
        self.state.card_instances[ansel].location = ZoneLocation(zone=CardZone.PLAY_AREA,
            server=ServerLocation(server_id="hq", area=ServerArea.ICE, ice_distance_from_server=0))
        replacement = self.add("Palisade", installed=False, zone=CardZone.DISCARD_PILE)
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        self.pass_paid()
        self.submit(PlayerRole.CORP, {"type": "install_card", "card_instance_id": str(replacement),
            "server_id": "hq", "trash_card_instance_ids": [str(ansel)]})
        self.assertFalse(self.state.card_instances[ansel].installed)
        self.assertEqual(self.state.run.phase, "movement")
        self.assertFalse(self.state.run.cannot_steal_or_trash)
        self.assertEqual(self.state.run.remaining_ice_ids, [])
        self.assertEqual(self.state.suspended_windows, [])

    def test_search_websocket_projects_private_choices_and_public_reveal_on_reconnect(self):
        self.turn(PlayerRole.RUNNER, clicks=20)
        event = self.add("Mutual Favor", PlayerRole.RUNNER, installed=False)
        with self.client.websocket_connect(self.path + "/events", headers=self.headers[PlayerRole.CORP]) as corp:
            with self.client.websocket_connect(self.path + "/events", headers=self.headers[PlayerRole.RUNNER]) as runner:
                corp.receive_json()
                runner.receive_json()
                self.submit(PlayerRole.RUNNER, {"type": "play_card", "card_instance_id": str(event)})
                corp_view, runner_view = corp.receive_json()["state"], runner.receive_json()["state"]
                self.assertEqual(corp_view["selection_cards"], [])
                self.assertTrue(runner_view["selection_cards"])
                found = runner_view["selection_cards"][0]["instance_id"]
                self.assertNotIn(found, str(corp_view))
                self.select(PlayerRole.RUNNER, [found])
                revealed = corp.receive_json()["state"]["revealed_cards"]
                self.assertEqual(revealed, runner.receive_json()["state"]["revealed_cards"])
        with self.client.websocket_connect(self.path + "/events", headers=self.headers[PlayerRole.CORP]) as corp:
            self.assertEqual(corp.receive_json()["state"]["revealed_cards"], revealed)
