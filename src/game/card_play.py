"""Validate and resolve immediate event/operation play using authored definitions.

Common costs and the hand -> play area -> discard lifecycle belong here rather
than in individual cards. More complex future cards can add explicit handlers
without duplicating the common lifecycle or introducing API-specific rules.
"""

from collections.abc import Mapping
from uuid import UUID

from content.cards.models import CardDefinition, CardType

from .effects import resolve_effect
from .models import CardZone, GameState, PlayerRole, PlayerState, ZoneLocation
from .setup import IllegalCommand


def validate_card_play(state: GameState, player: PlayerState, instance_id: UUID,
                       definitions: Mapping[str, CardDefinition]) -> CardDefinition:
    """Validate card-specific legality; the caller validates the action window."""
    card = state.card_instances.get(instance_id)
    if (card is None or card.owner_id != player.player_id
            or card.controller_id not in (None, player.player_id)
            or card.location.zone is not CardZone.HAND
            or card.location.player_id != player.player_id):
        raise IllegalCommand("Choose a card from your own hand")
    definition = definitions.get(card.definition_id)
    expected_type = CardType.OPERATION if player.role is PlayerRole.CORP else CardType.EVENT
    if (definition is None or definition.card_type is not expected_type
            or definition.side.value != player.role.value):
        raise IllegalCommand("Only your side's events or operations can be played")
    if definition.play_instructions is None or definition.play_cost is None:
        raise IllegalCommand("This card's play ability is not implemented")
    if player.credits < definition.play_cost:
        raise IllegalCommand(f"Playing {definition.title} costs {definition.play_cost} credits")
    return definition


def play_card(state: GameState, player: PlayerState, instance_id: UUID,
              definitions: Mapping[str, CardDefinition]) -> None:
    """Pay and resolve one card on a copied snapshot (CR 8.6.7)."""
    definition = validate_card_play(state, player, instance_id, definitions)
    player.clicks_remaining -= 1
    card = state.card_instances[instance_id]
    card.face_up = True
    card.location = ZoneLocation(zone=CardZone.PLAY_AREA)
    player.credits -= definition.play_cost
    for instruction in definition.play_instructions:
        resolve_effect(state, player, instruction)
        if state.winner is not None:
            return
    card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=player.player_id)
