"""Execute the small set of immediate, ordered instructions supported by cards.

Effects run against a private working snapshot. Draws reuse the same movement
helper as basic actions; no printed prose is interpreted at runtime.
"""

from content.cards.models import (
    AdditionalAccesses,
    DerezHost,
    DrawCards,
    GainAccessCredits,
    GainCredits,
    GainCreditsPerVirus,
    LoadCredits,
    LoseClickIfAny,
    PlaceVirusCounters,
    PlayInstruction,
    TakeCredits,
    TrashSource,
)

from .card_movement import draw_from_deck, trash_installed_card
from .models import (
    CardInstance,
    GameState,
    PlayerRole,
    PlayerState,
    RuleFact,
)


def resolve_effect(state: GameState, player: PlayerState, instruction: PlayInstruction,
                   source: CardInstance | None = None, fact: RuleFact | None = None,
                   *, set_aside_counters: dict[str, int] | None = None) -> None:
    """Resolve one effect fully before the next instruction or turn transition."""
    if isinstance(instruction, PlaceVirusCounters):
        target = source if instruction.target == "self" else (
            state.card_instances.get(fact.card_instance_id) if fact else None)
        if target is not None and target.installed:
            target.counters["virus"] = target.counters.get("virus", 0) + instruction.amount
    elif isinstance(instruction, DerezHost):
        if source is not None and source.counters.get("virus", 0) >= instruction.minimum_virus_counters:
            host = state.card_instances.get(source.location.host_card_instance_id)
            if host is not None and host.installed and host.rezzed:
                host.rezzed = False
                host.face_up = False
    elif isinstance(instruction, GainCreditsPerVirus):
        counters = set_aside_counters if set_aside_counters is not None else (source.counters if source else {})
        player.credits += instruction.amount * counters.get("virus", 0)
    elif isinstance(instruction, TrashSource):
        if source is not None:
            trash_installed_card(state, source)
            if state.run is None:
                from .installation import remove_empty_remotes
                remove_empty_remotes(state)
    elif isinstance(instruction, (LoadCredits, TakeCredits)):
        if source is None:
            return  # A resolving ability cannot act on its source after a zone change.
        hosted = source.counters.get("credit", 0)
        if isinstance(instruction, LoadCredits):
            source.loaded_counter_types.add("credit")
            source.counters["credit"] = hosted + instruction.amount
        else:
            amount = hosted if instruction.amount == "all" else min(hosted, instruction.amount)
            source.counters["credit"] = hosted - amount
            player.credits += amount
    elif isinstance(instruction, GainAccessCredits):
        player.credits += fact.access_count if fact else 0
    elif isinstance(instruction, AdditionalAccesses):
        state.run.access_limit += instruction.amount
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
