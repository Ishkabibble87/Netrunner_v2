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
    server_id: ServerId | None = None


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
class TrashPrograms:
    """Choose installed programs to trash at a memory-overflow checkpoint."""

    card_instance_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class InstallCard:
    """Install a card, optionally selecting a required host or replacement cards."""

    card_instance_id: UUID
    server_id: ServerId | Literal["new_remote"] | None = None
    trash_card_instance_ids: tuple[UUID, ...] = ()
    host_card_instance_id: UUID | None = None


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
    """Pass paid priority or decline remaining optional reactions in this window."""


@dataclass(frozen=True)
class ResolveReaction:
    """Resolve or decline one pending reaction; source-only selection is legacy."""

    card_instance_id: UUID
    reaction_id: UUID | None = None
    decline: bool = False


@dataclass(frozen=True)
class RunServer:
    """Spend one Runner click to initiate a run on an existing server."""

    server_id: ServerId


@dataclass(frozen=True)
class ContinueRun:
    """Decline to jack out and proceed inward toward the server."""


@dataclass(frozen=True)
class JackOut:
    """End a run at its movement opportunity, before success."""


@dataclass(frozen=True)
class AccessCard:
    """Choose a root/discard card or an anonymous central-zone access."""

    target: UUID | Literal["hq", "rd"]


@dataclass(frozen=True)
class FinishAccess:
    """Resolve basic trashing, mandatory agenda stealing, and finish access."""

    trash: bool = False


@dataclass(frozen=True)
class RezCard:
    """Pay the rez cost of a supported installed Corp card."""

    card_instance_id: UUID


@dataclass(frozen=True)
class UseCardAbility:
    """Activate an authored action or paid ability on a controlled installed card."""

    card_instance_id: UUID
    ability_id: str
    subroutine_indices: tuple[int, ...] = ()
    server_id: ServerId | None = None


GameCommand = TrashPrograms | RezCard | UseCardAbility | RunServer | ContinueRun | JackOut | AccessCard | FinishAccess | InstallCard | AdvanceCard | ScoreAgenda | PassPriority | ResolveReaction | ChooseMulligan | GainCredit | DrawCard | RemoveTag | PurgeVirusCounters | DiscardCards | PlayCard
