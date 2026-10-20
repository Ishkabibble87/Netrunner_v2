"""Exercise reaction timing, continuation and publication beyond the core path."""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from application.game_setup import create_ai_game
from application.sessions import advance_ai_decisions
from content.cards.models import TriggeredAbility
from game.models import (
    CardInstance,
    CardZone,
    PlayerRole,
    PriorityWindow,
    RuleFact,
    ServerArea,
    ServerLocation,
    TurnPhase,
    TurnState,
    ZoneLocation,
)
from game.reactions import checkpoint, emit_fact
from game.turns import advance_turn, hand_cards


class ReactionScenario(unittest.TestCase):
    """Two authenticated players with helpers for isolated timing scenarios."""
    def setUp(self):
        self.app = create_app()
        self.cards = self.app.state.card_definitions
        profile = self.app.state.opponent_profiles["system-gateway-training"]
        state = create_ai_game(player_id=uuid4(), decklist=profile.deck_selections[PlayerRole.CORP],
                               opponent_profile=profile, card_definitions=self.cards)
        self.session = self.app.state.sessions.create(state, profile.profile_id,
                                                     [state.corp.player_id, state.runner.player_id])
        self.client = self.enterContext(TestClient(self.app))
        self.path = f"/games/{state.game_id}"
        self.headers = {p.role: {"Authorization": f"Bearer {self.session.player_tokens[p.player_id]}"}
                        for p in (state.corp, state.runner)}
        for role in PlayerRole:
            self.submit(role, {"type": "choose_mulligan", "decision": "keep"})

    @property
    def state(self):
        return self.session.state

    def submit(self, role, command):
        response = self.client.post(self.path + "/commands", headers=self.headers[role], json=command)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["state"]

    def view(self, role):
        return self.client.get(self.path + "/state", headers=self.headers[role]).json()

    def reject(self, role, command):
        before, revision = self.state.model_dump(), self.session.revision
        response = self.client.post(self.path + "/commands", headers=self.headers[role], json=command)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.state.model_dump(), before)
        self.assertEqual(self.session.revision, revision)

    def turn(self, role, *, step="action", clicks=4):
        self.state.window = None
        self.state.turn = TurnState(number=self.state.turn.number + 1, active_player=role,
            phase=TurnPhase.DRAW if step in {"start", "turn_begin"} and role is PlayerRole.CORP else TurnPhase.ACTION,
            step=step)
        player = self.state.corp if role is PlayerRole.CORP else self.state.runner
        player.clicks_remaining = clicks

    def add_card(self, code, role, *, installed=True, credits=None):
        player = self.state.corp if role is PlayerRole.CORP else self.state.runner
        location = ZoneLocation(zone=CardZone.PLAY_AREA) if installed else ZoneLocation(zone=CardZone.HAND, player_id=player.player_id)
        if installed and role is PlayerRole.CORP:
            server = 1 + sum(isinstance(s, int) for s in self.state.servers)
            self.state.servers.append(server)
            location = ZoneLocation(zone=CardZone.PLAY_AREA, server=ServerLocation(server_id=server, area=ServerArea.ROOT))
        card = CardInstance(instance_id=uuid4(), definition_id=code, owner_id=player.player_id,
            controller_id=player.player_id, installed=installed,
            rezzed=installed and role is PlayerRole.CORP, face_up=True if installed else None,
            location=location, counters={"credit": credits} if credits is not None else {},
            loaded_counter_types={"credit"} if credits is not None else set())
        self.state.card_instances[card.instance_id] = card
        return card.instance_id

    def identity(self, code):
        identity = self.state.card_instances[self.state.corp.identity_card_instance_id]
        identity.definition_id = code
        self.state.corp.deck_selection.identity_definition_id = code

    def reactions(self, role):
        return [a for a in self.view(role)["legal_actions"] if a["type"] == "resolve_reaction" and not a["decline"]]

    def pass_paid(self):
        for _ in range(20):
            if self.state.window is None or self.state.window.kind != "paid":
                return
            self.submit(self.state.window.player, {"type": "pass_priority"})
        self.fail("Paid window did not close")

    def runner_identity(self, code):
        identity = self.state.card_instances[self.state.runner.identity_card_instance_id]
        identity.definition_id = code
        self.state.runner.deck_selection.identity_definition_id = code

    def run_to_breach(self, server, *, command=None):
        self.submit(PlayerRole.RUNNER, command or {"type": "run_server", "server_id": server})
        self.pass_paid()
        self.submit(PlayerRole.RUNNER, {"type": "continue_run"})
        self.pass_paid()

    def finish_breach(self):
        for _ in range(30):
            if self.state.run is None or self.state.run.phase == "ended":
                return
            if self.state.window:
                self.submit(self.state.window.player, {"type": "pass_priority"})
                continue
            actions = self.view(PlayerRole.RUNNER)["legal_actions"]
            action = next((a for a in actions if a["type"] in {"access_card", "finish_access"}), None)
            self.assertIsNotNone(action, actions)
            self.submit(PlayerRole.RUNNER, action)
        self.fail("Breach did not finish")

class ReactionTests(ReactionScenario):
    def test_loup_trash_reaction_suspends_access_and_only_first_trash_pays(self):
        self.runner_identity("30001")
        first = self.add_card("30071", PlayerRole.CORP)  # Regolith
        second = self.add_card("30071", PlayerRole.CORP)
        self.turn(PlayerRole.RUNNER)
        self.state.runner.credits = 20
        for index, target in enumerate((first, second)):
            self.run_to_breach(self.state.card_instances[target].location.server.server_id)
            self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(target)})
            credits, hand = self.state.runner.credits, len(hand_cards(self.state, self.state.runner))
            self.submit(PlayerRole.RUNNER, {"type": "finish_access", "trash": True})
            self.assertEqual(self.state.runner.credits, credits - 3)
            if index == 0:
                self.assertEqual(self.state.run.phase, "access")
                self.reject(PlayerRole.RUNNER, {"type": "finish_access"})
                self.reject(PlayerRole.RUNNER, {"type": "pass_priority"})
                self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
            self.assertIsNone(self.state.run)
            self.assertEqual(self.state.runner.credits, credits - 3 + (index == 0))
            self.assertEqual(len(hand_cards(self.state, self.state.runner)), hand + (index == 0))
            self.pass_paid()

    def test_docklands_success_before_breach_and_bonus_stacks_with_jailbreak(self):
        self.turn(PlayerRole.RUNNER)
        self.add_card("30013", PlayerRole.RUNNER)
        jailbreak = self.add_card("30028", PlayerRole.RUNNER, installed=False)
        identity = self.state.card_instances[self.state.runner.identity_card_instance_id]
        definition = self.cards[identity.definition_id]
        self.cards[identity.definition_id] = definition.model_copy(update={"triggered_abilities": (
            TriggeredAbility(ability_id="test_success", label="Success", trigger="run_success", optional=True,
                             instructions=({"type": "gain_credits", "amount": 1},)),)})
        self.run_to_breach("hq", command={"type": "play_card", "card_instance_id": str(jailbreak), "server_id": "hq"})
        self.assertEqual(self.state.run.phase, "success")
        self.assertEqual(self.state.run.access_limit, 1)
        self.assertEqual(len(self.reactions(PlayerRole.RUNNER)), 2)
        # Both success effects must complete before Docklands' breach trigger.
        while self.state.run.phase == "success":
            self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertEqual(self.state.run.phase, "breach")
        self.assertEqual(self.state.run.access_limit, 2)
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertEqual(self.state.run.access_limit, 3)
        self.finish_breach()
        self.pass_paid()
        self.run_to_breach("hq")
        self.submit(PlayerRole.RUNNER, {"type": "pass_priority"})  # optional success
        self.assertEqual(self.state.run.access_limit, 1)
        self.assertEqual(self.state.window, None)

    def test_first_hq_breach_counts_before_docklands_is_installed(self):
        self.turn(PlayerRole.RUNNER)
        self.run_to_breach("hq")
        self.finish_breach()
        self.pass_paid()
        card = self.add_card("30013", PlayerRole.RUNNER, installed=False)
        self.submit(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(card)})
        self.pass_paid()
        self.run_to_breach("hq")
        self.assertEqual(self.state.run.access_limit, 1)
        self.assertIsNone(self.state.window)
        self.finish_breach()
        self.turn(PlayerRole.RUNNER)
        self.run_to_breach("hq")
        self.assertEqual(len(self.reactions(PlayerRole.RUNNER)), 1)

    def test_zahya_decline_then_payout_keeps_run_context_until_cleanup(self):
        self.runner_identity("30010")
        self.turn(PlayerRole.RUNNER)
        self.state.corp.bad_publicity = 2
        self.run_to_breach("hq")
        self.finish_breach()
        self.assertEqual(self.state.run.phase, "ended")
        self.assertEqual(self.state.run.bad_publicity_credits, 0)
        first = self.reactions(PlayerRole.RUNNER)[0]
        self.submit(PlayerRole.RUNNER, dict(first, decline=True))
        self.assertIsNone(self.state.run)
        self.pass_paid()
        event = self.add_card(next(c.definition_id for c in self.cards.values() if c.title == "Jailbreak"), PlayerRole.RUNNER, installed=False)
        self.run_to_breach("rd", command={"type": "play_card", "card_instance_id": str(event), "server_id": "rd"})
        self.finish_breach()
        reaction = self.state.window.reactions[0]
        self.assertEqual(reaction.fact.access_count, 2)
        self.assertEqual(reaction.fact.source_card_id, event)
        self.assertEqual(self.state.card_instances[event].location.zone, CardZone.PLAY_AREA)
        credits = self.state.runner.credits
        self.reject(PlayerRole.RUNNER, {"type": "gain_credit"})
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertEqual(self.state.runner.credits, credits + 2)
        self.assertIsNone(self.state.run)
        self.assertEqual(self.state.card_instances[event].location.zone, CardZone.DISCARD_PILE)
        self.pass_paid()
        self.run_to_breach("rd")
        self.finish_breach()
        self.assertIsNone(self.state.run)

    def test_zahya_zero_access_does_not_consume_use_and_new_turn_resets(self):
        self.runner_identity("30010")
        self.turn(PlayerRole.RUNNER)
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        self.pass_paid()
        self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertEqual(self.state.card_instances[self.state.runner.identity_card_instance_id].ability_last_used_turn, {})
        self.pass_paid()
        for _ in range(2):
            self.run_to_breach("rd")
            self.finish_breach()
            credits = self.state.runner.credits
            self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
            self.assertEqual(self.state.runner.credits, credits + 1)
            self.turn(PlayerRole.RUNNER)

    def test_red_team_targets_include_failed_runs_and_final_payout_trashes_source(self):
        self.turn(PlayerRole.RUNNER)
        team = self.add_card("30018", PlayerRole.RUNNER, installed=False)
        self.submit(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(team)})
        self.assertEqual(self.state.card_instances[team].counters["credit"], 12)
        self.pass_paid()
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        self.pass_paid()
        self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        self.pass_paid()
        command = {"type": "use_card_ability", "card_instance_id": str(team), "ability_id": "run_central"}
        actions = [a for a in self.view(PlayerRole.RUNNER)["legal_actions"] if a["type"] == "use_card_ability" and a["card_instance_id"] == str(team)]
        self.assertEqual({a["server_id"] for a in actions}, {"rd", "archives"})
        self.reject(PlayerRole.RUNNER, dict(command, server_id="hq"))
        self.reject(PlayerRole.RUNNER, command)
        self.state.card_instances[team].counters["credit"] = 3
        credits = self.state.runner.credits
        self.run_to_breach("rd", command=dict(command, server_id="rd"))
        self.assertEqual(self.state.runner.credits, credits + 3)
        self.assertFalse(self.state.card_instances[team].installed)
        self.assertEqual(self.state.run.source_card_id, team)
        self.assertEqual(self.state.run.source_ability_id, "run_central")
        self.finish_breach()
        self.assertIsNone(self.state.run)
        self.turn(PlayerRole.RUNNER)
        self.assertEqual(self.state.turn.servers_run, set())

    def test_red_team_failed_ability_run_has_no_payout_and_event_runs_count(self):
        self.turn(PlayerRole.RUNNER)
        team = self.add_card("30018", PlayerRole.RUNNER, credits=12)
        command = {"type": "use_card_ability", "card_instance_id": str(team), "ability_id": "run_central"}
        credits = self.state.runner.credits
        self.submit(PlayerRole.RUNNER, dict(command, server_id="hq"))
        self.pass_paid()
        self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        self.assertEqual(self.state.runner.credits, credits)
        self.assertEqual(self.state.card_instances[team].counters["credit"], 12)
        self.pass_paid()
        event = self.add_card("30028", PlayerRole.RUNNER, installed=False)
        self.submit(PlayerRole.RUNNER, {"type": "play_card", "card_instance_id": str(event), "server_id": "rd"})
        self.pass_paid()
        self.submit(PlayerRole.RUNNER, {"type": "jack_out"})
        self.pass_paid()
        actions = [a for a in self.view(PlayerRole.RUNNER)["legal_actions"]
                   if a["type"] == "use_card_ability" and a["card_instance_id"] == str(team)]
        self.assertEqual({a["server_id"] for a in actions}, {"archives"})
        remote = self.add_card("30071", PlayerRole.CORP)
        self.reject(PlayerRole.RUNNER, dict(command, server_id=self.state.card_instances[remote].location.server.server_id))
        self.turn(PlayerRole.RUNNER)
        actions = [a for a in self.view(PlayerRole.RUNNER)["legal_actions"]
                   if a["type"] == "use_card_ability" and a["card_instance_id"] == str(team)]
        self.assertEqual({a["server_id"] for a in actions}, {"hq", "rd", "archives"})

    def test_repeated_archives_accesses_are_occurrences_and_steals_are_retained(self):
        target = self.add_card("30058", PlayerRole.CORP)  # AMAZE, then move to Archives root
        self.state.card_instances[target].location.server.server_id = "archives"
        self.state.servers.remove(1)
        self.turn(PlayerRole.RUNNER)
        self.state.runner.credits = 20
        self.run_to_breach("archives")
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(target)})
        self.submit(PlayerRole.RUNNER, {"type": "finish_access", "trash": True})
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(target)})
        self.assertEqual(self.state.run.access_count, 2)
        self.assertEqual(self.state.run.visible_card_ids, [target, target])
        self.finish_breach()
        self.pass_paid()
        self.runner_identity("30010")
        self.run_to_breach("rd")
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": "rd"})
        accessed = self.state.card_instances[self.state.run.current_card_id]
        # Keep the selected instance in place, author an agenda to exercise stealing.
        accessed.definition_id = "30067"  # Offworld Office
        self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
        self.assertEqual(self.state.window.reactions[0].fact.agendas_stolen, 1)

    def test_run_end_snapshot_reconnects_without_revealing_rd_to_corp(self):
        self.runner_identity("30010")
        self.turn(PlayerRole.RUNNER)
        top = min((c for c in self.state.card_instances.values()
                   if c.location.zone is CardZone.DECK and c.owner_id == self.state.corp.player_id),
                  key=lambda c: c.location.deck_position)
        top.definition_id = "30075"  # Leave an operation in R&D, keeping its identity private.
        self.run_to_breach("rd")
        with self.client.websocket_connect(self.path + "/events", headers=self.headers[PlayerRole.RUNNER]) as runner:
            with self.client.websocket_connect(self.path + "/events", headers=self.headers[PlayerRole.CORP]) as corp:
                runner.receive_json()
                corp.receive_json()
                self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": "rd"})
                runner_access, corp_access = runner.receive_json(), corp.receive_json()
                self.assertIsNotNone(runner_access["state"]["run"]["current_card"])
                self.assertIsNone(corp_access["state"]["run"]["current_card"])
                ended = self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
                runner_end, corp_end = runner.receive_json(), corp.receive_json()
                self.assertEqual(ended, runner_end["state"])
                self.assertEqual(runner_end["revision"], corp_end["revision"])
                self.assertEqual(ended["run"]["access_count"], 1)
                self.assertEqual(corp_end["state"]["run"]["accessed_cards"], [])
        with self.client.websocket_connect(self.path + "/events", headers=self.headers[PlayerRole.RUNNER]) as reconnect:
            self.assertEqual(reconnect.receive_json()["state"], ended)
            self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
            self.assertIsNone(reconnect.receive_json()["state"]["run"])

    def test_nico_order_nested_empty_draw_and_mandatory_draw(self):
        first = self.add_card("30037", PlayerRole.CORP, credits=3)
        second = self.add_card("30037", PlayerRole.CORP, credits=6)
        self.turn(PlayerRole.CORP, step="start", clicks=3)
        before_hand = len(hand_cards(self.state, self.state.corp))
        advance_turn(self.state, self.cards)
        self.pass_paid()
        self.assertEqual(len(self.reactions(PlayerRole.CORP)), 2)
        self.assertEqual(len(hand_cards(self.state, self.state.corp)), before_hand)
        self.reject(PlayerRole.CORP, {"type": "pass_priority"})
        action = next(a for a in self.reactions(PlayerRole.CORP) if a["card_instance_id"] == str(first))
        self.reject(PlayerRole.RUNNER, action)
        self.reject(PlayerRole.CORP, dict(action, decline=True))
        with self.client.websocket_connect(self.path + "/events", headers=self.headers[PlayerRole.RUNNER]) as socket:
            socket.receive_json()
            self.submit(PlayerRole.CORP, action)
            published = socket.receive_json()["state"]
            self.assertEqual(published["reactions"], [])
            self.assertEqual(published["legal_actions"], [])
            self.assertEqual(self.state.card_instances[first].location.zone, CardZone.DISCARD_PILE)
            self.assertEqual(len(hand_cards(self.state, self.state.corp)), before_hand + 1)
            self.assertEqual(self.state.card_instances[second].counters["credit"], 6)
        self.reject(PlayerRole.CORP, action)
        self.submit(PlayerRole.CORP, self.reactions(PlayerRole.CORP)[0])
        self.assertEqual(self.state.card_instances[second].counters["credit"], 3)
        self.assertEqual(len(hand_cards(self.state, self.state.corp)), before_hand + 2)
        self.assertEqual(self.state.suspended_windows, [])
        self.assertEqual(self.state.turn.phase, TurnPhase.ACTION)

    def test_nico_empty_draw_can_end_game_before_mandatory_draw(self):
        nico = self.add_card("30037", PlayerRole.CORP, credits=1)
        for card in self.state.card_instances.values():
            if card.location.zone is CardZone.DECK and card.owner_id == self.state.corp.player_id:
                card.location = ZoneLocation(zone=CardZone.HAND, player_id=card.owner_id)
        self.turn(PlayerRole.CORP, step="turn_begin", clicks=3)
        advance_turn(self.state, self.cards)
        self.submit(PlayerRole.CORP, self.reactions(PlayerRole.CORP)[0])
        self.assertEqual(self.state.winner, PlayerRole.RUNNER)
        self.assertEqual(self.state.card_instances[nico].location.zone, CardZone.DISCARD_PILE)
        self.assertEqual(self.state.suspended_windows, [])

    def test_smartware_copies_pay_independently_and_survive_empty(self):
        first = self.add_card("30033", PlayerRole.RUNNER, credits=1)
        second = self.add_card("30033", PlayerRole.RUNNER, credits=0)
        self.turn(PlayerRole.RUNNER, step="turn_begin")
        advance_turn(self.state, self.cards)
        credits = self.state.runner.credits
        for _ in range(2):
            self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[-1])
        self.assertEqual(self.state.runner.credits, credits + 1)
        self.assertTrue(self.state.card_instances[first].installed)
        self.assertTrue(self.state.card_instances[second].installed)
        self.submit(PlayerRole.RUNNER, {"type": "use_card_ability", "card_instance_id": str(first), "ability_id": "load_credits"})
        self.assertEqual(self.state.card_instances[first].counters["credit"], 3)

    def test_built_to_last_uses_previous_counters_and_finishes_last_click(self):
        self.identity("30059")
        first = self.add_card("30067", PlayerRole.CORP)
        second = self.add_card("30067", PlayerRole.CORP)
        self.turn(PlayerRole.CORP, clicks=3)
        credits = self.state.corp.credits
        for card, eligible in [(first, True), (first, False), (second, True)]:
            self.submit(PlayerRole.CORP, {"type": "advance_card", "card_instance_id": str(card)})
            if eligible:
                self.assertEqual(self.state.window.kind, "reaction")
                if card == second:
                    self.assertEqual(self.state.corp.clicks_remaining, 0)
                    self.assertEqual(self.state.turn.phase, TurnPhase.ACTION)
                self.submit(PlayerRole.CORP, self.reactions(PlayerRole.CORP)[0])
            self.pass_paid()
        self.assertEqual(self.state.corp.credits, credits - 3 + 4)

    def test_restoring_humanity_checks_facedown_archives_at_discard_end(self):
        self.identity("30043")
        card_id = self.add_card("30075", PlayerRole.CORP, installed=False)
        card = self.state.card_instances[card_id]
        card.face_up = False
        card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=card.owner_id)
        self.turn(PlayerRole.CORP)
        self.state.turn.phase = TurnPhase.DISCARD
        self.state.turn.step = "end"
        credits = self.state.corp.credits
        advance_turn(self.state, self.cards)
        self.assertEqual(self.state.turn.active_player, PlayerRole.CORP)
        self.submit(PlayerRole.CORP, self.reactions(PlayerRole.CORP)[0])
        self.assertEqual(self.state.corp.credits, credits + 1)
        self.assertEqual(self.state.turn.active_player, PlayerRole.RUNNER)
        card = self.state.card_instances[card_id]
        card.face_up = True
        self.turn(PlayerRole.CORP)
        self.state.turn.phase = TurnPhase.DISCARD
        self.state.turn.step = "end"
        advance_turn(self.state, self.cards)
        self.submit(PlayerRole.CORP, self.reactions(PlayerRole.CORP)[0])
        self.assertEqual(self.state.corp.credits, credits + 1)

    def test_verbal_plasticity_tracks_draws_before_install_and_resets_next_turn(self):
        verbal = self.add_card("30034", PlayerRole.RUNNER, installed=False)
        self.turn(PlayerRole.RUNNER)
        self.submit(PlayerRole.RUNNER, {"type": "draw_card"})
        self.submit(PlayerRole.RUNNER, {"type": "install_card", "card_instance_id": str(verbal)})
        hand = len(hand_cards(self.state, self.state.runner))
        self.submit(PlayerRole.RUNNER, {"type": "draw_card"})
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), hand + 1)
        self.turn(PlayerRole.RUNNER)
        hand = len(hand_cards(self.state, self.state.runner))
        self.submit(PlayerRole.RUNNER, {"type": "draw_card"})
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), hand + 2)
        self.submit(PlayerRole.RUNNER, {"type": "draw_card"})
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), hand + 3)

    def authored_trigger(self, code, ability_id, *, optional=False, once=False, first=False, scope="controller"):
        definition = self.cards[code]
        ability = TriggeredAbility(ability_id=ability_id, label=ability_id, trigger="action",
            optional=optional, once_per_turn=once, first_each_turn=first, scope=scope,
            instructions=({"type": "gain_credits", "amount": 1},))
        self.cards[code] = definition.model_copy(update={"triggered_abilities": (*definition.triggered_abilities, ability)})

    def test_optional_declines_and_once_usage_are_separate_from_first_occurrence(self):
        card_id = self.add_card("30033", PlayerRole.RUNNER)
        self.authored_trigger("30033", "once", once=True)
        self.authored_trigger("30033", "first", optional=True, first=True)
        self.turn(PlayerRole.RUNNER)
        self.submit(PlayerRole.RUNNER, {"type": "gain_credit"})
        actions = self.reactions(PlayerRole.RUNNER)
        self.reject(PlayerRole.RUNNER, {"type": "resolve_reaction", "card_instance_id": str(card_id)})
        self.submit(PlayerRole.RUNNER, dict(actions[0], decline=True))
        self.submit(PlayerRole.RUNNER, {"type": "pass_priority"})
        self.assertEqual(self.state.card_instances[card_id].ability_last_used_turn, {})
        self.submit(PlayerRole.RUNNER, {"type": "gain_credit"})
        self.assertEqual(len(self.reactions(PlayerRole.RUNNER)), 1)
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertEqual(self.state.card_instances[card_id].ability_last_used_turn["once"], self.state.turn.number)
        self.submit(PlayerRole.RUNNER, {"type": "gain_credit"})
        self.assertIsNone(self.state.window)

    def test_both_players_order_reactions_active_player_first_and_ai_resolves(self):
        self.add_card("30033", PlayerRole.RUNNER)
        self.add_card("30071", PlayerRole.CORP, credits=3)
        self.authored_trigger("30033", "runner_action", scope="any")
        self.authored_trigger("30071", "corp_action", scope="any")
        self.turn(PlayerRole.RUNNER)
        self.submit(PlayerRole.RUNNER, {"type": "gain_credit"})
        self.assertEqual(self.reactions(PlayerRole.CORP), [])
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertEqual(self.state.window.player, PlayerRole.CORP)
        state = advance_ai_decisions(self.state, {self.state.corp.player_id}, self.cards)
        self.assertIsNone(state.window)
        self.assertEqual(state.turn.active_player, PlayerRole.RUNNER)

    def test_optional_once_limit_is_not_used_when_effect_does_nothing(self):
        card_id = self.add_card("30033", PlayerRole.RUNNER, credits=0)
        definition = self.cards["30033"]
        self.cards["30033"] = definition.model_copy(update={"triggered_abilities": (
            TriggeredAbility(ability_id="optional_take", label="Optional take", trigger="action",
                once_per_turn=True, instructions=({"type": "take_credits", "amount": 1},)),)})
        self.turn(PlayerRole.RUNNER)
        self.submit(PlayerRole.RUNNER, {"type": "gain_credit"})
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertEqual(self.state.card_instances[card_id].ability_last_used_turn, {})
        self.submit(PlayerRole.RUNNER, {"type": "use_card_ability", "card_instance_id": str(card_id), "ability_id": "load_credits"})
        self.assertEqual(len(self.reactions(PlayerRole.RUNNER)), 1)
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertEqual(self.state.card_instances[card_id].counters["credit"], 2)
        self.assertEqual(self.state.card_instances[card_id].ability_last_used_turn["optional_take"], self.state.turn.number)

    def test_install_and_rez_share_ordering_and_keep_paid_priority(self):
        watcher = self.add_card("30033", PlayerRole.RUNNER)
        definition = self.cards["30033"]
        self.cards["30033"] = definition.model_copy(update={"triggered_abilities": (
            TriggeredAbility(ability_id="observe_rez", label="Observe rez", trigger="rez", scope="any",
                instructions=({"type": "gain_credits", "amount": 1},)),)})
        nico = self.add_card("30037", PlayerRole.CORP)
        self.state.card_instances[nico].rezzed = False
        self.state.card_instances[nico].face_up = False
        self.turn(PlayerRole.RUNNER)
        self.state.window = PriorityWindow(kind="paid", player=PlayerRole.CORP, opponent_passed=True)
        self.submit(PlayerRole.CORP, {"type": "rez_card", "card_instance_id": str(nico)})
        self.assertEqual(self.state.window.player, PlayerRole.RUNNER)
        self.assertTrue(self.state.card_instances[nico].installed)
        self.assertEqual(self.state.card_instances[nico].counters, {})
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.assertTrue(self.state.card_instances[nico].installed)
        self.submit(PlayerRole.CORP, self.reactions(PlayerRole.CORP)[0])
        self.assertEqual(self.state.card_instances[nico].counters, {"credit": 9})
        self.assertEqual(self.state.window.kind, "paid")
        self.assertEqual(self.state.window.player, PlayerRole.CORP)
        self.assertFalse(self.state.window.opponent_passed)
        self.assertTrue(self.state.card_instances[watcher].installed)

    def test_nested_choice_resumes_parent_instruction_after_source_is_trashed(self):
        nico = self.add_card("30037", PlayerRole.CORP, credits=0)
        definition = self.cards["30037"]
        # A second static ability makes this checkpoint require an ordering choice.
        extra = TriggeredAbility(ability_id="empty_bonus", label="Empty bonus", trigger="empty", scope="self",
            instructions=({"type": "gain_credits", "amount": 2},))
        self.cards["30037"] = definition.model_copy(update={"triggered_abilities": (*definition.triggered_abilities, extra)})
        self.turn(PlayerRole.CORP)
        parent = self.add_card("30071", PlayerRole.CORP, credits=1)
        definition = self.cards["30071"]
        self.cards["30071"] = definition.model_copy(update={"triggered_abilities": (
            TriggeredAbility(ability_id="parent", label="Parent", trigger="action",
                instructions=({"type": "gain_credits", "amount": 1}, {"type": "gain_credits", "amount": 5})),)})
        emit_fact(self.state, self.cards, RuleFact(type="action", player=PlayerRole.CORP))
        credits = self.state.corp.credits
        self.submit(PlayerRole.CORP, self.reactions(PlayerRole.CORP)[0])
        self.assertEqual(self.state.corp.credits, credits + 1)
        self.assertIsNotNone(self.state.suspended_windows[-1].resolving)
        action = next(a for a in self.reactions(PlayerRole.CORP)
                      if next(r for r in self.state.window.reactions if str(r.reaction_id) == a["reaction_id"]).ability_id == "empty")
        self.submit(PlayerRole.CORP, action)
        self.assertEqual(self.state.corp.credits, credits + 6)
        self.assertFalse(self.state.card_instances[nico].installed)
        self.assertTrue(self.state.card_instances[parent].installed)
        self.assertEqual(self.state.suspended_windows, [])

    def test_unloaded_nico_does_not_trigger_empty(self):
        self.add_card("30037", PlayerRole.CORP)
        checkpoint(self.state, self.cards)
        self.assertIsNone(self.state.window)

    def test_reactions_during_run_rez_resume_the_same_run_window(self):
        self.add_card("30033", PlayerRole.RUNNER)
        definition = self.cards["30033"]
        self.cards["30033"] = definition.model_copy(update={"triggered_abilities": (
            TriggeredAbility(ability_id="observe_rez", label="Observe rez", trigger="rez", scope="any",
                instructions=({"type": "gain_credits", "amount": 1},)),)})
        nico = self.add_card("30037", PlayerRole.CORP)
        self.state.card_instances[nico].rezzed = False
        self.state.card_instances[nico].face_up = False
        self.turn(PlayerRole.RUNNER)
        self.submit(PlayerRole.RUNNER, {"type": "run_server", "server_id": "hq"})
        self.submit(PlayerRole.CORP, {"type": "rez_card", "card_instance_id": str(nico)})
        self.assertEqual(self.state.window.kind, "reaction")
        self.submit(PlayerRole.RUNNER, self.reactions(PlayerRole.RUNNER)[0])
        self.submit(PlayerRole.CORP, self.reactions(PlayerRole.CORP)[0])
        self.assertEqual(self.state.window.run_continuation, "approach")
        self.pass_paid()
        self.assertEqual(self.state.run.phase, "movement")
        self.assertEqual(self.state.suspended_windows, [])

    def test_verbal_plasticity_does_not_replace_effect_draws(self):
        self.add_card("30034", PlayerRole.RUNNER)
        event = self.add_card("30029", PlayerRole.RUNNER, installed=False)
        # Use the authored draw event rather than depending on a shuffled hand.
        self.state.card_instances[event].definition_id = next(
            code for code, definition in self.cards.items() if definition.title == "VRcation")
        self.turn(PlayerRole.RUNNER)
        hand = len(hand_cards(self.state, self.state.runner))
        self.submit(PlayerRole.RUNNER, {"type": "play_card", "card_instance_id": str(event)})
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), hand - 1 + 4)
        self.submit(PlayerRole.RUNNER, {"type": "draw_card"})
        self.assertEqual(len(hand_cards(self.state, self.state.runner)), hand - 1 + 6)


if __name__ == "__main__":
    unittest.main()
