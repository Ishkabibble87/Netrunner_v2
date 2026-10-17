"""Advance automatic turn steps until a player must choose an action or discard.

Only the supported basic turn structure is resolved here. Priority windows,
recurring credits, and turn-triggered abilities will need explicit steps later.
"""

from .card_movement import draw_from_deck
from .models import CardZone, GameState, PlayerRole, PlayerState, TurnPhase, TurnState


def maximum_hand_size(player: PlayerState) -> int:
    """Apply the base hand limit and currently modeled core damage (CR 5.5.3)."""
    return 5 - player.core_damage if player.role is PlayerRole.RUNNER else 5


def hand_cards(state: GameState, player: PlayerState):
    """Return physical cards in this player's hand in stable registry order."""
    return tuple(card for card in state.card_instances.values()
                 if card.location.zone is CardZone.HAND
                 and card.location.player_id == player.player_id)


def advance_turn(state: GameState) -> None:
    """Mutate a working snapshot through automatic steps, stopping at a decision."""
    if state.winner is not None or state.turn is None:
        return
    turn = state.turn
    player = state.corp if turn.active_player is PlayerRole.CORP else state.runner
    if turn.phase is TurnPhase.ACTION:
        if player.clicks_remaining > 0:
            return
        turn.phase = TurnPhase.DISCARD
    if turn.phase is not TurnPhase.DISCARD:
        return
    limit = maximum_hand_size(player)
    # CR 5.5.4a: a negative Runner limit flatlines before discarding.
    if limit < 0:
        state.winner = PlayerRole.CORP
        return
    if len(hand_cards(state, player)) > limit:
        return
    player.clicks_remaining = 0
    next_role = PlayerRole.RUNNER if player.role is PlayerRole.CORP else PlayerRole.CORP
    # Count individual player turns, including the Runner's turn.
    state.turn = TurnState(number=turn.number + 1, active_player=next_role,
                           phase=TurnPhase.ACTION)
    incoming = state.runner if next_role is PlayerRole.RUNNER else state.corp
    incoming.clicks_remaining = 4 if next_role is PlayerRole.RUNNER else 3
    if next_role is PlayerRole.CORP and not draw_from_deck(state, incoming.player_id, 1):
        state.winner = PlayerRole.RUNNER
