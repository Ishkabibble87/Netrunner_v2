"""Player decisions accepted by the rules engine, independent of transport."""

from dataclasses import dataclass

from .models import MulliganDecision


@dataclass(frozen=True)
class ChooseMulligan:
    decision: MulliganDecision
