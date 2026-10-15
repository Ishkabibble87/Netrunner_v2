"""Small rules-engine slice for turn setup and the shared credit/draw actions."""

from uuid import UUID

from game.commands import BasicAction
from game.models import (
    CardZone,
    GameEndReason,
    GameOutcome,
    GameState,
    PlayerState,
    PlayerRole,
    TurnPhase,
    ZoneLocation,
)


CORP_CLICKS_PER_TURN = 3


class GameRuleError(ValueError):
    """A player command is not legal in the current game state."""


def begin_initial_turn(state: GameState) -> None:
    """Resolve the Corp's mandatory first draw and enter its action phase."""

    if (
        state.turn.active_player is not PlayerRole.CORP
        or state.turn.phase is not TurnPhase.DRAW
    ):
        raise GameRuleError("The initial turn must begin with the Corp draw phase")
    corp = state.corp
    corp.clicks_remaining = CORP_CLICKS_PER_TURN
    if not _draw_one(state, corp.player_id):
        state.outcome = GameOutcome(
            winner=PlayerRole.RUNNER,
            reason=GameEndReason.DECKED_OUT,
        )
        return
    state.turn.phase = TurnPhase.ACTION


def legal_actions(state: GameState, player_id: UUID) -> list[BasicAction]:
    """Return the basic actions this player can currently take."""

    player = _player_for_id(state, player_id)
    if (
        state.outcome is not None
        or state.turn.phase is not TurnPhase.ACTION
        or player.role is not state.turn.active_player
        or player.clicks_remaining <= 0
    ):
        return []
    return [BasicAction.GAIN_CREDIT, BasicAction.DRAW_CARD]


def resolve_basic_action(state: GameState, player_id: UUID, action: BasicAction) -> None:
    """Validate and resolve a basic action against the authoritative state."""

    player = _player_for_id(state, player_id)
    if state.outcome is not None:
        raise GameRuleError("The game has already ended")
    if player.role is not state.turn.active_player:
        raise GameRuleError("It is not your turn")
    if state.turn.phase is not TurnPhase.ACTION:
        raise GameRuleError("Basic actions are only available during the action phase")
    if player.clicks_remaining < 1:
        raise GameRuleError("You do not have a click available")
    if not isinstance(action, BasicAction):
        raise GameRuleError("Unknown basic action")
    if action not in legal_actions(state, player_id):
        raise GameRuleError(f"The {action.value.replace('_', ' ')} action is not legal now")

    player.clicks_remaining -= 1
    if action is BasicAction.GAIN_CREDIT:
        player.credits += 1
    elif action is BasicAction.DRAW_CARD:
        if not _draw_one(state, player_id):
            winner = PlayerRole.RUNNER if player.role is PlayerRole.CORP else PlayerRole.CORP
            state.outcome = GameOutcome(
                winner=winner,
                reason=GameEndReason.DECKED_OUT,
            )


def _draw_one(state: GameState, player_id: UUID) -> bool:
    deck = sorted(
        (
            card
            for card in state.card_instances.values()
            if card.location.zone is CardZone.DECK
            and card.location.player_id == player_id
        ),
        key=lambda card: card.location.deck_position or 0,
    )
    if not deck:
        return False

    top_card = deck[0]
    top_card.location = ZoneLocation(zone=CardZone.HAND, player_id=player_id)
    for position, card in enumerate(deck[1:], start=1):
        card.location = ZoneLocation(
            zone=CardZone.DECK,
            player_id=player_id,
            deck_position=position,
        )
    return True


def _player_for_id(state: GameState, player_id: UUID) -> PlayerState:
    if state.corp.player_id == player_id:
        return state.corp
    if state.runner.player_id == player_id:
        return state.runner
    raise GameRuleError("Player is not part of this game")
