"""Build the initial authoritative game state from two deck selections."""

from collections.abc import Mapping
from uuid import UUID, uuid4

from content.cards.models import CardDefinition, CardSide, CardType
from content.opponents.models import OpponentProfile
from game.models import DeckSelection, GameState, PlayerRole
from game.setup import OPENING_HAND_SIZE, create_game_state


class GameSetupError(ValueError):
    """A selected deck cannot be used to initialize a game."""


def create_ai_game(
    *,
    player_id: UUID,
    decklist: DeckSelection,
    opponent_profile: OpponentProfile,
    card_definitions: Mapping[str, CardDefinition],
) -> GameState:
    """Create an initial game using the client's decklist and a selected AI profile."""

    identity = card_definitions.get(decklist.identity_definition_id)
    if identity is None:
        raise GameSetupError(
            f"Unknown identity definition {decklist.identity_definition_id!r}"
        )
    player_role = PlayerRole(identity.side.value)
    validate_deck_selection(decklist, player_role, card_definitions)

    opponent_role = (
        PlayerRole.RUNNER if player_role is PlayerRole.CORP else PlayerRole.CORP
    )
    opponent_deck = opponent_profile.deck_selections.get(opponent_role)
    if opponent_deck is None:
        raise GameSetupError(
            f"Opponent profile {opponent_profile.profile_id!r} has no {opponent_role.value} deck"
        )

    validate_deck_selection(opponent_deck, opponent_role, card_definitions)
    opponent_id = uuid4()
    if player_role is PlayerRole.CORP:
        corp_player_id, corp_deck = player_id, decklist
        runner_player_id, runner_deck = opponent_id, opponent_deck
    else:
        corp_player_id, corp_deck = opponent_id, opponent_deck
        runner_player_id, runner_deck = player_id, decklist

    return create_game_state(
        game_id=uuid4(),
        corp_player_id=corp_player_id,
        corp_deck=corp_deck,
        runner_player_id=runner_player_id,
        runner_deck=runner_deck,
        card_definitions=card_definitions,
    )


def validate_deck_selection(
    selection: DeckSelection,
    role: PlayerRole,
    card_definitions: Mapping[str, CardDefinition],
) -> CardDefinition:
    """Validate that a selection names known cards for the requested side.

    This checks card references and sides only. Format legality is a separate
    content concern and is not inferred from a deck import.
    """

    # TODO: Check deck-construction rules, including identity minimum size,
    # influence limits, and per-card copy limits. This currently only verifies
    # known definitions, sides, and enough cards for the opening hand.

    identity = card_definitions.get(selection.identity_definition_id)
    if identity is None:
        raise GameSetupError(
            f"Unknown identity definition {selection.identity_definition_id!r}"
        )
    expected_side = CardSide(role.value)
    if identity.card_type is not CardType.IDENTITY:
        raise GameSetupError(f"{identity.title!r} is not an identity")
    if identity.side is not expected_side:
        raise GameSetupError(f"{identity.title!r} is not a {role.value} identity")

    main_deck_size = 0
    for definition_id, count in selection.card_counts.items():
        definition = card_definitions.get(definition_id)
        if definition is None:
            raise GameSetupError(f"Unknown card definition {definition_id!r}")
        if definition.card_type is CardType.IDENTITY:
            raise GameSetupError("An identity cannot be listed in the main deck")
        if definition.side is not expected_side:
            raise GameSetupError(
                f"{definition.title!r} belongs to the opposite side"
            )
        main_deck_size += count

    if main_deck_size < OPENING_HAND_SIZE:
        raise GameSetupError(
            f"The main deck needs at least {OPENING_HAND_SIZE} cards to deal an opening hand"
        )
    return identity
