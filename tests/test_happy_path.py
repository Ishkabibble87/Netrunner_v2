"""A growing game path through real content, application setup, and commands.

Each named step checks its checkpoint before the next step runs. Extend this
scenario as gameplay grows; HTTP transport and CLI interaction belong elsewhere.
"""

import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from application.game_setup import create_ai_game
from application.sessions import GameSession, SessionStore
from content.cards.loader import load_nsg_card_definitions
from content.decks.deck_importer import load_nrdb_deck
from content.opponents.loader import load_opponent_profiles
from game.commands import (
    AccessCard,
    AdvanceCard,
    ChooseMulligan,
    ContinueRun,
    DiscardCards,
    DiscardSelection,
    DrawCard,
    FinishAccess,
    GainCredit,
    InstallCard,
    JackOut,
    PassPriority,
    PlayCard,
    PurgeVirusCounters,
    ResolveReaction,
    RezCard,
    RunServer,
    ScoreAgenda,
    UseCardAbility,
)
from game.engine import legal_actions_for
from game.icebreakers import effective_strength
from game.models import (
    CardZone,
    DeckSelection,
    GameState,
    MulliganDecision,
    PlayerRole,
    TurnPhase,
)
from game.modifiers import memory_limit
from game.setup import IllegalCommand

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GameProgressTests(unittest.TestCase):
    """Follow one Corp player from deck selection into a game against the AI."""

    def test_game_path(self):
        self.cards = load_nsg_card_definitions(
            PROJECT_ROOT / "content/cards/system-gateway/cards.json"
        )
        self.sessions = SessionStore()
        self.addCleanup(self.sessions.clear)

        deck = self.select_and_parse_deck()
        session = self.select_opponent_and_start_game(deck)
        state = self.mulligan_while_opponent_keeps(session)
        self.assert_corp_first_turn_started(state)
        self.take_basic_actions(session)
        self.discard_and_continue_after_opponent_turn(session)
        self.play_economy_cards_and_continue(session)
        self.install_advance_and_score(session)
        self.contest_another_office(session)
        self.use_installed_economy(session)
        self.break_through_ice(session)
        self.run_from_events(session)
        self.use_run_triggered_cards(session)
        self.use_turn_triggered_economy(session)
        self.use_hosted_viruses(session)
        self.suffer_damage_and_flatline(session)

    def select_and_parse_deck(self) -> DeckSelection:
        deck = load_nrdb_deck(
            PROJECT_ROOT / "content/decks/system-gateway/starter-corp.txt", self.cards
        )
        self.assertEqual(
            self.cards[deck.identity_definition_id].title,
            "The Syndicate: Profit over Principle",
        )
        self.assertEqual(sum(deck.card_counts.values()), 34)
        self.assertEqual(
            {self.cards[card_id].title: count for card_id, count in deck.card_counts.items()},
            {
                "Offworld Office": 3, "Send a Message": 2, "Superconducting Hub": 2,
                "Nico Campaign": 2, "Urtica Cipher": 2, "Regolith Mining License": 2,
                "Manegarm Skunkworks": 1, "Seamless Launch": 2, "Government Subsidy": 2,
                "Hedge Fund": 3, "Brân 1.0": 2, "Palisade": 3, "Diviner": 2,
                "Whitespace": 2, "Karunā": 2, "Tithe": 2,
            },
        )
        return deck

    def select_opponent_and_start_game(self, deck: DeckSelection) -> GameSession:
        opponent = load_opponent_profiles(self.cards)["system-gateway-training"]
        # Add real virus cards to this scenario's deck without altering reusable decks.
        selections = dict(opponent.deck_selections)
        runner_deck = selections[PlayerRole.RUNNER]
        selections[PlayerRole.RUNNER] = runner_deck.model_copy(update={
            "card_counts": {**runner_deck.card_counts, "30009": 1, "30004": 1, "30007": 1}})
        opponent = opponent.model_copy(update={"deck_selections": selections})
        player_id = uuid4()
        game = create_ai_game(
            player_id=player_id,
            decklist=deck,
            opponent_profile=opponent,
            card_definitions=self.cards,
            deck_orders={role: self.explicit_order(selection)
                         for role, selection in opponent.deck_selections.items()},
        )
        session = self.sessions.create(game, opponent.profile_id, [player_id])
        state = session.state
        self.assertEqual(session.opponent_profile_id, opponent.profile_id)
        self.assertEqual(state.corp.player_id, player_id)
        self.assertEqual(state.corp.deck_selection, deck)
        self.assertEqual(state.runner.deck_selection, opponent.deck_selections[PlayerRole.RUNNER])
        self.assertIsNone(state.turn)
        self.assertIsNone(state.winner)
        self.assertEqual(state.setup.pending_player, PlayerRole.CORP)
        self.assertEqual(state.setup.decisions, {})
        for player in (state.corp, state.runner):
            with self.subTest(opening_player=player.role):
                expected_order = self.explicit_order(player.deck_selection)
                hand = self.cards_in_zone(state, player.player_id, CardZone.HAND)
                deck = sorted(self.cards_in_zone(state, player.player_id, CardZone.DECK).values(),
                              key=lambda c: c.location.deck_position)
                self.assertEqual([c.definition_id for c in hand.values()], expected_order[:5])
                self.assertEqual([c.definition_id for c in deck], expected_order[5:])
                self.assertEqual(player.credits, 5)
                self.assertEqual(player.clicks_remaining, 0)
                self.assertEqual(len(self.cards_in_zone(state, player.player_id, CardZone.HAND)), 5)
                self.assertEqual(
                    len(self.cards_in_zone(state, player.player_id, CardZone.DECK)),
                    sum(player.deck_selection.card_counts.values()) - 5,
                )
                self.assertEqual(
                    Counter(card.definition_id for card in state.card_instances.values()
                            if card.owner_id == player.player_id and card.location.zone != CardZone.IDENTITY),
                    Counter(player.deck_selection.card_counts),
                )
        self.assertEqual(
            legal_actions_for(state, player_id),
            (ChooseMulligan(MulliganDecision.KEEP), ChooseMulligan(MulliganDecision.MULLIGAN)),
        )
        self.assertEqual(legal_actions_for(state, state.runner.player_id), ())
        return session

    def mulligan_while_opponent_keeps(self, session: GameSession) -> GameState:
        before = session.state
        runner_hand = self.cards_in_zone(before, before.runner.player_id, CardZone.HAND)
        token = session.player_tokens[before.corp.player_id]
        class MulliganOrder:
            def shuffle(inner_self, cards):
                by_definition = {}
                for card in cards:
                    by_definition.setdefault(card.definition_id, []).append(card)
                cards[:] = [by_definition[code].pop(0) for code in self.explicit_order(before.corp.deck_selection)]
        with patch("game.setup.random.SystemRandom", return_value=MulliganOrder()):
            updated, player_id = self.sessions.submit(
                before.game_id, token, ChooseMulligan(MulliganDecision.MULLIGAN)
            )
        state = updated.state
        self.assertEqual(player_id, before.corp.player_id)
        self.assertIsNone(state.setup.pending_player)
        self.assertEqual(state.setup.decisions, {
            PlayerRole.CORP: MulliganDecision.MULLIGAN,
            PlayerRole.RUNNER: MulliganDecision.KEEP,
        })
        self.assertEqual(self.cards_in_zone(state, state.runner.player_id, CardZone.HAND), runner_hand)
        # Mulligans reuse physical copies; drawing the same cards again is legal.
        self.assertEqual(state.card_instances.keys(), before.card_instances.keys())
        self.assertEqual(state.runner, before.runner)
        return state

    def assert_corp_first_turn_started(self, state: GameState) -> None:
        self.assertIsNotNone(state.turn)
        self.assertEqual(state.turn.number, 1)
        self.assertEqual(state.turn.active_player, PlayerRole.CORP)
        self.assertEqual(state.turn.phase, TurnPhase.ACTION)
        self.assertIsNone(state.winner)
        self.assertEqual(state.corp.credits, 5)
        self.assertEqual(state.corp.clicks_remaining, 3)
        self.assertEqual(len(self.cards_in_zone(state, state.corp.player_id, CardZone.HAND)), 6)
        self.assertEqual(len(self.cards_in_zone(state, state.corp.player_id, CardZone.DECK)), 28)
        self.assertEqual(
            legal_actions_for(state, state.corp.player_id),
            (GainCredit(), DrawCard(), PurgeVirusCounters()),
        )
        self.assertEqual(legal_actions_for(state, state.runner.player_id), ())

    def take_basic_actions(self, session: GameSession) -> None:
        before = session.state
        player_id = before.corp.player_id
        token = session.player_tokens[player_id]
        hand = self.cards_in_zone(before, player_id, CardZone.HAND)
        deck = self.cards_in_zone(before, player_id, CardZone.DECK)
        top_card = next(card.instance_id for card in deck.values() if card.location.deck_position == 1)
        self.assertEqual(self.cards[deck[top_card].definition_id].title, "Offworld Office")

        session, _ = self.sessions.submit(before.game_id, token, DrawCard())
        drawn = session.state
        self.assertEqual(set(self.cards_in_zone(drawn, player_id, CardZone.HAND)), set(hand) | {top_card})
        self.assertEqual(set(self.cards_in_zone(drawn, player_id, CardZone.DECK)), set(deck) - {top_card})
        self.assertEqual(drawn.corp.clicks_remaining, 2)
        self.assertEqual(drawn.corp.credits, 5)
        self.assertEqual(legal_actions_for(drawn, player_id), (GainCredit(), DrawCard()))

        session, _ = self.sessions.submit(before.game_id, token, GainCredit())
        gained = session.state
        self.assertEqual(gained.corp.credits, 6)
        self.assertEqual(gained.corp.clicks_remaining, 1)
        self.assertEqual(self.cards_in_zone(gained, player_id, CardZone.HAND),
                         self.cards_in_zone(drawn, player_id, CardZone.HAND))
        self.assertEqual(self.cards_in_zone(gained, player_id, CardZone.DECK),
                         self.cards_in_zone(drawn, player_id, CardZone.DECK))
        self.assertEqual(gained.runner, before.runner)
        self.assertEqual(gained.turn.model_dump(exclude={"occurrences"}),
                         before.turn.model_dump(exclude={"occurrences"}))

    def discard_and_continue_after_opponent_turn(self, session: GameSession) -> None:
        """Finish the Corp turn, discard together, and let the training AI act."""
        state = session.state
        token = session.player_tokens[state.corp.player_id]
        session, _ = self.sessions.submit(state.game_id, token, GainCredit())
        state = session.state
        self.assertEqual(state.turn.phase, TurnPhase.DISCARD)
        selection, = legal_actions_for(state, state.corp.player_id)
        self.assertIsInstance(selection, DiscardSelection)
        self.assertEqual(selection.required_count, 2)
        chosen = tuple(next(i for i in selection.eligible_card_instance_ids
                            if self.cards[state.card_instances[i].definition_id].title == title)
                       for title in ("Government Subsidy", "Hedge Fund"))
        session, _ = self.sessions.submit(state.game_id, token, DiscardCards(chosen))
        state = session.state
        self.assertEqual(state.turn.active_player, PlayerRole.CORP)
        self.assertEqual(state.turn.number, 3)
        self.assertEqual(state.corp.clicks_remaining, 3)
        self.assertEqual(state.runner.credits, 9)
        self.assertEqual(state.runner.clicks_remaining, 0)
        self.assertEqual(len(self.cards_in_zone(state, state.corp.player_id, CardZone.HAND)), 6)
        for instance_id in chosen:
            self.assertEqual(state.card_instances[instance_id].location.zone, CardZone.DISCARD_PILE)
            self.assertFalse(state.card_instances[instance_id].face_up)

    def play_economy_cards_and_continue(self, session: GameSession) -> None:
        """Play separate physical economy cards, then finish the whole turn."""
        state = session.state
        player_id = state.corp.player_id
        token = session.player_tokens[player_id]
        for definition_id in ("30075", "30075", "30064"):
            before = session.state
            instance = next(card for card in self.cards_in_zone(before, player_id, CardZone.HAND).values()
                            if card.definition_id == definition_id)
            command = PlayCard(instance.instance_id)
            self.assertIn(command, legal_actions_for(before, player_id, self.cards))
            session, _ = self.sessions.submit(before.game_id, token, command, card_definitions=self.cards)
            state = session.state
            self.assertEqual(state.corp.credits, before.corp.credits + (5 if definition_id == "30064" else 4))
            self.assertEqual(state.card_instances[instance.instance_id].location.zone, CardZone.DISCARD_PILE)
            self.assertTrue(state.card_instances[instance.instance_id].face_up)
            self.assertEqual(before.card_instances[instance.instance_id].location.zone, CardZone.HAND)
        self.assertEqual(state.turn.number, 5)
        self.assertEqual(state.turn.active_player, PlayerRole.CORP)
        self.assertEqual(state.corp.clicks_remaining, 3)
        self.assertEqual(state.runner.credits, 13)

    def explicit_order(self, selection):
        """Full order for initial hands, mulligans, and named draws.

        Corp opens with three Hedge Funds and two Government Subsidies, followed
        by all three Offices. Remaining copies follow in definition-ID order.
        """
        titles = ["Hedge Fund", "Government Subsidy", "Offworld Office", "Regolith Mining License",
                  "Sure Gamble", "Creative Commission", "Telework Contract", "VRcation", "Cleaver", "Palisade", "Jailbreak", "Overclock", "Docklands Pass", "Red Team", "Pennyshaver"]
        codes = [next(code for code, card in self.cards.items() if card.title == title) for title in titles]
        final_cards = ["30009", "30004", "30007", "30032"]
        codes += sorted(set(selection.card_counts) - set(codes) - set(final_cards))
        codes += final_cards
        return [code for code in codes for _ in range(selection.card_counts.get(code, 0))]

    def install_advance_and_score(self, session):
        """Score after the last click, resolve the reaction, and resume the turn."""
        state = session.state
        player_id = state.corp.player_id
        token = session.player_tokens[player_id]
        office = next(c for c in self.cards_in_zone(state, player_id, CardZone.HAND).values()
                      if self.cards[c.definition_id].title == "Offworld Office")

        def submit(command):
            self.sessions.submit(state.game_id, token, command, card_definitions=self.cards)
            return session.state

        submit(InstallCard(office.instance_id, "new_remote"))
        installed = session.state.card_instances[office.instance_id]
        self.assertTrue(installed.installed)
        self.assertFalse(installed.face_up)
        self.assertEqual(installed.location.server.server_id, 1)
        for _ in range(2):
            submit(PassPriority())
            submit(AdvanceCard(office.instance_id))
        self.assertEqual(session.state.corp.clicks_remaining, 0)
        self.assertEqual(session.state.turn.number, 5)
        submit(PassPriority())
        while session.state.turn.number < 7:
            submit(PassPriority())
        self.assertEqual(session.state.turn.number, 7)
        self.assertEqual(session.state.turn.phase, TurnPhase.DRAW)
        deck_count = len(self.cards_in_zone(session.state, player_id, CardZone.DECK))
        submit(PassPriority())
        self.assertEqual(len(self.cards_in_zone(session.state, player_id, CardZone.DECK)), deck_count - 1)
        submit(PassPriority())
        submit(GainCredit())
        for _ in range(2):
            submit(PassPriority())
            submit(AdvanceCard(office.instance_id))
        before_score = session.state
        self.assertEqual(before_score.corp.clicks_remaining, 0)
        self.assertIn(ScoreAgenda(office.instance_id), legal_actions_for(before_score, player_id, self.cards))
        scored = submit(ScoreAgenda(office.instance_id))
        self.assertEqual(scored.turn.number, 7)
        self.assertEqual(scored.corp.credits, before_score.corp.credits)
        self.assertEqual(scored.window.kind, "reaction")
        card = scored.card_instances[office.instance_id]
        self.assertEqual(card.location.zone, CardZone.SCORE_AREA)
        self.assertTrue(card.face_up)
        self.assertFalse(card.installed)
        self.assertEqual(card.counters, {})
        self.assertNotIn(1, scored.servers)
        resolved = submit(ResolveReaction(office.instance_id))
        self.assertEqual(resolved.corp.credits, before_score.corp.credits + 7)
        self.assertEqual(resolved.corp.clicks_remaining, 0)
        self.assertEqual(resolved.turn.number, 7)
        submit(PassPriority())
        self.assertEqual(session.state.turn.number, 9)

    def contest_another_office(self, session):
        """Continue the same game with both seats controlled to contest a remote."""
        session.ai_player_ids.clear()
        session.player_tokens[session.state.runner.player_id] = "scenario-runner"

        def submit(role, command):
            player = session.state.corp if role is PlayerRole.CORP else session.state.runner
            self.sessions.submit(session.state.game_id, session.player_tokens[player.player_id],
                                 command, card_definitions=self.cards)
            return session.state

        corp, runner = PlayerRole.CORP, PlayerRole.RUNNER
        office = next(c for c in self.cards_in_zone(session.state, session.state.corp.player_id,
                                                   CardZone.HAND).values()
                      if self.cards[c.definition_id].title == "Offworld Office")
        while session.state.window:
            submit(corp, PassPriority())
        submit(corp, InstallCard(office.instance_id, "new_remote"))
        for _ in range(2):
            submit(corp, PassPriority())
            submit(corp, AdvanceCard(office.instance_id))
        submit(corp, PassPriority())
        while session.state.turn.active_player is corp:
            submit(corp, PassPriority())
        self.assertEqual(session.state.turn.active_player, runner)
        for _ in range(3):
            while session.state.window:
                submit(corp, PassPriority())
            submit(runner, GainCredit())
        while session.state.window:
            submit(corp, PassPriority())
        credits = session.state.corp.credits
        submit(runner, RunServer(1))
        self.assertEqual(session.state.runner.clicks_remaining, 0)
        self.assertEqual(session.state.turn.active_player, runner)
        while session.state.window:
            submit(corp, PassPriority())
        submit(runner, ContinueRun())
        while session.state.window:
            submit(corp, PassPriority())
        submit(runner, AccessCard(office.instance_id))
        self.assertTrue(session.state.run.successful)
        self.assertEqual(session.state.run.current_card_id, office.instance_id)
        stolen = submit(runner, FinishAccess())
        card = stolen.card_instances[office.instance_id]
        self.assertEqual(card.location.zone, CardZone.SCORE_AREA)
        self.assertEqual(card.location.player_id, stolen.runner.player_id)
        self.assertEqual(card.counters, {})
        self.assertEqual(stolen.corp.credits, credits)
        self.assertNotIn(1, stolen.servers)
        self.assertIsNone(stolen.run)
        self.assertEqual(stolen.turn.active_player, corp)
        self.assertEqual(len(self.cards_in_zone(stolen, stolen.corp.player_id, CardZone.SCORE_AREA)), 1)
        self.assertEqual(len(self.cards_in_zone(stolen, stolen.runner.player_id, CardZone.SCORE_AREA)), 1)

    def use_installed_economy(self, session):
        """Continue the same game through both economies and a contested asset."""
        corp, runner = PlayerRole.CORP, PlayerRole.RUNNER

        def submit(role, command):
            state = session.state
            player = state.corp if role is corp else state.runner
            self.sessions.submit(state.game_id, session.player_tokens[player.player_id], command,
                                 card_definitions=self.cards)
            return session.state

        def pass_windows():
            while session.state.window:
                submit(session.state.window.player, PassPriority())

        def action_for(role):
            for _ in range(100):
                state = session.state
                if state.window:
                    submit(state.window.player, PassPriority())
                elif state.turn.active_player is role and state.turn.phase is TurnPhase.ACTION:
                    return
                elif state.turn.phase is TurnPhase.DISCARD:
                    player = state.corp if state.turn.active_player is corp else state.runner
                    selection, = legal_actions_for(state, player.player_id, self.cards)
                    choices = sorted(selection.eligible_card_instance_ids,
                                     key=lambda i: {"Regolith Mining License": 1, "Telework Contract": 1,
                                                    "Offworld Office": 3, "Palisade": 2, "Cleaver": 3,
                                                    "Jailbreak": 3, "Overclock": 3}.get(
                                                        self.cards[state.card_instances[i].definition_id].title, 0))
                    submit(player.role, DiscardCards(tuple(choices[:selection.required_count])))
                else:
                    submit(state.turn.active_player, GainCredit())
            self.fail("Did not reach the requested action window")

        def acquire(role, title):
            for _ in range(30):
                action_for(role)
                player = session.state.corp if role is corp else session.state.runner
                found = next((c for c in self.cards_in_zone(session.state, player.player_id, CardZone.HAND).values()
                              if self.cards[c.definition_id].title == title), None)
                if found:
                    return found.instance_id
                submit(role, DrawCard())
            self.fail(f"Did not draw {title}")

        regolith = acquire(corp, "Regolith Mining License")
        before = session.state
        submit(corp, InstallCard(regolith, "new_remote"))
        self.assertEqual(session.state.corp.credits, before.corp.credits)
        self.assertEqual(session.state.corp.clicks_remaining, before.corp.clicks_remaining - 1)
        self.assertEqual(session.state.card_instances[regolith].counters, {})
        before = session.state
        submit(corp, RezCard(regolith))
        self.assertEqual(session.state.corp.credits, before.corp.credits - 2)
        self.assertEqual(session.state.corp.clicks_remaining, before.corp.clicks_remaining)
        self.assertEqual(session.state.card_instances[regolith].counters, {"credit": 15})
        for remaining in (12, 9, 6, 3, 0):
            action_for(corp)
            before = session.state
            command = UseCardAbility(regolith, "take_credits")
            self.assertIn(command, legal_actions_for(before, before.corp.player_id, self.cards))
            submit(corp, command)
            self.assertEqual(session.state.corp.credits, before.corp.credits + 3)
            card = session.state.card_instances[regolith]
            self.assertEqual(card.counters.get("credit", 0), remaining)
        self.assertEqual(card.location.zone, CardZone.DISCARD_PILE)
        self.assertFalse(card.installed)
        self.assertEqual(card.counters, {})
        self.assertNotIn(1, session.state.servers)

        telework = acquire(runner, "Telework Contract")
        before = session.state
        submit(runner, InstallCard(telework))
        self.assertEqual(session.state.runner.credits, before.runner.credits - 1)
        self.assertEqual(session.state.card_instances[telework].counters, {"credit": 9})
        self.assertTrue(session.state.card_instances[telework].face_up)
        self.assertFalse(session.state.card_instances[telework].rezzed)
        for remaining in (6, 3, 0):
            action_for(runner)
            before = session.state
            command = UseCardAbility(telework, "take_credits")
            submit(runner, command)
            self.assertEqual(session.state.runner.credits, before.runner.credits + 3)
            card = session.state.card_instances[telework]
            self.assertEqual(card.counters.get("credit", 0), remaining)
            if remaining:
                self.assertNotIn(command, legal_actions_for(session.state, before.runner.player_id, self.cards))
                unchanged = session.state.model_dump()
                with self.assertRaises(IllegalCommand):
                    submit(runner, command)
                self.assertEqual(session.state.model_dump(), unchanged)
                action_for(corp)
        self.assertEqual(card.location.zone, CardZone.DISCARD_PILE)
        self.assertFalse(card.installed)
        self.assertEqual(card.counters, {})

        # Leave the second asset facedown for a protected run's approach window.
        regolith = acquire(corp, "Regolith Mining License")
        submit(corp, InstallCard(regolith, "new_remote"))

    def break_through_ice(self, session):
        """Continue the core game through a defended remote and a paid asset rez."""
        corp, runner = PlayerRole.CORP, PlayerRole.RUNNER

        def submit(role, command):
            player = session.state.corp if role is corp else session.state.runner
            self.sessions.submit(session.state.game_id, session.player_tokens[player.player_id], command,
                                 card_definitions=self.cards)

        def pass_windows():
            while session.state.window:
                submit(session.state.window.player, PassPriority())

        def action_for(role):
            for _ in range(100):
                pass_windows()
                state = session.state
                if state.turn.active_player is role and state.turn.phase is TurnPhase.ACTION:
                    return
                if state.turn.phase is TurnPhase.DISCARD:
                    player = state.corp if state.turn.active_player is corp else state.runner
                    selection, = legal_actions_for(state, player.player_id, self.cards)
                    choices = sorted(selection.eligible_card_instance_ids,
                                     key=lambda i: self.cards[state.card_instances[i].definition_id].title
                                     in {"Offworld Office", "Palisade", "Cleaver", "Jailbreak", "Overclock"})
                    submit(player.role, DiscardCards(tuple(choices[:selection.required_count])))
                else:
                    submit(state.turn.active_player, GainCredit())
            self.fail("Did not reach the requested action window")

        def acquire(role, title):
            for _ in range(30):
                action_for(role)
                player = session.state.corp if role is corp else session.state.runner
                found = next((c for c in self.cards_in_zone(session.state, player.player_id, CardZone.HAND).values()
                              if self.cards[c.definition_id].title == title), None)
                if found:
                    return found.instance_id
                submit(role, DrawCard())
            self.fail(f"Did not draw {title}")

        office = acquire(corp, "Offworld Office")
        submit(corp, InstallCard(office, "new_remote"))
        target = session.state.card_instances[office].location.server.server_id
        ice = acquire(corp, "Palisade")
        submit(corp, InstallCard(ice, target))
        breaker = acquire(runner, "Cleaver")
        submit(runner, InstallCard(breaker))
        pass_windows()
        self.assertTrue(session.state.card_instances[breaker].installed)
        before = session.state.runner.credits
        submit(runner, RunServer(target))
        while session.state.run.phase != "approach" or session.state.window.player is not corp:
            submit(session.state.window.player, PassPriority())
        self.assertNotIn(JackOut(), legal_actions_for(session.state, session.state.runner.player_id, self.cards))
        regolith = next(c.instance_id for c in session.state.card_instances.values()
                        if c.installed and self.cards[c.definition_id].title == "Regolith Mining License")
        before_rez = session.state.corp.credits
        submit(corp, RezCard(regolith))
        self.assertEqual(session.state.card_instances[regolith].counters, {"credit": 15})
        self.assertEqual(session.state.corp.credits, before_rez - 2)
        self.assertIs(session.state.window.player, corp)
        submit(corp, RezCard(ice))
        while session.state.run.phase != "encounter":
            submit(session.state.window.player, PassPriority())
        submit(runner, UseCardAbility(breaker, "boost_strength"))
        submit(runner, UseCardAbility(breaker, "break_barrier", (0,)))
        self.assertEqual(session.state.runner.credits, before - 3)
        self.assertEqual(session.state.run.broken_subroutine_indices, [0])
        pass_windows()
        self.assertEqual(session.state.run.phase, "movement")
        self.assertEqual(session.state.run.strength_boosts, {})
        submit(runner, ContinueRun())
        pass_windows()
        submit(runner, AccessCard(office))
        submit(runner, FinishAccess())
        self.assertIsNone(session.state.run)
        self.assertEqual(session.state.card_instances[office].location.player_id, session.state.runner.player_id)
        action_for(runner)
        asset_server = session.state.card_instances[regolith].location.server.server_id
        submit(runner, RunServer(asset_server))
        pass_windows()
        submit(runner, ContinueRun())
        pass_windows()
        submit(runner, AccessCard(regolith))
        before = session.state.runner.credits
        submit(runner, FinishAccess(trash=True))
        self.assertEqual(session.state.runner.credits, before - 3)
        self.assertFalse(session.state.card_instances[regolith].installed)
        self.assertEqual(session.state.card_instances[regolith].counters, {})
        self.assertIsNone(session.state.run)

    def run_from_events(self, session):
        """Continue the same game through Jailbreak and an Overclock-funded encounter."""
        corp, runner = PlayerRole.CORP, PlayerRole.RUNNER

        def submit(role, command):
            player = session.state.corp if role is corp else session.state.runner
            self.sessions.submit(session.state.game_id, session.player_tokens[player.player_id], command,
                                 card_definitions=self.cards)

        def pass_windows():
            while session.state.window:
                submit(session.state.window.player, PassPriority())

        def action_for(role):
            for _ in range(150):
                pass_windows()
                state = session.state
                if state.turn.active_player is role and state.turn.phase is TurnPhase.ACTION:
                    return
                if state.turn.phase is TurnPhase.DISCARD:
                    player = state.corp if state.turn.active_player is corp else state.runner
                    selection, = legal_actions_for(state, player.player_id, self.cards)
                    choices = sorted(selection.eligible_card_instance_ids,
                                     key=lambda i: self.cards[state.card_instances[i].definition_id].title
                                     in {"Jailbreak", "Overclock"})
                    submit(player.role, DiscardCards(tuple(choices[:selection.required_count])))
                else:
                    submit(state.turn.active_player, GainCredit())
            self.fail("Did not reach the requested action window")

        def acquire(title):
            for _ in range(30):
                action_for(runner)
                found = next((c for c in self.cards_in_zone(session.state, session.state.runner.player_id,
                                                          CardZone.HAND).values()
                              if self.cards[c.definition_id].title == title), None)
                if found:
                    return found.instance_id
                submit(runner, DrawCard())
            self.fail(f"Did not draw {title}")

        jailbreak = acquire("Jailbreak")
        before = session.state
        submit(runner, PlayCard(jailbreak, "rd"))
        self.assertEqual(session.state.runner.clicks_remaining, before.runner.clicks_remaining - 1)
        self.assertEqual(session.state.card_instances[jailbreak].location.zone, CardZone.PLAY_AREA)
        pass_windows()
        submit(runner, ContinueRun())
        pass_windows()
        self.assertEqual(len(self.cards_in_zone(session.state, before.runner.player_id, CardZone.HAND)),
                         len(self.cards_in_zone(before, before.runner.player_id, CardZone.HAND)))
        self.assertEqual(session.state.run.access_limit, 2)
        seen = []
        for _ in range(2):
            submit(runner, AccessCard("rd"))
            seen.append(session.state.run.current_card_id)
            submit(runner, FinishAccess())
        self.assertEqual(len(set(seen)), 2)
        self.assertIsNone(session.state.run)
        self.assertEqual(session.state.card_instances[jailbreak].location.zone, CardZone.DISCARD_PILE)

        overclock = acquire("Overclock")
        ice = next(c for c in session.state.card_instances.values()
                   if c.installed and self.cards[c.definition_id].title == "Palisade")
        target = ice.location.server.server_id
        breaker = next(c.instance_id for c in session.state.card_instances.values()
                       if c.installed and self.cards[c.definition_id].title == "Cleaver")
        before = session.state.runner.credits
        submit(runner, PlayCard(overclock, target))
        self.assertEqual(session.state.runner.credits, before - 1)
        while session.state.run.phase != "encounter":
            submit(session.state.window.player, PassPriority())
        submit(runner, UseCardAbility(breaker, "boost_strength"))
        submit(runner, UseCardAbility(breaker, "break_barrier", (0,)))
        self.assertEqual(session.state.runner.credits, before - 1)
        self.assertEqual(session.state.card_instances[overclock].counters, {"credit": 2})
        pass_windows()
        submit(runner, ContinueRun())
        pass_windows()
        self.assertIsNone(session.state.run)
        self.assertEqual(session.state.card_instances[overclock].location.zone, CardZone.DISCARD_PILE)
        self.assertEqual(session.state.card_instances[overclock].counters, {})

    def use_run_triggered_cards(self, session):
        """Install hardware and run with Red Team through a multiaccess breach."""
        runner = PlayerRole.RUNNER
        wanted = {"Docklands Pass", "Red Team", "Pennyshaver"}

        def submit(role, command):
            player = session.state.corp if role is PlayerRole.CORP else session.state.runner
            self.sessions.submit(session.state.game_id, session.player_tokens[player.player_id], command,
                                 card_definitions=self.cards)

        def settle():
            for _ in range(150):
                state = session.state
                if state.window:
                    if state.window.kind == "reaction":
                        choices = legal_actions_for(state, state.runner.player_id, self.cards)
                        submit(runner, next(a for a in choices if isinstance(a, ResolveReaction) and not a.decline))
                    else:
                        submit(state.window.player, PassPriority())
                elif state.turn.phase is TurnPhase.DISCARD:
                    player = state.corp if state.turn.active_player is PlayerRole.CORP else state.runner
                    selection, = legal_actions_for(state, player.player_id, self.cards)
                    choices = sorted(selection.eligible_card_instance_ids,
                                     key=lambda i: self.cards[state.card_instances[i].definition_id].title in wanted)
                    submit(player.role, DiscardCards(tuple(choices[:selection.required_count])))
                elif state.turn.active_player is runner:
                    return
                else:
                    submit(PlayerRole.CORP, GainCredit())
            self.fail("Runner action did not arrive")

        for _ in range(30):
            settle()
            found = {self.cards[c.definition_id].title: c.instance_id for c in
                     self.cards_in_zone(session.state, session.state.runner.player_id, CardZone.HAND).values()
                     if self.cards[c.definition_id].title in wanted}
            if wanted <= found.keys():
                break
            submit(runner, DrawCard())
        self.assertEqual(found.keys(), wanted)
        turn = session.state.turn.number
        while session.state.turn.number == turn:
            settle()
            if session.state.turn.number == turn:
                submit(runner, GainCredit())
        settle()
        for title in ("Docklands Pass", "Red Team", "Pennyshaver"):
            submit(runner, InstallCard(found[title]))
            settle()
        penny = found["Pennyshaver"]
        self.assertEqual(memory_limit(session.state, session.state.runner, self.cards), 5)
        # Start the run on a fresh turn so its history stays available for inspection.
        turn = session.state.turn.number
        while session.state.turn.number == turn:
            submit(runner, GainCredit())
            settle()
        team = found["Red Team"]
        self.assertEqual(session.state.card_instances[team].counters["credit"], 12)
        credits = session.state.runner.credits
        submit(runner, UseCardAbility(team, "run_central", server_id="hq"))
        settle()
        self.assertEqual(session.state.run.source_card_id, team)
        submit(runner, ContinueRun())
        settle()
        self.assertEqual(session.state.runner.credits, credits + 3)
        self.assertEqual(session.state.card_instances[team].counters["credit"], 9)
        self.assertEqual(session.state.card_instances[penny].counters["credit"], 1)
        self.assertEqual(session.state.run.access_limit, 2)
        for _ in range(2):
            submit(runner, AccessCard("hq"))
            submit(runner, FinishAccess())
        self.assertIsNone(session.state.run)
        settle()
        targets = {a.server_id for a in legal_actions_for(session.state, session.state.runner.player_id, self.cards)
                   if isinstance(a, UseCardAbility) and a.card_instance_id == team}
        self.assertEqual(targets, {"rd", "archives"})
        before = session.state.runner.credits
        submit(runner, UseCardAbility(penny, "cash_out"))
        self.assertEqual(session.state.runner.credits, before + 2)
        self.assertEqual(session.state.card_instances[penny].counters["credit"], 0)

    def use_turn_triggered_economy(self, session):
        """Continue the core game through installation, loading and a turn-start payout."""
        runner = PlayerRole.RUNNER

        def submit(role, command):
            player = session.state.corp if role is PlayerRole.CORP else session.state.runner
            self.sessions.submit(session.state.game_id, session.player_tokens[player.player_id], command,
                                 card_definitions=self.cards)

        def action_for_runner():
            for _ in range(100):
                state = session.state
                if state.window:
                    actions = legal_actions_for(state,
                        (state.corp if state.window.player is PlayerRole.CORP else state.runner).player_id, self.cards)
                    submit(state.window.player, actions[0] if state.window.kind == "reaction" else PassPriority())
                elif state.turn.phase is TurnPhase.DISCARD:
                    player = state.corp if state.turn.active_player is PlayerRole.CORP else state.runner
                    selection, = legal_actions_for(state, player.player_id, self.cards)
                    choices = sorted(selection.eligible_card_instance_ids,
                        key=lambda i: self.cards[state.card_instances[i].definition_id].title == "Smartware Distributor")
                    submit(player.role, DiscardCards(tuple(choices[:selection.required_count])))
                elif state.turn.active_player is runner:
                    return
                else:
                    submit(PlayerRole.CORP, GainCredit())
            self.fail("Runner action did not arrive")

        for _ in range(30):
            action_for_runner()
            smartware = next((c.instance_id for c in self.cards_in_zone(session.state,
                session.state.runner.player_id, CardZone.HAND).values()
                if self.cards[c.definition_id].title == "Smartware Distributor"), None)
            if smartware:
                break
            submit(runner, DrawCard())
        self.assertIsNotNone(smartware)
        submit(runner, InstallCard(smartware))
        action_for_runner()
        submit(runner, UseCardAbility(smartware, "load_credits"))
        self.assertEqual(session.state.card_instances[smartware].counters["credit"], 3)
        turn = session.state.turn.number
        while session.state.turn.number == turn:
            action_for_runner()
            if session.state.turn.number == turn:
                submit(runner, GainCredit())
        action_for_runner()
        self.assertEqual(session.state.card_instances[smartware].counters["credit"], 2)

    def use_hosted_viruses(self, session):
        """Continue the same game through virus installation, a run, and a trash payout."""
        runner = PlayerRole.RUNNER
        wanted = {"Cookbook", "Botulus", "Fermenter"}

        def submit(role, command):
            player = session.state.corp if role is PlayerRole.CORP else session.state.runner
            self.sessions.submit(session.state.game_id, session.player_tokens[player.player_id], command,
                                 card_definitions=self.cards)

        def settle():
            for _ in range(150):
                state = session.state
                if state.window:
                    player = state.corp if state.window.player is PlayerRole.CORP else state.runner
                    actions = legal_actions_for(state, player.player_id, self.cards)
                    submit(player.role, actions[0] if state.window.kind == "reaction" else PassPriority())
                elif state.turn.phase is TurnPhase.DISCARD:
                    player = state.corp if state.turn.active_player is PlayerRole.CORP else state.runner
                    selection, = legal_actions_for(state, player.player_id, self.cards)
                    choices = sorted(selection.eligible_card_instance_ids,
                                     key=lambda i: self.cards[state.card_instances[i].definition_id].title in wanted)
                    submit(player.role, DiscardCards(tuple(choices[:selection.required_count])))
                elif state.turn.active_player is runner:
                    return
                else:
                    submit(PlayerRole.CORP, GainCredit())
            self.fail("Runner action did not arrive")

        for _ in range(30):
            settle()
            found = {self.cards[c.definition_id].title: c.instance_id for c in
                     self.cards_in_zone(session.state, session.state.runner.player_id, CardZone.HAND).values()
                     if self.cards[c.definition_id].title in wanted}
            if wanted <= found.keys():
                break
            submit(runner, DrawCard())
        self.assertEqual(found.keys(), wanted)
        submit(runner, InstallCard(found["Cookbook"]))
        settle()
        host = next(c for c in session.state.card_instances.values()
                    if c.installed and self.cards[c.definition_id].title == "Palisade")
        botulus = found["Botulus"]
        submit(runner, InstallCard(botulus, host_card_instance_id=host.instance_id))
        settle()
        self.assertEqual(session.state.card_instances[botulus].counters["virus"], 2)
        submit(runner, RunServer(host.location.server.server_id))
        for _ in range(20):
            if session.state.run.phase == "encounter":
                break
            submit(session.state.window.player, PassPriority())
        submit(runner, UseCardAbility(botulus, "break_host", (0,)))
        self.assertEqual(session.state.run.broken_subroutine_indices, [0])
        self.assertEqual(session.state.card_instances[botulus].counters["virus"], 1)
        settle()
        submit(runner, ContinueRun())
        settle()
        self.assertIsNone(session.state.run)
        fermenter = found["Fermenter"]
        submit(runner, InstallCard(fermenter))
        settle()
        count = session.state.card_instances[fermenter].counters["virus"]
        self.assertGreaterEqual(count, 2)
        before = session.state.runner.credits
        submit(runner, UseCardAbility(fermenter, "cash_out"))
        self.assertEqual(session.state.runner.credits, before + 2 * count)
        self.assertFalse(session.state.card_instances[fermenter].installed)
        self.assertEqual(session.state.card_instances[fermenter].counters, {})

        # Continue the same game with a breaker and its delayed run-end effect.
        wanted = {"Mayfly"}
        for _ in range(10):
            settle()
            mayfly = next((c.instance_id for c in self.cards_in_zone(
                session.state, session.state.runner.player_id, CardZone.HAND).values()
                if self.cards[c.definition_id].title == "Mayfly"), None)
            if mayfly:
                break
            submit(runner, DrawCard())
        self.assertIsNotNone(mayfly)
        programs = tuple(c.instance_id for c in session.state.card_instances.values()
                         if c.installed and c.controller_id == session.state.runner.player_id
                         and self.cards[c.definition_id].card_type.value == "program")
        submit(runner, InstallCard(mayfly, trash_card_instance_ids=programs))
        settle()
        while session.state.runner.credits < 5:
            submit(runner, GainCredit())
            settle()
        submit(runner, RunServer(host.location.server.server_id))
        for _ in range(20):
            if session.state.run.phase == "encounter":
                break
            submit(session.state.window.player, PassPriority())
        ice = session.state.card_instances[session.state.run.current_ice_id]
        strength = effective_strength(session.state, ice, self.cards[ice.definition_id], self.cards)
        for _ in range(max(0, strength - 1)):
            submit(runner, UseCardAbility(mayfly, "boost_strength"))
        submit(runner, UseCardAbility(mayfly, "break_subroutine", (0,)))
        settle()
        self.assertTrue(session.state.card_instances[mayfly].installed)
        submit(runner, JackOut())
        settle()
        self.assertIsNone(session.state.run)
        self.assertFalse(session.state.card_instances[mayfly].installed)
        self.assertEqual(session.state.card_instances[mayfly].location.zone, CardZone.DISCARD_PILE)

    def suffer_damage_and_flatline(self, session):
        """Continue the same game through Karuna's choice and a lethal second run."""
        def submit(role, command):
            player = session.state.corp if role is PlayerRole.CORP else session.state.runner
            self.sessions.submit(session.state.game_id, session.player_tokens[player.player_id], command,
                                 card_definitions=self.cards)

        def settle():
            for _ in range(80):
                state = session.state
                if not state.window:
                    return
                player = state.corp if state.window.player is PlayerRole.CORP else state.runner
                actions = legal_actions_for(state, player.player_id, self.cards)
                submit(player.role, actions[0] if state.window.kind == "reaction" else PassPriority())
            self.fail("Windows did not settle")

        def reach_action(role):
            for _ in range(80):
                settle()
                state = session.state
                if state.turn.phase is TurnPhase.DISCARD:
                    player = state.corp if state.turn.active_player is PlayerRole.CORP else state.runner
                    selection, = legal_actions_for(state, player.player_id, self.cards)
                    submit(player.role, DiscardCards(selection.eligible_card_instance_ids[:selection.required_count]))
                elif state.turn.active_player is role:
                    return
                else:
                    submit(state.turn.active_player, GainCredit())
            self.fail("Expected action phase did not arrive")

        reach_action(PlayerRole.CORP)
        ice = next(c.instance_id for c in self.cards_in_zone(session.state,
            session.state.corp.player_id, CardZone.HAND).values() if self.cards[c.definition_id].title == "Karunā")
        submit(PlayerRole.CORP, InstallCard(ice, "hq"))
        reach_action(PlayerRole.RUNNER)
        before = len(self.cards_in_zone(session.state, session.state.runner.player_id, CardZone.HAND))
        self.assertEqual(before, 3)
        for lethal in (False, True):
            submit(PlayerRole.RUNNER, RunServer("hq"))
            for _ in range(40):
                state = session.state
                if state.winner or state.run.phase == "subroutine_choice":
                    break
                if (state.window.player is PlayerRole.CORP and state.run.phase == "approach"
                        and state.run.current_ice_id == ice and not state.card_instances[ice].rezzed):
                    submit(PlayerRole.CORP, RezCard(ice))
                else:
                    submit(state.window.player, PassPriority())
            if lethal:
                self.assertEqual(session.state.winner, PlayerRole.CORP)
                self.assertIsNone(session.state.run)
                self.assertEqual(legal_actions_for(session.state, session.state.runner.player_id, self.cards), ())
            else:
                self.assertEqual(session.state.run.phase, "subroutine_choice")
                self.assertEqual(len(self.cards_in_zone(session.state, session.state.runner.player_id, CardZone.HAND)), before - 2)
                submit(PlayerRole.RUNNER, JackOut())
                settle()

    @staticmethod
    def cards_in_zone(state, player_id, zone):
        """Select physical cards without relying on shuffled order or random IDs."""
        return {
            card.instance_id: card for card in state.card_instances.values()
            if card.location.player_id == player_id and card.location.zone is zone
        }


if __name__ == "__main__":
    unittest.main()
