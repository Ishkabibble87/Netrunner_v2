"""Build the initial authoritative game state from two deck selections."""

from collections.abc import Mapping
from uuid import UUID, uuid4

from content.cards.models import CardDefinition
from content.opponents.models import OpponentProfile
from game.deck_validation import (
    STARTER_ONLY_IDENTITY_TITLES,
    DeckValidationError,
)
from game.models import DeckSelection, GameState, PlayerRole
from game.setup import create_game_state


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

    opponent_role = (
        PlayerRole.RUNNER if player_role is PlayerRole.CORP else PlayerRole.CORP
    )
    opponent_deck = opponent_profile.deck_selections.get(opponent_role)
    if opponent_deck is None:
        raise GameSetupError(
            f"Opponent profile {opponent_profile.profile_id!r} has no {opponent_role.value} deck"
        )

    selected_decks = {player_role: decklist, opponent_role: opponent_deck}
    allowed_starter_identity_ids: set[str] = set()
    # Starter-only identities are exempt only for the exact profile decklists.
    for role, selection in selected_decks.items():
        profile_selection = opponent_profile.deck_selections[role]
        profile_identity = card_definitions.get(profile_selection.identity_definition_id)
        if (
            selection == profile_selection
            and profile_identity is not None
            and profile_identity.title in STARTER_ONLY_IDENTITY_TITLES
        ):
            allowed_starter_identity_ids.add(profile_identity.definition_id)

    opponent_id = uuid4()
    if player_role is PlayerRole.CORP:
        corp_player_id, corp_deck = player_id, decklist
        runner_player_id, runner_deck = opponent_id, opponent_deck
    else:
        corp_player_id, corp_deck = opponent_id, opponent_deck
        runner_player_id, runner_deck = player_id, decklist

    try:
        return create_game_state(
            game_id=uuid4(),
            corp_player_id=corp_player_id,
            corp_deck=corp_deck,
            runner_player_id=runner_player_id,
            runner_deck=runner_deck,
            card_definitions=card_definitions,
            allowed_starter_only_identity_ids=frozenset(allowed_starter_identity_ids),
        )
    except DeckValidationError as error:
        raise GameSetupError(str(error)) from error
