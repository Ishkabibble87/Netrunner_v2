"""Resolve authenticated player commands and derive their legal choices.

The engine owns command legality and state changes. API and CLI layers may
display legal actions, but every submitted command is validated again here.
"""

from uuid import UUID

from .card_movement import draw_from_deck
from .commands import (
    ChooseMulligan,
    DiscardCards,
    DiscardSelection,
    DrawCard,
    GainCredit,
    GameCommand,
    PurgeVirusCounters,
    RemoveTag,
)
from .models import (
    CardZone,
    GameState,
    MulliganDecision,
    PendingDecision,
    PlayerRole,
    PlayerState,
    TurnPhase,
    ZoneLocation,
)
from .setup import IllegalCommand, choose_mulligan
from .turns import advance_turn, hand_cards, maximum_hand_size


def legal_actions_for(state: GameState, player_id: UUID) -> tuple[GameCommand | DiscardSelection, ...]:
    """Return commands currently available to one player, in menu order.

    This is a player-specific projection of the current state. It is not a
    substitute for validation when a command is submitted.
    """
    player = _player_for_id(state, player_id)
    if player is None or state.winner is not None:
        return ()

    if state.setup.pending_player is not None:
        pending = _player_for_role(state, state.setup.pending_player)
        if pending.player_id != player_id:
            return ()
        return (
            ChooseMulligan(MulliganDecision.KEEP),
            ChooseMulligan(MulliganDecision.MULLIGAN),
        )

    if (state.turn is not None and state.turn.phase is TurnPhase.DISCARD
            and state.turn.active_player is player.role):
        cards = hand_cards(state, player)
        count = len(cards) - maximum_hand_size(player)
        if count > 0:
            return (DiscardSelection(tuple(card.instance_id for card in cards), count),)
        return ()

    if (
        state.turn is None
        or state.turn.phase is not TurnPhase.ACTION
        or state.turn.active_player is not player.role
        or player.clicks_remaining <= 0
    ):
        return ()

    actions: list[GameCommand] = [GainCredit(), DrawCard()]
    if player.role is PlayerRole.RUNNER:
        if player.tags > 0 and player.credits >= 2:
            actions.append(RemoveTag())
    elif player.clicks_remaining >= 3:
        actions.append(PurgeVirusCounters())
    return tuple(actions)


def apply_command(state: GameState, player_id: UUID, command: GameCommand) -> GameState:
    """Validate and apply one command, returning a new validated snapshot."""
    if isinstance(command, ChooseMulligan):
        return choose_mulligan(state, player_id, command)

    player = _player_for_id(state, player_id)
    if player is None:
        raise IllegalCommand("Player is not part of this game")
    if isinstance(command, DiscardCards):
        if (state.winner is not None or state.setup.pending_player is not None
                or state.turn is None or state.turn.phase is not TurnPhase.DISCARD
                or state.turn.active_player is not player.role):
            raise IllegalCommand("Discard only during your discard decision")
        eligible = {card.instance_id for card in hand_cards(state, player)}
        required = len(eligible) - maximum_hand_size(player)
        selected = command.card_instance_ids
        if (required <= 0 or len(selected) != required
                or len(set(selected)) != required or not set(selected) <= eligible):
            raise IllegalCommand(f"Choose exactly {required} distinct cards from your hand")
        result = state.model_copy(deep=True)
        for instance_id in selected:
            card = result.card_instances[instance_id]
            card.face_up = player.role is PlayerRole.RUNNER
            card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=player_id)
        advance_turn(result)
        return GameState.model_validate(result.model_dump())
    _validate_action_window(state, player)

    if isinstance(command, GainCredit):
        result = state.model_copy(deep=True)
        _player_for_role(result, player.role).clicks_remaining -= 1
        _player_for_role(result, player.role).credits += 1
    elif isinstance(command, DrawCard):
        result = state.model_copy(deep=True)
        active_player = _player_for_role(result, player.role)
        active_player.clicks_remaining -= 1
        drew_all = draw_from_deck(result, player_id, 1)
        if not drew_all and player.role is PlayerRole.CORP:
            # CR 1.7.2c: the Corp loses when required to draw from an empty R&D.
            result.winner = PlayerRole.RUNNER
    elif isinstance(command, RemoveTag):
        if player.role is not PlayerRole.RUNNER:
            raise IllegalCommand("Only the Runner can remove a tag")
        if player.tags < 1:
            raise IllegalCommand("The Runner has no tags to remove")
        if player.credits < 2:
            raise IllegalCommand("Removing a tag costs 2 credits")
        result = state.model_copy(deep=True)
        result.runner.clicks_remaining -= 1
        result.runner.credits -= 2
        result.runner.tags -= 1
    elif isinstance(command, PurgeVirusCounters):
        if player.role is not PlayerRole.CORP:
            raise IllegalCommand("Only the Corp can purge virus counters")
        if player.clicks_remaining < 3:
            raise IllegalCommand("Purging virus counters costs 3 clicks")
        result = state.model_copy(deep=True)
        result.corp.clicks_remaining -= 3
        # The registry includes hosted cards, so one pass purges counters everywhere.
        for card in result.card_instances.values():
            card.counters.pop("virus", None)
    else:
        raise IllegalCommand("Unsupported command")

    advance_turn(result)
    return GameState.model_validate(result.model_dump())


def _validate_action_window(state: GameState, player: PlayerState) -> None:
    """Reject actions outside the authenticated player's action window."""
    if state.winner is not None:
        raise IllegalCommand("The game has already ended")
    if state.setup.pending_player is not None:
        raise IllegalCommand("Opening-hand decisions are still pending")
    if state.turn is None or state.turn.phase is not TurnPhase.ACTION:
        raise IllegalCommand("Basic actions are available only during an action phase")
    if state.turn.active_player is not player.role:
        raise IllegalCommand(f"Waiting for {state.turn.active_player.value} to act")
    if player.clicks_remaining < 1:
        raise IllegalCommand("At least 1 click is required")


def _player_for_id(state: GameState, player_id: UUID) -> PlayerState | None:
    """Find one of the two players by the identity authenticated by the session."""
    return next(
        (player for player in (state.corp, state.runner) if player.player_id == player_id),
        None,
    )


def _player_for_role(state: GameState, role: PlayerRole) -> PlayerState:
    """Return the player state for a role."""
    return state.corp if role is PlayerRole.CORP else state.runner


def pending_decision(state: GameState) -> PendingDecision | None:
    """Describe the public decision independently of either player's view."""
    if state.winner is not None:
        return None
    if state.setup.pending_player is not None:
        return PendingDecision(player=state.setup.pending_player, type="choose_opening_hand")
    if state.turn is None:
        return None
    player = _player_for_role(state, state.turn.active_player)
    if state.turn.phase is TurnPhase.DISCARD:
        return PendingDecision(player=player.role, type="discard_to_hand_limit",
                               cards_to_discard=len(hand_cards(state, player)) - maximum_hand_size(player))
    return PendingDecision(player=player.role, type="choose_action")
