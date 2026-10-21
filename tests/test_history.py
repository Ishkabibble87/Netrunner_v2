"""Verify causal grouping, occurrence-time privacy, and snapshot delivery."""

from test_reactions import ReactionScenario

from api.schemas import InitialGameView
from game.card_movement import trash_installed_card
from game.installation import remove_empty_remotes
from game.models import CardZone, PlayerRole, ZoneLocation


class HistoryTests(ReactionScenario):
    def test_paid_actions_are_separate_and_resume_automatic_turn_group(self):
        self.turn(PlayerRole.RUNNER, clicks=1)
        targets = [self.add_card("30071", PlayerRole.CORP) for _ in range(2)]
        for target in targets:
            self.state.card_instances[target].rezzed = False
            self.state.card_instances[target].face_up = False
        self.state.corp.credits = 20
        for card in self.state.card_instances.values():
            if card.owner_id == self.state.runner.player_id and card.location.zone is CardZone.HAND:
                card.face_up = True
                card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=card.owner_id)
        self.submit(PlayerRole.RUNNER, {"type": "gain_credit"})
        for _ in range(20):
            if self.state.turn.active_player is PlayerRole.CORP:
                break
            self.submit(self.state.window.player, {"type": "pass_priority"})
        turn_start = self.state.turn.history_start_id
        self.assertIsNotNone(turn_start)
        groups = []
        for target in targets:
            first = len(self.state.history)
            self.submit(PlayerRole.CORP, {"type": "rez_card", "card_instance_id": str(target)})
            groups.append(self.state.history[first].group_id)
        self.assertEqual(len(set(groups)), 2)
        self.pass_paid()
        draws = [e for e in self.state.history if e.turn_number == self.state.turn.number
                 and e.kind == "movement" and e.details[PlayerRole.CORP].text == "deck → hand"]
        self.assertTrue(draws)
        self.assertEqual({e.group_id for e in draws}, {turn_start})

    def test_rejected_command_is_atomic_and_draw_is_private(self):
        self.turn(PlayerRole.CORP)
        self.reject(PlayerRole.RUNNER, {"type": "draw_card"})
        before = len(self.state.history)
        card = min((c for c in self.state.card_instances.values()
                    if c.location.zone is CardZone.DECK and c.owner_id == self.state.corp.player_id),
                   key=lambda c: c.location.deck_position)
        corp = self.submit(PlayerRole.CORP, {"type": "draw_card"})
        runner = self.view(PlayerRole.RUNNER)
        corp_new = [e for e in corp["history"] if e["sequence"] > before]
        runner_new = [e for e in runner["history"] if e["sequence"] > before]
        self.assertTrue(any(e["detail"].get("definition_id") == card.definition_id for e in corp_new))
        self.assertFalse(any(e["detail"].get("definition_id") for e in runner_new))
        self.assertNotIn(str(card.instance_id), str(corp_new) + str(runner_new))
        self.assertEqual(len({e["group_id"] for e in corp_new}), 1)

    def test_run_keeps_one_group_and_reconnect_matches_live_snapshot(self):
        self.turn(PlayerRole.RUNNER)
        start = len(self.state.history)
        self.run_to_breach("rd")
        run_id = self.state.run.run_id
        view = self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": "rd"})
        viewed = self.state.run.current_card_id
        definition = self.state.card_instances[viewed].definition_id
        private = [e for e in view["history"] if e["sequence"] > start and e["kind"] == "reveal"]
        self.assertTrue(any(e["detail"]["definition_id"] == definition for e in private))
        corp = self.view(PlayerRole.CORP)
        self.assertFalse(any(e["detail"]["definition_id"] == definition
                             for e in corp["history"] if e["sequence"] > start))
        with self.client.websocket_connect(self.path + "/events", headers=self.headers[PlayerRole.RUNNER]) as socket:
            self.assertEqual(socket.receive_json()["state"]["history"], view["history"])
            response = self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
            self.assertEqual(socket.receive_json()["state"]["history"], response["history"])
        self.finish_breach()
        entries = [e for e in self.state.history if e.sequence > start]
        self.assertEqual(len({e.group_id for e in entries}), 1)
        self.assertTrue(any(e.run_id == run_id and e.kind == "run" for e in entries))
        self.assertTrue(any(e.details[PlayerRole.RUNNER].text == "Run finished" for e in entries))
        with self.client.websocket_connect(self.path + "/events", headers=self.headers[PlayerRole.RUNNER]) as socket:
            self.assertEqual(socket.receive_json()["state"]["history"], self.view(PlayerRole.RUNNER)["history"])

    def test_hidden_install_history_stays_hidden_after_rez_and_server_renumber(self):
        self.turn(PlayerRole.CORP)
        first = self.add_card("30071", PlayerRole.CORP)
        card_id = self.add_card("30071", PlayerRole.CORP, installed=False)
        self.submit(PlayerRole.CORP, {"type": "install_card", "card_instance_id": str(card_id), "server_id": "new_remote"})
        old = self.view(PlayerRole.RUNNER)["history"]
        self.assertTrue(any("Remote 2" in e["detail"]["text"] for e in old))
        self.submit(PlayerRole.CORP, {"type": "rez_card", "card_instance_id": str(card_id)})
        current = self.view(PlayerRole.RUNNER)["history"]
        self.assertEqual(current[:len(old)], old)
        self.assertFalse(any(e["detail"]["definition_id"] == "30071" for e in old))
        self.assertTrue(any(e["detail"]["definition_id"] == "30071" for e in current[len(old):]))
        trash_installed_card(self.state, self.state.card_instances[first])
        remove_empty_remotes(self.state)
        self.assertEqual(self.state.card_instances[card_id].location.server.server_id, 1)
        self.assertEqual(self.view(PlayerRole.RUNNER)["history"], current)

    def test_card_initiated_run_links_to_play_and_survives_decisions(self):
        self.turn(PlayerRole.RUNNER)
        event = self.add_card("30028", PlayerRole.RUNNER, installed=False)
        start = len(self.state.history)
        self.run_to_breach("hq", command={"type": "play_card", "card_instance_id": str(event), "server_id": "hq"})
        self.finish_breach()
        entries = self.state.history[start:]
        root = entries[0]
        self.assertEqual(root.kind, "command")
        self.assertEqual(root.details[PlayerRole.RUNNER].title, "Jailbreak")
        self.assertEqual({e.group_id for e in entries}, {root.entry_id})
        ids = {e.entry_id for e in self.state.history}
        self.assertTrue(all(e.parent_id is None or e.parent_id in ids for e in entries))

    def test_automatic_turn_effects_have_separate_groups_and_history_roundtrips(self):
        self.turn(PlayerRole.RUNNER, clicks=1)
        # Avoid a discard decision so the last click traverses turn end/start.
        for card in self.state.card_instances.values():
            if card.owner_id == self.state.runner.player_id and card.location.zone is CardZone.HAND:
                card.face_up = True
                card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=card.owner_id)
        start = len(self.state.history)
        self.submit(PlayerRole.RUNNER, {"type": "gain_credit"})
        entries = self.state.history[start:]
        self.assertGreaterEqual(len({e.group_id for e in entries}), 3)
        self.assertEqual([e.details[PlayerRole.CORP].text for e in entries if e.kind == "turn"],
                         ["Turn end", "Turn start"])
        restored = type(self.state).model_validate_json(self.state.model_dump_json())
        view = InitialGameView.from_game_state(restored, restored.corp.player_id, self.cards)
        self.assertEqual(view.model_dump(mode="json")["history"], self.view(PlayerRole.CORP)["history"])
