"""Resolve runs through authored ice encounters, breaches, and access (CR 6–7).

Each run window carries its continuation so passing resumes the exact timing
step. Event success effects and access limits are scoped to their initiating run.
Card-specific access abilities remain unsupported.
"""

from collections.abc import Mapping
from random import SystemRandom

from content.cards.models import CardDefinition, CardType

from .card_movement import draw_from_deck
from .commands import AccessCard, ContinueRun, FinishAccess, JackOut, RunServer
from .installation import agenda_points, remove_empty_remotes
from .models import (
    CardZone,
    CentralServer,
    GameState,
    PlayerRole,
    RunState,
    ServerArea,
    ZoneLocation,
)
from .payments import runner_funds, spend_runner_credits
from .priority import open_paid_window
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
    if state.run.phase == "movement":
        return (ContinueRun(), JackOut())
    if state.run.phase == "breach":
        return candidates(state)
    card = state.card_instances[state.run.current_card_id]
    definition = definitions.get(card.definition_id)
    choices = [FinishAccess()]
    if (definition and definition.trash_cost is not None
            and card.location.zone is not CardZone.DISCARD_PILE
            and runner_funds(state) >= definition.trash_cost):
        choices.append(FinishAccess(trash=True))
    return tuple(choices)


def end_run(state: GameState) -> None:
    """Discard the run fund and visibility, then clean up emptied remote rows."""
    source_event_id = state.run.source_event_id
    state.window = None
    state.run = None
    if source_event_id:
        event = state.card_instances[source_event_id]
        event.counters.clear()
        event.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=state.runner.player_id)
    remove_empty_remotes(state)


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
        end_run(state)
        return
    if isinstance(command, ContinueRun):
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
        run.accessed_card_ids.append(card.instance_id)
        run.visible_card_ids.append(card.instance_id)
        run.current_card_id = card.instance_id
        run.phase = "access"
        return
    elif isinstance(command, FinishAccess):
        card = state.card_instances[run.current_card_id]
        definition = definitions.get(card.definition_id)
        if definition is None:
            raise IllegalCommand("Card definitions are required to resolve access")
        if command.trash or definition.card_type is CardType.AGENDA:
            was_deck = card.location.zone is CardZone.DECK
            was_root = card.location.server is not None
            if command.trash:
                cost = definition.trash_cost
                spend_runner_credits(state, cost)
                destination = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=state.corp.player_id)
            else:
                destination = ZoneLocation(zone=CardZone.SCORE_AREA, player_id=state.runner.player_id)
            state.card_instances[card.instance_id] = card.model_copy(update={
                "location": destination, "installed": False, "rezzed": False,
                "face_up": True, "counters": {}, "controller_id": None, "ability_last_used_turn": {},
            })
            if was_deck:
                deck = sorted((c for c in state.card_instances.values()
                               if c.location.zone is CardZone.DECK and c.owner_id == state.corp.player_id),
                              key=lambda c: c.location.deck_position)
                for position, remaining in enumerate(deck, 1):
                    remaining.location.deck_position = position
            # CR 7.4.5: an upgrade trashed from Archives' root is a new candidate.
            if command.trash and was_root and run.server_id is CentralServer.ARCHIVES:
                run.accessed_card_ids.remove(card.instance_id)
            if agenda_points(state, state.runner, definitions) >= 7:
                state.winner = PlayerRole.RUNNER
                end_run(state)
                return
        run.current_card_id = None
        run.phase = "breach"
    if not candidates(state):
        end_run(state)


def begin_breach(state: GameState) -> None:
    """Resolve success effects before starting the breach (CR 6.7.4, 7.5.3)."""
    state.run.successful = True
    draw_from_deck(state, state.runner.player_id, state.run.successful_draw)
    state.run.access_limit += state.run.additional_accesses
    state.run.phase = "breach"
    if state.run.server_id is CentralServer.ARCHIVES:
        for card in state.card_instances.values():
            if card.location.zone is CardZone.DISCARD_PILE and card.owner_id == state.corp.player_id:
                card.face_up = True
    if not candidates(state):
        end_run(state)


def start_run(state, server_id, definitions, *, source_event_id=None,
              successful_draw=0, additional_accesses=0):
    """Enter the same run timing structure from a basic action or event."""
    state.run = RunState(server_id=server_id, source_event_id=source_event_id,
                         successful_draw=successful_draw, additional_accesses=additional_accesses,
                         bad_publicity_credits=state.corp.bad_publicity, phase="initiation")
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
            begin_breach(state)
    elif continuation == "encounter":
        ice = state.card_instances[run.current_ice_id]
        if ice.rezzed:
            run.phase = "encounter"
            run.broken_subroutine_indices = []
            run.strength_boosts = {}
            run_window(state, "resolve_subroutines", definitions, rez_allowed=False)
        else:
            enter_movement(state, definitions)
    elif continuation == "resolve_subroutines":
        definition = definitions[state.card_instances[run.current_ice_id].definition_id]
        for index, subroutine in enumerate(definition.subroutines or ()):
            if index not in run.broken_subroutine_indices and subroutine.type == "end_run":
                end_run(state)
                return
        enter_movement(state, definitions)
    elif continuation == "breach":
        begin_breach(state)


def enter_movement(state, definitions):
    """Pass ice, expire encounter effects, then offer the jack-out decision."""
    state.run.phase = "movement"
    state.run.current_ice_id = None
    state.run.strength_boosts = {}
    state.run.broken_subroutine_indices = []
    run_window(state, "movement", definitions, rez_allowed=False)  # CR 6.9.4b.
