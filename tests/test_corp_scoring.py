"""Check installation legality, private publication, and scoring timing boundaries.

The growing scenario owns the natural game path. These focused cases exercise
rejections, replacement costs, and the victory checkpoint that path cannot reach
with the three implemented agendas in the starter deck.
"""

import unittest
from collections import Counter
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from application.game_setup import GameSetupError, create_ai_game
from application.sessions import SessionStore
from game.commands import (
    GainCredit,
    InstallCard,
    PassPriority,
    ScoreAgenda,
)
from game.engine import apply_command, legal_actions_for
from game.models import CardZone, CentralServer, PlayerRole, ZoneLocation
from game.setup import IllegalCommand


class CorpScoringTests(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore()
        self.app = create_app(session_store=self.store)
        self.cards = self.app.state.card_definitions
        self.profile = self.app.state.opponent_profiles["system-gateway-training"]
        self.deck = self.profile.deck_selections[PlayerRole.CORP]
        self.order = self.ordered_deck(["Offworld Office", "Offworld Office", "Palisade",
                                        "Palisade", "Nico Campaign", "Regolith Mining License"])
        self.game = create_ai_game(player_id=uuid4(), decklist=self.deck, opponent_profile=self.profile,
                                   card_definitions=self.cards, deck_orders={PlayerRole.CORP: self.order})
        self.session = self.store.create(self.game, self.profile.profile_id,
                                         [self.game.corp.player_id, self.game.runner.player_id])
        self.client = self.enterContext(TestClient(self.app))
        self.path = f"/games/{self.game.game_id}"
        self.headers = {p.role: {"Authorization": f"Bearer {self.session.player_tokens[p.player_id]}"}
                        for p in (self.game.corp, self.game.runner)}
        for role in PlayerRole:
            self.submit(role, {"type": "choose_mulligan", "decision": "keep"})

    def ordered_deck(self, titles):
        remaining = Counter(self.deck.card_counts)
        prefix = [next(code for code, card in self.cards.items() if card.title == title) for title in titles]
        remaining.subtract(prefix)
        return prefix + [code for code in sorted(remaining) for _ in range(remaining[code])]

    def instance(self, title):
        return next(c for c in self.session.state.card_instances.values()
                    if self.cards[c.definition_id].title == title and c.location.zone is CardZone.HAND)

    def submit(self, role, command):
        response = self.client.post(f"{self.path}/commands", headers=self.headers[role], json=command)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["state"]

    def reject(self, role, command):
        before = self.session.state.model_dump()
        revision = self.session.revision
        response = self.client.post(f"{self.path}/commands", headers=self.headers[role], json=command)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.session.state.model_dump(), before)
        self.assertEqual(self.session.revision, revision)

    def test_private_install_and_scoring_reaction_publish_to_both_players(self):
        office = self.instance("Offworld Office")
        card_id = str(office.instance_id)
        with self.client.websocket_connect(f"{self.path}/events", headers=self.headers[PlayerRole.CORP]) as corp_socket:
            with self.client.websocket_connect(f"{self.path}/events", headers=self.headers[PlayerRole.RUNNER]) as runner_socket:
                corp_socket.receive_json()
                runner_socket.receive_json()
                def command(role, payload):
                    state = self.submit(role, payload)
                    corp = corp_socket.receive_json()
                    runner = runner_socket.receive_json()
                    self.assertEqual(corp["revision"], runner["revision"])
                    self.assertEqual(state, (corp if role is PlayerRole.CORP else runner)["state"])
                    return corp["state"], runner["state"]
                corp, runner = command(PlayerRole.CORP, {"type": "install_card", "card_instance_id": card_id,
                                                         "server_id": "new_remote"})
                self.assertEqual(corp["you"]["credits"], 5)
                self.assertEqual(corp["installed_cards"][0]["card"]["title"], "Offworld Office")
                self.assertIsNone(runner["installed_cards"][0]["card"])
                self.assertFalse(runner["installed_cards"][0]["face_up"])
                self.assertEqual(runner["legal_actions"], [])
                self.assertEqual(corp["decision"]["type"], "paid_ability_window")
                self.reject(PlayerRole.RUNNER, {"type": "pass_priority"})
                self.reject(PlayerRole.CORP, {"type": "advance_card", "card_instance_id": card_id})
                self.reject(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": card_id})
                for _ in range(2):
                    command(PlayerRole.CORP, {"type": "pass_priority"})
                    corp, runner = command(PlayerRole.CORP, {"type": "advance_card", "card_instance_id": card_id})
                self.assertEqual(runner["installed_cards"][0]["counters"], {"advancement": 2})
                command(PlayerRole.CORP, {"type": "pass_priority"})
                for _ in range(4):
                    while self.session.state.window:
                        command(PlayerRole.CORP, {"type": "pass_priority"})
                    command(PlayerRole.RUNNER, {"type": "gain_credit"})
                while self.session.state.turn.active_player is PlayerRole.RUNNER:
                    command(PlayerRole.CORP, {"type": "pass_priority"})
                # Resume the pre-draw window, then the pre-action window.
                command(PlayerRole.CORP, {"type": "pass_priority"})
                command(PlayerRole.CORP, {"type": "pass_priority"})
                command(PlayerRole.CORP, {"type": "gain_credit"})
                for _ in range(2):
                    command(PlayerRole.CORP, {"type": "pass_priority"})
                    command(PlayerRole.CORP, {"type": "advance_card", "card_instance_id": card_id})
                credits = self.session.state.corp.credits
                self.assertEqual(self.session.state.corp.clicks_remaining, 0)
                corp, runner = command(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": card_id})
                self.assertEqual(corp["you"]["agenda_points"], 2)
                self.assertEqual(runner["opponent"]["score_area"][0]["title"], "Offworld Office")
                self.assertEqual(corp["you"]["credits"], credits)
                self.assertEqual(corp["decision"]["type"], "resolve_reaction")
                self.assertEqual(corp["installed_cards"], [])
                self.assertEqual(corp["servers"], ["hq", "rd", "archives"])
                self.reject(PlayerRole.CORP, {"type": "pass_priority"})
                self.reject(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": card_id})
                self.reject(PlayerRole.CORP, {"type": "resolve_reaction", "card_instance_id": str(uuid4())})
                corp, runner = command(PlayerRole.CORP, {"type": "resolve_reaction", "card_instance_id": card_id})
                self.assertEqual(corp["you"]["credits"], credits + 7)
                self.assertEqual(runner["opponent"]["credits"], credits + 7)
                self.reject(PlayerRole.CORP, {"type": "resolve_reaction", "card_instance_id": card_id})
                corp, runner = command(PlayerRole.CORP, {"type": "pass_priority"})
                self.assertEqual(corp["turn"]["phase"], "discard")
                self.reject(PlayerRole.CORP, {"type": "score_agenda", "card_instance_id": card_id})

    def test_install_destinations_costs_and_replacement_trashing(self):
        state = self.session.state
        corp = state.corp.player_id
        office = self.instance("Offworld Office")
        ice = self.instance("Palisade")
        nico = self.instance("Nico Campaign")
        for target in (CentralServer.HQ, "hq", 99):
            with self.assertRaises(IllegalCommand):
                apply_command(state, corp, InstallCard(office.instance_id, target), self.cards)
        with self.assertRaises(IllegalCommand):
            apply_command(state, state.runner.player_id, InstallCard(ice.instance_id, CentralServer.HQ), self.cards)
        installed = apply_command(state, corp, InstallCard(office.instance_id, "new_remote"), self.cards)
        self.assertEqual(state.card_instances[office.instance_id].location.zone, CardZone.HAND)
        installed = apply_command(installed, corp, PassPriority(), self.cards)
        with self.assertRaises(IllegalCommand):
            apply_command(installed, corp, InstallCard(nico.instance_id, 1), self.cards)
        replaced = apply_command(installed, corp, InstallCard(nico.instance_id, 1, (office.instance_id,)), self.cards)
        self.assertIn(1, replaced.servers)
        trashed = replaced.card_instances[office.instance_id]
        self.assertEqual(trashed.location.zone, CardZone.DISCARD_PILE)
        self.assertFalse(trashed.face_up)
        self.assertFalse(trashed.installed)
        self.assertEqual(replaced.window.kind, "paid")
        # Removing an empty remote renumbers the surviving remote without moving its card elsewhere.
        two_remotes = apply_command(installed, corp, InstallCard(nico.instance_id, "new_remote"), self.cards)
        two_remotes.card_instances[office.instance_id].counters["advancement"] = 4
        renumbered = apply_command(two_remotes, corp, ScoreAgenda(office.instance_id), self.cards)
        self.assertEqual(renumbered.servers, [*CentralServer, 1])
        self.assertEqual(renumbered.card_instances[nico.instance_id].location.server.server_id, 1)
        # Scoring keeps a remote alive when ice still protects it.
        protected = apply_command(installed, corp, InstallCard(ice.instance_id, 1), self.cards)
        protected.card_instances[office.instance_id].counters["advancement"] = 4
        protected = apply_command(protected, corp, ScoreAgenda(office.instance_id), self.cards)
        self.assertIn(1, protected.servers)
        self.assertEqual(protected.card_instances[ice.instance_id].location.server.server_id, 1)
        # Fresh snapshot: install two ice, paying only for the already installed ice.
        first = apply_command(state, corp, InstallCard(ice.instance_id, CentralServer.HQ), self.cards)
        first = apply_command(first, corp, PassPriority(), self.cards)
        second_ice = next(c for c in state.card_instances.values() if c.definition_id == ice.definition_id
                          and c.instance_id != ice.instance_id and c.location.zone is CardZone.HAND)
        poor = first.model_copy(deep=True)
        poor.corp.credits = 0
        with self.assertRaises(IllegalCommand):
            apply_command(poor, corp, InstallCard(second_ice.instance_id, CentralServer.HQ), self.cards)
        second = apply_command(first, corp, InstallCard(second_ice.instance_id, CentralServer.HQ), self.cards)
        self.assertEqual(second.corp.credits, 4)
        self.assertEqual(second.card_instances[second_ice.instance_id].location.server.ice_distance_from_server, 1)
        # Trashing the old ice during installation removes its contribution to cost.
        replacement = apply_command(first, corp, InstallCard(second_ice.instance_id, CentralServer.HQ, (ice.instance_id,)), self.cards)
        self.assertEqual(replacement.corp.credits, 5)
        self.assertEqual(replacement.card_instances[second_ice.instance_id].location.server.ice_distance_from_server, 0)

    def test_victory_checkpoint_precedes_scoring_reward(self):
        state = self.session.state
        corp = state.corp.player_id
        office = self.instance("Offworld Office")
        state = apply_command(state, corp, InstallCard(office.instance_id, "new_remote"), self.cards)
        # Set up a score close to victory; the growing path covers earning the first agenda.
        other_agendas = [c for c in state.card_instances.values() if self.cards[c.definition_id].card_type.value == "agenda"
                         and c.instance_id != office.instance_id][:3]
        for card in other_agendas:
            state.card_instances[card.instance_id] = card.model_copy(update={
                "installed": False, "face_up": True,
                "location": ZoneLocation(zone=CardZone.SCORE_AREA, player_id=corp)})
        deck = sorted((c for c in state.card_instances.values() if c.location.zone is CardZone.DECK
                       and c.owner_id == corp), key=lambda c: c.location.deck_position)
        for position, card in enumerate(deck, 1):
            card.location.deck_position = position
        state.card_instances[office.instance_id].counters["advancement"] = 4
        credits = state.corp.credits
        scored = apply_command(state, corp, ScoreAgenda(office.instance_id), self.cards)
        self.assertEqual(scored.winner, PlayerRole.CORP)
        self.assertEqual(scored.corp.credits, credits)
        self.assertIsNone(scored.window)
        self.assertEqual(legal_actions_for(scored, corp, self.cards), ())
        with self.assertRaises(IllegalCommand):
            apply_command(scored, corp, GainCredit(), self.cards)

    def test_explicit_order_requires_exact_selected_copies(self):
        for order in (self.order[:-1], self.order + [self.order[0]], [self.order[0]] * len(self.order)):
            with self.assertRaises(GameSetupError):
                create_ai_game(player_id=uuid4(), decklist=self.deck, opponent_profile=self.profile,
                               card_definitions=self.cards, deck_orders={PlayerRole.CORP: order})
