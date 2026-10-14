"""Import NetrunnerDB text-file exports for game setup.

The caller supplies the supported card catalog. Importing resolves deck contents;
it does not establish format legality or whether card effects are implemented.
The CLI can upload a selected file through the API when game setup is added.
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

from content.cards.models import CardDefinition, CardType
from game.models import DeckSelection


class DeckImportError(ValueError):
    """A selected deck file cannot be converted into a deck selection."""


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
    text: str, card_definitions: Mapping[str, CardDefinition]
) -> DeckSelection:
    """Convert one NetrunnerDB text export to identity and main-deck counts.

    Expected layout: deck name, identity with set label, then quantity-prefixed
    cards, optionally grouped under type headings and followed by summaries.
    Set labels are display metadata: resolve titles against the supplied catalog,
    rejecting ambiguous titles rather than choosing a printing arbitrarily.
    Repeated entries for the same definition are combined.
    """
    lines = [
        (number, line.strip())
        for number, line in enumerate(text.removeprefix("\ufeff").splitlines(), start=1)
        if line.strip()
    ]
    if len(lines) < 3:
        raise DeckImportError("Expected a deck name, identity, and at least one main-deck card")

    by_title: dict[str, list[CardDefinition]] = defaultdict(list)
    for definition in card_definitions.values():
        by_title[_title_key(definition.title)].append(definition)

    def resolve(printed_card: str, line_number: int) -> CardDefinition:
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
    if identity.card_type is not CardType.IDENTITY:
        raise DeckImportError(f"Line {identity_line}: {identity.title!r} is not an identity")

    counts: dict[str, int] = {}
    declared_total: int | None = None
    for line_number, line in lines[2:]:
        card_match = _CARD_LINE.fullmatch(line)
        if card_match:
            quantity = int(card_match[1])
            if quantity < 1:
                raise DeckImportError(f"Line {line_number}: card quantity must be positive")
            card = resolve(card_match[2], line_number)
            if card.card_type is CardType.IDENTITY:
                raise DeckImportError(f"Line {line_number}: an identity cannot be in the main deck")
            if card.side is not identity.side:
                raise DeckImportError(
                    f"Line {line_number}: {card.title!r} belongs to the opposite side"
                )
            counts[card.definition_id] = counts.get(card.definition_id, 0) + quantity
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
    return DeckSelection(
        identity_definition_id=identity.definition_id, card_counts=counts
    )


def load_nrdb_deck(
    path: str | Path, card_definitions: Mapping[str, CardDefinition]
) -> DeckSelection:
    """Read a selected UTF-8 text file, reporting its path on import failures."""
    source_path = Path(path)
    try:
        text = source_path.read_text(encoding="utf-8-sig")
        return parse_nrdb_deck(text, card_definitions)
    except (OSError, UnicodeError, DeckImportError) as error:
        raise DeckImportError(f"Cannot import deck from {source_path}: {error}") from error
