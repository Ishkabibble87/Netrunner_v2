"""Validate and resolve authored breaker and virus encounter abilities (CR 3.9.5).

All costs and targets are validated before mutation. Strength boosts and broken
subroutines live on the encounter, not on permanent card instances.
"""

from collections.abc import Mapping
from itertools import combinations

from content.cards.models import CardDefinition, PaidAbility, TrashSource

from .commands import UseCardAbility
from .models import CardInstance, GameState, PlayerRole, PlayerState, RuleFact
from .modifiers import installed_icebreaker_count
from .payments import runner_funds, spend_runner_credits
from .reactions import make_reaction
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


def paid_ability_cost(state: GameState, ability: PaidAbility) -> int:
    """Use turn history for conditional credit costs, including displayed prices."""
    discount = ability.successful_run_discount if state.turn and state.turn.occurrences.get("run_success:runner", 0) else 0
    return max(0, ability.credit_cost - discount)


def paid_ability_label(state: GameState, card: CardInstance, ability: PaidAbility,
                       definitions: Mapping[str, CardDefinition]) -> str:
    """Render authored placeholders from the same values used during resolution."""
    cost = paid_ability_cost(state, ability)
    return ability.label.replace("{cost}", f"{cost} credit" + ("s" if cost != 1 else "")).replace(
        "+X", f"+{paid_ability_amount(state, card, ability, definitions)}")


def validate_paid_ability(state: GameState, player: PlayerState, command: UseCardAbility,
                          definitions: Mapping[str, CardDefinition]) -> PaidAbility:
    """Require priority, a controlled active source, funds, and valid targets."""
    card = state.card_instances.get(command.card_instance_id)
    definition = definitions.get(card.definition_id) if card else None
    ability = next((a for a in definition.paid_abilities if a.ability_id == command.ability_id), None) if definition else None
    if ability and ability.type == "break_self_subroutine":
        run = state.run
        if (player.role is not PlayerRole.RUNNER or not card.installed or not card.rezzed
                or not card.face_up or run is None or run.phase != "encounter"
                or run.current_ice_id != card.instance_id or command.server_id is not None
                or state.window is None or state.window.kind != "paid" or state.window.player is not player.role):
            raise IllegalCommand("Only the Runner can use this ability on the encountered ice with priority")
        if player.clicks_remaining < ability.click_cost or runner_funds(state) < paid_ability_cost(state, ability):
            raise IllegalCommand("Not enough clicks or credits to use this ability")
        validate_break_targets(state, command, definition, ability)
        return ability
    if ability and ability.type == "effects":
        if (card is None or not card.installed or not card.face_up or not card.rezzed
                or card.controller_id != player.player_id or command.subroutine_indices or command.server_id is not None
                or state.window is None or state.window.kind != "paid" or state.window.player is not player.role):
            raise IllegalCommand("Choose an active paid ability while you have priority")
        return ability
    if (player.role is not PlayerRole.RUNNER or card is None or not card.installed
            or not card.face_up or card.controller_id != player.player_id or ability is None):
        raise IllegalCommand("Choose an active supported paid ability you control")
    if state.window is None or state.window.kind != "paid" or state.window.player is not player.role:
        raise IllegalCommand("Use paid abilities only while you have priority")
    if command.server_id is not None:
        raise IllegalCommand("Paid abilities do not choose a server")
    funds = runner_funds(state)
    if funds < paid_ability_cost(state, ability):
        raise IllegalCommand("Not enough credits to use this ability")
    if player.clicks_remaining < ability.click_cost:
        raise IllegalCommand("Not enough clicks to use this ability")
    if card.counters.get("virus", 0) < ability.virus_cost:
        raise IllegalCommand("Not enough hosted virus counters")
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
    if ability.type == "lower_ice_strength":
        if targets:
            raise IllegalCommand("Strength abilities do not target subroutines")
        return ability
    if ability.type == "break_host_subroutine":
        if card.location.host_card_instance_id != ice.instance_id:
            raise IllegalCommand("This ability can only break subroutines on its host ice")
    elif ("Icebreaker" not in definition.subtypes or (ability.subtype is not None and ability.subtype not in ice_definition.subtypes)
            or effective_strength(state, card, definition, definitions) < effective_strength(state, ice, ice_definition, definitions)):
        raise IllegalCommand("The icebreaker must match the ice subtype and strength")
    validate_break_targets(state, command, ice_definition, ability)
    return ability


def validate_break_targets(state, command, ice_definition, ability):
    """All break abilities share distinct, unbroken, encounter-local targets."""
    targets = command.subroutine_indices
    if (not targets or len(targets) > ability.amount or len(set(targets)) != len(targets)
            or any(i < 0 or i >= len(ice_definition.subroutines or ())
                   or i in state.run.broken_subroutine_indices for i in targets)):
        raise IllegalCommand("Choose distinct unbroken subroutines within the ability's limit")


def paid_actions(state: GameState, player: PlayerState,
                 definitions: Mapping[str, CardDefinition]) -> list[UseCardAbility]:
    """Enumerate the small set of supported breaker target selections."""
    choices = []
    run = state.run
    for card in state.card_instances.values():
        definition = definitions.get(card.definition_id)
        for ability in definition.paid_abilities if definition else ():
            selections = [()]
            if ability.type in {"break_subroutines", "break_host_subroutine", "break_self_subroutine"}:
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
    if ability.type == "effects":
        from .card_movement import trash_installed_card
        from .installation import remove_empty_remotes
        from .models import CardZone, ZoneLocation
        from .reactions import resolve_instructions
        card = state.card_instances[command.card_instance_id]
        if ability.remove_source_cost:
            trash_installed_card(state, card)
            card = state.card_instances[card.instance_id]
            card.location = ZoneLocation(zone=CardZone.REMOVED_FROM_GAME)
            if state.run is None:
                remove_empty_remotes(state)
        resolve_instructions(state, definitions, player, card, ability.instructions,
                             ability.ability_id, ability.label, cost_paid=True)
        return
    spend_runner_credits(state, paid_ability_cost(state, ability))
    player.clicks_remaining -= ability.click_cost
    card = state.card_instances[command.card_instance_id]
    if ability.virus_cost:
        card.counters["virus"] -= ability.virus_cost
    if ability.type in {"break_subroutines", "break_host_subroutine", "break_self_subroutine"}:
        state.run.broken_subroutine_indices.extend(command.subroutine_indices)
        if ability.trash_at_run_end:
            # CR 9.6.13: the nested ability is created by breaking, not boosting.
            reaction = make_reaction(state, card, "trash_at_run_end",
                f"{definitions[card.definition_id].title}: trash at run end", (TrashSource(),),
                RuleFact(type="run_end", player=player.role, run_id=state.run.run_id))
            state.run.delayed_reactions.append(reaction)
    elif ability.type == "lower_ice_strength":
        boosts = state.run.strength_boosts
        ice_id = state.run.current_ice_id
        boosts[ice_id] = boosts.get(ice_id, 0) - ability.amount
    elif state.run and state.run.phase == "encounter":
        boosts = state.run.strength_boosts
        card = state.card_instances[command.card_instance_id]
        amount = paid_ability_amount(state, card, ability, definitions)
        boosts[command.card_instance_id] = boosts.get(command.card_instance_id, 0) + amount
