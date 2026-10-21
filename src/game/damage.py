"""Resolve simultaneous net/meat damage and immediate flatline victory (CR 10.4).

A packet returns the trashed instances for follow-up effects such as Diviner.
Random selection is private until the selected cards enter the public heap.
No prevention or replacement window is needed by the currently supported cards.
"""

from random import SystemRandom

from .history import boundary
from .models import CardInstance, CardZone, GameState, PlayerRole, ZoneLocation


@boundary
def deal_damage(state: GameState, amount: int, damage_type: str) -> tuple[CardInstance, ...]:
    """Trash a random sample simultaneously, or end the game before further effects."""
    if amount < 0 or damage_type not in {"net", "meat"}:
        raise ValueError("Damage requires a nonnegative amount and a supported type")
    if state.winner is not None or amount == 0:
        return ()
    grip = [c for c in state.card_instances.values() if c.location.zone is CardZone.HAND
            and c.location.player_id == state.runner.player_id]
    # CR 1.7.2b / 10.4.4: lethal damage ends the game immediately.
    if amount > len(grip):
        state.winner = PlayerRole.CORP
        state.window = None
        state.suspended_windows.clear()
        state.pending_action_fact = None
        state.memory_trash_pending = False
        state.run = None
        return ()
    selected = SystemRandom().sample(grip, amount)
    for card in selected:
        card.face_up = True
        card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=state.runner.player_id)
    return tuple(selected)
