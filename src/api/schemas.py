"""Define validated API requests and player-visible response models.

These Pydantic schemas describe the HTTP and WebSocket boundary: they shape
incoming commands and catalog/setup responses. Command adapters and projection
builders live in focused modules while these models define what the API accepts
and returns. Game rules and authoritative state belong in ``src/game``.
"""

from collections.abc import Mapping
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from content.cards.models import CardDefinition, CardSide, CardType
from content.decks.models import PreconstructedDeck
from content.opponents.models import OpponentProfile
from game.models import (
    DeckSelection,
    GameState,
    HistoryDetail,
    MulliganDecision,
    PlayerRole,
    ServerId,
)


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


class HistoryView(ApiModel):
    """A history occurrence containing only the authenticated viewer's details."""

    entry_id: UUID
    sequence: int
    turn_number: int | None
    turn_player: PlayerRole | None
    group_id: UUID
    parent_id: UUID | None
    run_id: UUID | None
    actor: PlayerRole | None
    kind: str
    detail: HistoryDetail


class InitialGameView(ApiModel):
    """Current state projection with private cards and legal actions for one viewer."""

    game_id: UUID
    history: list[HistoryView] = Field(default_factory=list)
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
        """Build this view through the dedicated game-state projection module."""
        from .projections import game_view_from_state

        return game_view_from_state(state, viewer_player_id, card_definitions)


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
        return cls(**definition.model_dump(include=set(cls.model_fields)))
