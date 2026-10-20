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
    amount: Annotated[int, Field(gt=0)] | Literal["all"]


class PlaceVirusCounters(CardContentModel):
    """Place counters on the source or the card named by the triggering fact."""

    type: Literal["place_virus_counters"] = "place_virus_counters"
    amount: int = Field(default=1, gt=0)
    target: Literal["self", "installed_card"] = "self"


class DerezHost(CardContentModel):
    type: Literal["derez_host"] = "derez_host"
    minimum_virus_counters: int = Field(gt=0)


class GainCreditsPerVirus(CardContentModel):
    type: Literal["gain_credits_per_virus"] = "gain_credits_per_virus"
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
    additional_accesses_per_virus: bool = False
    hosted_credits: int = Field(default=0, ge=0)
    unrun_this_turn: bool = False
    successful_take_credits: int = Field(default=0, ge=0)


class TrashSource(CardContentModel):
    type: Literal["trash_source"] = "trash_source"


class DealDamage(CardContentModel):
    """One simultaneous damage packet, optionally calculated from rules context."""

    type: Literal["damage"] = "damage"
    damage_type: Literal["net", "meat"]
    amount: int = Field(default=0, ge=0)
    per_advancement: bool = False
    scored_this_turn: bool = False
    trash_source: bool = False


class GiveTags(CardContentModel):
    """One tag-taking occurrence, even when multiple tags are given."""

    type: Literal["give_tags"] = "give_tags"
    amount: int = Field(gt=0)


class EndRunEffect(CardContentModel):
    type: Literal["end_run"] = "end_run"


class PreventStealTrash(CardContentModel):
    """Ansel's run-long restriction, scoped to the current run."""

    type: Literal["prevent_steal_trash"] = "prevent_steal_trash"


class SelectCards(CardContentModel):
    """Choose a set at resolution time; effect targets allow partial resolution."""

    type: Literal["select_cards"] = "select_cards"
    player: Literal["corp", "runner"]
    zone: Literal["hand", "installed", "deck", "discard", "hand_or_discard", "set_aside"]
    side: Literal["corp", "runner", "any"]
    card_types: tuple[CardType, ...] = ()
    count: int = Field(default=1, gt=0)
    effect: Literal["trash", "place_advancement", "to_hand", "shuffle", "search", "install"] = "trash"
    subtypes: tuple[str, ...] = ()
    exclude_types: tuple[CardType, ...] = ()
    up_to: bool = False
    any_number: bool = False
    install_if_successful: bool = False
    ignore_costs: bool = False
    inward_from_source: bool = False
    amount: int = Field(default=2, gt=0)
    not_installed_this_turn: bool = False
    optional: bool = False


class TrashAccessed(CardContentModel):
    type: Literal["trash_accessed"] = "trash_accessed"


class EffectOption(CardContentModel):
    """A mode or payment branch, with full costs validated before payment."""

    option_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    credit_cost: int = Field(default=0, ge=0)
    click_cost: int = Field(default=0, ge=0)
    trash_hand_cost: int = Field(default=0, ge=0)
    instructions: tuple["PlayInstruction", ...] = ()


class ChooseEffect(CardContentModel):
    """The chooser need not be the controller who receives a mode's effects."""

    type: Literal["choose_effect"] = "choose_effect"
    player: Literal["corp", "runner"]
    options: tuple[EffectOption, ...] = Field(min_length=2)
    repeat_if_tagged: bool = False


class TaggedDamage(CardContentModel):
    """Orbital Superiority's minimal tagged/untagged scoring branch."""

    type: Literal["tagged_damage"] = "tagged_damage"
    amount: int = Field(gt=0)


PlayInstruction = Annotated[SelectCards | TrashAccessed | GiveTags | ChooseEffect | PreventStealTrash | EndRunEffect | DealDamage | TaggedDamage | PlaceVirusCounters | DerezHost | GainCreditsPerVirus | GainCredits | GainAccessCredits | AdditionalAccesses | DrawCards | LoseClickIfAny | LoadCredits | TakeCredits | InitiateRun | TrashSource, Field(discriminator="type")]


class TriggeredAbility(CardContentModel):
    """A reaction to a rule fact, with authored conditions and usage limits."""

    ability_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    trigger: Literal["turn_begin", "turn_end", "discard_end", "action", "advance", "install", "rez", "score", "steal", "encounter", "approach_server", "take_tags", "empty", "run_success", "breach", "access_trash", "run_end", "access", "mid_access"]
    scope: Literal["controller", "self", "any"] = "controller"
    condition: Literal["previously_unadvanced", "facedown_archives", "installed_virus", "successful_run", "run_on_source_server", "agenda_stolen_here", "accessed_outside_archives", "scored_from_source_server"] | None = None
    persistent: bool = False
    servers: tuple[Literal["hq", "rd", "archives"], ...] | None = None
    optional: bool = False
    once_per_turn: bool = False
    first_each_turn: bool = False
    instructions: tuple[PlayInstruction, ...]


class ActionAbility(CardContentModel):
    """A named action with a click cost, effects, and optional usage limit."""

    ability_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    trash_cost: bool = False
    click_cost: int = Field(default=1, gt=0)
    once_per_turn: bool = False
    instructions: tuple[PlayInstruction, ...]


class PaidAbility(CardContentModel):
    """Paid encounter effects with conditional costs, authored targets, and run-end effects."""

    ability_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    type: Literal["boost_strength", "break_subroutines", "lower_ice_strength", "break_host_subroutine", "effects"]
    instructions: tuple[PlayInstruction, ...] = ()
    remove_source_cost: bool = False
    virus_cost: int = Field(default=0, ge=0)
    successful_run_discount: int = Field(default=0, ge=0)
    trash_at_run_end: bool = False
    credit_cost: int = Field(ge=0)
    amount: int = Field(gt=0)
    amount_per_icebreaker: bool = False
    subtype: str | None = None


class EndRun(CardContentModel):
    type: Literal["end_run"] = "end_run"
    label: str = "End the run"


class EffectSubroutine(CardContentModel):
    """Ordered immediate effects, with the two damage-ice follow-up behaviors."""

    type: Literal["effects"] = "effects"
    label: str
    instructions: tuple[PlayInstruction, ...]
    end_run_if_odd_cost: bool = False
    offer_jack_out: bool = False


Subroutine = Annotated[EndRun | EffectSubroutine, Field(discriminator="type")]


class AuthoredAbilities(CardContentModel):
    """Executable instructions authored separately from imported display metadata."""

    play_instructions: tuple[PlayInstruction, ...] | None = None
    play_requirement: Literal["tagged", "successful_run_last_turn"] | None = None
    score_instructions: tuple[PlayInstruction, ...] | None = None
    install_instructions: tuple[PlayInstruction, ...] | None = None
    rez_instructions: tuple[PlayInstruction, ...] | None = None
    action_abilities: tuple[ActionAbility, ...] = ()
    trash_when_empty: bool = False
    paid_abilities: tuple[PaidAbility, ...] = ()
    subroutines: tuple[Subroutine, ...] | None = None
    advanceable: bool = False
    remote_strength_bonus: int = Field(default=0, ge=0)
    triggered_abilities: tuple[TriggeredAbility, ...] = ()
    first_basic_draw_amount: int | None = Field(default=None, gt=0)
    install_on_ice: bool = False
    memory_bonus: int = Field(default=0, ge=0)
    hand_size_bonus: int = Field(default=0, ge=0)
    first_program_discount: int = Field(default=0, ge=0)
    successful_run_install_discount: int = Field(default=0, ge=0)
    strength_per_icebreaker: int = Field(default=0, ge=0)


class CardDefinition(CardContentModel):
    """Static gameplay and display data for one card identity."""

    definition_id: str = Field(
        min_length=1,
        description="Unique card code from the NSG card data, distinct from game instance IDs",
    )
    title: str = Field(min_length=1)
    play_requirement: Literal["tagged", "successful_run_last_turn"] | None = None
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
    subroutines: tuple[Subroutine, ...] | None = None
    advanceable: bool = False
    remote_strength_bonus: int = Field(default=0, ge=0)
    triggered_abilities: tuple[TriggeredAbility, ...] = ()
    first_basic_draw_amount: int | None = Field(default=None, gt=0)
    install_on_ice: bool = False
    memory_bonus: int = Field(default=0, ge=0)
    hand_size_bonus: int = Field(default=0, ge=0)
    first_program_discount: int = Field(default=0, ge=0)
    successful_run_install_discount: int = Field(default=0, ge=0)
    strength_per_icebreaker: int = Field(default=0, ge=0)

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


# Resolve the recursive instruction tree through models, never printed prose.
EffectOption.model_rebuild()
ChooseEffect.model_rebuild()
