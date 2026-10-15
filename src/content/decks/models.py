"""Named, server-provided decks available during game setup."""

from pydantic import BaseModel, ConfigDict, Field

from game.models import DeckSelection, PlayerRole


class PreconstructedDeck(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    deck_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: PlayerRole
    decklist: DeckSelection
