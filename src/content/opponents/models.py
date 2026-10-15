"""Normalized profile data for selectable scripted opponents."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from game.models import DeckSelection, PlayerRole


class OpponentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    profile_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    deck_selections: dict[PlayerRole, DeckSelection]

    @model_validator(mode="after")
    def has_both_sides(self) -> "OpponentProfile":
        if set(self.deck_selections) != {PlayerRole.CORP, PlayerRole.RUNNER}:
            raise ValueError("An opponent profile needs a Corp and Runner deck")
        return self
