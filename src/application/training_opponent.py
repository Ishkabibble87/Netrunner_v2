"""Exercise legal gameplay with a random, non-strategic training opponent."""

import random
from collections.abc import Mapping, Sequence
from uuid import UUID

from content.cards.models import CardDefinition
from game import commands as cmd
from game.engine import apply_command, legal_actions_for, pending_decision
from game.models import GameState, MulliganDecision, PlayerRole


def advance_ai_setup(state: GameState, ai_player_ids: set[UUID]) -> GameState:
    """Keep opening hands for AI players until setup needs a human choice."""
    while state.setup.pending_player is not None:
        player = state.corp if state.setup.pending_player is PlayerRole.CORP else state.runner
        if player.player_id not in ai_player_ids:
            break
        state = apply_command(
            state,
            player.player_id,
            cmd.ChooseMulligan(MulliganDecision.KEEP),
        )
    return state


def choose_random_command(
    actions: Sequence[cmd.GameCommand | cmd.CardSelection | cmd.DiscardSelection],
) -> cmd.GameCommand:
    """Sample a legal action and materialize any required random card selection."""
    command = random.choice(actions)
    if isinstance(command, (cmd.CardSelection, cmd.DiscardSelection)):
        count = command.required_count
        if isinstance(command, cmd.CardSelection):
            if command.up_to:
                count = random.randint(0, count)
            elif command.optional:
                count = random.choice((0, count))
        selected = tuple(random.sample(command.eligible_card_instance_ids, count))
        return (
            cmd.SelectEffectCards(selected)
            if isinstance(command, cmd.CardSelection)
            else cmd.DiscardCards(selected)
        )
    return command


def advance_ai_decisions(
    state: GameState,
    ai_player_ids: set[UUID],
    card_definitions: Mapping[str, CardDefinition] | None = None,
) -> GameState:
    """Choose random legal moves until a human decision or game end.

    Opening hands are kept. Each later decision samples the engine's legal
    actions, including passes and declines. Selection descriptors are converted
    into commands with randomly sampled eligible cards, without enumerating all
    combinations. Every submitted command still goes through the engine.
    """
    state = advance_ai_setup(state, ai_player_ids)
    while state.winner is None and state.turn is not None:
        role = pending_decision(state, card_definitions).player
        player = state.corp if role is PlayerRole.CORP else state.runner
        if player.player_id not in ai_player_ids:
            break

        actions = legal_actions_for(state, player.player_id, card_definitions)
        if not actions:
            break

        command = choose_random_command(actions)
        state = apply_command(state, player.player_id, command, card_definitions)
    return state
