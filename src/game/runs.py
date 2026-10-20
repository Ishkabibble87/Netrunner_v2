"""Resolve runs through authored ice encounters, breaches, and access (CR 6–7).

Each run window carries its continuation so passing resumes the exact timing
step. Event success effects and access limits are scoped to their initiating run.
Installed access reactions resolve before mid-access choices.
"""

from collections.abc import Mapping
from random import SystemRandom

from content.cards.models import (
    CardDefinition,
    CardType,
    ChooseEffect,
    DrawCards,
    GiveTags,
    InitiateRun,
    TakeCredits,
)

from .card_movement import trash_installed_card
from .commands import AccessCard, ContinueRun, FinishAccess, JackOut, RunServer
from .effects import resolve_effect
from .installation import agenda_points, remove_empty_remotes
from .models import (
    CardZone,
    CentralServer,
    GameState,
    PlayerRole,
    RuleFact,
    RunState,
    ServerArea,
    ZoneLocation,
)
from .payments import runner_funds, spend_runner_credits
from .priority import open_paid_window
from .reactions import (
    collect_fact,
    emit_fact,
    make_reaction,
    open_reactions,
    resolve_instructions,
    source_present,
)
from .setup import IllegalCommand


def runnable_servers(state: GameState):
    """Offer targets using public ice positions, never hidden identities."""
    return tuple(state.servers)


def candidates(state: GameState) -> tuple[AccessCard, ...]:
    """Return legal choices without exposing HQ or R&D instance IDs."""
    run = state.run
    choices = []
    for card in state.card_instances.values():
        if card.instance_id in run.accessed_card_ids:
            continue
        location = card.location
        if (card.installed and location.server and location.server.server_id == run.server_id
                and location.server.area is ServerArea.ROOT):
            choices.append(AccessCard(card.instance_id))
        elif (run.server_id is CentralServer.ARCHIVES and location.zone is CardZone.DISCARD_PILE
              and location.player_id == state.corp.player_id):
            choices.append(AccessCard(card.instance_id))
    if run.central_accesses < run.access_limit:
        zone = {CentralServer.HQ: CardZone.HAND, CentralServer.RD: CardZone.DECK}.get(run.server_id)
        if zone and any(c.instance_id not in run.accessed_card_ids
                        and c.location.zone is zone and c.location.player_id == state.corp.player_id
                        for c in state.card_instances.values()):
            choices.append(AccessCard(run.server_id.value))
    return tuple(choices)


def run_actions(state: GameState, definitions: Mapping[str, CardDefinition]):
    """Describe only the Runner's current run decision."""
    run = state.run
    if state.run.phase in {"movement", "subroutine_choice"}:
        return (ContinueRun(), JackOut())
    if state.run.phase == "breach":
        return candidates(state)
    card = state.card_instances[state.run.current_card_id]
    definition = definitions.get(card.definition_id)
    choices = [FinishAccess()]
    if (definition and definition.trash_cost is not None
            and not run.cannot_steal_or_trash
            and card.location.zone is not CardZone.DISCARD_PILE
            and runner_funds(state) >= definition.trash_cost):
        choices.append(FinishAccess(trash=True))
    return tuple(choices)


def validate_run_target(state: GameState, instruction: InitiateRun, server_id) -> None:
    """Validate authored restrictions before paying a run action's costs."""
    if (server_id not in state.servers
            or (instruction.servers is not None and server_id not in instruction.servers)
            or (instruction.unrun_this_turn and server_id in state.turn.servers_run)):
        raise IllegalCommand("Choose a legal server not excluded by this run ability")


def run_fact(state: GameState, kind: str, *, card_instance_id=None) -> RuleFact:
    """Snapshot rules context without exposing accessed card identities to clients."""
    run = state.run
    return RuleFact(type=kind, player=PlayerRole.RUNNER, card_instance_id=card_instance_id,
                    run_id=run.run_id, server_id=run.server_id, access_count=run.access_count,
                    agendas_stolen=run.agendas_stolen, source_card_id=run.source_card_id,
                    source_ability_id=run.source_ability_id)


def cleanup_run(state: GameState) -> None:
    """Release context only after run-end reactions, then resume the initiating action."""
    source_event_id = state.run.source_event_id
    state.window = None
    state.run = None
    if source_event_id:
        event = state.card_instances[source_event_id]
        event.counters.clear()
        event.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=state.runner.player_id)
    remove_empty_remotes(state)


def end_run(state: GameState, definitions: Mapping[str, CardDefinition]) -> None:
    """Expire run-only funds before run-end reactions (CR 6.9.6), retaining context."""
    run = state.run
    if run.phase == "ended":
        return
    if state.window and state.window.kind == "paid":
        state.window = None
    run.current_card_id = None
    run.current_ice_id = None
    run.strength_boosts = {}
    run.broken_subroutine_indices = []
    run.bad_publicity_credits = 0
    if run.source_event_id:
        state.card_instances[run.source_event_id].counters.clear()
    run.phase = "ended"
    run.pending_step = "cleanup"
    if state.winner is None:
        reactions = collect_fact(state, definitions, run_fact(state, "run_end"))
        reactions.extend(run.delayed_reactions)
        run.delayed_reactions = []
        open_reactions(state, definitions, reactions)
    else:
        state.suspended_windows.clear()
        cleanup_run(state)


def advance_run(state: GameState, definitions: Mapping[str, CardDefinition]) -> None:
    """Resume automatic run steps after the entire nested reaction stack settles."""
    while state.winner is None and state.run and state.window is None and state.run.pending_step and not state.memory_trash_pending:
        step = state.run.pending_step
        state.run.pending_step = None
        if step == "success":
            begin_success(state, definitions)
        elif step == "mid_access":
            emit_fact(state, definitions, run_fact(state, "mid_access", card_instance_id=state.run.current_card_id))
        elif step == "breach":
            begin_breach(state, definitions)
        elif step == "encounter_window":
            run_window(state, "resolve_subroutines", definitions, rez_allowed=False)
        elif step == "subroutines":
            resolve_subroutines(state, definitions)
        elif step == "uninstalled_ice":
            enter_movement(state, definitions)
        elif step == "finish_access":
            state.run.current_card_id = None
            state.run.phase = "breach"
            if not candidates(state):
                end_run(state, definitions)
        elif step == "access_choices":
            if not candidates(state):
                end_run(state, definitions)
        elif step == "cleanup":
            cleanup_run(state)


def resolve_run_command(state: GameState, command, definitions: Mapping[str, CardDefinition]) -> None:
    """Mutate a working snapshot after validation; the engine resumes the turn."""
    if isinstance(command, RunServer):
        if command.server_id not in runnable_servers(state):
            raise IllegalCommand("Choose an existing server")
        state.runner.clicks_remaining -= 1
        start_run(state, command.server_id, definitions)
        return
    run = state.run
    if command not in run_actions(state, definitions):
        raise IllegalCommand("That choice is not available during this run")
    if isinstance(command, JackOut):
        end_run(state, definitions)
        return
    if isinstance(command, ContinueRun):
        if run.phase == "subroutine_choice":
            run.phase = "encounter"
            resolve_subroutines(state, definitions)
            return
        run_window(state, "approach_next", definitions)  # CR 6.9.4e.
        return
    elif isinstance(command, AccessCard):
        if command.target in {"hq", "rd"}:
            zone = CardZone.HAND if command.target == "hq" else CardZone.DECK
            cards = [c for c in state.card_instances.values()
                     if c.instance_id not in run.accessed_card_ids
                     and c.location.zone is zone and c.location.player_id == state.corp.player_id]
            card = (SystemRandom().choice(cards) if zone is CardZone.HAND
                    else min(cards, key=lambda c: c.location.deck_position))
            run.central_accesses += 1
        else:
            card = state.card_instances[command.target]
        run.access_count += 1
        run.accessed_card_ids.append(card.instance_id)
        run.visible_card_ids.append(card.instance_id)
        run.current_card_id = card.instance_id
        run.phase = "access"
        run.pending_step = "mid_access"
        emit_fact(state, definitions, run_fact(state, "access", card_instance_id=card.instance_id), auto_single=True)
        return
    elif isinstance(command, FinishAccess):
        card = state.card_instances[run.current_card_id]
        definition = definitions.get(card.definition_id)
        if definition is None:
            raise IllegalCommand("Card definitions are required to resolve access")
        if command.trash:
            if (run.cannot_steal_or_trash or definition.trash_cost is None or card.location.zone is CardZone.DISCARD_PILE):
                raise IllegalCommand("This accessed card cannot be trashed for its trash cost")
            spend_runner_credits(state, definition.trash_cost)
            trash_accessed_card(state, definitions)
            return
        can_steal = definition.card_type is CardType.AGENDA and not run.cannot_steal_or_trash
        if can_steal:
            was_deck = card.location.zone is CardZone.DECK
            run.agendas_stolen += 1
            state.card_instances[card.instance_id] = card.model_copy(update={
                "location": ZoneLocation(zone=CardZone.SCORE_AREA, player_id=state.runner.player_id),
                "installed": False, "rezzed": False, "face_up": True, "counters": {},
                "loaded_counter_types": set(), "controller_id": None, "ability_last_used_turn": {},
            })
            if was_deck:
                reindex_corp_deck(state)
            if agenda_points(state, state.runner, definitions) >= 7:
                state.winner = PlayerRole.RUNNER
                end_run(state, definitions)
                return
        run.pending_step = "finish_access"
        if can_steal:
            emit_fact(state, definitions, run_fact(state, "steal", card_instance_id=card.instance_id), auto_single=True)


def reindex_corp_deck(state):
    from .card_movement import reindex_deck
    reindex_deck(state, state.corp.player_id)


def trash_accessed_card(state, definitions):
    """Share basic/effect trashing, persistence and hosted-card cleanup (CR 7.1.5)."""
    run = state.run
    if run.cannot_steal_or_trash:
        raise IllegalCommand("The Runner cannot trash Corp cards for the remainder of this run")
    card = state.card_instances[run.current_card_id]
    if card.location.zone is CardZone.DISCARD_PILE:
        raise IllegalCommand("Cannot trash a card already in Archives")
    definition = definitions[card.definition_id]
    was_deck = card.location.zone is CardZone.DECK
    was_root = card.location.server is not None
    if card.rezzed and any(a.persistent for a in definition.triggered_abilities):
        run.persistent_sources[card.instance_id] = card.location.model_copy(deep=True)
    trash_installed_card(state, card)
    if was_deck:
        reindex_corp_deck(state)
    if was_root and run.server_id is CentralServer.ARCHIVES:
        run.accessed_card_ids.remove(card.instance_id)
    run.pending_step = "finish_access"
    emit_fact(state, definitions, run_fact(state, "access_trash", card_instance_id=card.instance_id))


def approach_server(state, definitions):
    """Resolve approach abilities before success, after the last rez window."""
    state.run.pending_step = "success"
    emit_fact(state, definitions, run_fact(state, "approach_server"))
    advance_run(state, definitions)


def begin_success(state: GameState, definitions: Mapping[str, CardDefinition]) -> None:
    """Resolve successful-run abilities before the breach begins (CR 6.9.5a)."""
    run = state.run
    run.successful = True
    state.turn.successful_run = True
    run.phase = "success"
    run.pending_step = "breach"
    fact = run_fact(state, "run_success")
    reactions = collect_fact(state, definitions, fact)
    source_id = run.source_card_id
    instructions = []
    if run.successful_draw:
        instructions.append(DrawCards(type="draw_cards", amount=run.successful_draw))
    if run.successful_take_credits:
        instructions.append(TakeCredits(type="take_credits", amount=run.successful_take_credits))
    if source_id and instructions:
        card = state.card_instances[source_id]
        reaction = make_reaction(state, card, run.source_ability_id or "run_success",
                                f"{definitions[card.definition_id].title}: successful run",
                                tuple(instructions), fact)
        reaction.source_location = run.source_location
        # A moved source cannot supply a delayed payout. Installed sources can also
        # disappear at the empty-counter checkpoint without losing the run origin.
        if source_present(state, reaction):
            reactions.append(reaction)
    open_reactions(state, definitions, reactions, auto_single=True)


def begin_breach(state: GameState, definitions: Mapping[str, CardDefinition]) -> None:
    """Open breach reactions separately, before offering any access candidates."""
    run = state.run
    run.access_limit += run.additional_accesses
    if run.additional_accesses_per_virus:
        source = state.card_instances.get(run.source_card_id)
        if source and source.installed and source.location == run.source_location:
            run.access_limit += source.counters.get("virus", 0)
    run.phase = "breach"
    if run.server_id is CentralServer.ARCHIVES:
        for card in state.card_instances.values():
            if card.location.zone is CardZone.DISCARD_PILE and card.owner_id == state.corp.player_id:
                card.face_up = True
    run.pending_step = "access_choices"
    emit_fact(state, definitions, run_fact(state, "breach"))


def start_run(state, server_id, definitions, *, source_event_id=None,
              successful_draw=0, additional_accesses=0, source_card_id=None,
              source_ability_id=None, successful_take_credits=0, additional_accesses_per_virus=False):
    """Enter the same run timing structure from a basic action or event."""
    source_card_id = source_card_id or source_event_id
    state.run = RunState(server_id=server_id, source_event_id=source_event_id,
                         source_card_id=source_card_id, source_ability_id=source_ability_id,
                         source_location=state.card_instances[source_card_id].location.model_copy(deep=True) if source_card_id else None,
                         successful_take_credits=successful_take_credits,
                         successful_draw=successful_draw, additional_accesses=additional_accesses,
                         additional_accesses_per_virus=additional_accesses_per_virus,
                         bad_publicity_credits=state.corp.bad_publicity, phase="initiation")
    state.turn.servers_run.add(state.run.server_id)
    ice = sorted((c for c in state.card_instances.values() if c.installed and c.location.server
                  and c.location.server.server_id == server_id
                  and c.location.server.area is ServerArea.ICE),
                 key=lambda c: c.location.server.ice_distance_from_server, reverse=True)
    state.run.remaining_ice_ids = [c.instance_id for c in ice]
    run_window(state, "approach", definitions)  # CR 6.9.1e.


def run_window(state, continuation, definitions, *, rez_allowed=True):
    """Suspend a run, or immediately resume after automatic passes."""
    open_paid_window(state, continuation, definitions, rez_allowed=rez_allowed)
    if state.window is None:
        resume_run(state, continuation, definitions)


def resume_run(state, continuation, definitions):
    """Advance approach, encounter, movement, and success (CR 6.9)."""
    run = state.run
    if continuation in {"approach", "approach_next"}:
        if run.remaining_ice_ids:
            run.current_ice_id = run.remaining_ice_ids.pop(0)
            run.phase = "approach"
            run_window(state, "encounter", definitions)
        elif continuation == "approach":
            enter_movement(state, definitions)
        else:
            approach_server(state, definitions)
    elif continuation == "encounter":
        ice = state.card_instances[run.current_ice_id]
        if ice.rezzed:
            run.phase = "encounter"
            run.broken_subroutine_indices = []
            run.next_subroutine = 0
            run.strength_boosts = {}
            run.pending_step = "encounter_window"
            emit_fact(state, definitions, run_fact(state, "encounter", card_instance_id=ice.instance_id), auto_single=True)
        else:
            enter_movement(state, definitions)
    elif continuation == "resolve_subroutines":
        resolve_subroutines(state, definitions)
    elif continuation == "breach":
        approach_server(state, definitions)


def enter_movement(state, definitions):
    """Pass ice, expire encounter effects, then offer the jack-out decision."""
    state.run.phase = "movement"
    state.run.current_ice_id = None
    state.run.strength_boosts = {}
    state.run.broken_subroutine_indices = []
    run_window(state, "movement", definitions, rez_allowed=False)  # CR 6.9.4b.


def resolve_subroutines(state, definitions):
    """Resolve unbroken subroutines, resuming after tag and jack-out decisions.

    The cursor advances before a choice so declining to jack out cannot replay
    damage. Damage packets return their trashed cards for Diviner's cost check.
    """
    run = state.run
    ice = state.card_instances[run.current_ice_id]
    subroutines = definitions[ice.definition_id].subroutines or ()
    while run.next_subroutine < len(subroutines):
        index = run.next_subroutine
        run.next_subroutine += 1
        if index in run.broken_subroutine_indices:
            continue
        subroutine = subroutines[index]
        if subroutine.type == "end_run":
            end_run(state, definitions)
            return
        trashed = []
        if any(isinstance(i, (GiveTags, ChooseEffect)) or i.type == "select_cards" for i in subroutine.instructions):
            run.pending_step = "subroutines"
            resolve_instructions(state, definitions, state.corp, ice, subroutine.instructions,
                                 f"subroutine_{index}", subroutine.label)
            if state.window is not None or state.run is None or state.run.phase != "encounter":
                return
            run.pending_step = None
            continue
        for instruction in subroutine.instructions:
            trashed.extend(resolve_effect(state, state.corp, instruction, ice, definitions=definitions) or ())
            if state.winner is not None:
                return
        if subroutine.end_run_if_odd_cost:
            for card in trashed:
                definition = definitions[card.definition_id]
                cost = definition.play_cost if definition.play_cost is not None else definition.install_cost
                if cost is not None and cost % 2:
                    end_run(state, definitions)
                    return
        if subroutine.offer_jack_out:
            run.phase = "subroutine_choice"
            return
    enter_movement(state, definitions)
