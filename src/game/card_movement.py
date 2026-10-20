"""Shared card-zone movements used by setup and in-game actions."""

from uuid import UUID

from .models import CardZone, GameState, ZoneLocation


def trash_installed_card(state, card, *, face_up=True):
    """Trash a source and its hosted descendants, clearing counters and locations."""
    for hosted in tuple(state.card_instances.values()):
        if hosted.location.host_card_instance_id == card.instance_id:
            trash_installed_card(state, hosted)
    state.card_instances[card.instance_id] = card.model_copy(update={
        "installed": False, "rezzed": False, "face_up": face_up,
        "controller_id": None, "counters": {}, "loaded_counter_types": set(),
        "ability_last_used_turn": {},
        "location": ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=card.owner_id),
    })


def draw_from_deck(state: GameState, player_id: UUID, count: int) -> bool:
    """Move up to ``count`` top cards into a player's hand.

    Return whether the full requested count was available. The current rules
    engine has no draw-reaction window, so this helper performs the zone move
    directly and keeps the remaining deck positions contiguous.
    """
    if count < 0:
        raise ValueError("A draw count cannot be negative")
    deck = sorted(
        (
            card for card in state.card_instances.values()
            if card.location.zone is CardZone.DECK
            and card.location.player_id == player_id
        ),
        key=lambda card: card.location.deck_position,
    )
    for card in deck[:count]:
        card.location = ZoneLocation(zone=CardZone.HAND, player_id=player_id)
    for position, card in enumerate(deck[count:], start=1):
        card.location = ZoneLocation(
            zone=CardZone.DECK, player_id=player_id, deck_position=position,
        )
    return len(deck) >= count
