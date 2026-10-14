"""Small, validated domain models for an in-progress game.

Game creation and waiting-room lifecycle belong in ``src.application``. This
module starts with the state needed once both players have joined.
"""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class CardZone(StrEnum):
    """Zones that can contain cards (the bank contains counters, never cards)."""

    DECK = "deck"
    HAND = "hand"
    DISCARD_PILE = "discard_pile"
    SCORE_AREA = "score_area"
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
    face_up: bool | None = Field(
        default=None,
        description="Face status when the current zone or game state uses it; otherwise None",
    )
    installed: bool = False
    rezzed: bool = False

    @model_validator(mode="after")
    def instance_state_is_consistent(self) -> "CardInstance":
        face_status_zones = {
            CardZone.PLAY_AREA,
            CardZone.DISCARD_PILE,
            CardZone.SCORE_AREA,
            CardZone.SET_ASIDE,
            CardZone.REMOVED_FROM_GAME,
        }
        if self.location.zone in face_status_zones and self.face_up is None:
            raise ValueError(f"Cards in {self.location.zone.value} need an explicit face status")
        if self.location.server is not None and not self.installed:
            raise ValueError("A card in a server location must be installed")
        if self.rezzed and not self.installed:
            raise ValueError("Only an installed card can be rezzed")
        if self.rezzed and self.face_up is not True:
            raise ValueError("A rezzed card must be face up")
        if self.rezzed and self.location.server is None:
            raise ValueError("A rezzed card must be installed in a server")
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

    @model_validator(mode="after")
    def runner_has_no_draw_phase(self) -> "TurnState":
        if self.active_player is PlayerRole.RUNNER and self.phase is TurnPhase.DRAW:
            raise ValueError("The Runner does not have a draw phase")
        return self


class GameState(DomainModel):
    """Authoritative state for a game that has started."""

    game_id: UUID
    corp: PlayerState
    runner: PlayerState
    turn: TurnState
    servers: list[ServerId] = Field(default_factory=lambda: list(CentralServer))

    @model_validator(mode="after")
    def player_fields_match_roles(self) -> "GameState":
        if self.corp.player_id == self.runner.player_id:
            raise ValueError("Corp and Runner must be different players")
        if self.corp.role is not PlayerRole.CORP:
            raise ValueError("corp must have the Corp role")
        if self.runner.role is not PlayerRole.RUNNER:
            raise ValueError("runner must have the Runner role")
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
