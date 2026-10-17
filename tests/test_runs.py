"""Exercise access privacy, candidate selection, and run timing through the API."""

import unittest
from collections import Counter
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from application.game_setup import create_ai_game
from game.models import CardZone, PlayerRole, ZoneLocation


class RunTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.cards = self.app.state.card_definitions
        profile = self.app.state.opponent_profiles['system-gateway-training']
        deck = profile.deck_selections[PlayerRole.CORP]
        prefix = [self.code(t) for t in ['Offworld Office', 'Offworld Office', 'Offworld Office',
                                        'Regolith Mining License', 'Manegarm Skunkworks', 'Palisade']]
        remaining = Counter(deck.card_counts)
        remaining.subtract(prefix)
        order = prefix + [code for code in sorted(remaining) for _ in range(remaining[code])]
        game = create_ai_game(player_id=uuid4(), decklist=deck, opponent_profile=profile,
                              card_definitions=self.cards, deck_orders={PlayerRole.CORP: order})
        self.session = self.app.state.sessions.create(game, profile.profile_id,
                                                      [game.corp.player_id, game.runner.player_id])
        self.client = self.enterContext(TestClient(self.app))
        self.path = f'/games/{game.game_id}'
        self.headers = {p.role.value: {'Authorization': f'Bearer {self.session.player_tokens[p.player_id]}'}
                        for p in (game.corp, game.runner)}
        for role in ('corp', 'runner'):
            self.submit(role, {'type': 'choose_mulligan', 'decision': 'keep'})

    def code(self, title):
        return next(i for i, c in self.cards.items() if c.title == title)

    def card(self, title):
        return next(c for c in self.session.state.card_instances.values()
                    if c.definition_id == self.code(title) and c.location.zone is CardZone.HAND)

    def submit(self, role, command):
        response = self.client.post(self.path + '/commands', headers=self.headers[role], json=command)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['state']

    def view(self, role):
        return self.client.get(self.path + '/state', headers=self.headers[role]).json()

    def reject(self, role, command):
        before = self.session.state.model_dump()
        revision = self.session.revision
        response = self.client.post(self.path + '/commands', headers=self.headers[role], json=command)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(before, self.session.state.model_dump())
        self.assertEqual(revision, self.session.revision)

    def runner_turn(self):
        while self.session.state.turn.active_player is PlayerRole.CORP:
            view = self.view('corp')
            action = next((a for a in view['legal_actions'] if a['type'] == 'pass_priority'), None)
            if action is None:
                action = next((a for a in view['legal_actions'] if a['type'] == 'discard_cards'), None)
                if action:
                    action = {'type': 'discard_cards',
                              'card_instance_ids': action['eligible_card_instance_ids'][:action['required_count']]}
                else:
                    action = {'type': 'gain_credit'}
            self.submit('corp', action)

    def begin(self, server):
        self.submit('runner', {'type': 'run_server', 'server_id': server})
        return self.submit('runner', {'type': 'continue_run'})

    def access(self, target):
        return self.submit('runner', {'type': 'access_card', 'target': str(target)})

    def finish(self, trash=False):
        return self.submit('runner', {'type': 'finish_access', 'trash': trash})

    def install(self, title, server='new_remote'):
        card = self.card(title)
        self.submit('corp', {'type': 'install_card', 'card_instance_id': str(card.instance_id), 'server_id': server})
        self.submit('corp', {'type': 'pass_priority'})
        return card.instance_id

    def move_to_archives(self, card):
        self.session.state.card_instances[card.instance_id] = card.model_copy(update={
            'location': ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=self.session.state.corp.player_id),
            'face_up': False})

    def test_remote_access_publishes_to_both_sockets_and_clears_run_on_steal(self):
        office = self.install('Offworld Office')
        self.runner_turn()
        with self.client.websocket_connect(self.path + '/events', headers=self.headers['runner']) as runner:
            with self.client.websocket_connect(self.path + '/events', headers=self.headers['corp']) as corp:
                runner.receive_json()
                corp.receive_json()
                self.begin(1)
                for _ in range(2):
                    runner.receive_json()
                    corp.receive_json()
                self.reject('corp', {'type': 'continue_run'})
                self.reject('runner', {'type': 'gain_credit'})
                self.reject('runner', {'type': 'run_server', 'server_id': 'hq'})
                self.reject('runner', {'type': 'access_card', 'target': str(uuid4())})
                accessed = self.access(office)
                r, c = runner.receive_json(), corp.receive_json()
                self.assertEqual(r['revision'], c['revision'])
                self.assertEqual(accessed, r['state'])
                self.assertEqual(c['state']['run']['current_card']['title'], 'Offworld Office')
                self.assertFalse(accessed['installed_cards'][0]['face_up'])
                credits = self.session.state.corp.credits
                stolen = self.finish()
                runner.receive_json()
                corp.receive_json()
                self.assertIsNone(stolen['run'])
                self.assertEqual(stolen['you']['agenda_points'], 2)
                self.assertEqual(self.session.state.corp.credits, credits)
                self.assertNotIn(1, stolen['servers'])
                self.assertEqual(stolen['you']['clicks_remaining'], 3)

    def test_hq_random_access_has_no_private_candidates_and_visibility_expires(self):
        self.install('Manegarm Skunkworks', 'hq')
        self.runner_turn()
        view = self.begin('hq')
        self.assertIn({'type': 'access_card', 'target': 'hq'}, view['legal_actions'])
        hand = self.view('corp')['you']['hand']
        self.assertTrue(all(a.get('target') not in {c['instance_id'] for c in hand}
                            for a in view['legal_actions']))
        target = next(c for c in self.session.state.card_instances.values()
                      if c.definition_id == self.code('Palisade') and c.location.zone is CardZone.HAND)
        with patch('game.runs.SystemRandom') as rng:
            rng.return_value.choice.return_value = target
            view = self.access('hq')
            self.assertTrue(rng.return_value.choice.called)
        self.assertEqual(view['run']['current_card']['title'], 'Palisade')
        view = self.finish()
        self.assertIn(str(target.instance_id), [c['instance_id'] for c in view['run']['accessed_cards']])
        self.assertNotIn({'type': 'access_card', 'target': 'hq'}, view['legal_actions'])
        root = next(a for a in view['legal_actions'] if a['type'] == 'access_card')
        self.access(root['target'])
        view = self.finish()
        self.assertIsNone(view['run'])
        self.assertNotIn(str(target.instance_id), str(view))
        self.assertIsNone(self.session.state.card_instances[target.instance_id].face_up)

    def test_rd_private_access_leaves_order_unchanged_and_stealing_compacts_deck(self):
        self.runner_turn()
        deck = sorted((c for c in self.session.state.card_instances.values() if c.location.zone is CardZone.DECK
                       and c.owner_id == self.session.state.corp.player_id), key=lambda c: c.location.deck_position)
        # Keep physical copies but put an agenda on top for the second run.
        office = self.card('Offworld Office')
        top = deck[0]
        self.session.state.card_instances[top.instance_id] = top.model_copy(update={'definition_id': self.code('Hedge Fund')})
        self.begin('rd')
        accessed = self.access('rd')
        self.assertEqual(accessed['run']['current_card']['title'], 'Hedge Fund')
        self.assertIsNone(self.view('corp')['run']['current_card'])
        self.assertEqual(self.view('corp')['run']['accessed_cards'], [])
        self.finish()
        self.assertEqual(self.session.state.card_instances[top.instance_id].location.deck_position, 1)
        self.session.state.card_instances[top.instance_id].definition_id = office.definition_id
        self.begin('rd')
        self.access('rd')
        self.finish()
        positions = [c.location.deck_position for c in self.session.state.card_instances.values()
                     if c.location.zone is CardZone.DECK and c.owner_id == self.session.state.corp.player_id]
        self.assertEqual(sorted(positions), list(range(1, len(positions) + 1)))
        self.assertEqual(self.view('runner')['you']['agenda_points'], 2)

    def test_archives_reveals_discards_and_accesses_every_card_without_trash_option(self):
        office = self.card('Offworld Office')
        asset = self.card('Regolith Mining License')
        self.move_to_archives(office)
        self.move_to_archives(asset)
        self.runner_turn()
        before = self.view('runner')['opponent']['discard_pile']
        self.assertNotIn(str(office.instance_id), [c['instance_id'] for c in before])
        view = self.begin('archives')
        self.assertEqual(len(view['legal_actions']), 2)
        self.assertTrue(self.session.state.card_instances[office.instance_id].face_up)
        self.access(asset.instance_id)
        self.reject('runner', {'type': 'finish_access', 'trash': True})
        self.finish()
        self.access(office.instance_id)
        view = self.finish()
        self.assertIsNone(view['run'])
        self.assertEqual(view['you']['agenda_points'], 2)

    def test_trash_uses_run_fund_and_remote_rows_cleanup_after_breach(self):
        asset = self.install('Regolith Mining License')
        office = self.install('Offworld Office')
        self.runner_turn()
        self.session.state.runner.credits = 0
        self.session.state.corp.bad_publicity = 3
        self.begin(1)
        self.access(asset)
        self.finish(trash=True)
        self.assertEqual(self.session.state.runner.credits, 0)
        self.assertEqual(self.session.state.card_instances[office].location.server.server_id, 1)
        self.assertIsNone(self.session.state.run)
        self.assertTrue(self.session.state.card_instances[asset].face_up)

    def test_protected_servers_and_wrong_timing_reject_without_mutation(self):
        self.install('Palisade', 'hq')
        self.reject('corp', {'type': 'run_server', 'server_id': 'rd'})
        self.runner_turn()
        self.reject('runner', {'type': 'run_server', 'server_id': 'hq'})
        self.reject('runner', {'type': 'run_server', 'server_id': 99})
        self.reject('runner', {'type': 'finish_access'})
        self.submit('runner', {'type': 'run_server', 'server_id': 'rd'})
        self.reject('runner', {'type': 'access_card', 'target': 'rd'})
        self.submit('runner', {'type': 'jack_out'})
        self.assertIsNone(self.session.state.run)
        self.assertEqual(self.session.state.runner.clicks_remaining, 3)

    def test_stealing_wins_immediately_without_scoring_reward(self):
        office = self.install('Offworld Office')
        self.runner_turn()
        messages = [c for c in self.session.state.card_instances.values()
                    if c.definition_id == self.code('Send a Message')]
        for card in messages:
            self.session.state.card_instances[card.instance_id] = card.model_copy(update={
                'face_up': True,
                'location': ZoneLocation(zone=CardZone.SCORE_AREA, player_id=self.session.state.runner.player_id)})
        deck = sorted((c for c in self.session.state.card_instances.values()
                       if c.location.zone is CardZone.DECK and c.owner_id == self.session.state.corp.player_id),
                      key=lambda c: c.location.deck_position)
        for position, card in enumerate(deck, 1):
            card.location.deck_position = position
        self.begin(1)
        self.access(office)
        credits = self.session.state.corp.credits
        view = self.finish()
        self.assertEqual(view['winner'], 'runner')
        self.assertEqual(view['you']['agenda_points'], 8)
        self.assertEqual(view['legal_actions'], [])
        self.assertEqual(self.session.state.corp.credits, credits)
        self.reject('runner', {'type': 'run_server', 'server_id': 'rd'})

    def test_empty_breach_ends_normally_without_decking_the_corp(self):
        self.runner_turn()
        for card in list(self.session.state.card_instances.values()):
            if card.location.zone is CardZone.DECK and card.owner_id == self.session.state.corp.player_id:
                self.session.state.card_instances[card.instance_id] = card.model_copy(update={
                    'face_up': True,
                    'location': ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=self.session.state.corp.player_id)})
        view = self.begin('rd')
        self.assertIsNone(view['run'])
        self.assertIsNone(view['winner'])
        self.assertEqual(view['you']['clicks_remaining'], 3)

    def test_unaffordable_trashing_rejects_and_leaving_retains_facedown_identity(self):
        asset = self.install('Regolith Mining License')
        self.runner_turn()
        self.session.state.runner.credits = 0
        self.begin(1)
        self.access(asset)
        self.reject('runner', {'type': 'finish_access', 'trash': True})
        self.finish()
        view = self.view('runner')
        self.assertIsNone(view['installed_cards'][0]['card'])
        self.assertFalse(view['installed_cards'][0]['face_up'])
        self.assertEqual(view['you']['clicks_remaining'], 3)
        self.assertEqual(self.session.state.card_instances[asset].location.server.server_id, 1)
