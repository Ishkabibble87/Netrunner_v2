"""Resolve scripted training-opponent choices through the game engine."""

from collections.abc import Mapping
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


def advance_ai_decisions(
    state: GameState,
    ai_player_ids: set[UUID],
    card_definitions: Mapping[str, CardDefinition] | None = None,
) -> GameState:
    """Run the training policy until a human decision or game end.

    The opponent keeps its opening hand, gains credits, and discards the first
    eligible excess cards. During a run the Corp rezzes affordable supported
    cards. Every choice goes through the authoritative engine.
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

        targets = next(
            (action for action in actions if isinstance(action, cmd.CardSelection)),
            None,
        )
        if targets is not None:
            command = cmd.SelectEffectCards(
                targets.eligible_card_instance_ids[:targets.required_count]
            )
        else:
            selection = next(
                (action for action in actions if isinstance(action, cmd.DiscardSelection)),
                None,
            )
            if selection is not None:
                command = cmd.DiscardCards(
                    selection.eligible_card_instance_ids[:selection.required_count]
                )
            else:
                command = next(
                    (
                        action for action in actions
                        if isinstance(
                            action,
                            (
                                cmd.ChooseEffectOption,
                                cmd.SelectEffectCards,
                                cmd.TrashPrograms,
                                cmd.ResolveReaction,
                                cmd.PassPriority,
                            ),
                        )
                    ),
                    cmd.GainCredit(),
                )

        if state.run is not None:
            rez_command = next(
                (action for action in actions if isinstance(action, cmd.RezCard)),
                None,
            )
            if rez_command is not None:
                command = rez_command

        state = apply_command(state, player.player_id, command, card_definitions)
    return state
