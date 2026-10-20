"""Resolve authenticated player commands and derive their legal choices.

The engine owns command legality and state changes. API and CLI layers may
display legal actions, but every submitted command is validated again here.
"""

from collections.abc import Mapping
from itertools import combinations
from uuid import UUID

from content.cards.models import CardDefinition, InitiateRun

from .card_movement import draw_from_deck, trash_installed_card
from .card_play import play_card, validate_card_play
from .choices import card_selection, choice_options, resolve_choice, resolve_selection
from .commands import (
    AccessCard,
    AdvanceCard,
    CardSelection,
    ChooseEffectOption,
    ChooseMulligan,
    ContinueRun,
    DiscardCards,
    DiscardSelection,
    DrawCard,
    FinishAccess,
    GainCredit,
    GameCommand,
    InstallCard,
    JackOut,
    PassPriority,
    PlayCard,
    PurgeVirusCounters,
    RemoveTag,
    ResolveReaction,
    RezCard,
    RunServer,
    ScoreAgenda,
    SelectEffectCards,
    TrashPrograms,
    TrashResource,
    UseCardAbility,
)
from .icebreakers import paid_actions, use_paid_ability
from .installation import (
    advanceable_card,
    agenda_points,
    install_card,
    installed_agenda,
    installed_programs,
    remove_empty_remotes,
    validate_install,
)
from .installed_abilities import (
    resolve_lifecycle,
    use_ability,
    validate_ability,
    validate_rez,
)
from .models import (
    CardZone,
    GameState,
    MulliganDecision,
    PendingDecision,
    PlayerRole,
    PlayerState,
    RuleFact,
    TurnPhase,
    ZoneLocation,
)
from .modifiers import memory_limit, memory_used
from .priority import pass_priority
from .reactions import (
    basic_draw_amount,
    checkpoint,
    collect_fact,
    emit_fact,
    open_reactions,
    pass_reactions,
    resolve_reaction,
    settle_reactions,
)
from .runs import (
    advance_run,
    resolve_run_command,
    resume_run,
    run_actions,
    runnable_servers,
)
from .setup import IllegalCommand, choose_mulligan
from .turns import advance_turn, hand_cards, maximum_hand_size


def legal_actions_for(
    state: GameState, player_id: UUID,
    card_definitions: Mapping[str, CardDefinition] | None = None,
) -> tuple[GameCommand | DiscardSelection | CardSelection, ...]:
    """Return commands currently available to one player, in menu order.

    This is a player-specific projection of the current state. It is not a
    substitute for validation when a command is submitted.
    """
    player = _player_for_id(state, player_id)
    if player is None or state.winner is not None:
        return ()

    if state.setup.pending_player is not None:
        pending = _player_for_role(state, state.setup.pending_player)
        if pending.player_id != player_id:
            return ()
        return (
            ChooseMulligan(MulliganDecision.KEEP),
            ChooseMulligan(MulliganDecision.MULLIGAN),
        )

    definitions = card_definitions or {}
    if state.memory_trash_pending:
        if player.role is not PlayerRole.RUNNER:
            return ()
        programs = installed_programs(state, player, definitions)
        excess = memory_used(state, definitions) - memory_limit(state, player, definitions)
        return tuple(TrashPrograms(tuple(c.instance_id for c in choice))
                     for count in range(1, len(programs) + 1)
                     for choice in combinations(programs, count)
                     if sum(definitions[c.definition_id].memory_cost or 0 for c in choice) >= excess)
    if state.run is not None and state.window is None:
        return run_actions(state, definitions) if player.role is PlayerRole.RUNNER else ()
    if state.window is not None:
        if state.window.player is not player.role:
            return ()
        if state.window.kind == "install":
            from .effect_installation import install_actions
            return install_actions(state, definitions)
        if state.window.kind == "target":
            return (card_selection(state, definitions),)
        if state.window.kind == "choice":
            return tuple(ChooseEffectOption(o.option_id) for o in choice_options(state))
        if state.window.kind == "reaction":
            reactions = [r for r in state.window.reactions if r.player is player.role]
            actions = [ResolveReaction(r.card_instance_id, r.reaction_id) for r in reactions]
            actions.extend(ResolveReaction(r.card_instance_id, r.reaction_id, decline=True)
                           for r in reactions if r.optional)
            if all(r.optional for r in reactions):
                actions.append(PassPriority())
            return tuple(actions)
        actions = paid_actions(state, player, definitions)
        for card in state.card_instances.values():
            try:
                validate_rez(state, player, card.instance_id, definitions)
            except IllegalCommand:
                pass
            else:
                actions.append(RezCard(card.instance_id))
            if state.run is not None or state.turn.active_player is not PlayerRole.CORP or state.turn.phase not in {TurnPhase.DRAW, TurnPhase.ACTION}:
                continue
            try:
                _, definition = installed_agenda(state, player, card.instance_id, definitions)
            except IllegalCommand:
                continue
            if card.counters.get("advancement", 0) >= definition.advancement_requirement:
                actions.append(ScoreAgenda(card.instance_id))
        return (*actions, PassPriority())

    if (state.turn is not None and state.turn.phase is TurnPhase.DISCARD
            and state.turn.active_player is player.role):
        cards = hand_cards(state, player)
        count = len(cards) - maximum_hand_size(player, state, definitions)
        if count > 0:
            return (DiscardSelection(tuple(card.instance_id for card in cards), count),)
        return ()

    if (
        state.turn is None
        or state.turn.phase is not TurnPhase.ACTION
        or state.turn.active_player is not player.role
        or player.clicks_remaining <= 0
    ):
        return ()

    actions: list[GameCommand] = [GainCredit(), DrawCard()]
    for card in state.card_instances.values():
        definition = definitions.get(card.definition_id)
        for ability in definition.action_abilities if definition else ():
            targets = state.servers if any(isinstance(i, InitiateRun) for i in ability.instructions) else (None,)
            for target in targets:
                command = UseCardAbility(card.instance_id, ability.ability_id, server_id=target)
                try:
                    validate_ability(state, player, command, definitions)
                except IllegalCommand:
                    continue
                actions.append(command)
    if player.role is PlayerRole.RUNNER:
        actions.extend(RunServer(server) for server in runnable_servers(state))
        for card in hand_cards(state, player):
            programs = installed_programs(state, player, definitions)
            # At most a handful of programs fit in base memory. Offer explicit
            # replacement choices through the existing installation command.
            for count in range(len(programs) + 1):
                for trash in combinations(programs, count):
                    definition = definitions.get(card.definition_id)
                    hosts = (tuple(c.instance_id for c in state.card_instances.values()
                                   if c.installed and c.location.server and c.location.server.area.value == "ice")
                             if definition and definition.install_on_ice else (None,))
                    for host_id in hosts:
                        command = InstallCard(card.instance_id, trash_card_instance_ids=tuple(c.instance_id for c in trash),
                                              host_card_instance_id=host_id)
                        try:
                            validate_install(state, player, command, definitions)
                        except IllegalCommand:
                            continue
                        actions.append(command)
        if player.tags > 0 and player.credits >= 2:
            actions.append(RemoveTag())
    elif player.clicks_remaining >= 3:
        actions.append(PurgeVirusCounters())
    if card_definitions is not None:
        for card in hand_cards(state, player):
            for server in (None, *state.servers):
                try:
                    validate_card_play(state, player, card.instance_id, card_definitions, server)
                except IllegalCommand:
                    continue
                actions.append(PlayCard(card.instance_id, server))
    if player.role is PlayerRole.CORP:
        if state.runner.tags and player.credits >= 2:
            actions.extend(TrashResource(c.instance_id) for c in state.card_instances.values()
                           if c.installed and c.controller_id == state.runner.player_id
                           and (definition := definitions.get(c.definition_id)) is not None
                           and definition.card_type.value == "resource")
        for card in state.card_instances.values():
            try:
                advanceable_card(state, player, card.instance_id, definitions)
            except IllegalCommand:
                pass
            else:
                if player.credits >= 1:
                    actions.append(AdvanceCard(card.instance_id))
        for card in hand_cards(state, player):
            for target in (*state.servers, "new_remote"):
                # Offer mandatory replacement trashing; optional trash choices can
                # also be submitted through the generic install command.
                definition = definitions.get(card.definition_id)
                replacements = tuple(c.instance_id for c in state.card_instances.values()
                    if c.location.server and c.location.server.server_id == target
                    and c.location.server.area.value == "root"
                    and definition is not None
                    and ((definition.card_type.value in {"agenda", "asset"}
                          and definitions[c.definition_id].card_type.value in {"agenda", "asset"})
                         or ("Region" in definition.subtypes and "Region" in definitions[c.definition_id].subtypes)))
                command = InstallCard(card.instance_id, target, replacements)
                try:
                    validate_install(state, player, command, definitions)
                except IllegalCommand:
                    continue
                actions.append(command)
    return tuple(actions)


def apply_command(
    state: GameState, player_id: UUID, command: GameCommand,
    card_definitions: Mapping[str, CardDefinition] | None = None,
) -> GameState:
    """Validate and apply one command, returning a new validated snapshot."""
    if state.window and state.window.kind == "install":
        from .effect_installation import resolve_install
        actor = _player_for_id(state, player_id)
        if state.winner or actor is None or actor.role is not state.window.player:
            raise IllegalCommand("You do not control the pending installation")
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        resolve_install(result, card_definitions or {}, command)
        advance_run(result, card_definitions or {})
        advance_turn(result, card_definitions)
        return GameState.model_validate(result.model_dump())
    if isinstance(command, ChooseMulligan):
        return choose_mulligan(state, player_id, command)

    player = _player_for_id(state, player_id)
    if player is None:
        raise IllegalCommand("Player is not part of this game")
    if isinstance(command, (ChooseEffectOption, SelectEffectCards)) or (state.window and state.window.kind in {"choice", "target"}):
        if (state.winner or not state.window or state.window.kind not in {"choice", "target"}
                or state.window.player is not player.role
                or not isinstance(command, SelectEffectCards if state.window.kind == "target" else ChooseEffectOption)):
            raise IllegalCommand("Resolve the pending effect choice first")
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        if isinstance(command, SelectEffectCards):
            resolve_selection(result, card_definitions or {}, command)
        else:
            resolve_choice(result, card_definitions or {}, command)
        advance_run(result, card_definitions or {})
        advance_turn(result, card_definitions)
        return GameState.model_validate(result.model_dump())
    if state.memory_trash_pending:
        definitions = card_definitions or {}
        if not isinstance(command, TrashPrograms) or player.role is not PlayerRole.RUNNER or state.winner:
            raise IllegalCommand("The Runner must trash installed programs to satisfy the memory limit")
        selected = set(command.card_instance_ids)
        eligible = {c.instance_id for c in installed_programs(state, player, definitions)}
        if (not selected or len(selected) != len(command.card_instance_ids) or not selected <= eligible
                or memory_used(state, definitions) - sum(definitions[state.card_instances[i].definition_id].memory_cost or 0
                    for i in selected) > memory_limit(state, player, definitions)):
            raise IllegalCommand("Choose distinct installed programs that free enough memory")
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        for instance_id in command.card_instance_ids:
            trash_installed_card(result, result.card_instances[instance_id])
        checkpoint(result, definitions)
        settle_reactions(result, definitions)
        advance_run(result, definitions)
        advance_turn(result, definitions)
        return GameState.model_validate(result.model_dump())
    run_commands = (RunServer, ContinueRun, JackOut, AccessCard, FinishAccess)
    if ((state.run is not None and state.window is None)
            or isinstance(command, run_commands)):
        if state.window is not None:
            raise IllegalCommand("Finish the current priority window first")
        if state.winner is not None or player.role is not PlayerRole.RUNNER:
            raise IllegalCommand("Only the Runner can make run decisions")
        if isinstance(command, RunServer):
            if state.run is not None:
                raise IllegalCommand("Finish the current run first")
            _validate_action_window(state, player)
        elif state.run is None or not isinstance(command, run_commands):
            raise IllegalCommand("Finish the current run before taking another action")
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        resolve_run_command(result, command, card_definitions or {})
        if isinstance(command, RunServer) and result.winner is None:
            result.pending_action_fact = RuleFact(type="action", player=player.role, action="run")
        result.turn.step = "before_action"
        advance_run(result, card_definitions or {})
        advance_turn(result, card_definitions)
        return GameState.model_validate(result.model_dump())
    if isinstance(command, (PassPriority, ScoreAgenda, ResolveReaction, RezCard)) or (state.window is not None and isinstance(command, UseCardAbility)):
        if state.winner is not None or state.window is None or state.window.player is not player.role:
            raise IllegalCommand("You do not have priority in a window")
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        actor = _player_for_role(result, player.role)
        definitions = card_definitions or {}
        if not isinstance(command, PassPriority) and result.window.kind == "paid":
            result.window.opponent_passed = False
        if isinstance(command, PassPriority):
            if result.window.kind == "reaction":
                pass_reactions(result, definitions)
            else:
                continuation = pass_priority(result, definitions)
                if continuation is not None:
                    resume_run(result, continuation, definitions)
        elif isinstance(command, UseCardAbility):
            if result.window.kind != "paid":
                raise IllegalCommand("Resolve reactions before using paid abilities")
            use_paid_ability(result, actor, command, definitions)
        elif isinstance(command, RezCard):
            card, definition = validate_rez(result, actor, command.card_instance_id, definitions)
            actor.credits -= definition.rez_cost
            result.card_instances[card.instance_id] = card.model_copy(update={"face_up": True, "rezzed": True})
            card = result.card_instances[card.instance_id]
            resolve_lifecycle(result, actor, card, definitions, trigger="rez")
        elif isinstance(command, ScoreAgenda):
            if (result.run is not None or result.window.kind != "paid" or result.turn.active_player is not PlayerRole.CORP
                    or result.turn.phase not in {TurnPhase.DRAW, TurnPhase.ACTION}):
                raise IllegalCommand("Score only during an eligible Corp paid ability window")
            card, definition = installed_agenda(result, actor, command.card_instance_id, definitions)
            if card.counters.get("advancement", 0) < definition.advancement_requirement:
                raise IllegalCommand("The agenda has not met its advancement requirement")
            scoring_server = card.location.server.server_id
            result.card_instances[card.instance_id] = card.model_copy(update={
                "installed": False, "rezzed": False, "face_up": True, "counters": {}, "loaded_counter_types": set(),
                "location": ZoneLocation(zone=CardZone.SCORE_AREA, player_id=actor.player_id)})
            result.turn.printed_agenda_points_scored += definition.agenda_points or 0
            scoring_reactions = collect_fact(result, definitions, RuleFact(type="score", player=player.role,
                card_instance_id=card.instance_id, server_id=scoring_server)) if agenda_points(result, actor, definitions) < 7 else []
            remove_empty_remotes(result)
            # CR 1.17.2: the victory checkpoint precedes scoring reactions.
            if agenda_points(result, actor, definitions) >= 7:
                result.winner = player.role
                result.window = None
            else:
                open_reactions(result, definitions, scoring_reactions)
        else:
            if result.window.kind != "reaction":
                raise IllegalCommand("Choose a pending reaction")
            resolve_reaction(result, definitions, command)
        advance_run(result, card_definitions or {})
        advance_turn(result, card_definitions)
        return GameState.model_validate(result.model_dump())
    if isinstance(command, DiscardCards):
        if (state.winner is not None or state.window is not None or state.setup.pending_player is not None
                or state.turn is None or state.turn.phase is not TurnPhase.DISCARD
                or state.turn.active_player is not player.role):
            raise IllegalCommand("Discard only during your discard decision")
        eligible = {card.instance_id for card in hand_cards(state, player)}
        required = len(eligible) - maximum_hand_size(player, state, card_definitions)
        selected = command.card_instance_ids
        if (required <= 0 or len(selected) != required
                or len(set(selected)) != required or not set(selected) <= eligible):
            raise IllegalCommand(f"Choose exactly {required} distinct cards from your hand")
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        for instance_id in selected:
            card = result.card_instances[instance_id]
            card.face_up = player.role is PlayerRole.RUNNER
            card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=player_id)
        advance_turn(result, card_definitions)
        return GameState.model_validate(result.model_dump())
    _validate_action_window(state, player)

    if isinstance(command, UseCardAbility):
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        use_ability(result, _player_for_role(result, player.role), command, card_definitions or {})
        remove_empty_remotes(result)
    elif isinstance(command, InstallCard):
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        install_card(result, _player_for_role(result, player.role), command, card_definitions or {})
    elif isinstance(command, AdvanceCard):
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        actor = _player_for_role(result, player.role)
        card, _ = advanceable_card(result, actor, command.card_instance_id, card_definitions or {})
        if actor.credits < 1:
            raise IllegalCommand("Advancing costs 1 credit")
        actor.credits -= 1
        actor.clicks_remaining -= 1
        previous = card.counters.get("advancement", 0)
        card.counters["advancement"] = card.counters.get("advancement", 0) + 1
        emit_fact(result, card_definitions or {}, RuleFact(type="advance", player=player.role,
                  card_instance_id=card.instance_id, previous_advancement=previous))
    elif isinstance(command, PlayCard):
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        play_card(result, _player_for_role(result, player.role), command.card_instance_id,
                  card_definitions if card_definitions is not None else {}, command.server_id)
    elif isinstance(command, GainCredit):
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        _player_for_role(result, player.role).clicks_remaining -= 1
        _player_for_role(result, player.role).credits += 1
    elif isinstance(command, DrawCard):
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        active_player = _player_for_role(result, player.role)
        active_player.clicks_remaining -= 1
        amount = basic_draw_amount(result, active_player, card_definitions or {})
        drew_all = draw_from_deck(result, player_id, amount)
        if not drew_all and player.role is PlayerRole.CORP:
            # CR 1.7.2c: the Corp loses when required to draw from an empty R&D.
            result.winner = PlayerRole.RUNNER
    elif isinstance(command, TrashResource):
        card = state.card_instances.get(command.card_instance_id)
        definition = (card_definitions or {}).get(card.definition_id) if card else None
        if (player.role is not PlayerRole.CORP or not state.runner.tags or player.credits < 2
                or not card or not card.installed or card.controller_id != state.runner.player_id
                or not definition or definition.card_type.value != "resource"):
            raise IllegalCommand("Spend 2 credits to trash an installed resource while the Runner is tagged")
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        result.corp.clicks_remaining -= 1
        result.corp.credits -= 2
        trash_installed_card(result, result.card_instances[command.card_instance_id])
    elif isinstance(command, RemoveTag):
        if player.role is not PlayerRole.RUNNER:
            raise IllegalCommand("Only the Runner can remove a tag")
        if player.tags < 1:
            raise IllegalCommand("The Runner has no tags to remove")
        if player.credits < 2:
            raise IllegalCommand("Removing a tag costs 2 credits")
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        result.runner.clicks_remaining -= 1
        result.runner.credits -= 2
        result.runner.tags -= 1
    elif isinstance(command, PurgeVirusCounters):
        if player.role is not PlayerRole.CORP:
            raise IllegalCommand("Only the Corp can purge virus counters")
        if player.clicks_remaining < 3:
            raise IllegalCommand("Purging virus counters costs 3 clicks")
        result = state.model_copy(deep=True)
        result.revealed_card_ids = []
        result.corp.clicks_remaining -= 3
        # The registry includes hosted cards, so one pass purges counters everywhere.
        for card in result.card_instances.values():
            card.counters.pop("virus", None)
    else:
        raise IllegalCommand("Unsupported command")

    if result.winner is None:
        result.pending_action_fact = RuleFact(type="action", player=player.role,
            card_instance_id=getattr(command, "card_instance_id", None), action=type(command).__name__)
    result.turn.step = "before_action"
    checkpoint(result, card_definitions or {})
    advance_run(result, card_definitions or {})
    advance_turn(result, card_definitions)
    return GameState.model_validate(result.model_dump())


def _validate_action_window(state: GameState, player: PlayerState) -> None:
    """Reject actions outside the authenticated player's action window."""
    if state.window is not None:
        raise IllegalCommand("Finish the current priority window first")
    if state.winner is not None:
        raise IllegalCommand("The game has already ended")
    if state.setup.pending_player is not None:
        raise IllegalCommand("Opening-hand decisions are still pending")
    if state.turn is None or state.turn.phase is not TurnPhase.ACTION:
        raise IllegalCommand("Basic actions are available only during an action phase")
    if state.turn.active_player is not player.role:
        raise IllegalCommand(f"Waiting for {state.turn.active_player.value} to act")
    if player.clicks_remaining < 1:
        raise IllegalCommand("At least 1 click is required")


def _player_for_id(state: GameState, player_id: UUID) -> PlayerState | None:
    """Find one of the two players by the identity authenticated by the session."""
    return next(
        (player for player in (state.corp, state.runner) if player.player_id == player_id),
        None,
    )


def _player_for_role(state: GameState, role: PlayerRole) -> PlayerState:
    """Return the player state for a role."""
    return state.corp if role is PlayerRole.CORP else state.runner


def pending_decision(state: GameState, definitions=None) -> PendingDecision | None:
    """Describe the public decision independently of either player's view."""
    if state.winner is not None:
        return None
    if state.memory_trash_pending:
        return PendingDecision(player=PlayerRole.RUNNER, type="trash_for_memory")
    if state.setup.pending_player is not None:
        return PendingDecision(player=state.setup.pending_player, type="choose_opening_hand")
    if state.run is not None and state.window is None:
        kind = {"movement": "continue_run", "subroutine_choice": "continue_run", "breach": "choose_access", "access": "resolve_access"}[state.run.phase]
        return PendingDecision(player=PlayerRole.RUNNER, type=kind)
    if state.window is not None:
        if state.window.kind in {"target", "install"}:
            return PendingDecision(player=state.window.player, type="choose_cards")
        if state.window.kind == "choice":
            return PendingDecision(player=state.window.player, type="choose_effect")
        return PendingDecision(player=state.window.player, type=("paid_ability_window" if state.window.kind == "paid" else "resolve_reaction"))
    if state.turn is None:
        return None
    player = _player_for_role(state, state.turn.active_player)
    if state.turn.phase is TurnPhase.DISCARD:
        return PendingDecision(player=player.role, type="discard_to_hand_limit",
                               cards_to_discard=len(hand_cards(state, player)) - maximum_hand_size(player, state, definitions))
    return PendingDecision(player=player.role, type="choose_action")
