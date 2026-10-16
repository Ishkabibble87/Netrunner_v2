"""Player decisions and basic actions accepted by the rules engine.

These commands describe player intent without depending on the HTTP API or a
client. The engine derives the acting player from the authenticated session.
"""

from dataclasses import dataclass

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


GameCommand = ChooseMulligan | GainCredit | DrawCard | RemoveTag | PurgeVirusCounters
