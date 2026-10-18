"""Execute the small set of immediate, ordered instructions supported by cards.

Effects run against a private working snapshot. Draws reuse the same movement
helper as basic actions; no printed prose is interpreted at runtime.
"""

from content.cards.models import (
    DrawCards,
    GainCredits,
    LoadCredits,
    LoseClickIfAny,
    PlayInstruction,
    TakeCredits,
)

from .card_movement import draw_from_deck
from .models import CardInstance, GameState, PlayerRole, PlayerState


def resolve_effect(state: GameState, player: PlayerState, instruction: PlayInstruction,
                   source: CardInstance | None = None) -> None:
    """Resolve one effect fully before the next instruction or turn transition."""
    if isinstance(instruction, (LoadCredits, TakeCredits)):
        if source is None:
            raise ValueError("A hosted-credit instruction requires its source card")
        hosted = source.counters.get("credit", 0)
        if isinstance(instruction, LoadCredits):
            source.counters["credit"] = hosted + instruction.amount
        else:
            amount = min(hosted, instruction.amount)
            source.counters["credit"] = hosted - amount
            player.credits += amount
    elif isinstance(instruction, GainCredits):
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
