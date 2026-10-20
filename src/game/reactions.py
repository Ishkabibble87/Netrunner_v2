"""Order conditional abilities and resume instructions across nested checkpoints.

Facts are short-lived inputs, not a persisted event log. Each reaction window
owns a fixed batch; a checkpoint inside an instruction opens a nested batch.
The stack retains both paid priority and the next instruction of its parent.
"""

from collections.abc import Mapping
from uuid import uuid4

from content.cards.models import (
    ActionAbility,
    CardDefinition,
    PlayInstruction,
    TrashSource,
)

from .commands import ResolveReaction
from .effects import resolve_effect
from .models import (
    CardInstance,
    CardZone,
    EffectContinuation,
    GameState,
    PendingReaction,
    PlayerRole,
    PlayerState,
    PriorityWindow,
    RuleFact,
)
from .setup import IllegalCommand


def active(card: CardInstance, definition: CardDefinition) -> bool:
    """Identities and face-up Runner cards or rezzed Corp cards are active."""
    return (card.location.zone is CardZone.IDENTITY or
            (card.installed and card.face_up and
             (definition.side.value == "runner" or card.rezzed)))


def make_reaction(
    state: GameState, card: CardInstance, ability_id: str, label: str,
    instructions: tuple[PlayInstruction, ...], fact: RuleFact, *, optional: bool = False,
    once_per_turn: bool = False, condition: str | None = None,
) -> PendingReaction:
    player = state.corp if card.controller_id == state.corp.player_id else state.runner
    return PendingReaction(
        reaction_id=uuid4(), card_instance_id=card.instance_id,
        ability_id=ability_id, label=label, player=player.role, fact=fact,
        source_location=card.location.model_copy(deep=True),
        instructions=instructions, optional=optional, once_per_turn=once_per_turn, condition=condition,
    )


def record_occurrence(state: GameState, key: str) -> int:
    """Record occurrences regardless of whether a listening card is active."""
    state.turn.occurrences[key] = state.turn.occurrences.get(key, 0) + 1
    return state.turn.occurrences[key]


def collect_fact(state: GameState, definitions: Mapping[str, CardDefinition], fact: RuleFact) -> list[PendingReaction]:
    """Collect a fixed batch, including existing install, rez and score effects."""
    key = f"{fact.type}:{fact.player}"
    if fact.type == "breach":
        key += f":{fact.server_id}"
    occurrence = record_occurrence(state, key)
    reactions = []
    for card in state.card_instances.values():
        definition = definitions.get(card.definition_id)
        if definition is None:
            continue
        if card.instance_id == fact.card_instance_id:
            instructions = getattr(definition, f"{fact.type}_instructions", None)
            if instructions:
                reactions.append(make_reaction(state, card, fact.type,
                    f"{definition.title}: {fact.type}", instructions, fact))
        if not active(card, definition):
            continue
        for ability in definition.triggered_abilities:
            if ability.trigger != fact.type:
                continue
            controller = state.corp if card.controller_id == state.corp.player_id else state.runner
            if ability.scope == "controller" and controller.role is not fact.player:
                continue
            if ability.scope == "self" and card.instance_id != fact.card_instance_id:
                continue
            if ability.condition == "previously_unadvanced" and fact.previous_advancement != 0:
                continue
            if ability.servers is not None and fact.server_id not in ability.servers:
                continue
            if ability.first_each_turn and occurrence != 1:
                continue
            if ability.once_per_turn and card.ability_last_used_turn.get(ability.ability_id) == state.turn.number:
                continue
            reactions.append(make_reaction(state, card, ability.ability_id,
                f"{definition.title}: {ability.label}", ability.instructions, fact,
                optional=ability.optional or ability.once_per_turn,
                once_per_turn=ability.once_per_turn, condition=ability.condition))
    return reactions


def emit_fact(state: GameState, definitions: Mapping[str, CardDefinition], fact: RuleFact, *, auto_single: bool = False) -> None:
    open_reactions(state, definitions, collect_fact(state, definitions, fact), auto_single=auto_single)


def collect_turn_end(state: GameState, definitions: Mapping[str, CardDefinition], role: PlayerRole) -> None:
    """Turn end and discard phase end share a single reaction window."""
    reactions = [r for kind in ("turn_end", "discard_end")
                 for r in collect_fact(state, definitions, RuleFact(type=kind, player=role))]
    open_reactions(state, definitions, reactions)


def open_reactions(state: GameState, definitions: Mapping[str, CardDefinition],
                   reactions: list[PendingReaction], *, auto_single: bool = False) -> None:
    """Suspend the current window; only an unambiguous mandatory effect auto-runs."""
    if not reactions or state.winner is not None:
        return
    state.suspended_windows.append(state.window)
    state.window = PriorityWindow(kind="reaction", player=state.turn.active_player,
                                  reactions=reactions)
    if auto_single and len(reactions) == 1 and not reactions[0].optional:
        reaction = reactions[0]
        state.window.reactions = []
        state.window.resolving = EffectContinuation(reaction=reaction)
    settle_reactions(state, definitions)


def checkpoint(state: GameState, definitions: Mapping[str, CardDefinition]) -> None:
    """Collect empty-counter conditions once per pending/resolving source ability."""
    windows = [state.window, *state.suspended_windows]
    occupied = {(r.card_instance_id, r.ability_id) for w in windows if w
                for r in [*w.reactions, *([w.resolving.reaction] if w.resolving else [])]}
    reactions = []
    for card in state.card_instances.values():
        definition = definitions.get(card.definition_id)
        if (not definition or not active(card, definition)
                or "credit" not in card.loaded_counter_types or card.counters.get("credit", 0)):
            continue
        player = state.corp if card.controller_id == state.corp.player_id else state.runner
        fact = RuleFact(type="empty", player=player.role, card_instance_id=card.instance_id)
        if definition.trash_when_empty and (card.instance_id, "empty") not in occupied:
            reactions.append(make_reaction(state, card, "empty", f"{definition.title}: trash when empty",
                                           (TrashSource(),), fact))
        for ability in definition.triggered_abilities:
            if ability.trigger == "empty" and (card.instance_id, ability.ability_id) not in occupied:
                if ability.once_per_turn and card.ability_last_used_turn.get(ability.ability_id) == state.turn.number:
                    continue
                reactions.append(make_reaction(state, card, ability.ability_id,
                    f"{definition.title}: {ability.label}", ability.instructions, fact,
                    optional=ability.optional or ability.once_per_turn, once_per_turn=ability.once_per_turn))
    open_reactions(state, definitions, reactions, auto_single=True)


def source_present(state: GameState, reaction: PendingReaction) -> bool:
    card = state.card_instances[reaction.card_instance_id]
    # Remote display positions may change when a different card is trashed.
    return (card.location.zone == reaction.source_location.zone
            and card.location.player_id == reaction.source_location.player_id)


def pending_valid(state: GameState, definitions: Mapping[str, CardDefinition], reaction: PendingReaction) -> bool:
    card = state.card_instances[reaction.card_instance_id]
    if not source_present(state, reaction):
        return False
    if (reaction.fact.type != "score"
            and not (reaction.fact.type == "run_success" and state.run
                     and state.run.source_event_id == card.instance_id)
            and not active(card, definitions[card.definition_id])):
        return False
    return not (reaction.once_per_turn and
                card.ability_last_used_turn.get(reaction.ability_id) == state.turn.number)


def settle_reactions(state: GameState, definitions: Mapping[str, CardDefinition]) -> None:
    """Run automatic instructions, stopping for the next player's reaction choice."""
    while state.window and state.window.kind == "reaction" and state.winner is None:
        window = state.window
        if window.resolving:
            continuation = window.resolving
            reaction = continuation.reaction
            if continuation.next_instruction == len(reaction.instructions):
                window.resolving = None
                continue
            instruction = reaction.instructions[continuation.next_instruction]
            continuation.next_instruction += 1
            player = state.corp if reaction.player is PlayerRole.CORP else state.runner
            condition_met = (reaction.condition != "facedown_archives" or any(
                c.location.zone is CardZone.DISCARD_PILE and c.owner_id == player.player_id
                and c.face_up is False for c in state.card_instances.values()))
            if condition_met:
                source = state.card_instances[reaction.card_instance_id] if source_present(state, reaction) else None
                before = state.model_dump() if reaction.once_per_turn else None
                resolve_effect(state, player, instruction, source, reaction.fact)
                if reaction.once_per_turn and before != state.model_dump() and source_present(state, reaction):
                    state.card_instances[reaction.card_instance_id].ability_last_used_turn[reaction.ability_id] = state.turn.number
            checkpoint(state, definitions)
            continue
        window.reactions = [r for r in window.reactions if pending_valid(state, definitions, r)]
        if any(r.player is window.player for r in window.reactions):
            return
        if not window.opponent_passed:
            window.player = PlayerRole.RUNNER if window.player is PlayerRole.CORP else PlayerRole.CORP
            window.opponent_passed = True
        else:
            state.window = state.suspended_windows.pop()
    if state.winner is not None:
        state.window = None
        state.suspended_windows.clear()


def resolve_reaction(state: GameState, definitions: Mapping[str, CardDefinition], command: ResolveReaction) -> None:
    """Resolve a unique pending instance or decline an optional instance."""
    window = state.window
    matches = [r for r in window.reactions if r.player is window.player
               and r.card_instance_id == command.card_instance_id
               and (command.reaction_id is None or r.reaction_id == command.reaction_id)]
    if len(matches) != 1:
        raise IllegalCommand("Choose a unique pending reaction")
    reaction = matches[0]
    if command.decline and not reaction.optional:
        raise IllegalCommand("Mandatory reactions cannot be declined")
    window.reactions.remove(reaction)
    if not command.decline:
        window.resolving = EffectContinuation(reaction=reaction)
    settle_reactions(state, definitions)


def pass_reactions(state: GameState, definitions: Mapping[str, CardDefinition]) -> None:
    """Passing declines remaining optional abilities for this player only."""
    window = state.window
    if any(r.player is window.player and not r.optional for r in window.reactions):
        raise IllegalCommand("Resolve mandatory reactions before passing")
    window.reactions = [r for r in window.reactions if r.player is not window.player]
    settle_reactions(state, definitions)


def resolve_sequence(state: GameState, definitions: Mapping[str, CardDefinition],
                     player: PlayerState, card: CardInstance, ability: ActionAbility) -> None:
    """Start action effects without a choice, preserving instruction checkpoints."""
    reaction = make_reaction(state, card, ability.ability_id, ability.label,
        ability.instructions, RuleFact(type="action", player=player.role, card_instance_id=card.instance_id))
    open_reactions(state, definitions, [reaction], auto_single=True)


def basic_draw_amount(state: GameState, player: PlayerState, definitions: Mapping[str, CardDefinition]) -> int:
    """Apply only the first basic-draw replacement, without an interrupt window."""
    occurrence = record_occurrence(state, f"basic_draw:{player.role}")
    if occurrence != 1:
        return 1
    return next((definition.first_basic_draw_amount
                 for card in state.card_instances.values()
                 if card.controller_id == player.player_id
                 and (definition := definitions.get(card.definition_id))
                 and active(card, definition) and definition.first_basic_draw_amount), 1)
