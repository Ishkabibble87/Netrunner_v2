"""Load local opponent profile JSON and resolve its deck export files."""

import json
from pathlib import Path
from typing import Any

from content.cards.models import CardDefinition
from content.decks.deck_importer import load_nrdb_deck
from game.models import PlayerRole

from .models import OpponentProfile


def load_opponent_profiles(
    card_definitions: dict[str, CardDefinition],
    profile_directory: str | Path | None = None,
) -> dict[str, OpponentProfile]:
    """Load every ``*.json`` profile in a directory, keyed by profile ID."""

    directory = (
        Path(profile_directory)
        if profile_directory is not None
        else Path(__file__).with_name("profiles")
    )
    if not directory.is_dir():
        raise ValueError(f"Opponent profile directory does not exist: {directory}")

    profiles: dict[str, OpponentProfile] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            profile = _load_profile(record, path, card_definitions)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid opponent profile in {path}: {error}") from error
        if profile.profile_id in profiles:
            raise ValueError(
                f"Duplicate opponent profile ID {profile.profile_id!r} in {directory}"
            )
        profiles[profile.profile_id] = profile
    return profiles


def _load_profile(
    record: Any,
    source_path: Path,
    card_definitions: dict[str, CardDefinition],
) -> OpponentProfile:
    if not isinstance(record, dict):
        raise TypeError("profile record must be an object")
    deck_files = record["decks"]
    if not isinstance(deck_files, dict):
        raise TypeError("decks must map roles to NetrunnerDB deck files")

    deck_selections = {}
    for role in (PlayerRole.CORP, PlayerRole.RUNNER):
        relative_path = deck_files[role.value]
        if not isinstance(relative_path, str):
            raise TypeError(f"{role.value} deck path must be a string")
        deck_selections[role] = load_nrdb_deck(
            source_path.parent / relative_path,
            card_definitions,
        )

    return OpponentProfile(
        profile_id=record["profile_id"],
        name=record["name"],
        deck_selections=deck_selections,
    )
