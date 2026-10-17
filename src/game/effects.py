"""Execute the small set of immediate, ordered instructions supported by cards.

Effects run against a private working snapshot. Draws reuse the same movement
helper as basic actions; no printed prose is interpreted at runtime.
"""

from content.cards.models import DrawCards, GainCredits, LoseClickIfAny, PlayInstruction
from .card_movement import draw_from_deck
from .models import GameState, PlayerRole, PlayerState


def resolve_effect(state: GameState, player: PlayerState, instruction: PlayInstruction) -> None:
    """Resolve one effect fully before the next instruction or turn transition."""
    if isinstance(instruction, GainCredits):
        player.credits += instruction.amount
    elif isinstance(instruction, DrawCards):
        complete = draw_from_deck(state, player.player_id, instruction.amount)
        if not complete and player.role is PlayerRole.CORP:
            state.winner = PlayerRole.RUNNER
    elif isinstance(instruction, LoseClickIfAny):
        if player.clicks_remaining > 0:
            player.clicks_remaining -= 1
    else:
        raise ValueError("Unsupported play instruction")
