"""Resume the supported turn steps until an action, discard, or priority decision.

The step is a continuation: completing a scoring/reaction window resumes exactly
where the turn paused. Windows stay visible regardless of hidden eligibility;
players without supported paid options pass automatically.
"""

from .card_movement import draw_from_deck
from .models import (
    CardZone,
    GameState,
    PlayerRole,
    PlayerState,
    RuleFact,
    TurnPhase,
    TurnState,
)
from .priority import open_paid_window


def maximum_hand_size(player: PlayerState) -> int:
    """Apply the base hand limit and currently modeled core damage (CR 5.5.3)."""
    return 5 - player.core_damage if player.role is PlayerRole.RUNNER else 5


def hand_cards(state: GameState, player: PlayerState):
    """Return physical cards in this player's hand in stable registry order."""
    return tuple(card for card in state.card_instances.values()
                 if card.location.zone is CardZone.HAND
                 and card.location.player_id == player.player_id)


def advance_turn(state: GameState, definitions=None) -> None:
    """Mutate a working snapshot through automatic steps, stopping at a decision."""
    from .reactions import collect_turn_end, emit_fact

    while state.winner is None and state.turn is not None and state.window is None and state.run is None:
        if state.pending_action_fact is not None:
            fact = state.pending_action_fact
            state.pending_action_fact = None
            emit_fact(state, definitions or {}, fact)
            if state.window is not None:
                return
        turn = state.turn
        player = state.corp if turn.active_player is PlayerRole.CORP else state.runner
        if turn.step == "start":
            turn.step = "turn_begin"
            open_paid_window(state, definitions=definitions)  # CR 5.6.1b / 5.7.1b, before turn-begin steps.
        elif turn.step == "turn_begin":
            turn.step = "mandatory_draw" if player.role is PlayerRole.CORP else "before_action"
            emit_fact(state, definitions or {}, RuleFact(type="turn_begin", player=player.role))
        elif turn.step == "mandatory_draw":
            if not draw_from_deck(state, player.player_id, 1):
                state.winner = PlayerRole.RUNNER
                return
            turn.phase = TurnPhase.ACTION
            turn.step = "before_action"
        elif turn.step == "before_action":
            turn.step = "action"
            open_paid_window(state, definitions=definitions)  # CR 5.6.2a / 5.7.1e, including after the final click.
        elif turn.step == "action":
            if player.clicks_remaining > 0:
                return
            turn.phase = TurnPhase.DISCARD
            turn.step = "discard"
        elif turn.step == "discard":
            limit = maximum_hand_size(player)
            if limit < 0:
                state.winner = PlayerRole.CORP
                return
            if len(hand_cards(state, player)) > limit:
                return
            turn.step = "end"
            open_paid_window(state, definitions=definitions)  # CR 5.6.3b / 5.7.2b, after discarding.
        elif turn.step == "end":
            player.clicks_remaining = 0
            turn.step = "next_turn"
            # Both conditions are met at the same timing point (CR 5.6.3d/5.7.2d).
            collect_turn_end(state, definitions or {}, player.role)
        else:
            next_role = PlayerRole.RUNNER if player.role is PlayerRole.CORP else PlayerRole.CORP
            state.turn = TurnState(number=turn.number + 1, active_player=next_role,
                                   phase=TurnPhase.DRAW if next_role is PlayerRole.CORP else TurnPhase.ACTION,
                                   step="start")
            incoming = state.runner if next_role is PlayerRole.RUNNER else state.corp
            incoming.clicks_remaining = 4 if next_role is PlayerRole.RUNNER else 3
