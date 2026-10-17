"""Validate decks, deal opening hands, and resolve the standard pregame sequence (CR 1.6).

Identity setup abilities and turn-beginning abilities are not implemented yet.
The supported starter identities have no such abilities.
"""

import random
from collections.abc import Mapping
from uuid import UUID, uuid4

from content.cards.models import CardDefinition

from .card_movement import draw_from_deck
from .commands import ChooseMulligan
from .deck_validation import DeckValidationError, validate_deck
from .models import (
    CardInstance,
    CardZone,
    DeckSelection,
    GameState,
    MulliganDecision,
    PlayerRole,
    PlayerState,
    TurnPhase,
    TurnState,
    ZoneLocation,
)

OPENING_HAND_SIZE = 5
STARTING_CREDITS = 5


class IllegalCommand(ValueError):
    """A player decision is not allowed in the current game state."""


def choose_mulligan(
    state: GameState,
    player_id: UUID,
    command: ChooseMulligan,
    *,
    rng: random.Random | None = None,
) -> GameState:
    """Resolve one opening-hand choice and start turn one after both (CR 1.6.6–7).

    Return a new state so rejected commands and readers retain the old snapshot.
    A mulligan reuses physical copies and can draw some of the same cards again.
    """
    role = state.setup.pending_player
    if role is None:
        raise IllegalCommand("Opening-hand decisions are already complete")
    player = state.corp if role is PlayerRole.CORP else state.runner
    if player.player_id != player_id:
        raise IllegalCommand(f"Waiting for {role.value} to choose their opening hand")

    result = state.model_copy(deep=True)
    if command.decision is MulliganDecision.MULLIGAN:
        cards = [
            card for card in result.card_instances.values()
            if card.location.player_id == player_id
            and card.location.zone in {CardZone.HAND, CardZone.DECK}
        ]
        source = rng if rng is not None else random.SystemRandom()
        source.shuffle(cards)
        for position, card in enumerate(cards, start=1):
            card.location = ZoneLocation(
                zone=CardZone.DECK, player_id=player_id, deck_position=position,
            )
        draw_from_deck(result, player_id, OPENING_HAND_SIZE)

    result.setup.decisions[role] = command.decision
    result.setup.pending_player = PlayerRole.RUNNER if role is PlayerRole.CORP else None
    if result.setup.pending_player is None:
        # CR 5.6.1: gain three clicks, then draw without spending a click.
        result.corp.clicks_remaining = 3
        result.turn = TurnState(number=1, active_player=PlayerRole.CORP, phase=TurnPhase.DRAW)
        if draw_from_deck(result, result.corp.player_id, 1):
            result.turn.phase = TurnPhase.ACTION
        else:
            # CR 1.7.2: an empty R&D on a mandatory draw ends the game.
            result.winner = PlayerRole.RUNNER
    return GameState.model_validate(result.model_dump())


def create_game_state(
    *,
    game_id: UUID | None,
    corp_player_id: UUID,
    corp_deck: DeckSelection,
    runner_player_id: UUID,
    runner_deck: DeckSelection,
    card_definitions: Mapping[str, CardDefinition],
    allowed_starter_only_identity_ids: frozenset[str] = frozenset(),
    rng: random.Random | None = None,
) -> GameState:
    """Validate both selections, create unique copies, shuffle, and deal hands."""

    if corp_player_id == runner_player_id:
        raise ValueError("Corp and Runner must be different players")
    corp_identity = validate_deck(
        corp_deck, PlayerRole.CORP, card_definitions,
        allowed_starter_only_identity_ids=allowed_starter_only_identity_ids,
    )
    runner_identity = validate_deck(
        runner_deck, PlayerRole.RUNNER, card_definitions,
        allowed_starter_only_identity_ids=allowed_starter_only_identity_ids,
    )
    if (
        sum(corp_deck.card_counts.values()) < OPENING_HAND_SIZE
        or sum(runner_deck.card_counts.values()) < OPENING_HAND_SIZE
    ):
        raise DeckValidationError(
            f"Each main deck needs at least {OPENING_HAND_SIZE} cards to deal an opening hand"
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

    # CR 1.6.4–1.6.6: five credits and five cards, before mulligan decisions.
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
        card_instances=cards,
    )
