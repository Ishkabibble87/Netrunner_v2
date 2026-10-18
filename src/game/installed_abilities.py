"""Resolve the supported lifecycle and action abilities of installed economy.

Definitions supply effects and limits; instances hold counters and usage. Sole
mandatory install/rez reactions resolve immediately, before another decision.
"""

from collections.abc import Mapping
from uuid import UUID

from content.cards.models import (
    ActionAbility,
    CardDefinition,
    CardSide,
    CardType,
    PlayInstruction,
)

from .commands import UseCardAbility
from .effects import resolve_effect
from .models import (
    CardInstance,
    CardZone,
    GameState,
    PlayerRole,
    PlayerState,
    ZoneLocation,
)
from .setup import IllegalCommand


def controlled_card(state: GameState, player: PlayerState, instance_id: UUID,
                    definitions: Mapping[str, CardDefinition]) -> tuple[CardInstance, CardDefinition]:
    """Require an installed physical copy controlled by the acting player."""
    card = state.card_instances.get(instance_id)
    definition = definitions.get(card.definition_id) if card else None
    if (card is None or definition is None or not card.installed
            or card.controller_id != player.player_id):
        raise IllegalCommand("Choose an installed card you control")
    return card, definition


def validate_rez(state: GameState, player: PlayerState, instance_id: UUID,
                 definitions: Mapping[str, CardDefinition]) -> tuple[CardInstance, CardDefinition]:
    """Reject unsupported cards, insufficient funds, and invalid rez windows."""
    card, definition = controlled_card(state, player, instance_id, definitions)
    if (player.role is not PlayerRole.CORP or definition.side is not CardSide.CORP
            or definition.card_type not in {CardType.ASSET, CardType.UPGRADE, CardType.ICE}
            or card.rezzed or definition.rez_instructions is None):
        raise IllegalCommand("Choose an unrezzed Corp card with supported abilities")
    if state.window is None or state.window.kind != "paid" or state.window.player is not player.role:
        raise IllegalCommand("Rez only while you have priority in an eligible paid ability window")
    if not state.window.rez_allowed:
        raise IllegalCommand("Cards cannot be rezzed in this paid ability window")
    if definition.card_type is CardType.ICE and (
            state.run is None or state.run.phase != "approach"
            or state.run.current_ice_id != instance_id or definition.subroutines is None):
        raise IllegalCommand("Rez only the supported ice currently being approached")
    if definition.rez_cost is None or player.credits < definition.rez_cost:
        raise IllegalCommand("Not enough credits to rez this card")
    return card, definition


def resolve_lifecycle(state: GameState, player: PlayerState, card: CardInstance,
                      instructions: tuple[PlayInstruction, ...] | None, definition: CardDefinition) -> None:
    """Resolve a sole mandatory reaction and its empty-card checkpoint."""
    for instruction in instructions or ():
        resolve_effect(state, player, instruction, card)
    trash_if_empty(state, card, definition)


def validate_ability(state: GameState, player: PlayerState, command: UseCardAbility,
                     definitions: Mapping[str, CardDefinition]) -> tuple[CardInstance, CardDefinition, ActionAbility]:
    """Check activity, ownership, clicks, and limits before paying any cost."""
    card, definition = controlled_card(state, player, command.card_instance_id, definitions)
    ability = next((a for a in definition.action_abilities if a.ability_id == command.ability_id), None)
    if (ability is None or not card.face_up
            or (definition.side is CardSide.CORP and not card.rezzed)):
        raise IllegalCommand("Choose an active supported ability")
    if command.subroutine_indices:
        raise IllegalCommand("Action abilities do not target subroutines")
    if player.clicks_remaining < ability.click_cost:
        raise IllegalCommand("Not enough clicks to use this ability")
    if ability.once_per_turn and card.ability_last_used_turn.get(ability.ability_id) == state.turn.number:
        raise IllegalCommand("This ability has already been used this turn")
    return card, definition, ability


def use_ability(state: GameState, player: PlayerState, command: UseCardAbility,
                definitions: Mapping[str, CardDefinition]) -> None:
    """Pay once and transfer credits before checking automatic trashing."""
    card, definition, ability = validate_ability(state, player, command, definitions)
    player.clicks_remaining -= ability.click_cost
    card.ability_last_used_turn[ability.ability_id] = state.turn.number
    resolve_lifecycle(state, player, card, ability.instructions, definition)


def trash_if_empty(state: GameState, card: CardInstance, definition: CardDefinition) -> None:
    """Apply the active 'when empty' condition (CR 9.6, 10.3)."""
    if (definition.trash_when_empty and card.installed
            and (definition.side is CardSide.RUNNER or card.rezzed)
            and card.counters.get("credit", 0) == 0):
        state.card_instances[card.instance_id] = card.model_copy(update={
            "location": ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=card.owner_id),
            "installed": False, "rezzed": False, "face_up": True,
            "controller_id": None, "counters": {}, "ability_last_used_turn": {},
        })
