"""Corp and supported Runner installation, scoring, and server cleanup.

Only agendas with authored scoring instructions can be installed in this slice.
Other Corp permanents can be installed; only authored abilities can be activated.
No rule behavior is inferred from printed text.
"""

from collections.abc import Mapping
from uuid import UUID

from content.cards.models import CardDefinition, CardSide, CardType

from .card_movement import trash_installed_card
from .commands import InstallCard
from .installed_abilities import resolve_lifecycle
from .models import (
    CardInstance,
    CardZone,
    CentralServer,
    GameState,
    PlayerRole,
    PlayerState,
    ServerArea,
    ServerLocation,
    ZoneLocation,
)
from .modifiers import install_cost, memory_limit
from .setup import IllegalCommand


def installed_agenda(state: GameState, player: PlayerState, instance_id: UUID,
                     definitions: Mapping[str, CardDefinition]) -> tuple[CardInstance, CardDefinition]:
    """Require a controlled, installed agenda with implemented scoring behavior."""
    card = state.card_instances.get(instance_id)
    definition = definitions.get(card.definition_id) if card else None
    if (player.role is not PlayerRole.CORP or card is None or not card.installed
            or card.owner_id != player.player_id or card.controller_id != player.player_id
            or definition is None or definition.card_type is not CardType.AGENDA
            or definition.score_instructions is None):
        raise IllegalCommand("Choose an installed agenda you control with supported scoring abilities")
    return card, definition


def validate_install(state: GameState, player: PlayerState, command: InstallCard,
                     definitions: Mapping[str, CardDefinition]) -> tuple[CardDefinition, int]:
    """Validate destination, optional trash choices, and installation costs."""
    card = state.card_instances.get(command.card_instance_id)
    definition = definitions.get(card.definition_id) if card else None
    if player.role is PlayerRole.RUNNER:
        if (card is None or definition is None or card.owner_id != player.player_id
                or card.controller_id != player.player_id or card.location.zone is not CardZone.HAND
                or card.location.player_id != player.player_id or definition.side is not CardSide.RUNNER
                or definition.card_type not in {CardType.RESOURCE, CardType.PROGRAM, CardType.HARDWARE} or definition.install_instructions is None):
            raise IllegalCommand("Choose a supported resource, program, or hardware from your hand")
        if command.server_id is not None:
            raise IllegalCommand("Runner cards have no server destination")
        trash_ids = set(command.trash_card_instance_ids)
        programs = installed_programs(state, player, definitions)
        if (len(trash_ids) != len(command.trash_card_instance_ids)
                or not trash_ids <= {c.instance_id for c in programs}
                or (trash_ids and definition.card_type is not CardType.PROGRAM)):
            raise IllegalCommand("Choose distinct programs you control to trash when installing a program")
        if definition.card_type is CardType.PROGRAM:
            used = sum(definitions[c.definition_id].memory_cost or 0 for c in programs if c.instance_id not in trash_ids)
            if used + (definition.memory_cost or 0) > memory_limit(state, player, definitions):
                raise IllegalCommand("Trash installed programs to make enough memory available")
        if definition.install_cost is None or player.credits < install_cost(state, player, definition, definitions):
            raise IllegalCommand("Not enough credits to install this card")
        return definition, install_cost(state, player, definition, definitions)
    if (player.role is not PlayerRole.CORP or card is None
            or card.owner_id != player.player_id or card.controller_id != player.player_id
            or card.location.zone is not CardZone.HAND or card.location.player_id != player.player_id
            or definition is None or definition.side is not CardSide.CORP
            or definition.card_type not in {CardType.AGENDA, CardType.ASSET, CardType.UPGRADE, CardType.ICE}):
        raise IllegalCommand("Choose an installable Corp card from your hand")
    if definition.card_type is CardType.AGENDA and definition.score_instructions is None:
        raise IllegalCommand("This agenda's abilities are not implemented")
    target = command.server_id
    if target != "new_remote" and target not in state.servers:
        raise IllegalCommand("Choose an existing server or a new remote")
    if definition.card_type in {CardType.AGENDA, CardType.ASSET} and target in tuple(CentralServer):
        raise IllegalCommand("Agendas and assets must be installed in a remote")
    area = ServerArea.ICE if definition.card_type is CardType.ICE else ServerArea.ROOT
    existing = [c for c in state.card_instances.values() if c.installed and c.location.server
                and c.location.server.server_id == target and c.location.server.area is area]
    trash_ids = set(command.trash_card_instance_ids)
    if len(trash_ids) != len(command.trash_card_instance_ids) or not trash_ids <= {c.instance_id for c in existing}:
        raise IllegalCommand("Choose distinct installed cards in the destination's matching area to trash")
    remaining = [c for c in existing if c.instance_id not in trash_ids]
    if definition.card_type in {CardType.AGENDA, CardType.ASSET} and any(
            definitions[c.definition_id].card_type in {CardType.AGENDA, CardType.ASSET} for c in remaining):
        raise IllegalCommand("Trash the existing asset or agenda before installing its replacement")
    if "Region" in definition.subtypes and any("Region" in definitions[c.definition_id].subtypes for c in remaining):
        raise IllegalCommand("A server can contain only one region")
    cost = len(remaining) if area is ServerArea.ICE else 0
    if player.credits < cost:
        raise IllegalCommand(f"Installing this ice costs {cost} credits")
    return definition, cost


def install_card(state: GameState, player: PlayerState, command: InstallCard,
                 definitions: Mapping[str, CardDefinition]) -> None:
    """Install on a working copy, keeping a replacement's destination alive."""
    definition, cost = validate_install(state, player, command, definitions)
    if player.role is PlayerRole.RUNNER:
        for instance_id in command.trash_card_instance_ids:
            trash_installed_card(state, state.card_instances[instance_id])
        player.credits -= cost
        player.clicks_remaining -= 1
        card = state.card_instances[command.card_instance_id].model_copy(update={
            "installed": True, "rezzed": False, "face_up": True,
            "location": ZoneLocation(zone=CardZone.PLAY_AREA),
        })
        state.card_instances[card.instance_id] = card
        if "Console" in definition.subtypes:
            # CR 3.8.5: keep the most recently active console, trash the others.
            for old in tuple(state.card_instances.values()):
                if (old.instance_id != card.instance_id and old.installed and old.face_up
                        and old.controller_id == player.player_id
                        and definitions[old.definition_id].card_type is CardType.HARDWARE
                        and "Console" in definitions[old.definition_id].subtypes):
                    trash_installed_card(state, old)
        if definition.card_type is CardType.PROGRAM:
            key = f"program_install:{player.role}"
            state.turn.occurrences[key] = state.turn.occurrences.get(key, 0) + 1
        resolve_lifecycle(state, player, card, definitions, trigger="install")
        return
    target = command.server_id
    if target == "new_remote":
        target = 1 + sum(isinstance(s, int) for s in state.servers)
        state.servers.append(target)
    for instance_id in command.trash_card_instance_ids:
        card = state.card_instances[instance_id]
        state.card_instances[instance_id] = card.model_copy(update={
            "installed": False, "rezzed": False, "counters": {}, "loaded_counter_types": set(), "ability_last_used_turn": {},
            "controller_id": None,
            "location": ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=player.player_id)})
    ice = sorted((c for c in state.card_instances.values() if c.installed and c.location.server
                  and c.location.server.server_id == target and c.location.server.area is ServerArea.ICE),
                 key=lambda c: c.location.server.ice_distance_from_server)
    for distance, card in enumerate(ice):
        card.location.server.ice_distance_from_server = distance
    card = state.card_instances[command.card_instance_id]
    area = ServerArea.ICE if definition.card_type is CardType.ICE else ServerArea.ROOT
    state.card_instances[card.instance_id] = card.model_copy(update={
        "installed": True, "rezzed": False, "face_up": False,
        "location": ZoneLocation(zone=CardZone.PLAY_AREA, server=ServerLocation(
            server_id=target, area=area, ice_distance_from_server=len(ice) if area is ServerArea.ICE else None))})
    player.credits -= cost
    player.clicks_remaining -= 1

    resolve_lifecycle(state, player, state.card_instances[card.instance_id],
                      definitions, trigger="install")


def remove_empty_remotes(state: GameState) -> None:
    """Remove empty remotes and preserve the model's contiguous display positions."""
    occupied = {c.location.server.server_id for c in state.card_instances.values() if c.location.server}
    remotes = [s for s in state.servers if isinstance(s, int) and s in occupied]
    positions = {server: position for position, server in enumerate(remotes, 1)}
    for card in state.card_instances.values():
        if card.location.server and isinstance(card.location.server.server_id, int):
            card.location.server.server_id = positions[card.location.server.server_id]
    state.servers = [*CentralServer, *positions.values()]


def agenda_points(state: GameState, player: PlayerState, definitions: Mapping[str, CardDefinition]) -> int:
    """Derive the score from agendas actually present in the player's score area."""
    return sum(definitions[c.definition_id].agenda_points or 0 for c in state.card_instances.values()
               if c.location.zone is CardZone.SCORE_AREA and c.location.player_id == player.player_id)


def installed_programs(state, player, definitions):
    """Programs claim memory while installed; trashing releases it (CR 3.9.3)."""
    return [c for c in state.card_instances.values() if c.installed
            and c.controller_id == player.player_id
            and definitions[c.definition_id].card_type is CardType.PROGRAM]
