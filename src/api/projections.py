"""Build viewer-specific API views from authoritative game state."""

from collections.abc import Mapping
from uuid import UUID

from content.cards.models import CardDefinition, CardSide
from game.choices import card_selection, choice_options
from game.engine import legal_actions_for, pending_decision
from game.icebreakers import effective_strength, paid_ability_label
from game.installation import agenda_points, installed_programs
from game.models import CardZone, GameState, PlayerRole, PlayerState
from game.modifiers import install_cost, memory_limit, rez_cost
from game.payments import event_credits
from game.turns import maximum_hand_size

from . import command_adapters
from .schemas import (
    CardView,
    DecisionView,
    HiddenSelectionCardView,
    HistoryView,
    InitialGameView,
    InstalledCardView,
    OpponentView,
    OwnPlayerView,
    PlayerSummary,
    ReactionView,
    RunView,
    SetupView,
    TurnView,
)


def game_view_from_state(
    state: GameState,
    viewer_player_id: UUID,
    card_definitions: Mapping[str, CardDefinition],
) -> InitialGameView:
    """Project current state while enforcing each viewer's visibility boundary."""
    players = (state.corp, state.runner)
    you = next(
        (player for player in players if player.player_id == viewer_player_id),
        None,
    )
    if you is None:
        raise ValueError("Viewer is not a player in this game")
    opponent = state.runner if you is state.corp else state.corp

    def card_view(instance_id: UUID) -> CardView:
        instance = state.card_instances[instance_id]
        definition = card_definitions[instance.definition_id]
        return CardView(
            instance_id=instance.instance_id,
            definition_id=instance.definition_id,
            title=definition.title,
            card_type=definition.card_type,
            ability_labels={a.ability_id: (
                paid_ability_label(state, instance, a, card_definitions)
                if a in definition.paid_abilities else a.label)
                for a in (*definition.action_abilities, *definition.paid_abilities)},
            paid_ability_ids=[a.ability_id for a in definition.paid_abilities],
            strength=effective_strength(state, instance, definition, card_definitions) if definition.strength is not None else None,
            effective_rez_cost=rez_cost(state, definition),
            effective_install_cost=(install_cost(state, state.runner, definition, card_definitions)
                if state.turn and definition.side is CardSide.RUNNER and definition.install_cost is not None else None),
        )

    def player_summary(player: PlayerState) -> PlayerSummary:
        identity = card_view(player.identity_card_instance_id)
        return PlayerSummary(
            agenda_points=agenda_points(state, player, card_definitions),
            score_area=[card_view(c.instance_id) for c in state.card_instances.values()
                        if c.location.zone is CardZone.SCORE_AREA and c.location.player_id == player.player_id],
            player_id=player.player_id,
            role=player.role,
            credits=player.credits,
            clicks_remaining=player.clicks_remaining,
            tags=player.tags,
            memory_limit=memory_limit(state, player, card_definitions),
            memory_used=sum(card_definitions[c.definition_id].memory_cost or 0
                            for c in installed_programs(state, player, card_definitions)),
            maximum_hand_size=maximum_hand_size(player, state, card_definitions),
            discard_count=sum(card.location.zone is CardZone.DISCARD_PILE
                              and card.location.player_id == player.player_id
                              for card in state.card_instances.values()),
            discard_pile=[card_view(card.instance_id) for card in state.card_instances.values()
                          if card.location.zone is CardZone.DISCARD_PILE
                          and card.location.player_id == player.player_id
                          and (card.face_up or player.player_id == viewer_player_id)],
            identity=identity,
            deck_count=sum(
                card.location.zone is CardZone.DECK
                and card.location.player_id == player.player_id
                for card in state.card_instances.values()
            ),
        )

    own_hand = [
        card_view(card.instance_id)
        for card in state.card_instances.values()
        if card.location.zone is CardZone.HAND
        and card.location.player_id == you.player_id
    ]
    opponent_hand_count = sum(
        card.location.zone is CardZone.HAND
        and card.location.player_id == opponent.player_id
        for card in state.card_instances.values()
    )
    visible_ids = set()
    if state.run is not None:
        visible_ids = {i for i in state.run.visible_card_ids
                       if you.role is PlayerRole.RUNNER
                       or state.card_instances[i].location.zone is not CardZone.DECK}
    decision = pending_decision(state, card_definitions)
    return InitialGameView(
        history=[HistoryView(**entry.model_dump(exclude={"details"}), detail=entry.details[you.role])
                 for entry in state.history if you.role in entry.details],
        revealed_cards=[card_view(i) for i in state.revealed_card_ids],
        removed_cards=[card_view(c.instance_id) for c in state.card_instances.values()
                       if c.location.zone is CardZone.REMOVED_FROM_GAME and c.face_up],
        effect_install=bool(state.window and state.window.kind == "install"),
        selection_cards=[HiddenSelectionCardView(instance_id=i)
                         if (state.card_instances[i].installed and not state.card_instances[i].face_up
                             and state.card_instances[i].controller_id != you.player_id)
                         else card_view(i)
                         for i in card_selection(state, card_definitions).eligible_card_instance_ids]
            if state.window and state.window.kind == "target" and state.window.player is you.role else
            [card_view(i) for i in state.window.install_card_ids]
            if state.window and state.window.kind == "install" and state.window.player is you.role else [],
        effect_options={o.option_id: o.label for o in choice_options(state, card_definitions)}
            if state.window and state.window.kind == "choice" and state.window.player is you.role else {},
        run=RunView(
            cannot_steal_or_trash=state.run.cannot_steal_or_trash,
            server_id=state.run.server_id, phase=state.run.phase,
            successful=state.run.successful,
            current_ice_id=state.run.current_ice_id,
            current_ice=card_view(state.run.current_ice_id)
                if state.run.current_ice_id and (you.role is PlayerRole.CORP
                   or state.card_instances[state.run.current_ice_id].face_up) else None,
            subroutines=[{"index": i, "label": sub.label,
                          "broken": i in state.run.broken_subroutine_indices}
                         for i, sub in enumerate(card_definitions[state.card_instances[state.run.current_ice_id].definition_id].subroutines or ())]
                if state.run.current_ice_id and state.card_instances[state.run.current_ice_id].rezzed else [],
            rez_allowed=bool(state.window and state.window.rez_allowed),
            bad_publicity_credits=state.run.bad_publicity_credits,
            event_credits=event_credits(state),
            source_event=card_view(state.run.source_event_id) if state.run.source_event_id else None,
            source_card=card_view(state.run.source_card_id) if state.run.source_card_id else None,
            source_ability_id=state.run.source_ability_id,
            access_count=state.run.access_count, agendas_stolen=state.run.agendas_stolen,
            central_access_limit=state.run.access_limit,
            central_accesses=state.run.central_accesses,
            current_card=card_view(state.run.current_card_id)
                if state.run.current_card_id in visible_ids else None,
            accessed_cards=[card_view(i) for i in dict.fromkeys(state.run.visible_card_ids) if i in visible_ids],
        ) if state.run else None,
        reactions=[ReactionView(reaction_id=r.reaction_id, card_instance_id=r.card_instance_id,
            label=r.label, optional=r.optional) for r in state.window.reactions
            if r.player is you.role] if state.window and state.window.kind == "reaction"
            and state.window.player is you.role else [],
        game_id=state.game_id,
        viewer_player_id=viewer_player_id,
        viewer_role=you.role,
        setup=SetupView(**state.setup.model_dump()),
        turn=TurnView(
            number=state.turn.number,
            active_player=state.turn.active_player,
            phase=state.turn.phase.value,
            cannot_score=state.turn.cannot_score,
        ) if state.turn is not None else None,
        winner=state.winner,
        decision=DecisionView(**decision.model_dump()) if decision else None,
        legal_actions=[
            command_adapters.command_request_from_game(command)
            for command in legal_actions_for(state, viewer_player_id, card_definitions)
        ],
        you=OwnPlayerView(**player_summary(you).model_dump(), hand=own_hand),
        opponent=OpponentView(
            **player_summary(opponent).model_dump(),
            hand_count=opponent_hand_count,
        ),
        installed_cards=[InstalledCardView(
            instance_id=c.instance_id, server_id=c.location.server.server_id if c.location.server else None,
            controller_id=c.controller_id, rezzed=c.rezzed, host_card_instance_id=c.location.host_card_instance_id,
            area=c.location.server.area.value if c.location.server else "rig",
            ice_distance_from_server=c.location.server.ice_distance_from_server if c.location.server else None,
            face_up=bool(c.face_up), counters=c.counters,
            card=card_view(c.instance_id) if c.face_up or c.controller_id == viewer_player_id or c.instance_id in visible_ids else None)
            for c in state.card_instances.values() if c.installed],
        servers=[
            server.value if hasattr(server, "value") else server
            for server in state.servers
        ],
    )
