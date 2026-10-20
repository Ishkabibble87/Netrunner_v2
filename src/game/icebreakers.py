"""Validate and resolve the first authored paid breaker abilities (CR 3.9.5).

All costs and targets are validated before mutation. Strength boosts and broken
subroutines live on the encounter, not on permanent card instances.
"""

from collections.abc import Mapping
from itertools import combinations

from content.cards.models import CardDefinition, PaidAbility

from .commands import UseCardAbility
from .models import CardInstance, GameState, PlayerRole, PlayerState
from .modifiers import installed_icebreaker_count
from .payments import runner_funds, spend_runner_credits
from .setup import IllegalCommand


def effective_strength(state: GameState, card: CardInstance, definition: CardDefinition,
                       definitions: Mapping[str, CardDefinition]) -> int:
    """Combine live static bonuses with strength paid for this encounter."""
    strength = definition.strength or 0
    if card.installed and card.face_up and definition.strength_per_icebreaker:
        strength += definition.strength_per_icebreaker * installed_icebreaker_count(
            state, card.controller_id, definitions)
    if card.rezzed and card.location.server and isinstance(card.location.server.server_id, int):
        strength += definition.remote_strength_bonus
    if state.run and state.run.phase == "encounter":
        strength += state.run.strength_boosts.get(card.instance_id, 0)
    return strength


def paid_ability_amount(state: GameState, card: CardInstance, ability: PaidAbility,
                        definitions: Mapping[str, CardDefinition]) -> int:
    """Evaluate a boost when paid; stored encounter boosts keep that fixed value."""
    if ability.amount_per_icebreaker:
        return ability.amount * installed_icebreaker_count(state, card.controller_id, definitions)
    return ability.amount


def validate_paid_ability(state: GameState, player: PlayerState, command: UseCardAbility,
                          definitions: Mapping[str, CardDefinition]) -> PaidAbility:
    """Require priority, a controlled active source, funds, and valid targets."""
    card = state.card_instances.get(command.card_instance_id)
    definition = definitions.get(card.definition_id) if card else None
    ability = next((a for a in definition.paid_abilities if a.ability_id == command.ability_id), None) if definition else None
    if (player.role is not PlayerRole.RUNNER or card is None or not card.installed
            or not card.face_up or card.controller_id != player.player_id or ability is None):
        raise IllegalCommand("Choose an active supported paid ability you control")
    if state.window is None or state.window.kind != "paid" or state.window.player is not player.role:
        raise IllegalCommand("Use paid abilities only while you have priority")
    if command.server_id is not None:
        raise IllegalCommand("Paid abilities do not choose a server")
    funds = runner_funds(state)
    if funds < ability.credit_cost:
        raise IllegalCommand("Not enough credits to use this ability")
    targets = command.subroutine_indices
    if ability.type == "boost_strength":
        if targets:
            raise IllegalCommand("Strength abilities do not target subroutines")
        return ability
    run = state.run
    if run is None or run.phase != "encounter" or run.current_ice_id is None:
        raise IllegalCommand("Interface abilities require an ice encounter")
    ice = state.card_instances[run.current_ice_id]
    ice_definition = definitions[ice.definition_id]
    if ("Icebreaker" not in definition.subtypes or ability.subtype not in ice_definition.subtypes
            or effective_strength(state, card, definition, definitions) < effective_strength(state, ice, ice_definition, definitions)):
        raise IllegalCommand("The icebreaker must match the ice subtype and strength")
    if (not targets or len(targets) > ability.amount or len(set(targets)) != len(targets)
            or any(i < 0 or i >= len(ice_definition.subroutines or ())
                   or i in run.broken_subroutine_indices for i in targets)):
        raise IllegalCommand("Choose distinct unbroken subroutines within the ability's limit")
    return ability


def paid_actions(state: GameState, player: PlayerState,
                 definitions: Mapping[str, CardDefinition]) -> list[UseCardAbility]:
    """Enumerate the small set of supported breaker target selections."""
    choices = []
    run = state.run
    for card in state.card_instances.values():
        definition = definitions.get(card.definition_id)
        for ability in definition.paid_abilities if definition else ():
            selections = [()]
            if ability.type == "break_subroutines":
                if not run or run.phase != "encounter":
                    continue
                ice_definition = definitions[state.card_instances[run.current_ice_id].definition_id]
                available = [i for i in range(len(ice_definition.subroutines or ()))
                             if i not in run.broken_subroutine_indices]
                selections = [selection for n in range(1, min(ability.amount, len(available)) + 1)
                              for selection in combinations(available, n)]
            for selection in selections:
                command = UseCardAbility(card.instance_id, ability.ability_id, selection)
                try:
                    validate_paid_ability(state, player, command, definitions)
                except IllegalCommand:
                    continue
                choices.append(command)
    return choices


def use_paid_ability(state: GameState, player: PlayerState, command: UseCardAbility,
                     definitions: Mapping[str, CardDefinition]) -> None:
    """Pay from the run fund first; boosts outside an encounter expire at once."""
    ability = validate_paid_ability(state, player, command, definitions)
    spend_runner_credits(state, ability.credit_cost)
    if ability.type == "break_subroutines":
        state.run.broken_subroutine_indices.extend(command.subroutine_indices)
    elif state.run and state.run.phase == "encounter":
        boosts = state.run.strength_boosts
        card = state.card_instances[command.card_instance_id]
        amount = paid_ability_amount(state, card, ability, definitions)
        boosts[command.card_instance_id] = boosts.get(command.card_instance_id, 0) + amount
