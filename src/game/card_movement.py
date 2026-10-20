"""Shared card-zone movements used by setup and in-game actions."""

from random import SystemRandom
from uuid import UUID

from .models import CardZone, GameState, ZoneLocation


def swap_installed_ice(state, first, second):
    """Exchange installed locations simultaneously, preserving hosts and counters (CR 8.8.3).

    Pending run positions follow their new occupants. During an encounter the
    Runner instead follows the encountered ice to its new location (CR 6.2.7d).
    """
    replacements = {first.instance_id: second.instance_id, second.instance_id: first.instance_id}
    first.location, second.location = second.location, first.location
    run = state.run
    if run is None:
        return
    run.remaining_ice_ids = [replacements.get(i, i) for i in run.remaining_ice_ids]
    if run.current_ice_id in replacements:
        if run.phase == "approach":
            run.current_ice_id = replacements[run.current_ice_id]
        elif run.phase == "encounter":
            encountered = state.card_instances[run.current_ice_id]
            position = encountered.location.server
            run.server_id = position.server_id
            run.remaining_ice_ids = [c.instance_id for c in sorted(
                (c for c in state.card_instances.values() if c.installed and c.location.server
                 and c.location.server.area.value == "ice" and c.location.server.server_id == run.server_id
                 and c.location.server.ice_distance_from_server < position.ice_distance_from_server),
                key=lambda c: c.location.server.ice_distance_from_server, reverse=True)]


def shuffle_into_deck(state, player_id, cards=(), *, rng=None):
    """Shuffle returned cards with the deck, maintaining contiguous positions."""
    deck = [c for c in state.card_instances.values()
            if c.location.zone is CardZone.DECK and c.owner_id == player_id]
    deck.extend(c for c in cards if c not in deck)
    (rng or SystemRandom()).shuffle(deck)
    for position, card in enumerate(deck, 1):
        card.location = ZoneLocation(zone=CardZone.DECK, player_id=player_id, deck_position=position)
        card.face_up = None


def reindex_deck(state, player_id):
    """Close gaps after removing searched or accessed cards from either deck."""
    deck = sorted((c for c in state.card_instances.values()
                   if c.location.zone is CardZone.DECK and c.owner_id == player_id),
                  key=lambda c: c.location.deck_position)
    for position, card in enumerate(deck, 1):
        card.location.deck_position = position


def trash_installed_card(state, card, *, face_up=True):
    """Trash a source and its hosted descendants, clearing counters and locations."""
    if state.run:
        run = state.run
        run.remaining_ice_ids = [i for i in run.remaining_ice_ids if i != card.instance_id]
        if run.current_ice_id == card.instance_id:
            run.current_ice_id = None
            run.phase = "movement"
            run.strength_boosts = {}
            run.broken_subroutine_indices = []
            run.pending_step = "uninstalled_ice"
        # Leaving play makes this installation an invalid delayed-trash target.
        state.run.delayed_reactions = [r for r in state.run.delayed_reactions
                                       if r.card_instance_id != card.instance_id]
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
