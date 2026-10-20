"""Authenticated scoring, ice swapping, and effect rezzing across reaction windows."""

from test_reactions import ReactionScenario

from game.models import CardZone, PlayerRole, ServerArea, ServerLocation, ZoneLocation
from game.turns import advance_turn


class BoardChangeTests(ReactionScenario):
    def setUp(self):
        super().setUp()
        self.turn(PlayerRole.CORP, clicks=20)
        self.state.corp.credits = 50

    def ice(self, code="30063", server="hq", position=0, rezzed=False):
        target = self.add_card(code, PlayerRole.CORP)
        self.state.servers.pop()
        card = self.state.card_instances[target]
        card.rezzed = False
        card.face_up = rezzed
        card.rezzed = rezzed
        card.location = ZoneLocation(zone=CardZone.PLAY_AREA, server=ServerLocation(
            server_id=server, area=ServerArea.ICE, ice_distance_from_server=position))
        return target

    def agenda(self, code):
        target = self.add_card(code, PlayerRole.CORP)
        self.state.card_instances[target].counters["advancement"] = self.cards[code].advancement_requirement
        return target

    def score(self, target):
        self.state.turn.step = "before_action"
        advance_turn(self.state, self.cards)
        self.submit(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": str(target)})

    def select(self, role, *targets):
        self.submit(role, {"type": "select_effect_cards", "card_instance_ids": [str(i) for i in targets]})

    def resolve(self, role, target):
        self.submit(role, next(a for a in self.reactions(role) if a["card_instance_id"] == str(target)))

    def test_pharos_advancement_threshold_and_live_strength(self):
        ice = self.ice(rezzed=True)
        for counters in range(1, 5):
            self.pass_paid()
            self.submit(PlayerRole.CORP, {"type": "advance_card", "card_instance_id": str(ice)})
            entry = next(c for c in self.view(PlayerRole.RUNNER)["installed_cards"] if c["instance_id"] == str(ice))
            self.assertEqual(entry["counters"]["advancement"], counters)
            self.assertEqual(entry["card"]["strength"], self.cards["30063"].strength + (5 if counters >= 3 else 0))
        self.assertEqual(self.state.corp.credits, 46)
        self.assertEqual(self.state.corp.clicks_remaining, 16)

    def test_luminal_locks_all_further_scoring_until_next_turn(self):
        luminal, other = self.agenda("30036"), self.agenda("30067")
        before = self.state.corp.clicks_remaining
        self.score(luminal)
        self.resolve(PlayerRole.CORP, luminal)
        self.assertEqual(self.state.corp.clicks_remaining, before + 3)
        command = {"type": "score_agenda", "card_instance_id": str(other)}
        self.assertNotIn(command, self.view(PlayerRole.CORP)["legal_actions"])
        self.reject(PlayerRole.CORP, command)
        self.pass_paid()
        self.state.corp.clicks_remaining = 0
        advance_turn(self.state, self.cards)
        for _ in range(30):
            if self.state.turn.active_player is PlayerRole.RUNNER:
                break
            if self.state.window:
                self.submit(self.state.window.player, {"type": "pass_priority"})
            else:
                actions = self.view(PlayerRole.CORP)["legal_actions"]
                action = next(a for a in actions if a["type"] == "discard_cards")
                self.submit(PlayerRole.CORP, {"type": "discard_cards",
                    "card_instance_ids": action["eligible_card_instance_ids"][:action["required_count"]]})
        self.assertFalse(self.state.turn.cannot_score)
        self.turn(PlayerRole.CORP)
        self.score(other)
        self.assertEqual(self.state.card_instances[other].location.zone, CardZone.SCORE_AREA)

    def test_tao_swap_hides_unrezzed_identity_and_preserves_hosts(self):
        self.runner_identity("30019")
        first, second = self.ice(), self.ice("30072", "rd", rezzed=True)
        host = self.add_card("30004", PlayerRole.RUNNER)
        self.state.card_instances[host].location = ZoneLocation(zone=CardZone.PLAY_AREA, host_card_instance_id=first)
        self.state.card_instances[first].counters["advancement"] = 3
        agenda = self.agenda("30067")
        self.score(agenda)
        # Resolve the mandatory Corp payout before the Runner's optional ability.
        self.resolve(PlayerRole.CORP, agenda)
        identity = self.state.runner.identity_card_instance_id
        self.resolve(PlayerRole.RUNNER, identity)
        view = self.view(PlayerRole.RUNNER)
        hidden = next(c for c in view["selection_cards"] if c["instance_id"] == str(first))
        self.assertEqual(hidden, {"instance_id": str(first), "title": "Unrezzed ice"})
        self.assertFalse(self.view(PlayerRole.CORP)["selection_cards"])
        self.reject(PlayerRole.RUNNER, {"type": "select_effect_cards", "card_instance_ids": [str(first), str(first)]})
        self.select(PlayerRole.RUNNER, first, second)
        self.assertEqual(self.state.card_instances[first].location.server.server_id, "rd")
        self.assertEqual(self.state.card_instances[second].location.server.server_id, "hq")
        self.assertEqual(self.state.card_instances[first].counters["advancement"], 3)
        self.assertEqual(self.state.card_instances[host].location.host_card_instance_id, first)
        self.assertFalse(self.state.card_instances[first].rezzed)

    def test_message_stolen_rezzes_outside_approach_at_zero_credits_and_resumes_access(self):
        self.runner_identity("30019")
        first, second = self.ice("30072"), self.ice("30055", "rd")
        agenda = self.agenda("30069")
        self.state.card_instances[first].location.server.server_id = self.state.card_instances[agenda].location.server.server_id
        self.turn(PlayerRole.RUNNER, clicks=10)
        self.state.corp.credits = 0
        self.run_to_breach(self.state.card_instances[agenda].location.server.server_id)
        self.submit(PlayerRole.RUNNER, {"type": "access_card", "target": str(agenda)})
        self.submit(PlayerRole.RUNNER, {"type": "finish_access"})
        self.resolve(PlayerRole.RUNNER, self.state.runner.identity_card_instance_id)
        self.select(PlayerRole.RUNNER, first, second)
        self.assertEqual(self.state.run.phase, "access")
        self.state.run.ice_rez_cost_increase = 3
        self.resolve(PlayerRole.CORP, agenda)
        self.assertEqual(self.view(PlayerRole.CORP)["legal_actions"][0]["required_count"], 1)
        self.select(PlayerRole.CORP, second)
        self.assertTrue(self.state.card_instances[second].rezzed)
        self.assertEqual(self.state.corp.credits, 0)
        # Ping's on-rez tag still resolves through its ordinary lifecycle.
        self.assertEqual(self.state.runner.tags, 1)
        self.assertIsNone(self.state.run)

    def test_message_scored_decline_and_no_rezzable_targets(self):
        agenda = self.agenda("30069")
        self.ice(rezzed=True)
        self.score(agenda)
        self.resolve(PlayerRole.CORP, agenda)
        self.assertNotEqual(self.state.window.kind if self.state.window else None, "target")

    def test_tao_same_server_swap_and_declining_selection(self):
        self.runner_identity("30019")
        first, second = self.ice(), self.ice("30072", position=1)
        agenda = self.agenda("30067")
        self.score(agenda)
        self.resolve(PlayerRole.CORP, agenda)
        self.resolve(PlayerRole.RUNNER, self.state.runner.identity_card_instance_id)
        self.select(PlayerRole.RUNNER, first, second)
        self.assertEqual(self.state.card_instances[first].location.server.ice_distance_from_server, 1)
        self.assertEqual(self.state.card_instances[second].location.server.ice_distance_from_server, 0)
        agenda = self.agenda("30067")
        self.score(agenda)
        self.resolve(PlayerRole.CORP, agenda)
        self.resolve(PlayerRole.RUNNER, self.state.runner.identity_card_instance_id)
        self.select(PlayerRole.RUNNER)
        self.assertEqual(self.state.card_instances[first].location.server.ice_distance_from_server, 1)

    def test_tao_with_only_one_ice_does_not_offer_partial_swap(self):
        self.runner_identity("30019")
        first = self.ice()
        agenda = self.agenda("30067")
        self.score(agenda)
        self.resolve(PlayerRole.CORP, agenda)
        self.resolve(PlayerRole.RUNNER, self.state.runner.identity_card_instance_id)
        self.assertNotEqual(self.state.window.kind if self.state.window else None, "target")
        self.assertEqual(self.state.card_instances[first].location.server.server_id, "hq")

    def test_victory_precedes_luminal_and_message_reactions(self):
        for code in ("30036", "30069"):
            with self.subTest(code=code):
                self.setUp()
                for _ in range(2):
                    card = self.state.card_instances[self.agenda("30067")]
                    self.state.card_instances[card.instance_id] = card.model_copy(update={
                        "location": ZoneLocation(zone=CardZone.SCORE_AREA, player_id=self.state.corp.player_id),
                        "rezzed": False, "installed": False})
                agenda = self.agenda(code)
                # Enough existing points for either agenda to win.
                self.cards[code] = self.cards[code].model_copy(update={"agenda_points": 3})
                before = self.state.corp.clicks_remaining
                self.score(agenda)
                self.assertEqual(self.state.winner, PlayerRole.CORP)
                self.assertIsNone(self.state.window)
                self.assertEqual(self.state.corp.clicks_remaining, before)
