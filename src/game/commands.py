"""Player commands understood by the initial game engine."""

from enum import StrEnum


class BasicAction(StrEnum):
    GAIN_CREDIT = "gain_credit"
    DRAW_CARD = "draw_card"
