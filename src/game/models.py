"""Small, validated domain models for an in-progress game.

Game creation and waiting-room lifecycle belong in ``src.application``. This
module starts with the state needed once both players have joined.
"""

from enum import StrEnum
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


class PlayerState(DomainModel):
    """Changing values that belong to one player."""

    player_id: UUID
    role: PlayerRole
    credits: int = Field(ge=0)
    clicks_remaining: int = Field(default=0, ge=0)
    tags: int = Field(default=0, ge=0, description="Runner tags; zero for the Corp")
    bad_publicity: int = Field(default=0, ge=0, description="Corp bad publicity counters")
    core_damage: int = Field(default=0, ge=0, description="Runner core damage counters")

    @model_validator(mode="after")
    def counters_match_role(self) -> "PlayerState":
        if self.role is PlayerRole.CORP and (self.tags or self.core_damage):
            raise ValueError("Tags and core damage counters belong to the Runner")
        if self.role is PlayerRole.RUNNER and self.bad_publicity:
            raise ValueError("Bad publicity counters belong to the Corp")
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

    @model_validator(mode="after")
    def player_fields_match_roles(self) -> "GameState":
        if self.corp.player_id == self.runner.player_id:
            raise ValueError("Corp and Runner must be different players")
        if self.corp.role is not PlayerRole.CORP:
            raise ValueError("corp must have the Corp role")
        if self.runner.role is not PlayerRole.RUNNER:
            raise ValueError("runner must have the Runner role")
        return self
