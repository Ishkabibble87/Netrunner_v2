"""Player decisions and basic actions accepted by the rules engine.

These commands describe player intent without depending on the HTTP API or a
client. The engine derives the acting player from the authenticated session.
"""

from dataclasses import dataclass
from uuid import UUID

from .models import MulliganDecision


@dataclass(frozen=True)
class ChooseMulligan:
    decision: MulliganDecision


@dataclass(frozen=True)
class GainCredit:
    """Spend one click to gain one credit during an action phase."""


@dataclass(frozen=True)
class DrawCard:
    """Spend one click to draw the top card of the player's deck."""


@dataclass(frozen=True)
class RemoveTag:
    """Spend one click and two credits to remove one Runner tag."""


@dataclass(frozen=True)
class PurgeVirusCounters:
    """Spend three Corp clicks to remove every virus counter in play."""


@dataclass(frozen=True)
class PlayCard:
    """Play one supported event or operation identified by its physical copy."""

    card_instance_id: UUID


@dataclass(frozen=True)
class DiscardCards:
    """Choose all excess cards to discard simultaneously."""

    card_instance_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class DiscardSelection:
    """Describe a legal selection without enumerating every card combination."""

    eligible_card_instance_ids: tuple[UUID, ...]
    required_count: int


GameCommand = ChooseMulligan | GainCredit | DrawCard | RemoveTag | PurgeVirusCounters | DiscardCards | PlayCard
