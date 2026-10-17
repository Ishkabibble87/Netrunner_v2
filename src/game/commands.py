"""Player decisions and basic actions accepted by the rules engine.

These commands describe player intent without depending on the HTTP API or a
client. The engine derives the acting player from the authenticated session.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from .models import MulliganDecision, ServerId


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


@dataclass(frozen=True)
class InstallCard:
    """Install a Corp card, optionally trashing like cards in its destination."""

    card_instance_id: UUID
    server_id: ServerId | Literal["new_remote"]
    trash_card_instance_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class AdvanceCard:
    """Spend one click and one credit to advance an installed agenda."""

    card_instance_id: UUID


@dataclass(frozen=True)
class ScoreAgenda:
    """Score an advanced agenda during an eligible paid ability window."""

    card_instance_id: UUID


@dataclass(frozen=True)
class PassPriority:
    """Pass the current paid ability window; unsupported opposing choices auto-pass."""


@dataclass(frozen=True)
class ResolveReaction:
    """Choose the next pending mandatory scoring reaction to resolve."""

    card_instance_id: UUID


GameCommand = InstallCard | AdvanceCard | ScoreAgenda | PassPriority | ResolveReaction | ChooseMulligan | GainCredit | DrawCard | RemoveTag | PurgeVirusCounters | DiscardCards | PlayCard
