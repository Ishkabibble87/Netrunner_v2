"""Define the API's validated requests and player-visible response models.

These Pydantic schemas describe the HTTP and WebSocket boundary: they shape
incoming commands, catalog/setup responses, and per-player game projections.
Conversion helpers turn content and authoritative game state into transport
views while keeping concealed information out of an opponent's response. Game
rules and authoritative state belong in ``src/game``; these models describe
what the API accepts and returns.
"""

from collections.abc import Mapping
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from content.cards.models import CardDefinition, CardSide, CardType
from content.decks.models import PreconstructedDeck
from content.opponents.models import OpponentProfile
from game.choices import card_selection, choice_options
from game.commands import (
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
from game.engine import legal_actions_for, pending_decision
from game.icebreakers import effective_strength, paid_ability_label
from game.installation import agenda_points, installed_programs
from game.models import (
    CardZone,
    DeckSelection,
    GameState,
    MulliganDecision,
    PlayerRole,
    PlayerState,
    ServerId,
)
from game.modifiers import install_cost, memory_limit, rez_cost
from game.payments import event_credits
from game.turns import maximum_hand_size


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateGameRequest(ApiModel):
    player_id: UUID
    decklist: DeckSelection
    opponent_profile_id: str = Field(min_length=1)


class DeckImportRequest(ApiModel):
    text: str


class MulliganCommandRequest(ApiModel):
    """Choose whether to keep the current opening hand or mulligan."""

    type: Literal["choose_mulligan"]
    decision: MulliganDecision


class GainCreditCommandRequest(ApiModel):
    """Request the one-click basic action that gains one credit."""

    type: Literal["gain_credit"]


class DrawCardCommandRequest(ApiModel):
    """Request the one-click basic action that draws one card."""

    type: Literal["draw_card"]


class RemoveTagCommandRequest(ApiModel):
    """Request the Runner's one-click, two-credit tag-removal action."""

    type: Literal["remove_tag"]


class ChooseEffectOptionRequest(ApiModel):
    """Select an offered effect; labels are projected separately by the server."""

    type: Literal["choose_effect_option"]
    option_id: str


class SelectEffectCardsRequest(ApiModel):
    type: Literal["select_effect_cards"]
    card_instance_ids: list[UUID]


class CardSelectionAction(ApiModel):
    """Private eligible set; the server validates the submitted selection."""

    type: Literal["select_effect_cards"] = "select_effect_cards"
    eligible_card_instance_ids: list[UUID]
    required_count: int
    optional: bool = False
    up_to: bool = False


class TrashResourceRequest(ApiModel):
    type: Literal["trash_resource"]
    card_instance_id: UUID


class PlayCardCommandRequest(ApiModel):
    """Identify the physical copy; costs and instructions are server-owned."""

    type: Literal["play_card"]
    card_instance_id: UUID
    server_id: ServerId | None = None


class DiscardCardsCommandRequest(ApiModel):
    """Submit the complete set of excess cards; the engine checks eligibility."""

    type: Literal["discard_cards"]
    card_instance_ids: list[UUID] = Field(min_length=1)


class TrashProgramsCommandRequest(ApiModel):
    """Choose programs to trash after losing available memory."""

    type: Literal["trash_programs"]
    card_instance_ids: list[UUID] = Field(min_length=1)


class DiscardCardsAction(ApiModel):
    """A private selection action, avoiding combinatorial command enumeration."""

    type: Literal["discard_cards"] = "discard_cards"
    eligible_card_instance_ids: list[UUID]
    required_count: int = Field(gt=0)


class DecisionView(ApiModel):
    """Public context shared by both players, never hidden card choices."""

    player: PlayerRole
    type: Literal["choose_opening_hand", "choose_action", "discard_to_hand_limit", "paid_ability_window", "resolve_reaction", "choose_effect", "choose_cards", "continue_run", "choose_access", "resolve_access", "trash_for_memory"]
    cards_to_discard: int | None = None


class PurgeVirusCountersCommandRequest(ApiModel):
    """Request the Corp's three-click virus-counter purge action."""

    type: Literal["purge_virus_counters"]


class InstallCardCommandRequest(ApiModel):
    type: Literal["install_card"]
    card_instance_id: UUID
    server_id: ServerId | Literal["new_remote"] | None = None
    trash_card_instance_ids: list[UUID] = Field(default_factory=list)
    host_card_instance_id: UUID | None = None


class RezCardCommandRequest(ApiModel):
    type: Literal["rez_card"]
    card_instance_id: UUID


class UseCardAbilityCommandRequest(ApiModel):
    type: Literal["use_card_ability"]
    card_instance_id: UUID
    ability_id: str = Field(min_length=1)
    subroutine_indices: list[Annotated[int, Field(ge=0)]] = Field(default_factory=list)
    server_id: ServerId | None = None


class AdvanceCardCommandRequest(ApiModel):
    type: Literal["advance_card"]
    card_instance_id: UUID


class ScoreAgendaCommandRequest(ApiModel):
    type: Literal["score_agenda"]
    card_instance_id: UUID


class PassPriorityCommandRequest(ApiModel):
    type: Literal["pass_priority"]


class ResolveReactionCommandRequest(ApiModel):
    type: Literal["resolve_reaction"]
    card_instance_id: UUID
    reaction_id: UUID | None = None
    decline: bool = False


class RunServerCommandRequest(ApiModel):
    type: Literal["run_server"]
    server_id: ServerId


class ContinueRunCommandRequest(ApiModel):
    type: Literal["continue_run"]


class JackOutCommandRequest(ApiModel):
    type: Literal["jack_out"]


class AccessCardCommandRequest(ApiModel):
    type: Literal["access_card"]
    target: UUID | Literal["hq", "rd"]


class FinishAccessCommandRequest(ApiModel):
    type: Literal["finish_access"]
    trash: bool = False


PlayerCommandRequest = Annotated[
    RunServerCommandRequest
    | ContinueRunCommandRequest
    | JackOutCommandRequest
    | AccessCardCommandRequest
    | FinishAccessCommandRequest
    | MulliganCommandRequest
    | GainCreditCommandRequest
    | DrawCardCommandRequest
    | RemoveTagCommandRequest
    | SelectEffectCardsRequest
    | ChooseEffectOptionRequest
    | TrashResourceRequest
    | PurgeVirusCountersCommandRequest
    | DiscardCardsCommandRequest
    | TrashProgramsCommandRequest
    | PlayCardCommandRequest
    | InstallCardCommandRequest
    | RezCardCommandRequest
    | UseCardAbilityCommandRequest
    | AdvanceCardCommandRequest
    | ScoreAgendaCommandRequest
    | PassPriorityCommandRequest
    | ResolveReactionCommandRequest,
    Field(discriminator="type"),
]


LegalAction = PlayerCommandRequest | DiscardCardsAction | CardSelectionAction


def game_command_from_request(request: PlayerCommandRequest) -> GameCommand:
    """Translate a validated API command into the transport-independent game type."""
    if isinstance(request, SelectEffectCardsRequest):
        return SelectEffectCards(tuple(request.card_instance_ids))
    if isinstance(request, ChooseEffectOptionRequest):
        return ChooseEffectOption(request.option_id)
    if isinstance(request, TrashResourceRequest):
        return TrashResource(request.card_instance_id)
    if isinstance(request, TrashProgramsCommandRequest):
        return TrashPrograms(tuple(request.card_instance_ids))
    if isinstance(request, RunServerCommandRequest):
        return RunServer(request.server_id)
    if isinstance(request, ContinueRunCommandRequest):
        return ContinueRun()
    if isinstance(request, JackOutCommandRequest):
        return JackOut()
    if isinstance(request, AccessCardCommandRequest):
        return AccessCard(request.target)
    if isinstance(request, FinishAccessCommandRequest):
        return FinishAccess(request.trash)
    if isinstance(request, PlayCardCommandRequest):
        return PlayCard(request.card_instance_id, request.server_id)
    if isinstance(request, InstallCardCommandRequest):
        return InstallCard(request.card_instance_id, request.server_id, tuple(request.trash_card_instance_ids), request.host_card_instance_id)
    if isinstance(request, RezCardCommandRequest):
        return RezCard(request.card_instance_id)
    if isinstance(request, UseCardAbilityCommandRequest):
        return UseCardAbility(request.card_instance_id, request.ability_id, tuple(request.subroutine_indices), request.server_id)
    if isinstance(request, AdvanceCardCommandRequest):
        return AdvanceCard(request.card_instance_id)
    if isinstance(request, ScoreAgendaCommandRequest):
        return ScoreAgenda(request.card_instance_id)
    if isinstance(request, PassPriorityCommandRequest):
        return PassPriority()
    if isinstance(request, ResolveReactionCommandRequest):
        return ResolveReaction(request.card_instance_id, request.reaction_id, request.decline)
    if isinstance(request, DiscardCardsCommandRequest):
        return DiscardCards(tuple(request.card_instance_ids))
    if isinstance(request, MulliganCommandRequest):
        return ChooseMulligan(request.decision)
    if isinstance(request, GainCreditCommandRequest):
        return GainCredit()
    if isinstance(request, DrawCardCommandRequest):
        return DrawCard()
    if isinstance(request, RemoveTagCommandRequest):
        return RemoveTag()
    if isinstance(request, PurgeVirusCountersCommandRequest):
        return PurgeVirusCounters()
    raise ValueError("Unsupported API command")


def command_request_from_game(command: GameCommand | DiscardSelection | CardSelection) -> LegalAction:
    """Project a domain command into the shape clients can submit and display."""
    if isinstance(command, CardSelection):
        return CardSelectionAction(eligible_card_instance_ids=list(command.eligible_card_instance_ids),
                                   required_count=command.required_count, optional=command.optional, up_to=command.up_to)
    if isinstance(command, SelectEffectCards):
        return SelectEffectCardsRequest(type="select_effect_cards", card_instance_ids=list(command.card_instance_ids))
    if isinstance(command, ChooseEffectOption):
        return ChooseEffectOptionRequest(type="choose_effect_option", option_id=command.option_id)
    if isinstance(command, TrashResource):
        return TrashResourceRequest(type="trash_resource", card_instance_id=command.card_instance_id)
    if isinstance(command, RunServer):
        return RunServerCommandRequest(type="run_server", server_id=command.server_id)
    if isinstance(command, ContinueRun):
        return ContinueRunCommandRequest(type="continue_run")
    if isinstance(command, JackOut):
        return JackOutCommandRequest(type="jack_out")
    if isinstance(command, AccessCard):
        return AccessCardCommandRequest(type="access_card", target=command.target)
    if isinstance(command, FinishAccess):
        return FinishAccessCommandRequest(type="finish_access", trash=command.trash)
    if isinstance(command, TrashPrograms):
        return TrashProgramsCommandRequest(type="trash_programs", card_instance_ids=list(command.card_instance_ids))
    if isinstance(command, PlayCard):
        return PlayCardCommandRequest(type="play_card", card_instance_id=command.card_instance_id, server_id=command.server_id)
    if isinstance(command, InstallCard):
        return InstallCardCommandRequest(type="install_card", card_instance_id=command.card_instance_id,
            server_id=command.server_id, trash_card_instance_ids=list(command.trash_card_instance_ids),
            host_card_instance_id=command.host_card_instance_id)
    if isinstance(command, RezCard):
        return RezCardCommandRequest(type="rez_card", card_instance_id=command.card_instance_id)
    if isinstance(command, UseCardAbility):
        return UseCardAbilityCommandRequest(type="use_card_ability", card_instance_id=command.card_instance_id,
                                           ability_id=command.ability_id, subroutine_indices=list(command.subroutine_indices), server_id=command.server_id)
    if isinstance(command, AdvanceCard):
        return AdvanceCardCommandRequest(type="advance_card", card_instance_id=command.card_instance_id)
    if isinstance(command, ScoreAgenda):
        return ScoreAgendaCommandRequest(type="score_agenda", card_instance_id=command.card_instance_id)
    if isinstance(command, PassPriority):
        return PassPriorityCommandRequest(type="pass_priority")
    if isinstance(command, ResolveReaction):
        return ResolveReactionCommandRequest(type="resolve_reaction", card_instance_id=command.card_instance_id,
            reaction_id=command.reaction_id, decline=command.decline)
    if isinstance(command, DiscardSelection):
        return DiscardCardsAction(eligible_card_instance_ids=list(command.eligible_card_instance_ids),
                                 required_count=command.required_count)
    if isinstance(command, DiscardCards):
        return DiscardCardsCommandRequest(type="discard_cards", card_instance_ids=list(command.card_instance_ids))
    if isinstance(command, ChooseMulligan):
        return MulliganCommandRequest(type="choose_mulligan", decision=command.decision)
    if isinstance(command, GainCredit):
        return GainCreditCommandRequest(type="gain_credit")
    if isinstance(command, DrawCard):
        return DrawCardCommandRequest(type="draw_card")
    if isinstance(command, RemoveTag):
        return RemoveTagCommandRequest(type="remove_tag")
    if isinstance(command, PurgeVirusCounters):
        return PurgeVirusCountersCommandRequest(type="purge_virus_counters")
    raise ValueError("Unsupported game command")


class SetupView(ApiModel):
    pending_player: PlayerRole | None
    decisions: dict[PlayerRole, MulliganDecision]


class CardView(ApiModel):
    instance_id: UUID
    definition_id: str
    title: str
    card_type: CardType
    ability_labels: dict[str, str] = Field(default_factory=dict)
    paid_ability_ids: list[str] = Field(default_factory=list)
    strength: int | None = None
    effective_install_cost: int | None = None
    effective_rez_cost: int | None = None


class HiddenSelectionCardView(ApiModel):
    """An installed target can be chosen by position without revealing its identity."""

    instance_id: UUID
    title: str = "Unrezzed ice"


class InstalledCardView(ApiModel):
    """Public position and counters, with identity concealed from the Runner."""

    instance_id: UUID
    server_id: ServerId | None
    controller_id: UUID
    rezzed: bool
    area: str
    host_card_instance_id: UUID | None = None
    ice_distance_from_server: int | None
    face_up: bool
    counters: dict[str, int]
    card: CardView | None


class TurnView(ApiModel):
    number: int
    active_player: PlayerRole
    phase: str
    cannot_score: bool = False


class PlayerSummary(ApiModel):
    """Public information shared for either player in a game view."""

    player_id: UUID
    role: PlayerRole
    credits: int
    clicks_remaining: int
    tags: int
    memory_limit: int | None = None
    memory_used: int = 0
    identity: CardView
    deck_count: int
    maximum_hand_size: int
    agenda_points: int
    score_area: list[CardView]
    discard_count: int
    discard_pile: list[CardView]


class OwnPlayerView(PlayerSummary):
    hand: list[CardView]


class OpponentView(PlayerSummary):
    hand_count: int


class SubroutineView(ApiModel):
    """An ordered public subroutine and its status in the current encounter."""

    index: int = Field(ge=0)
    label: str
    broken: bool


class RunView(ApiModel):
    """Public run progress and viewer-specific temporary access information."""

    server_id: ServerId
    phase: str
    successful: bool
    cannot_steal_or_trash: bool = False
    bad_publicity_credits: int
    event_credits: int = 0
    source_event: CardView | None = None
    source_card: CardView | None = None
    source_ability_id: str | None = None
    access_count: int = 0
    agendas_stolen: int = 0
    central_access_limit: int = 1
    central_accesses: int = 0
    current_card: CardView | None
    current_ice_id: UUID | None = None
    current_ice: CardView | None = None
    subroutines: list[SubroutineView] = Field(default_factory=list)
    rez_allowed: bool = False
    accessed_cards: list[CardView]
    access_note: str = "Access abilities resolve before trashing or stealing."


class ReactionView(ApiModel):
    """Reaction labels visible only to the player choosing their next ability."""

    reaction_id: UUID
    card_instance_id: UUID
    label: str
    optional: bool


class InitialGameView(ApiModel):
    """Current state projection with private cards and legal actions for one viewer."""

    game_id: UUID
    viewer_player_id: UUID
    viewer_role: PlayerRole
    setup: SetupView
    turn: TurnView | None
    winner: PlayerRole | None
    decision: DecisionView | None
    legal_actions: list[LegalAction] = Field(
        description="Commands currently available to this viewer; submission is validated again",
    )
    you: OwnPlayerView
    opponent: OpponentView
    servers: list[str | int]
    reactions: list[ReactionView] = Field(default_factory=list)
    effect_options: dict[str, str] = Field(default_factory=dict)
    selection_cards: list[CardView | HiddenSelectionCardView] = Field(default_factory=list)
    revealed_cards: list[CardView] = Field(default_factory=list)
    removed_cards: list[CardView] = Field(default_factory=list)
    effect_install: bool = False
    installed_cards: list[InstalledCardView]
    run: RunView | None = None

    @classmethod
    def from_game_state(
        cls,
        state: GameState,
        viewer_player_id: UUID,
        card_definitions: Mapping[str, CardDefinition],
    ) -> "InitialGameView":
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
        return cls(
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
                command_request_from_game(command)
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


class GameCreatedResponse(ApiModel):
    opponent_profile_id: str
    player_token: str
    reconnect_timeout_seconds: float
    initial_state: InitialGameView


class SessionSnapshot(ApiModel):
    type: Literal["state"] = "state"
    revision: int = 0
    state: InitialGameView


class OpponentProfileSummary(ApiModel):
    profile_id: str
    name: str

    @classmethod
    def from_profile(cls, profile: OpponentProfile) -> "OpponentProfileSummary":
        return cls(profile_id=profile.profile_id, name=profile.name)


class PreconstructedDeckResponse(ApiModel):
    deck_id: str
    name: str
    role: PlayerRole
    decklist: DeckSelection

    @classmethod
    def from_deck(cls, deck: PreconstructedDeck) -> "PreconstructedDeckResponse":
        return cls(**deck.model_dump())


class CardCatalogEntry(ApiModel):
    definition_id: str
    title: str
    side: CardSide
    card_type: CardType
    faction_code: str
    subtypes: tuple[str, ...]
    printed_text: str
    is_unique: bool
    play_cost: int | None
    install_cost: int | None
    rez_cost: int | None
    trash_cost: int | None
    advancement_requirement: int | None
    agenda_points: int | None
    strength: int | None
    memory_cost: int | None
    influence_cost: int | None
    deck_limit: int | None
    base_link: int | None
    minimum_deck_size: int | None
    influence_limit: int | None
    flavor_text: str | None
    illustrator: str | None

    @classmethod
    def from_definition(cls, definition: CardDefinition) -> "CardCatalogEntry":
        return cls(**definition.model_dump(exclude={"play_requirement", "play_instructions", "score_instructions", "install_instructions",
                                                   "rez_instructions", "action_abilities", "trash_when_empty", "paid_abilities", "subroutines", "advanceable", "remote_strength_bonus", "triggered_abilities", "first_basic_draw_amount", "install_on_ice", "memory_bonus", "hand_size_bonus", "first_program_discount", "successful_run_install_discount", "strength_per_icebreaker", "advancement_strength_threshold", "advancement_strength_bonus"}))
