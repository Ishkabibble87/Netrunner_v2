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
from game.commands import (
    AdvanceCard,
    ChooseMulligan,
    DiscardCards,
    DiscardSelection,
    DrawCard,
    GainCredit,
    GameCommand,
    InstallCard,
    PassPriority,
    PlayCard,
    PurgeVirusCounters,
    RemoveTag,
    ResolveReaction,
    ScoreAgenda,
)
from game.engine import legal_actions_for, pending_decision
from game.installation import agenda_points
from game.models import (
    CardZone,
    DeckSelection,
    GameState,
    MulliganDecision,
    PlayerRole,
    PlayerState,
    ServerId,
)
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


class PlayCardCommandRequest(ApiModel):
    """Identify the physical copy; costs and instructions are server-owned."""

    type: Literal["play_card"]
    card_instance_id: UUID


class DiscardCardsCommandRequest(ApiModel):
    """Submit the complete set of excess cards; the engine checks eligibility."""

    type: Literal["discard_cards"]
    card_instance_ids: list[UUID] = Field(min_length=1)


class DiscardCardsAction(ApiModel):
    """A private selection action, avoiding combinatorial command enumeration."""

    type: Literal["discard_cards"] = "discard_cards"
    eligible_card_instance_ids: list[UUID]
    required_count: int = Field(gt=0)


class DecisionView(ApiModel):
    """Public context shared by both players, never hidden card choices."""

    player: PlayerRole
    type: Literal["choose_opening_hand", "choose_action", "discard_to_hand_limit", "paid_ability_window", "resolve_reaction"]
    cards_to_discard: int | None = None


class PurgeVirusCountersCommandRequest(ApiModel):
    """Request the Corp's three-click virus-counter purge action."""

    type: Literal["purge_virus_counters"]


class InstallCardCommandRequest(ApiModel):
    type: Literal["install_card"]
    card_instance_id: UUID
    server_id: ServerId | Literal["new_remote"]
    trash_card_instance_ids: list[UUID] = Field(default_factory=list)


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


PlayerCommandRequest = Annotated[
    MulliganCommandRequest
    | GainCreditCommandRequest
    | DrawCardCommandRequest
    | RemoveTagCommandRequest
    | PurgeVirusCountersCommandRequest
    | DiscardCardsCommandRequest
    | PlayCardCommandRequest
    | InstallCardCommandRequest
    | AdvanceCardCommandRequest
    | ScoreAgendaCommandRequest
    | PassPriorityCommandRequest
    | ResolveReactionCommandRequest,
    Field(discriminator="type"),
]


LegalAction = PlayerCommandRequest | DiscardCardsAction


def game_command_from_request(request: PlayerCommandRequest) -> GameCommand:
    """Translate a validated API command into the transport-independent game type."""
    if isinstance(request, PlayCardCommandRequest):
        return PlayCard(request.card_instance_id)
    if isinstance(request, InstallCardCommandRequest):
        return InstallCard(request.card_instance_id, request.server_id, tuple(request.trash_card_instance_ids))
    if isinstance(request, AdvanceCardCommandRequest):
        return AdvanceCard(request.card_instance_id)
    if isinstance(request, ScoreAgendaCommandRequest):
        return ScoreAgenda(request.card_instance_id)
    if isinstance(request, PassPriorityCommandRequest):
        return PassPriority()
    if isinstance(request, ResolveReactionCommandRequest):
        return ResolveReaction(request.card_instance_id)
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


def command_request_from_game(command: GameCommand | DiscardSelection) -> LegalAction:
    """Project a domain command into the shape clients can submit and display."""
    if isinstance(command, PlayCard):
        return PlayCardCommandRequest(type="play_card", card_instance_id=command.card_instance_id)
    if isinstance(command, InstallCard):
        return InstallCardCommandRequest(type="install_card", card_instance_id=command.card_instance_id,
            server_id=command.server_id, trash_card_instance_ids=list(command.trash_card_instance_ids))
    if isinstance(command, AdvanceCard):
        return AdvanceCardCommandRequest(type="advance_card", card_instance_id=command.card_instance_id)
    if isinstance(command, ScoreAgenda):
        return ScoreAgendaCommandRequest(type="score_agenda", card_instance_id=command.card_instance_id)
    if isinstance(command, PassPriority):
        return PassPriorityCommandRequest(type="pass_priority")
    if isinstance(command, ResolveReaction):
        return ResolveReactionCommandRequest(type="resolve_reaction", card_instance_id=command.card_instance_id)
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


class InstalledCardView(ApiModel):
    """Public position and counters, with identity concealed from the Runner."""

    instance_id: UUID
    server_id: ServerId
    area: str
    ice_distance_from_server: int | None
    face_up: bool
    counters: dict[str, int]
    card: CardView | None


class TurnView(ApiModel):
    number: int
    active_player: PlayerRole
    phase: str


class PlayerSummary(ApiModel):
    """Public information shared for either player in a game view."""

    player_id: UUID
    role: PlayerRole
    credits: int
    clicks_remaining: int
    tags: int
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
    installed_cards: list[InstalledCardView]

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
                maximum_hand_size=maximum_hand_size(player),
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
        decision = pending_decision(state)
        return cls(
            game_id=state.game_id,
            viewer_player_id=viewer_player_id,
            viewer_role=you.role,
            setup=SetupView(**state.setup.model_dump()),
            turn=TurnView(
                number=state.turn.number,
                active_player=state.turn.active_player,
                phase=state.turn.phase.value,
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
                instance_id=c.instance_id, server_id=c.location.server.server_id,
                area=c.location.server.area.value, ice_distance_from_server=c.location.server.ice_distance_from_server,
                face_up=bool(c.face_up), counters=c.counters,
                card=card_view(c.instance_id) if c.face_up or c.controller_id == viewer_player_id else None)
                for c in state.card_instances.values() if c.installed and c.location.server],
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
        return cls(**definition.model_dump(exclude={"play_instructions", "score_instructions"}))
