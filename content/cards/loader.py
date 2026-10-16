"""Load normalized card definitions from Null Signal Games card JSON."""

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import CardDefinition, CardSide, CardType


_CORP_REZZABLE_TYPES = {CardType.ASSET, CardType.ICE, CardType.UPGRADE}
_RUNNER_INSTALLABLE_TYPES = {CardType.HARDWARE, CardType.PROGRAM, CardType.RESOURCE}


def load_nsg_card_definitions(path: str | Path) -> dict[str, CardDefinition]:
    """Read one NSG ``cards.json`` array into definitions keyed by card code.

    Pack membership and printing fields such as ``pack_code`` and ``quantity``
    belong to content/set metadata, so they are not copied into definitions.
    """

    source_path = Path(path)
    try:
        records = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid NSG card JSON in {source_path}: {error}") from error

    if not isinstance(records, list):
        raise ValueError(f"Expected an array of card records in {source_path}")

    definitions: dict[str, CardDefinition] = {}
    for index, record in enumerate(records):
        try:
            definition = _definition_from_nsg_record(record)
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise ValueError(
                f"Invalid card record at index {index} in {source_path}: {error}"
            ) from error

        if definition.definition_id in definitions:
            raise ValueError(
                f"Duplicate NSG card code {definition.definition_id!r} in {source_path}"
            )
        definitions[definition.definition_id] = definition

    return definitions


def _definition_from_nsg_record(record: Any) -> CardDefinition:
    if not isinstance(record, dict):
        raise TypeError("card record must be an object")

    definition_id = record["code"]
    side = CardSide(record["side_code"])
    card_type = CardType(record["type_code"])
    source_cost = record.get("cost")

    play_cost = None
    install_cost = None
    rez_cost = None
    if card_type in {CardType.EVENT, CardType.OPERATION}:
        play_cost = source_cost
    elif side is CardSide.RUNNER and card_type in _RUNNER_INSTALLABLE_TYPES:
        install_cost = source_cost
    elif side is CardSide.CORP and card_type in _CORP_REZZABLE_TYPES:
        rez_cost = source_cost

    keywords = record.get("keywords") or ""
    subtypes = tuple(part.strip() for part in keywords.split(" - ") if part.strip())

    return CardDefinition(
        definition_id=definition_id,
        title=record["title"],
        side=side,
        card_type=card_type,
        faction_code=record["faction_code"],
        subtypes=subtypes,
        printed_text=record.get("text") or "",
        is_unique=record.get("uniqueness", False),
        play_cost=play_cost,
        install_cost=install_cost,
        rez_cost=rez_cost,
        trash_cost=record.get("trash_cost"),
        advancement_requirement=record.get("advancement_cost"),
        agenda_points=record.get("agenda_points"),
        strength=record.get("strength"),
        memory_cost=record.get("memory_cost"),
        influence_cost=record.get("faction_cost"),
        deck_limit=record.get("deck_limit"),
        base_link=record.get("base_link"),
        minimum_deck_size=record.get("minimum_deck_size"),
        influence_limit=record.get("influence_limit"),
        flavor_text=record.get("flavor"),
        illustrator=record.get("illustrator"),
    )
