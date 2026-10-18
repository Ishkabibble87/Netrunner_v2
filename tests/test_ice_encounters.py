"""Protected runs, priority, and breaker legality through the API boundary."""

import unittest
from collections import Counter
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from application.game_setup import create_ai_game
from application.sessions import advance_ai_decisions
from game.models import CardZone, PlayerRole


class RunScenario(unittest.TestCase):
    """API fixture shared by protected-run and run-event scenarios."""

    corp_opening = ["Offworld Office", "Regolith Mining License", "Palisade", "Palisade", "Hedge Fund"]
    runner_opening = ["Cleaver", "Cleaver", "Sure Gamble", "Sure Gamble", "Telework Contract"]

    def setUp(self):
        self.app = create_app()
        self.cards = self.app.state.card_definitions
        profile = self.app.state.opponent_profiles["system-gateway-training"]
        def ordered(role, titles):
            selection = profile.deck_selections[role]
            prefix = [next(code for code, card in self.cards.items() if card.title == title) for title in titles]
            counts = Counter(selection.card_counts)
            counts.subtract(prefix)
            return prefix + [code for code in counts for _ in range(counts[code])]
        game = create_ai_game(player_id=uuid4(), decklist=profile.deck_selections[PlayerRole.CORP],
                              opponent_profile=profile, card_definitions=self.cards,
                              deck_orders={
                                  PlayerRole.CORP: ordered(PlayerRole.CORP, self.corp_opening),
                                  PlayerRole.RUNNER: ordered(PlayerRole.RUNNER, self.runner_opening),
                              })
        self.session = self.app.state.sessions.create(game, profile.profile_id, [game.corp.player_id, game.runner.player_id])
        self.client = self.enterContext(TestClient(self.app))
        self.path = f"/games/{game.game_id}"
        self.headers = {p.role.value: {"Authorization": f"Bearer {self.session.player_tokens[p.player_id]}"} for p in (game.corp, game.runner)}
        for role in ("corp", "runner"):
            self.submit(role, {"type": "choose_mulligan", "decision": "keep"})
        self.session.state.corp.credits = 30
        self.session.state.runner.credits = 30

    def submit(self, role, command):
        response = self.client.post(self.path + "/commands", headers=self.headers[role], json=command)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["state"]

    def view(self, role="runner"):
        return self.client.get(self.path + "/state", headers=self.headers[role]).json()

    def reject(self, role, command, status=409):
        before = self.session.state.model_dump()
        revision = self.session.revision
        response = self.client.post(self.path + "/commands", headers=self.headers[role], json=command)
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.session.state.model_dump(), before)
        self.assertEqual(self.session.revision, revision)

    def card(self, title, zone=CardZone.HAND):
        return next(c for c in self.session.state.card_instances.values()
                    if self.cards[c.definition_id].title == title and c.location.zone is zone)

    def pass_windows(self):
        for _ in range(30):
            if self.session.state.window is None:
                return
            self.submit(self.session.state.window.player.value, {"type": "pass_priority"})
        self.fail("Priority did not settle")

    def install(self, title, server):
        card = self.card(title)
        self.submit("corp", {"type": "install_card", "card_instance_id": str(card.instance_id), "server_id": server})
        self.pass_windows()
        return card.instance_id

    def runner_turn(self):
        for _ in range(30):
            self.pass_windows()
            if self.session.state.turn.active_player is PlayerRole.RUNNER:
                return
            view = self.view("corp")
            selection = next((a for a in view["legal_actions"] if a["type"] == "discard_cards"), None)
            action = {"type": "gain_credit"} if selection is None else {
                "type": "discard_cards", "card_instance_ids": selection["eligible_card_instance_ids"][:selection["required_count"]]}
            self.submit("corp", action)
        self.fail("Runner turn not reached")

    def breaker(self):
        card = self.card("Cleaver")
        self.submit("runner", {"type": "install_card", "card_instance_id": str(card.instance_id)})
        self.pass_windows()
        return card.instance_id

    def ability(self, breaker, name, targets=()):
        return {"type": "use_card_ability", "card_instance_id": str(breaker),
                "ability_id": name, "subroutine_indices": list(targets)}

    def approach(self, server, breaker=None):
        self.submit("runner", {"type": "run_server", "server_id": server})
        for _ in range(10):
            run = self.session.state.run
            if run.phase == "approach" and self.session.state.window.player is PlayerRole.CORP:
                return
            self.submit(self.session.state.window.player.value, {"type": "pass_priority"})
        self.fail("Did not reach Corp approach priority")

    def rez_and_encounter(self, ice):
        self.submit("corp", {"type": "rez_card", "card_instance_id": str(ice)})
        self.submit("corp", {"type": "pass_priority"})
        if self.session.state.run.phase == "approach":
            self.submit("runner", {"type": "pass_priority"})
        return self.view()

class IceEncounterTests(RunScenario):
    def test_remote_priority_asset_rez_break_and_steal_publishes_both_views(self):
        office = self.install("Offworld Office", "new_remote")
        ice = self.install("Palisade", 1)
        asset = self.install("Regolith Mining License", "new_remote")
        self.runner_turn()
        breaker = self.breaker()
        self.approach(1)
        hidden = self.view()["run"]
        self.assertIsNone(hidden["current_ice"])
        self.assertEqual(hidden["subroutines"], [])
        self.reject("runner", {"type": "jack_out"})
        self.reject("runner", self.ability(breaker, "boost_strength"))
        self.submit("corp", {"type": "rez_card", "card_instance_id": str(asset)})
        self.assertEqual(self.session.state.card_instances[asset].counters, {"credit": 15})
        self.assertIs(self.session.state.window.player, PlayerRole.CORP)
        # An action after receiving priority resets the consecutive-pass count.
        self.submit("corp", {"type": "pass_priority"})
        self.assertIs(self.session.state.window.player, PlayerRole.RUNNER)
        self.submit("runner", self.ability(breaker, "boost_strength"))
        self.assertEqual(self.session.state.run.strength_boosts, {})
        self.submit("runner", {"type": "pass_priority"})
        self.assertIs(self.session.state.window.player, PlayerRole.CORP)
        self.assertEqual(self.session.state.run.phase, "approach")
        self.submit("corp", {"type": "rez_card", "card_instance_id": str(ice)})
        self.submit("corp", {"type": "pass_priority"})
        self.submit("runner", {"type": "pass_priority"})
        self.assertEqual(self.session.state.run.phase, "encounter")
        self.assertFalse(self.session.state.window.rez_allowed)
        self.reject("corp", {"type": "rez_card", "card_instance_id": str(asset)})
        self.reject("runner", self.ability(breaker, "break_barrier", (0,)))
        before = self.session.state.runner.credits
        self.submit("runner", self.ability(breaker, "boost_strength"))
        with self.client.websocket_connect(self.path + "/events", headers=self.headers["runner"]) as r:
            with self.client.websocket_connect(self.path + "/events", headers=self.headers["corp"]) as c:
                r.receive_json()
                c.receive_json()
                self.submit("runner", self.ability(breaker, "break_barrier", (0,)))
                for message in (r.receive_json(), c.receive_json()):
                    self.assertTrue(message["state"]["run"]["subroutines"][0]["broken"])
                    self.assertEqual(message["state"]["run"]["current_ice"]["strength"], 4)
        self.assertEqual(self.session.state.runner.credits, before - 3)
        self.reject("runner", self.ability(breaker, "break_barrier", (0,)))
        self.pass_windows()
        self.assertEqual(self.session.state.run.phase, "movement")
        self.assertEqual(self.session.state.run.strength_boosts, {})
        self.assertEqual(self.session.state.run.broken_subroutine_indices, [])
        self.submit("runner", {"type": "continue_run"})
        self.pass_windows()
        self.submit("runner", {"type": "access_card", "target": str(office)})
        self.submit("runner", {"type": "finish_access"})
        self.assertEqual(self.view()["you"]["agenda_points"], 2)
        self.assertIsNone(self.session.state.run)

    def test_unbroken_ice_ends_run_and_next_encounter_starts_fresh(self):
        ice = self.install("Palisade", "hq")
        self.runner_turn()
        breaker = self.breaker()
        self.approach("hq")
        self.rez_and_encounter(ice)
        self.assertEqual(self.view()["run"]["current_ice"]["strength"], 2)
        self.submit("runner", self.ability(breaker, "boost_strength"))
        self.pass_windows()
        self.assertIsNone(self.session.state.run)
        self.pass_windows()
        self.submit("runner", {"type": "run_server", "server_id": "hq"})
        for _ in range(10):
            if self.session.state.run.phase == "encounter":
                break
            self.submit(self.session.state.window.player.value, {"type": "pass_priority"})
        self.assertEqual(self.session.state.run.strength_boosts, {})
        self.assertFalse(self.view()["run"]["subroutines"][0]["broken"])
        before = self.session.state.runner.credits
        self.submit("runner", self.ability(breaker, "break_barrier", (0,)))
        self.assertEqual(self.session.state.runner.credits, before - 1)
        self.pass_windows()
        self.submit("runner", {"type": "jack_out"})
        self.assertIsNone(self.session.state.run)

    def test_unrezzed_ice_passes_outermost_first_and_jack_out_between(self):
        inner = self.install("Palisade", "hq")
        outer = self.install("Palisade", "hq")
        self.assertEqual(self.session.state.corp.credits, 29)
        self.runner_turn()
        self.approach("hq")
        self.assertEqual(self.session.state.run.current_ice_id, outer)
        self.reject("corp", {"type": "rez_card", "card_instance_id": str(inner)})
        self.submit("corp", {"type": "pass_priority"})
        self.assertEqual(self.session.state.run.phase, "movement")
        self.submit("runner", {"type": "continue_run"})
        self.submit("corp", {"type": "pass_priority"})  # CR 6.9.4e, before next approach.
        self.assertEqual(self.session.state.run.current_ice_id, inner)
        self.pass_windows()
        self.assertEqual(self.session.state.run.phase, "movement")
        self.submit("runner", {"type": "jack_out"})
        self.assertIsNone(self.session.state.run)

    def test_memory_replacement_and_invalid_costs_targets_are_atomic(self):
        ice = self.install("Palisade", "hq")
        self.runner_turn()
        self.session.state.runner.memory_limit = 1
        breaker = self.breaker()
        second = self.card("Cleaver").instance_id
        install = {"type": "install_card", "card_instance_id": str(second)}
        self.reject("runner", install)
        self.reject("runner", dict(install, trash_card_instance_ids=[str(ice)]))
        self.submit("runner", dict(install, trash_card_instance_ids=[str(breaker)]))
        self.assertEqual(self.view()["you"]["memory_used"], 1)
        self.assertFalse(self.session.state.card_instances[breaker].installed)
        self.pass_windows()
        self.approach("hq")
        self.rez_and_encounter(ice)
        self.reject("runner", self.ability(breaker, "boost_strength"))
        self.reject("runner", self.ability(second, "break_barrier", (0, 0)))
        self.reject("runner", self.ability(second, "break_barrier", (1,)))
        self.reject("runner", self.ability(second, "break_barrier", (-1,)), 422)
        self.session.state.runner.credits = 0
        self.reject("runner", self.ability(second, "break_barrier", (0,)))
        self.session.state.run.bad_publicity_credits = 1
        self.submit("runner", self.ability(second, "break_barrier", (0,)))
        self.assertEqual(self.session.state.runner.credits, 0)
        self.assertEqual(self.session.state.run.bad_publicity_credits, 0)

    def test_multiple_encounters_reset_boosts_and_subtype_restriction(self):
        inner = self.install("Palisade", "hq")
        outer = self.install("Palisade", "hq")
        self.runner_turn()
        breaker = self.breaker()
        self.approach("hq")
        self.rez_and_encounter(outer)
        self.submit("runner", self.ability(breaker, "boost_strength"))
        self.submit("runner", self.ability(breaker, "break_barrier", (0,)))
        self.pass_windows()
        self.submit("runner", {"type": "continue_run"})
        while self.session.state.window.player is PlayerRole.RUNNER:
            self.submit("runner", {"type": "pass_priority"})
        self.submit("corp", {"type": "pass_priority"})  # Movement's (P)(R) window.
        self.submit("runner", {"type": "pass_priority"})  # Approach begins with the active player.
        self.rez_and_encounter(inner)
        self.assertEqual(self.session.state.run.phase, "encounter")
        self.assertEqual(self.session.state.run.strength_boosts, {})
        self.assertFalse(self.view()["run"]["subroutines"][0]["broken"])
        definition = self.cards[self.session.state.card_instances[inner].definition_id]
        original = definition.subtypes
        definition.subtypes = ("Code Gate",)
        self.reject("runner", self.ability(breaker, "break_barrier", (0,)))
        definition.subtypes = original

    def test_rez_funds_and_encounter_forbids_asset_rez(self):
        ice = self.install("Palisade", "hq")
        asset = self.install("Regolith Mining License", "new_remote")
        self.runner_turn()
        breaker = self.breaker()
        self.approach("hq")
        self.session.state.corp.credits = 2
        self.reject("corp", {"type": "rez_card", "card_instance_id": str(ice)})
        self.session.state.corp.credits = 3
        self.rez_and_encounter(ice)
        # Even a forged Corp-priority encounter cannot turn (P) into (P)(R).
        self.session.state.window.player = PlayerRole.CORP
        self.reject("corp", {"type": "rez_card", "card_instance_id": str(asset)})
        self.session.state.window.player = PlayerRole.RUNNER
        self.assertIn(self.ability(breaker, "break_barrier", (0,)), self.view()["legal_actions"])

    def test_training_corp_rezzes_supported_ice_during_human_run(self):
        ice = self.install("Palisade", "hq")
        self.runner_turn()
        self.breaker()
        self.submit("runner", {"type": "run_server", "server_id": "hq"})
        while self.session.state.run and self.session.state.run.phase != "encounter":
            if self.session.state.window.player is PlayerRole.CORP:
                self.session.state = advance_ai_decisions(self.session.state,
                    {self.session.state.corp.player_id}, self.cards)
            else:
                self.submit("runner", {"type": "pass_priority"})
        self.assertTrue(self.session.state.card_instances[ice].rezzed)
        self.assertIs(self.session.state.window.player, PlayerRole.RUNNER)


    def test_break_two_subroutines_once_and_finish_a_run_on_the_last_click(self):
        ice = self.install("Palisade", "hq")
        definition = self.cards[self.session.state.card_instances[ice].definition_id]
        # Isolate Cleaver's "up to 2" selection without implementing another card.
        self.cards[definition.definition_id] = definition.model_copy(update={
            "subroutines": definition.subroutines * 2})
        self.runner_turn()
        breaker = self.breaker()
        self.session.state.runner.clicks_remaining = 1
        self.approach("hq")
        self.rez_and_encounter(ice)
        before = self.session.state.runner.credits
        self.assertEqual(self.session.state.runner.clicks_remaining, 0)
        self.reject("runner", {"type": "draw_card"})
        self.submit("runner", self.ability(breaker, "break_barrier", (0, 1)))
        self.assertEqual(self.session.state.runner.credits, before - 1)
        self.assertEqual(self.session.state.run.broken_subroutine_indices, [0, 1])
        self.pass_windows()
        self.submit("runner", {"type": "continue_run"})
        self.pass_windows()
        self.submit("runner", {"type": "access_card", "target": "hq"})
        self.submit("runner", {"type": "finish_access"})
        self.assertIsNone(self.session.state.run)
        self.pass_windows()
        self.assertIs(self.session.state.turn.active_player, PlayerRole.CORP)


class RunEventTests(RunScenario):
    """Exercise event timing, distinct accesses, and restricted credit lifetimes."""

    corp_opening = ["Offworld Office", "Regolith Mining License", "Palisade", "Palisade", "Manegarm Skunkworks"]
    runner_opening = ["Jailbreak", "Jailbreak", "Overclock", "Overclock", "Cleaver"]

    def play(self, title, server):
        card = self.card(title)
        self.submit("runner", {"type": "play_card", "card_instance_id": str(card.instance_id), "server_id": server})
        self.assertEqual(self.session.state.card_instances[card.instance_id].location.zone, CardZone.PLAY_AREA)
        return card.instance_id

    def breach(self):
        self.pass_windows()
        self.submit("runner", {"type": "continue_run"})
        self.pass_windows()

    def test_jailbreak_distinct_accesses_draw_and_private_choices(self):
        self.runner_turn()
        for server in ("hq", "rd"):
            with self.subTest(server=server):
                before = self.view()
                event = self.play("Jailbreak", server)
                self.assertEqual(self.view()["you"]["clicks_remaining"], before["you"]["clicks_remaining"] - 1)
                self.assertEqual(len(self.view()["you"]["hand"]), len(before["you"]["hand"]) - 1)
                self.breach()
                self.assertEqual(len(self.view()["you"]["hand"]), len(before["you"]["hand"]))
                self.assertEqual(self.view()["run"]["central_access_limit"], 2)
                seen = []
                for _ in range(2):
                    view = self.submit("runner", {"type": "access_card", "target": server})
                    seen.append(view["run"]["current_card"]["instance_id"])
                    if server == "rd":
                        self.assertIsNone(self.view("corp")["run"]["current_card"])
                    self.submit("runner", {"type": "finish_access"})
                self.assertEqual(len(set(seen)), 2)
                self.assertIsNone(self.session.state.run)
                self.assertEqual(self.session.state.card_instances[event].location.zone, CardZone.DISCARD_PILE)
                self.pass_windows()

    def test_illegal_targets_and_failed_run_have_no_success_bonus(self):
        ice = self.install("Palisade", "hq")
        self.runner_turn()
        jailbreak = self.card("Jailbreak")
        for target in (None, "archives", 99):
            self.reject("runner", {"type": "play_card", "card_instance_id": str(jailbreak.instance_id), "server_id": target})
        hand_size = len(self.view()["you"]["hand"])
        event = self.play("Jailbreak", "hq")
        while self.session.state.run.phase != "approach" or self.session.state.window.player is not PlayerRole.CORP:
            self.submit(self.session.state.window.player.value, {"type": "pass_priority"})
        self.submit("corp", {"type": "rez_card", "card_instance_id": str(ice)})
        self.pass_windows()
        self.assertIsNone(self.session.state.run)
        self.assertEqual(len(self.view()["you"]["hand"]), hand_size - 1)
        self.assertEqual(self.session.state.card_instances[event].location.zone, CardZone.DISCARD_PILE)

    def test_jailbreak_small_centrals_and_rd_removal(self):
        from game.models import ZoneLocation

        self.runner_turn()
        # Leave two R&D cards, forcing the first to be an agenda that moves on access.
        state = self.session.state
        deck = sorted((c for c in state.card_instances.values() if c.location.zone is CardZone.DECK
                       and c.owner_id == state.corp.player_id), key=lambda c: c.location.deck_position)
        agenda = next(c for c in deck if self.cards[c.definition_id].card_type.value == "agenda")
        other = next(c for c in deck if self.cards[c.definition_id].card_type.value != "agenda")
        for card in deck:
            card.face_up = False
            card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=state.corp.player_id)
        for position, card in enumerate((agenda, other), 1):
            card.location = ZoneLocation(zone=CardZone.DECK, player_id=state.corp.player_id, deck_position=position)
        self.play("Jailbreak", "rd")
        self.breach()
        view = self.submit("runner", {"type": "access_card", "target": "rd"})
        self.assertEqual(view["run"]["current_card"]["instance_id"], str(agenda.instance_id))
        self.submit("runner", {"type": "finish_access"})
        view = self.submit("runner", {"type": "access_card", "target": "rd"})
        self.assertEqual(view["run"]["current_card"]["instance_id"], str(other.instance_id))
        self.submit("runner", {"type": "finish_access"})
        self.pass_windows()
        self.play("Jailbreak", "rd")
        self.breach()
        self.submit("runner", {"type": "access_card", "target": "rd"})
        self.submit("runner", {"type": "finish_access"})
        self.assertIsNone(self.session.state.run)

    def test_overclock_pays_breaker_and_trash_then_expires(self):
        asset = self.install("Regolith Mining License", "new_remote")
        ice = self.install("Palisade", 1)
        self.runner_turn()
        breaker = self.breaker()
        self.session.state.runner.credits = 2
        self.session.state.corp.bad_publicity = 1
        event = self.play("Overclock", 1)
        self.assertEqual(self.view()["run"]["event_credits"], 5)
        self.assertEqual(self.view("corp")["run"]["source_event"]["title"], "Overclock")
        self.assertEqual(self.session.state.runner.credits, 1)
        while self.session.state.run.phase != "approach" or self.session.state.window.player is not PlayerRole.CORP:
            self.submit(self.session.state.window.player.value, {"type": "pass_priority"})
        self.submit("corp", {"type": "rez_card", "card_instance_id": str(ice)})
        while self.session.state.run.phase != "encounter":
            self.submit(self.session.state.window.player.value, {"type": "pass_priority"})
        self.submit("runner", self.ability(breaker, "boost_strength"))
        self.submit("runner", self.ability(breaker, "break_barrier", (0,)))
        self.assertEqual(self.view()["run"]["event_credits"], 3)
        self.assertEqual(self.session.state.runner.credits, 1)
        self.breach()
        self.submit("runner", {"type": "access_card", "target": str(asset)})
        self.submit("runner", {"type": "finish_access", "trash": True})
        self.assertEqual(self.session.state.runner.credits, 1)
        self.assertEqual(self.session.state.card_instances[event].counters, {})
        self.assertIsNone(self.session.state.run)
        self.pass_windows()
        event = self.play("Overclock", "archives")
        self.pass_windows()
        self.submit("runner", {"type": "jack_out"})
        self.assertEqual(self.session.state.card_instances[event].counters, {})
        self.assertEqual(self.session.state.runner.credits, 0)

    def test_overclock_cost_and_last_click_cleanup(self):
        self.runner_turn()
        event = self.card("Overclock")
        self.session.state.runner.credits = 0
        self.reject("runner", {"type": "play_card", "card_instance_id": str(event.instance_id), "server_id": "hq"})
        self.session.state.runner.credits = 1
        self.session.state.runner.clicks_remaining = 1
        turn = self.session.state.turn.number
        event = self.play("Overclock", "hq")
        self.assertEqual(self.session.state.runner.clicks_remaining, 0)
        self.assertEqual(self.session.state.turn.number, turn)
        self.pass_windows()
        self.submit("runner", {"type": "jack_out"})
        self.assertIsNone(self.session.state.run)
        self.assertEqual(self.session.state.card_instances[event].counters, {})
        self.assertEqual(self.session.state.card_instances[event].location.zone, CardZone.DISCARD_PILE)


    def test_jailbreak_root_access_does_not_use_central_limit_and_empty_hq_finishes(self):
        from game.models import ZoneLocation

        asset = self.install("Manegarm Skunkworks", "hq")
        self.runner_turn()
        state = self.session.state
        hand = [c for c in state.card_instances.values() if c.location.zone is CardZone.HAND
                and c.owner_id == state.corp.player_id]
        kept = next(c for c in hand if self.cards[c.definition_id].card_type.value != "agenda")
        for card in hand:
            if card is not kept:
                card.face_up = False
                card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=state.corp.player_id)
        self.play("Jailbreak", "hq")
        self.breach()
        self.submit("runner", {"type": "access_card", "target": str(asset)})
        self.submit("runner", {"type": "finish_access"})
        self.assertEqual(self.session.state.run.central_accesses, 0)
        self.submit("runner", {"type": "access_card", "target": "hq"})
        self.submit("runner", {"type": "finish_access"})
        self.assertIsNone(self.session.state.run)
        self.pass_windows()
        kept = self.session.state.card_instances[kept.instance_id]
        kept.face_up = False
        kept.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=state.corp.player_id)
        self.play("Jailbreak", "hq")
        self.breach()
        self.assertNotIn({"type": "access_card", "target": "hq"}, self.view()["legal_actions"])
        self.submit("runner", {"type": "access_card", "target": str(asset)})
        self.submit("runner", {"type": "finish_access"})
        self.assertIsNone(self.session.state.run)

    def test_overclock_insufficient_hosted_funds_and_ice_end_cleanup(self):
        ice = self.install("Palisade", "hq")
        self.runner_turn()
        breaker = self.breaker()
        self.session.state.runner.credits = 1
        event = self.play("Overclock", "hq")
        while self.session.state.run.phase != "approach" or self.session.state.window.player is not PlayerRole.CORP:
            self.submit(self.session.state.window.player.value, {"type": "pass_priority"})
        self.submit("corp", {"type": "rez_card", "card_instance_id": str(ice)})
        while self.session.state.run.phase != "encounter":
            self.submit(self.session.state.window.player.value, {"type": "pass_priority"})
        for _ in range(2):
            self.submit("runner", self.ability(breaker, "boost_strength"))
        self.assertEqual(self.view()["run"]["event_credits"], 1)
        self.reject("runner", self.ability(breaker, "boost_strength"))
        self.pass_windows()
        self.assertIsNone(self.session.state.run)
        self.assertEqual(self.session.state.card_instances[event].counters, {})
        self.assertEqual(self.session.state.runner.credits, 0)
        self.assertEqual(self.session.state.card_instances[event].location.zone, CardZone.DISCARD_PILE)
