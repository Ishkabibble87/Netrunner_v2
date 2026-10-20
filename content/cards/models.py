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


class LoadCredits(CardContentModel):
    type: Literal["load_credits"]
    amount: int = Field(gt=0)


class TakeCredits(CardContentModel):
    type: Literal["take_credits"]
    amount: int = Field(gt=0)


class GainAccessCredits(CardContentModel):
    """Gain one credit per access occurrence in the triggering run."""

    type: Literal["gain_access_credits"] = "gain_access_credits"


class AdditionalAccesses(CardContentModel):
    type: Literal["additional_accesses"] = "additional_accesses"
    amount: int = Field(gt=0)


class InitiateRun(CardContentModel):
    """Run targets and effects scoped to the run created by a card ability."""

    type: Literal["initiate_run"]
    servers: tuple[Literal["hq", "rd", "archives"], ...] | None = None
    successful_draw: int = Field(default=0, ge=0)
    additional_accesses: int = Field(default=0, ge=0)
    hosted_credits: int = Field(default=0, ge=0)
    unrun_this_turn: bool = False
    successful_take_credits: int = Field(default=0, ge=0)


class TrashSource(CardContentModel):
    type: Literal["trash_source"] = "trash_source"


PlayInstruction = Annotated[GainCredits | GainAccessCredits | AdditionalAccesses | DrawCards | LoseClickIfAny | LoadCredits | TakeCredits | InitiateRun | TrashSource, Field(discriminator="type")]


class TriggeredAbility(CardContentModel):
    """A reaction to a rule fact, with authored conditions and usage limits."""

    ability_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    trigger: Literal["turn_begin", "turn_end", "discard_end", "action", "advance", "install", "rez", "score", "empty", "run_success", "breach", "access_trash", "run_end"]
    scope: Literal["controller", "self", "any"] = "controller"
    condition: Literal["previously_unadvanced", "facedown_archives"] | None = None
    servers: tuple[Literal["hq", "rd", "archives"], ...] | None = None
    optional: bool = False
    once_per_turn: bool = False
    first_each_turn: bool = False
    instructions: tuple[PlayInstruction, ...]


class ActionAbility(CardContentModel):
    """A named action with a click cost, effects, and optional usage limit."""

    ability_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    click_cost: int = Field(default=1, gt=0)
    once_per_turn: bool = False
    instructions: tuple[PlayInstruction, ...]


class PaidAbility(CardContentModel):
    """The first breaker abilities: encounter strength and barrier interfaces."""

    ability_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    type: Literal["boost_strength", "break_subroutines"]
    credit_cost: int = Field(ge=0)
    amount: int = Field(gt=0)
    subtype: str | None = None


class EndRun(CardContentModel):
    type: Literal["end_run"] = "end_run"
    label: str = "End the run"


class AuthoredAbilities(CardContentModel):
    """Executable instructions authored separately from imported display metadata."""

    play_instructions: tuple[PlayInstruction, ...] | None = None
    score_instructions: tuple[PlayInstruction, ...] | None = None
    install_instructions: tuple[PlayInstruction, ...] | None = None
    rez_instructions: tuple[PlayInstruction, ...] | None = None
    action_abilities: tuple[ActionAbility, ...] = ()
    trash_when_empty: bool = False
    paid_abilities: tuple[PaidAbility, ...] = ()
    subroutines: tuple[EndRun, ...] | None = None
    remote_strength_bonus: int = Field(default=0, ge=0)
    triggered_abilities: tuple[TriggeredAbility, ...] = ()
    first_basic_draw_amount: int | None = Field(default=None, gt=0)


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
    install_instructions: tuple[PlayInstruction, ...] | None = None
    rez_instructions: tuple[PlayInstruction, ...] | None = None
    action_abilities: tuple[ActionAbility, ...] = ()
    trash_when_empty: bool = False
    paid_abilities: tuple[PaidAbility, ...] = ()
    subroutines: tuple[EndRun, ...] | None = None
    remote_strength_bonus: int = Field(default=0, ge=0)
    triggered_abilities: tuple[TriggeredAbility, ...] = ()
    first_basic_draw_amount: int | None = Field(default=None, gt=0)

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
