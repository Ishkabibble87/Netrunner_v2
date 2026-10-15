"""Build the initial authoritative game state from two deck selections."""

import random
from collections.abc import Mapping
from uuid import UUID, uuid4

from content.cards.models import CardDefinition, CardSide, CardType
from content.opponents.models import OpponentProfile
from game.models import (
    CardInstance,
    CardZone,
    DeckSelection,
    GameState,
    PlayerRole,
    PlayerState,
    TurnPhase,
    TurnState,
    ZoneLocation,
)


OPENING_HAND_SIZE = 5
STARTING_CREDITS = 5


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


def create_game_state(
    *,
    game_id: UUID | None,
    corp_player_id: UUID,
    corp_deck: DeckSelection,
    runner_player_id: UUID,
    runner_deck: DeckSelection,
    card_definitions: Mapping[str, CardDefinition],
    rng: random.Random | None = None,
) -> GameState:
    """Create unique card instances, shuffle both decks, and deal opening hands."""

    if corp_player_id == runner_player_id:
        raise GameSetupError("Corp and Runner must be different players")
    corp_identity = validate_deck_selection(
        corp_deck, PlayerRole.CORP, card_definitions
    )
    runner_identity = validate_deck_selection(
        runner_deck, PlayerRole.RUNNER, card_definitions
    )
    source = rng if rng is not None else random.SystemRandom()
    game_id = game_id or uuid4()
    cards: dict[UUID, CardInstance] = {}

    def add_card(
        definition_id: str,
        owner_id: UUID,
        zone: CardZone,
        *,
        deck_position: int | None = None,
        face_up: bool | None = None,
    ) -> UUID:
        instance_id = uuid4()
        cards[instance_id] = CardInstance(
            instance_id=instance_id,
            definition_id=definition_id,
            owner_id=owner_id,
            controller_id=owner_id,
            location=ZoneLocation(
                zone=zone,
                player_id=owner_id,
                deck_position=deck_position,
            ),
            face_up=face_up,
        )
        return instance_id

    corp_identity_instance = add_card(
        corp_identity.definition_id,
        corp_player_id,
        CardZone.IDENTITY,
        face_up=True,
    )
    runner_identity_instance = add_card(
        runner_identity.definition_id,
        runner_player_id,
        CardZone.IDENTITY,
        face_up=True,
    )

    def deal_deck(player_id: UUID, selection: DeckSelection) -> None:
        shuffled = [
            definition_id
            for definition_id, count in selection.card_counts.items()
            for _ in range(count)
        ]
        # TODO: Allow callers to provide a top-to-bottom deck order so tests can
        # deal known opening hands without depending on shuffle seeds.
        source.shuffle(shuffled)
        for definition_id in shuffled[:OPENING_HAND_SIZE]:
            add_card(definition_id, player_id, CardZone.HAND)
        for position, definition_id in enumerate(
            shuffled[OPENING_HAND_SIZE:], start=1
        ):
            add_card(
                definition_id,
                player_id,
                CardZone.DECK,
                deck_position=position,
            )

    deal_deck(corp_player_id, corp_deck)
    deal_deck(runner_player_id, runner_deck)

    # The v26.03 rule reference does not define pregame setup. Use the standard
    # five-card/five-credit opening, with the Corp at the start of turn one.
    # Turn setup itself remains pending at the Corp's draw phase.
    corp = PlayerState(
        player_id=corp_player_id,
        role=PlayerRole.CORP,
        credits=STARTING_CREDITS,
        clicks_remaining=0,
        deck_selection=corp_deck,
        identity_card_instance_id=corp_identity_instance,
    )
    runner = PlayerState(
        player_id=runner_player_id,
        role=PlayerRole.RUNNER,
        credits=STARTING_CREDITS,
        clicks_remaining=0,
        memory_limit=(
            runner_identity.memory_cost
            if runner_identity.memory_cost is not None
            else 4
        ),
        deck_selection=runner_deck,
        identity_card_instance_id=runner_identity_instance,
    )
    return GameState(
        game_id=game_id,
        corp=corp,
        runner=runner,
        turn=TurnState(
            number=1,
            active_player=PlayerRole.CORP,
            phase=TurnPhase.DRAW,
        ),
        card_instances=cards,
    )
