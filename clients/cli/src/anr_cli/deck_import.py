"""Parse NetrunnerDB plaintext exports into the public DeckSelection shape."""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


class DeckImportError(ValueError):
    """A selected deck file cannot be converted to a deck selection."""


_PRINTING = re.compile(r"^(.+) \(([^()]+)\)(?:\s+[•●\s]+)?$")
_CARD_LINE = re.compile(r"^(\d+)x\s+(.+)$")
_SECTION = re.compile(
    r"^(?:Event|Hardware|Resource|Program|Icebreaker|Agenda|Asset|Upgrade|"
    r"Operation|ICE|Ice|Barrier|Code Gate|Sentry|Other) \(\d+\)$"
)
_TOTAL = re.compile(r"^(\d+) cards \(min (?:\d+|infinite)\)$")
_INFLUENCE = re.compile(r"^\d+ influence spent \(max (?:\d+|infinite), available -?\d+\)$")
_AGENDA = re.compile(r"^\d+ agenda points \(between \d+ and \d+\)$")


def _title_key(title: str) -> str:
    return unicodedata.normalize("NFC", title).casefold()


def parse_nrdb_deck(
    text: str, card_catalog: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Resolve exported card titles against API catalog entries.

    The returned object matches the API's ``DeckSelection`` request shape but
    does not import the server's game model.
    """
    lines = [
        (number, line.strip())
        for number, line in enumerate(text.removeprefix("\ufeff").splitlines(), start=1)
        if line.strip()
    ]
    if len(lines) < 3:
        raise DeckImportError("Expected a deck name, identity, and at least one main-deck card")

    by_title: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for card in card_catalog:
        try:
            by_title[_title_key(str(card["title"]))].append(card)
        except KeyError as error:
            raise DeckImportError("The API returned an invalid card catalog entry") from error

    def resolve(printed_card: str, line_number: int) -> Mapping[str, Any]:
        match = _PRINTING.fullmatch(printed_card)
        if match is None:
            raise DeckImportError(
                f"Line {line_number}: expected a card title followed by its set in parentheses"
            )
        title = match[1]
        matches = by_title.get(_title_key(title), [])
        if not matches:
            raise DeckImportError(f"Line {line_number}: unknown or unsupported card {title!r}")
        if len(matches) != 1:
            raise DeckImportError(f"Line {line_number}: ambiguous card title {title!r}")
        return matches[0]

    identity_line, identity_text = lines[1]
    identity = resolve(identity_text, identity_line)
    if identity.get("card_type") != "identity":
        raise DeckImportError(f"Line {identity_line}: {identity['title']!r} is not an identity")

    counts: dict[str, int] = {}
    declared_total: int | None = None
    for line_number, line in lines[2:]:
        card_match = _CARD_LINE.fullmatch(line)
        if card_match:
            quantity = int(card_match[1])
            if quantity < 1:
                raise DeckImportError(f"Line {line_number}: card quantity must be positive")
            card = resolve(card_match[2], line_number)
            if card.get("card_type") == "identity":
                raise DeckImportError(f"Line {line_number}: an identity cannot be in the main deck")
            if card.get("side") != identity.get("side"):
                raise DeckImportError(
                    f"Line {line_number}: {card['title']!r} belongs to the opposite side"
                )
            definition_id = str(card["definition_id"])
            counts[definition_id] = counts.get(definition_id, 0) + quantity
        elif total_match := _TOTAL.fullmatch(line):
            if declared_total is not None:
                raise DeckImportError(f"Line {line_number}: duplicate card-count summary")
            declared_total = int(total_match[1])
        elif (
            _SECTION.fullmatch(line)
            or _INFLUENCE.fullmatch(line)
            or _AGENDA.fullmatch(line)
            or re.fullmatch(r"Cards up to .+", line)
        ):
            continue
        else:
            raise DeckImportError(f"Line {line_number}: unrecognized deck line {line!r}")

    if not counts:
        raise DeckImportError("The main deck must contain at least one card")
    actual_total = sum(counts.values())
    if declared_total is not None and declared_total != actual_total:
        raise DeckImportError(
            f"Card-count summary says {declared_total}, but main-deck entries total {actual_total}"
        )
    return {
        "identity_definition_id": str(identity["definition_id"]),
        "card_counts": counts,
    }
