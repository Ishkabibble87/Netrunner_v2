"""Normalized static card definitions used by the game engine.

Set membership and printings are intentionally modeled separately from a card
definition. Executable play instructions are authored separately from imported printed
rules text, which is retained only for display.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class CardContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CardSide(StrEnum):
    CORP = "corp"
    RUNNER = "runner"


class CardType(StrEnum):
    IDENTITY = "identity"
    AGENDA = "agenda"
    ASSET = "asset"
    ICE = "ice"
    OPERATION = "operation"
    UPGRADE = "upgrade"
    EVENT = "event"
    HARDWARE = "hardware"
    PROGRAM = "program"
    RESOURCE = "resource"


class GainCredits(CardContentModel):
    type: Literal["gain_credits"]
    amount: int = Field(gt=0)


class DrawCards(CardContentModel):
    type: Literal["draw_cards"]
    amount: int = Field(gt=0)


class LoseClickIfAny(CardContentModel):
    type: Literal["lose_click_if_any"]


PlayInstruction = Annotated[GainCredits | DrawCards | LoseClickIfAny, Field(discriminator="type")]


class AuthoredAbilities(CardContentModel):
    """Executable instructions authored separately from imported display metadata."""

    play_instructions: tuple[PlayInstruction, ...] | None = None
    score_instructions: tuple[PlayInstruction, ...] | None = None


class CardDefinition(CardContentModel):
    """Static gameplay and display data for one card identity."""

    definition_id: str = Field(
        min_length=1,
        description="Unique card code from the NSG card data, distinct from game instance IDs",
    )
    title: str = Field(min_length=1)
    side: CardSide
    card_type: CardType
    faction_code: str = Field(min_length=1)
    subtypes: tuple[str, ...] = ()
    printed_text: str = ""
    play_instructions: tuple[PlayInstruction, ...] | None = Field(
        default=None, description="Authored executable instructions; None means unsupported",
    )
    score_instructions: tuple[PlayInstruction, ...] | None = None
    is_unique: bool = False

    play_cost: int | None = Field(default=None, ge=0)
    install_cost: int | None = Field(default=None, ge=0)
    rez_cost: int | None = Field(default=None, ge=0)
    trash_cost: int | None = Field(default=None, ge=0)
    advancement_requirement: int | None = Field(default=None, ge=0)
    agenda_points: int | None = Field(default=None, ge=0)
    strength: int | None = Field(default=None, ge=0)
    memory_cost: int | None = Field(default=None, ge=0)
    influence_cost: int | None = Field(default=None, ge=0)
    deck_limit: int | None = Field(default=None, ge=0)
    base_link: int | None = Field(default=None, ge=0)
    minimum_deck_size: int | None = Field(default=None, ge=0)
    influence_limit: int | None = Field(default=None, ge=0)

    flavor_text: str | None = None
    illustrator: str | None = None
