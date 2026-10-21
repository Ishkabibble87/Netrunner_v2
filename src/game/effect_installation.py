"""Install authored effect targets using the ordinary installation rules.

The decision retains its source and parent continuation. A declined Mutual
Favor install returns the found card to hand; other optional installs do nothing.
"""

from itertools import combinations

from content.cards.models import CardType

from .choices import selection_candidates
from .commands import InstallCard, SelectEffectCards
from .installation import install_card, installed_programs, validate_install
from .models import CardZone, PlayerRole, PriorityWindow, ZoneLocation
from .setup import IllegalCommand


def install_actions(state, definitions):
    """Offer legal card/destination/replacement combinations for this effect."""
    window = state.window
    player = state.corp if window.player is PlayerRole.CORP else state.runner
    actions = []
    source = state.card_instances.get(window.choice_source_id)
    for instance_id in window.install_card_ids:
        card = state.card_instances[instance_id]
        definition = definitions[card.definition_id]
        if window.selection.inward_from_source:
            if not source or not source.installed or not source.location.server:
                continue
            targets = (source.location.server.server_id,)
        else:
            targets = (*state.servers, "new_remote") if player.role is PlayerRole.CORP else (None,)
        for target in targets:
            if player.role is PlayerRole.RUNNER:
                replacements = installed_programs(state, player, definitions) if definition.card_type is CardType.PROGRAM else []
            else:
                area = "ice" if definition.card_type is CardType.ICE else "root"
                replacements = [c for c in state.card_instances.values() if c.installed
                    and c.location.server and c.location.server.server_id == target
                    and c.location.server.area.value == area]
            hosts = [c.instance_id for c in state.card_instances.values() if c.installed
                     and c.location.server and c.location.server.area.value == "ice"] if definition.install_on_ice else [None]
            for count in range(len(replacements) + 1):
                for trash in combinations(replacements, count):
                    for host in hosts:
                        command = InstallCard(instance_id, target, tuple(c.instance_id for c in trash), host)
                        try:
                            validate_install(state, player, command, definitions, effect=window.selection)
                        except IllegalCommand:
                            continue
                        actions.append(command)
    return (*actions, SelectEffectCards(()))


def validate_effect_install(state, definitions, command):
    """Validate the submitted set directly; trash-card order has no meaning."""
    window = state.window
    eligible = selection_candidates(state, window.selection, definitions)
    if command.card_instance_id not in window.install_card_ids or command.card_instance_id not in eligible:
        raise IllegalCommand("Choose a card from the effect's eligible source")
    if window.selection.inward_from_source:
        source = state.card_instances.get(window.choice_source_id)
        if (not source or not source.installed or not source.location.server
                or source.location.server.area.value != "ice"
                or command.server_id != source.location.server.server_id):
            raise IllegalCommand("Install directly inward from the source ice")
    player = state.corp if window.player is PlayerRole.CORP else state.runner
    validate_install(state, player, command, definitions, effect=window.selection)


def open_install(state, instruction, source, fact, definitions, *, card_ids=None, fallback_hand=False):
    """Suspend the resolving ability until installation or an explicit decline."""
    state.suspended_windows.append(state.window)
    state.window = PriorityWindow(history_parent_id=state.history_parent_id, kind="install", player=PlayerRole(instruction.player), selection=instruction,
        choice_source_id=source.instance_id if source else None, choice_fact=fact,
        install_card_ids=list(card_ids if card_ids is not None else selection_candidates(state, instruction, definitions)),
        install_fallback_hand=fallback_hand)
    if len(install_actions(state, definitions)) == 1:
        resolve_install(state, definitions, SelectEffectCards(()))


def resolve_install(state, definitions, command):
    """Revalidate targets and payment before restoring the parent's window."""
    from .reactions import checkpoint, settle_reactions

    window = state.window
    if isinstance(command, SelectEffectCards):
        if command.card_instance_ids:
            raise IllegalCommand("Choose an installation or decline")
    elif isinstance(command, InstallCard):
        validate_effect_install(state, definitions, command)
    else:
        raise IllegalCommand("Finish the pending installation")
    state.window = state.suspended_windows.pop()
    if isinstance(command, InstallCard):
        player = state.corp if window.player is PlayerRole.CORP else state.runner
        install_card(state, player, command, definitions, effect=window.selection,
                     inward_source=window.choice_source_id if window.selection.inward_from_source else None)
    elif window.install_fallback_hand:
        for instance_id in window.install_card_ids:
            card = state.card_instances[instance_id]
            card.location = ZoneLocation(zone=CardZone.HAND, player_id=card.owner_id)
    checkpoint(state, definitions)
    settle_reactions(state, definitions)
