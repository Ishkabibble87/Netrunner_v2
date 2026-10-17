"""Resume the supported turn steps until an action, discard, or priority decision.

The step is a continuation: completing a scoring/reaction window resumes exactly
where the turn paused. Windows with no supported choices for either side are
passed automatically. Rezzing, turn triggers, and regular paid abilities follow
in later slices; scoring windows stay visible regardless of hidden eligibility.
"""

from .card_movement import draw_from_deck
from .models import (
    CardZone,
    GameState,
    PlayerRole,
    PlayerState,
    PriorityWindow,
    TurnPhase,
    TurnState,
)


def maximum_hand_size(player: PlayerState) -> int:
    """Apply the base hand limit and currently modeled core damage (CR 5.5.3)."""
    return 5 - player.core_damage if player.role is PlayerRole.RUNNER else 5


def hand_cards(state: GameState, player: PlayerState):
    """Return physical cards in this player's hand in stable registry order."""
    return tuple(card for card in state.card_instances.values()
                 if card.location.zone is CardZone.HAND
                 and card.location.player_id == player.player_id)


def scoring_window(state: GameState) -> None:
    """Pause every eligible Corp window if any Corp card is installed.

    Testing scoreability here would reveal the identity of facedown cards.
    With no installed Corp cards, neither player has a supported window option.
    """
    if state.turn.active_player is PlayerRole.CORP and any(
            c.installed and c.controller_id == state.corp.player_id for c in state.card_instances.values()):
        state.window = PriorityWindow(kind="paid", player=PlayerRole.CORP)


def advance_turn(state: GameState) -> None:
    """Mutate a working snapshot through automatic steps, stopping at a decision."""
    while state.winner is None and state.turn is not None and state.window is None:
        turn = state.turn
        player = state.corp if turn.active_player is PlayerRole.CORP else state.runner
        if turn.step == "start":
            turn.step = "mandatory_draw" if player.role is PlayerRole.CORP else "before_action"
            scoring_window(state)  # CR 5.6.1b, before the Corp's mandatory draw.
        elif turn.step == "mandatory_draw":
            if not draw_from_deck(state, player.player_id, 1):
                state.winner = PlayerRole.RUNNER
                return
            turn.phase = TurnPhase.ACTION
            turn.step = "before_action"
        elif turn.step == "before_action":
            turn.step = "action"
            scoring_window(state)  # CR 5.6.2a, including after the final click.
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
        else:
            player.clicks_remaining = 0
            next_role = PlayerRole.RUNNER if player.role is PlayerRole.CORP else PlayerRole.CORP
            state.turn = TurnState(number=turn.number + 1, active_player=next_role,
                                   phase=TurnPhase.DRAW if next_role is PlayerRole.CORP else TurnPhase.ACTION,
                                   step="start")
            incoming = state.runner if next_role is PlayerRole.RUNNER else state.corp
            incoming.clicks_remaining = 4 if next_role is PlayerRole.RUNNER else 3
