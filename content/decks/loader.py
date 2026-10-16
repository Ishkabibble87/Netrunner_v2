"""Load preconstructed deck metadata and resolve its tracked deck exports."""

import json
from collections.abc import Mapping
from pathlib import Path

from content.cards.models import CardDefinition
from game.models import PlayerRole

from .deck_importer import load_nrdb_deck
from .models import PreconstructedDeck


def load_preconstructed_decks(
    card_definitions: Mapping[str, CardDefinition],
    deck_directory: str | Path | None = None,
) -> dict[str, PreconstructedDeck]:
    """Load every ``*.json`` deck in a directory, keyed by stable deck ID."""

    directory = (
        Path(deck_directory)
        if deck_directory is not None
        else Path(__file__).with_name("preconstructed")
    )
    if not directory.is_dir():
        raise ValueError(f"Preconstructed deck directory does not exist: {directory}")

    decks: dict[str, PreconstructedDeck] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise TypeError("deck record must be an object")
            deck_file = record["deck_file"]
            if not isinstance(deck_file, str):
                raise TypeError("deck_file must be a NetrunnerDB deck file path")
            decklist = load_nrdb_deck(path.parent / deck_file, card_definitions)
            identity = card_definitions[decklist.identity_definition_id]
            deck = PreconstructedDeck(
                deck_id=record["deck_id"],
                name=record["name"],
                role=PlayerRole(identity.side.value),
                decklist=decklist,
            )
        except (OSError, UnicodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid preconstructed deck in {path}: {error}") from error
        if deck.deck_id in decks:
            raise ValueError(
                f"Duplicate preconstructed deck ID {deck.deck_id!r} in {directory}"
            )
        decks[deck.deck_id] = deck
    return decks
