"""Random opponents must respect selection sizes and yield to human decisions."""

import unittest
from unittest.mock import patch
from uuid import uuid4

from test_reactions import ReactionScenario

from application.training_opponent import advance_ai_decisions, choose_random_command
from game import commands as cmd
from game.models import PlayerRole


class RandomSelectionTests(unittest.TestCase):
    def test_actions_are_sampled_without_a_strategic_preference(self):
        actions = (cmd.GainCredit(), cmd.DrawCard(), cmd.RunServer("hq"))
        for action in actions:
            with self.subTest(action=action), patch(
                "application.training_opponent.random.choice", return_value=action,
            ) as choice:
                self.assertEqual(choose_random_command(actions), action)
                choice.assert_called_once_with(actions)

    def test_card_selections_respect_exact_optional_and_up_to_counts(self):
        eligible = tuple(uuid4() for _ in range(4))
        for descriptor, count, result_type in (
            (cmd.DiscardSelection(eligible, 2), 2, cmd.DiscardCards),
            (cmd.CardSelection(eligible, 2), 2, cmd.SelectEffectCards),
            (cmd.CardSelection(eligible, 2, optional=True), 0, cmd.SelectEffectCards),
            (cmd.CardSelection(eligible, 2, optional=True), 2, cmd.SelectEffectCards),
            (cmd.CardSelection(eligible, 3, up_to=True), 1, cmd.SelectEffectCards),
            (cmd.CardSelection(eligible, 3, up_to=True), 0, cmd.SelectEffectCards),
        ):
            with self.subTest(descriptor=descriptor, count=count), patch(
                "application.training_opponent.random.choice", side_effect=[descriptor, count],
            ), patch("application.training_opponent.random.randint", return_value=count), patch(
                "application.training_opponent.random.sample", return_value=list(eligible[-count:]) if count else [],
            ) as sample:
                result = choose_random_command((descriptor,))
                sample.assert_called_once_with(eligible, count)
                self.assertEqual(result, result_type(eligible[-count:] if count else ()))


class RandomOpponentFlowTests(ReactionScenario):
    def test_random_runner_can_start_a_run_and_wait_for_the_human_corp(self):
        asset = self.add_card(next(code for code, card in self.cards.items() if card.title == "Nico Campaign"), PlayerRole.CORP)
        self.state.card_instances[asset].rezzed = False
        self.state.card_instances[asset].face_up = False
        self.turn(PlayerRole.RUNNER, clicks=1)
        with patch("application.training_opponent.random.choice", side_effect=
                   lambda actions: next((a for a in actions if isinstance(a, cmd.RunServer)), actions[-1])) as choice:
            state = advance_ai_decisions(self.state, {self.state.runner.player_id}, self.cards)
        self.assertTrue(choice.called)
        self.assertIsNotNone(state.run)
        self.assertEqual(state.window.player, PlayerRole.CORP)
        self.assertEqual(state.runner.clicks_remaining, 0)
        self.assertIsNone(self.state.run)
