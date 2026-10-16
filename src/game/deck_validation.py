"""Validate constructed decks against the universal rules in section 1.4."""

from collections.abc import Mapping

from content.cards.models import CardDefinition, CardSide, CardType
from game.models import DeckSelection, PlayerRole


class DeckValidationError(ValueError):
    """A deck selection violates a deck-construction requirement."""


def validate_deck(
    selection: DeckSelection,
    role: PlayerRole,
    card_definitions: Mapping[str, CardDefinition],
    *,
    allowed_starter_only_identity_ids: frozenset[str] = frozenset(),
) -> CardDefinition:
    """Validate a deck and return its identity definition.

    Card copy limits are checked by printed name and influence is counted for
    each copy. Tournament restrictions are format-specific and are not applied
    here. The bundled starter-only identities are accepted only when setup
    explicitly identifies their included starter-pack decks.
    """

    identity = card_definitions.get(selection.identity_definition_id)
    if identity is None:
        raise DeckValidationError(
            f"Unknown identity definition {selection.identity_definition_id!r}"
        )
    expected_side = CardSide(role.value)
    if identity.card_type is not CardType.IDENTITY:
        raise DeckValidationError(f"{identity.title!r} is not an identity")
    if identity.side is not expected_side:
        raise DeckValidationError(f"{identity.title!r} is not a {role.value} identity")
    if (
        identity.is_starter_only
        and identity.definition_id not in allowed_starter_only_identity_ids
    ):
        raise DeckValidationError(
            f"{identity.title!r} is not legal under full deck construction rules"
        )
    if identity.minimum_deck_size is None:
        raise DeckValidationError(
            f"{identity.title!r} has no minimum deck size in card data"
        )

    main_deck_size = sum(selection.card_counts.values())
    if main_deck_size < identity.minimum_deck_size:
        raise DeckValidationError(
            f"The main deck needs at least {identity.minimum_deck_size} cards "
            f"for {identity.title!r}"
        )

    copies_by_name: dict[str, int] = {}
    copy_limit_by_name: dict[str, int] = {}
    influence_spent = 0
    agenda_points = 0
    for definition_id, count in selection.card_counts.items():
        definition = card_definitions.get(definition_id)
        if definition is None:
            raise DeckValidationError(f"Unknown card definition {definition_id!r}")
        if definition.card_type is CardType.IDENTITY:
            raise DeckValidationError("An identity cannot be listed in the main deck")
        if definition.side is not expected_side:
            raise DeckValidationError(
                f"{definition.title!r} belongs to the opposite side"
            )

        copies_by_name[definition.title] = (
            copies_by_name.get(definition.title, 0) + count
        )
        card_limit = definition.deck_limit if definition.deck_limit is not None else 3
        copy_limit_by_name[definition.title] = min(
            copy_limit_by_name.get(definition.title, card_limit), card_limit
        )

        if definition.faction_code != identity.faction_code:
            if definition.influence_cost is None:
                raise DeckValidationError(
                    f"Out-of-faction card {definition.title!r} has no influence cost"
                )
            influence_spent += count * definition.influence_cost

        if definition.card_type is CardType.AGENDA:
            if definition.agenda_points is None:
                raise DeckValidationError(
                    f"Agenda {definition.title!r} has no agenda point value"
                )
            agenda_points += count * definition.agenda_points

    for title, copies in copies_by_name.items():
        limit = copy_limit_by_name[title]
        if copies > limit:
            raise DeckValidationError(
                f"The deck cannot contain more than {limit} copies of {title!r} by name"
            )

    if (
        identity.influence_limit is not None
        and influence_spent > identity.influence_limit
    ):
        raise DeckValidationError(
            f"The deck spends {influence_spent} influence, exceeding "
            f"{identity.title!r}'s limit of {identity.influence_limit}"
        )

    if role is PlayerRole.CORP:
        agenda_range = _corp_agenda_point_range(main_deck_size)
        if agenda_range is not None and agenda_points not in agenda_range:
            low, high = agenda_range
            raise DeckValidationError(
                f"A {main_deck_size}-card Corp deck must contain {low} or {high} "
                f"agenda points; this deck has {agenda_points}"
            )

    return identity


def _corp_agenda_point_range(deck_size: int) -> tuple[int, int] | None:
    """Return the legal agenda-point totals specified by rule 1.4.6."""

    if 40 <= deck_size <= 44:
        return 18, 19
    if 45 <= deck_size <= 49:
        return 20, 21
    if 50 <= deck_size <= 54:
        return 22, 23
    if deck_size >= 55:
        additional_points = 2 * ((deck_size - 50) // 5)
        return 22 + additional_points, 23 + additional_points
    return None
