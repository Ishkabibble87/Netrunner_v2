"""Small, validated domain models for an in-progress game.

Game creation and waiting-room lifecycle belong in ``src.application``. This
module starts with the state needed once both players have joined.
"""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from content.cards.models import PlayInstruction


class DomainModel(BaseModel):
    """Shared Pydantic configuration for game-domain models."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PlayerRole(StrEnum):
    CORP = "corp"
    RUNNER = "runner"


class TurnPhase(StrEnum):
    DRAW = "draw"
    ACTION = "action"
    DISCARD = "discard"


class MulliganDecision(StrEnum):
    KEEP = "keep"
    MULLIGAN = "mulligan"


class SetupState(DomainModel):
    """Opening-hand decisions, resolved once per player in Corp-first order."""

    pending_player: PlayerRole | None = PlayerRole.CORP
    decisions: dict[PlayerRole, MulliganDecision] = Field(default_factory=dict)


class CardZone(StrEnum):
    """Zones that can contain cards (the bank contains counters, never cards)."""

    DECK = "deck"
    HAND = "hand"
    DISCARD_PILE = "discard_pile"
    SCORE_AREA = "score_area"
    IDENTITY = "identity"
    PLAY_AREA = "play_area"
    SET_ASIDE = "set_aside"
    REMOVED_FROM_GAME = "removed_from_game"


class ServerArea(StrEnum):
    ROOT = "root"
    ICE = "ice"


class CentralServer(StrEnum):
    """The three Corp servers that exist for the entire game."""

    HQ = "hq"
    RD = "rd"
    ARCHIVES = "archives"


# Remote server numbers are current row positions, starting nearest the central servers.
RemoteServerPosition = Annotated[
    int,
    Field(ge=1, description="One-based current position among remote servers"),
]
ServerId = CentralServer | RemoteServerPosition


NonNegativeCounter = Annotated[int, Field(ge=0)]
PositiveCardCount = Annotated[int, Field(gt=0)]


class ServerLocation(DomainModel):
    """A specific location in a Corp server; ice distance counts from the server."""

    server_id: ServerId = Field(description="A CentralServer ID or current remote-server position")
    area: ServerArea
    ice_distance_from_server: int | None = Field(
        default=None,
        ge=0,
        description="Number of other ice between this ice and its server; 0 is innermost",
    )

    @model_validator(mode="after")
    def ice_distance_matches_area(self) -> "ServerLocation":
        if self.area is ServerArea.ICE and self.ice_distance_from_server is None:
            raise ValueError("An ice location requires its distance from the server")
        if self.area is ServerArea.ROOT and self.ice_distance_from_server is not None:
            raise ValueError("A root location cannot have an ice distance")
        return self


class ZoneLocation(DomainModel):
    """A card's zone and, where applicable, its more specific location."""

    zone: CardZone
    player_id: UUID | None = Field(
        default=None,
        description="Player whose deck, hand, discard pile, or score area this is",
    )
    deck_position: int | None = Field(
        default=None,
        ge=1,
        description="One-based distance from the top of the deck",
    )
    server: ServerLocation | None = None
    host_card_instance_id: UUID | None = None
    set_aside_group_id: UUID | None = None

    @model_validator(mode="after")
    def location_fields_match_zone(self) -> "ZoneLocation":
        player_zones = {
            CardZone.DECK,
            CardZone.HAND,
            CardZone.DISCARD_PILE,
            CardZone.SCORE_AREA,
            CardZone.IDENTITY,
        }
        if self.zone in player_zones and self.player_id is None:
            raise ValueError(f"The {self.zone.value} zone requires a player_id")
        if self.zone not in player_zones and self.player_id is not None:
            raise ValueError(f"The {self.zone.value} zone is shared and cannot have a player_id")

        if (self.zone is CardZone.DECK) != (self.deck_position is not None):
            raise ValueError("Only cards in a deck have a deck_position, and every deck card needs one")

        if self.server is not None and self.zone is not CardZone.PLAY_AREA:
            raise ValueError("Server locations are part of the play area")
        if self.host_card_instance_id is not None and self.zone is not CardZone.PLAY_AREA:
            raise ValueError("Hosted cards are in the play area")
        if self.server is not None and self.host_card_instance_id is not None:
            raise ValueError("A card location cannot be both a server location and hosted on a card")

        if self.zone is not CardZone.SET_ASIDE and self.set_aside_group_id is not None:
            raise ValueError("Only set-aside cards can have a set-aside group ID")
        return self


class CardInstance(DomainModel):
    """One physical copy of a card in a game, separate from its static definition."""

    instance_id: UUID
    definition_id: str = Field(min_length=1)
    owner_id: UUID
    controller_id: UUID | None = None
    location: ZoneLocation
    counters: dict[str, NonNegativeCounter] = Field(default_factory=dict)
    loaded_counter_types: set[str] = Field(default_factory=set)
    face_up: bool | None = Field(
        default=None,
        description="Face status when the current zone or game state uses it; otherwise None",
    )
    installed: bool = False
    rezzed: bool = False
    ability_last_used_turn: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def instance_state_is_consistent(self) -> "CardInstance":
        face_status_zones = {
            CardZone.PLAY_AREA,
            CardZone.DISCARD_PILE,
            CardZone.SCORE_AREA,
            CardZone.IDENTITY,
            CardZone.SET_ASIDE,
            CardZone.REMOVED_FROM_GAME,
        }
        if self.location.zone in face_status_zones and self.face_up is None:
            raise ValueError(f"Cards in {self.location.zone.value} need an explicit face status")
        if self.location.server is not None and not self.installed:
            raise ValueError("A card in a server location must be installed")
        if self.installed and self.location.zone is not CardZone.PLAY_AREA:
            raise ValueError("An installed card must be in the play area")
        if self.rezzed and not self.installed:
            raise ValueError("Only an installed card can be rezzed")
        if self.rezzed and self.face_up is not True:
            raise ValueError("A rezzed card must be face up")
        if self.rezzed and self.location.server is None:
            raise ValueError("A rezzed card must be installed in a server")
        if self.location.zone is CardZone.IDENTITY and self.face_up is not True:
            raise ValueError("An identity card must be face up")
        return self


class DeckSelection(DomainModel):
    """The chosen identity and card-definition counts used to build a player's deck."""

    identity_definition_id: str = Field(min_length=1)
    card_counts: dict[str, PositiveCardCount] = Field(
        default_factory=dict,
        description="Main-deck card definition IDs and their selected copy counts",
    )

    @model_validator(mode="after")
    def deck_does_not_list_identity(self) -> "DeckSelection":
        if self.identity_definition_id in self.card_counts:
            raise ValueError("The identity belongs in its own field, not the main deck")
        return self


class PlayerState(DomainModel):
    """Changing values that belong to one player."""

    player_id: UUID
    role: PlayerRole
    credits: int = Field(ge=0)
    clicks_remaining: int = Field(default=0, ge=0)
    tags: int = Field(default=0, ge=0, description="Runner tags; zero for the Corp")
    bad_publicity: int = Field(default=0, ge=0, description="Corp bad publicity counters")
    core_damage: int = Field(default=0, ge=0, description="Runner core damage counters")
    memory_limit: int | None = Field(
        default=None,
        ge=0,
        description="Runner's maximum available memory, including active modifiers",
    )
    deck_selection: DeckSelection
    identity_card_instance_id: UUID

    @model_validator(mode="after")
    def counters_match_role(self) -> "PlayerState":
        if self.role is PlayerRole.CORP and (self.tags or self.core_damage):
            raise ValueError("Tags and core damage counters belong to the Runner")
        if self.role is PlayerRole.RUNNER and self.bad_publicity:
            raise ValueError("Bad publicity counters belong to the Corp")
        if self.role is PlayerRole.CORP and self.memory_limit is not None:
            raise ValueError("Memory limit belongs to the Runner")
        if self.role is PlayerRole.RUNNER and self.memory_limit is None:
            raise ValueError("The Runner's maximum memory must be set from their identity and effects")
        return self


class TurnState(DomainModel):
    """The current turn; click count lives with its owning player."""

    number: int = Field(ge=1)
    active_player: PlayerRole
    phase: TurnPhase
    step: Literal["start", "turn_begin", "mandatory_draw", "before_action", "action", "discard", "end", "next_turn"] = "action"
    occurrences: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def runner_has_no_draw_phase(self) -> "TurnState":
        if self.active_player is PlayerRole.RUNNER and self.phase is TurnPhase.DRAW:
            raise ValueError("The Runner does not have a draw phase")
        return self


class RuleFact(DomainModel):
    """An ephemeral rules occurrence, separate from commands and public updates."""

    type: str
    player: PlayerRole
    card_instance_id: UUID | None = None
    previous_advancement: int | None = None
    action: str | None = None


class PendingReaction(DomainModel):
    """One occurrence of an ability, retaining its source and triggering context."""

    reaction_id: UUID
    card_instance_id: UUID
    ability_id: str
    label: str
    player: PlayerRole
    fact: RuleFact
    source_location: ZoneLocation
    instructions: tuple[PlayInstruction, ...]
    optional: bool = False
    once_per_turn: bool = False
    condition: str | None = None


class EffectContinuation(DomainModel):
    """The next instruction of a resolving ability, suspended by nested reactions."""

    reaction: PendingReaction
    next_instruction: int = 0


class PriorityWindow(DomainModel):
    """Paid priority or a fixed reaction batch, with a resumable instruction.

    Players without supported choices pass automatically. Empty scoring windows
    remain visible while Corp cards are installed, concealing agenda eligibility.
    Nested reactions retain this entire window on GameState.suspended_windows.
    """

    kind: Literal["paid", "reaction"]
    player: PlayerRole
    reactions: list[PendingReaction] = Field(default_factory=list)
    resolving: EffectContinuation | None = None
    run_continuation: Literal["movement", "breach", "approach", "encounter", "resolve_subroutines", "approach_next"] | None = None
    rez_allowed: bool = True
    opponent_passed: bool = False


class RunState(DomainModel):
    """A paused run and its current encounter; card visibility lasts only through its breach.

    Remote cleanup is deferred until the run ends because remote IDs are display
    positions and may be renumbered when the accessed card leaves its server.
    """

    server_id: ServerId
    phase: Literal["initiation", "approach", "encounter", "movement", "breach", "access"] = "movement"
    remaining_ice_ids: list[UUID] = Field(default_factory=list)
    current_ice_id: UUID | None = None
    broken_subroutine_indices: list[NonNegativeCounter] = Field(default_factory=list)
    strength_boosts: dict[UUID, NonNegativeCounter] = Field(default_factory=dict)
    source_event_id: UUID | None = None
    successful_draw: int = Field(default=0, ge=0)
    additional_accesses: int = Field(default=0, ge=0)
    successful: bool = False
    access_limit: int = Field(default=1, ge=1)
    central_accesses: int = Field(default=0, ge=0)
    accessed_card_ids: list[UUID] = Field(default_factory=list)
    visible_card_ids: list[UUID] = Field(default_factory=list)
    current_card_id: UUID | None = None
    bad_publicity_credits: int = Field(default=0, ge=0)


class GameState(DomainModel):
    """Authoritative state from opening-hand decisions through play."""

    game_id: UUID
    corp: PlayerState
    runner: PlayerState
    setup: SetupState = Field(default_factory=SetupState)
    turn: TurnState | None = None
    winner: PlayerRole | None = None
    window: PriorityWindow | None = None
    suspended_windows: list[PriorityWindow | None] = Field(default_factory=list)
    pending_action_fact: RuleFact | None = None
    run: RunState | None = None
    servers: list[ServerId] = Field(default_factory=lambda: list(CentralServer))
    card_instances: dict[UUID, CardInstance] = Field(default_factory=dict)

    @model_validator(mode="after")
    def player_fields_match_roles(self) -> "GameState":
        if self.corp.player_id == self.runner.player_id:
            raise ValueError("Corp and Runner must be different players")
        if self.corp.role is not PlayerRole.CORP:
            raise ValueError("corp must have the Corp role")
        if self.runner.role is not PlayerRole.RUNNER:
            raise ValueError("runner must have the Runner role")
        players_by_id = {
            self.corp.player_id: self.corp,
            self.runner.player_id: self.runner,
        }
        if any(key != card.instance_id for key, card in self.card_instances.items()):
            raise ValueError("Card instance registry keys must match instance IDs")
        deck_positions: dict[UUID, list[int]] = {}
        for card in self.card_instances.values():
            if card.owner_id not in players_by_id:
                raise ValueError("Every card owner must be a player in this game")
            if card.controller_id is not None and card.controller_id not in players_by_id:
                raise ValueError("Every card controller must be a player in this game")
            if card.location.player_id is not None and card.location.player_id not in players_by_id:
                raise ValueError("Every player-owned card zone must belong to a player in this game")
            if card.location.server is not None and card.location.server.server_id not in self.servers:
                raise ValueError("A card cannot be located on a server that does not exist")
            if card.location.zone is CardZone.DECK:
                deck_player_id = card.location.player_id
                deck_position = card.location.deck_position
                if deck_player_id is None or deck_position is None:
                    raise ValueError("A deck card needs an owner and a position")
                deck_positions.setdefault(deck_player_id, []).append(deck_position)
            host_id = card.location.host_card_instance_id
            if host_id is not None:
                host = self.card_instances.get(host_id)
                if host_id == card.instance_id:
                    raise ValueError("A card cannot host itself")
                if host is None or host.location.zone is not CardZone.PLAY_AREA:
                    raise ValueError("A hosted card must reference a card in the play area")

        for positions in deck_positions.values():
            if sorted(positions) != list(range(1, len(positions) + 1)):
                raise ValueError(
                    "Each player's deck positions must be unique and contiguous from the top"
                )

        for player in (self.corp, self.runner):
            identity = self.card_instances.get(player.identity_card_instance_id)
            if identity is None:
                raise ValueError("Each player's identity must be present in the game card registry")
            if identity.owner_id != player.player_id:
                raise ValueError("A player's identity card must be owned by that player")
            if identity.definition_id != player.deck_selection.identity_definition_id:
                raise ValueError("Identity instance must match the selected identity definition")
            if (
                identity.location.zone is not CardZone.IDENTITY
                or identity.location.player_id != player.player_id
            ):
                raise ValueError("A player's identity must be in that player's identity zone")

        for card in self.card_instances.values():
            if card.location.zone is CardZone.IDENTITY:
                identity_owner = players_by_id[card.owner_id]
                if card.instance_id != identity_owner.identity_card_instance_id:
                    raise ValueError(
                        "Each player can have only their selected identity in the identity zone"
                    )

        if self.run is not None:
            if (self.setup.pending_player is not None
                    or self.turn is None or self.turn.active_player is not PlayerRole.RUNNER
                    or self.turn.phase is not TurnPhase.ACTION):
                raise ValueError("A run requires the Runner action phase without another priority window")
            if self.run.server_id not in self.servers:
                raise ValueError("The attacked server must exist during a run")
            if self.window is not None and self.window.kind == "paid" and self.window.run_continuation is None:
                raise ValueError("A run window must have a supported run continuation")
            run = self.run
            if run.source_event_id is not None:
                event = self.card_instances.get(run.source_event_id)
                if (event is None or event.owner_id != self.runner.player_id
                        or event.location.zone is not CardZone.PLAY_AREA or not event.face_up
                        or event.installed):
                    raise ValueError("A run event must remain active in the Runner's play area")
            ice_ids = [*run.remaining_ice_ids, *([run.current_ice_id] if run.current_ice_id else [])]
            if len(ice_ids) != len(set(ice_ids)):
                raise ValueError("Run positions must reference distinct ice")
            for instance_id in ice_ids:
                ice = self.card_instances.get(instance_id)
                if (ice is None or not ice.installed or ice.location.server is None
                        or ice.location.server.area is not ServerArea.ICE
                        or ice.location.server.server_id != run.server_id):
                    raise ValueError("Run positions must reference installed ice protecting the attacked server")
            if (run.phase in {"approach", "encounter"}) != (run.current_ice_id is not None):
                raise ValueError("Only approach and encounter phases have a current ice")
            if run.phase != "encounter" and (run.strength_boosts or run.broken_subroutine_indices):
                raise ValueError("Encounter effects cannot outlast their encounter")
            if len(run.broken_subroutine_indices) != len(set(run.broken_subroutine_indices)):
                raise ValueError("Each subroutine can be broken only once per encounter")
            if (self.run.phase == "access") != (self.run.current_card_id is not None):
                raise ValueError("Only an access phase has a current card")
            if self.run.current_card_id is not None and self.run.current_card_id not in self.run.visible_card_ids:
                raise ValueError("The current access must be visible to the Runner")
            if any(i not in self.card_instances for i in self.run.visible_card_ids + self.run.accessed_card_ids):
                raise ValueError("Run access records must reference physical cards")

        if len(self.servers) != len(set(self.servers)):
            raise ValueError("Server IDs must be unique")
        present_central_servers = {
            server for server in self.servers if isinstance(server, CentralServer)
        }
        if present_central_servers != set(CentralServer):
            raise ValueError("HQ, R&D, and Archives must always be present")
        remote_positions = sorted(
            server for server in self.servers if isinstance(server, int)
        )
        if remote_positions != list(range(1, len(remote_positions) + 1)):
            raise ValueError("Remote server positions must be contiguous starting at 1")
        return self


class PendingDecision(DomainModel):
    """Public decision context, excluding private cards and executable choices."""

    player: PlayerRole
    type: Literal["choose_opening_hand", "choose_action", "discard_to_hand_limit", "paid_ability_window", "resolve_reaction", "continue_run", "choose_access", "resolve_access"]
    cards_to_discard: int | None = None
