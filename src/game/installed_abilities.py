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
    InitiateRun,
)

from .card_movement import trash_installed_card
from .commands import UseCardAbility
from .models import (
    CardInstance,
    GameState,
    PlayerRole,
    PlayerState,
    RuleFact,
)
from .modifiers import rez_cost
from .reactions import emit_fact, resolve_sequence
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
                 definitions: Mapping[str, CardDefinition], *, during_effect: bool = False,
                 ignore_costs: bool = False) -> tuple[CardInstance, CardDefinition]:
    """Reject unsupported cards, insufficient funds, and invalid rez windows."""
    card, definition = controlled_card(state, player, instance_id, definitions)
    if (player.role is not PlayerRole.CORP or definition.side is not CardSide.CORP
            or definition.card_type not in {CardType.ASSET, CardType.UPGRADE, CardType.ICE}
            or card.rezzed or definition.rez_instructions is None):
        raise IllegalCommand("Choose an unrezzed Corp card with supported abilities")
    if definition.card_type is CardType.ICE and definition.subroutines is None:
        raise IllegalCommand("Choose ice with supported subroutines")
    if during_effect:
        if not ignore_costs and (rez_cost(state, definition) is None or player.credits < rez_cost(state, definition)):
            raise IllegalCommand("Not enough credits to rez this card")
        return card, definition
    if state.window is None or state.window.kind != "paid" or state.window.player is not player.role:
        raise IllegalCommand("Rez only while you have priority in an eligible paid ability window")
    if not state.window.rez_allowed:
        raise IllegalCommand("Cards cannot be rezzed in this paid ability window")
    if definition.card_type is CardType.ICE and (
            state.run is None or state.run.phase != "approach"
            or state.run.current_ice_id != instance_id or definition.subroutines is None):
        raise IllegalCommand("Rez only the supported ice currently being approached")
    cost = rez_cost(state, definition)
    if cost is None or player.credits < cost:
        raise IllegalCommand("Not enough credits to rez this card")
    return card, definition


def rez_card(state, player, instance_id, definitions, *, during_effect=False, ignore_costs=False):
    """Share payment, activation and rez reactions with effect-directed rezzing."""
    card, definition = validate_rez(state, player, instance_id, definitions,
                                    during_effect=during_effect, ignore_costs=ignore_costs)
    if not ignore_costs:
        player.credits -= rez_cost(state, definition)
    card.face_up = True
    card.rezzed = True
    resolve_lifecycle(state, player, card, definitions, trigger="rez")


def resolve_lifecycle(state: GameState, player: PlayerState, card: CardInstance,
                      definitions: Mapping[str, CardDefinition], *, trigger: str) -> None:
    """Collect lifecycle effects alongside all abilities observing that occurrence."""
    emit_fact(state, definitions, RuleFact(type=trigger, player=player.role,
              card_instance_id=card.instance_id), auto_single=True)


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
    run = next((i for i in ability.instructions if isinstance(i, InitiateRun)), None)
    if run:
        from .runs import validate_run_target
        if ability.instructions != (run,) or run.hosted_credits:
            raise IllegalCommand("Supported installed run actions require a single run instruction without an event fund")
        if player.role is not PlayerRole.RUNNER:
            raise IllegalCommand("Only the Runner can initiate a run")
        validate_run_target(state, run, command.server_id)
    elif command.server_id is not None:
        raise IllegalCommand("This ability does not choose a server")
    return card, definition, ability


def use_ability(state: GameState, player: PlayerState, command: UseCardAbility,
                definitions: Mapping[str, CardDefinition]) -> None:
    """Pay an action once, then resolve its effects or enter its run timing."""
    card, _, ability = validate_ability(state, player, command, definitions)
    player.clicks_remaining -= ability.click_cost
    card.ability_last_used_turn[ability.ability_id] = state.turn.number
    run = next((i for i in ability.instructions if isinstance(i, InitiateRun)), None)
    if run:
        from .runs import start_run
        start_run(state, command.server_id, definitions, source_card_id=card.instance_id,
                  source_ability_id=ability.ability_id,
                  successful_take_credits=run.successful_take_credits,
                  successful_draw=run.successful_draw, additional_accesses=run.additional_accesses,
                  ice_rez_cost_increase=run.ice_rez_cost_increase,
                  additional_accesses_per_virus=run.additional_accesses_per_virus)
    else:
        counters = None
        if ability.trash_cost:
            # CR 9.5.5: preserve hosted counters privately while the paid effect resolves.
            counters = dict(card.counters)
            trash_installed_card(state, card)
        resolve_sequence(state, definitions, player, card, ability, set_aside_counters=counters)
