"""HTTP request and player-visible response schemas."""

from collections.abc import Mapping
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from content.cards.models import CardDefinition, CardSide, CardType
from content.decks.models import PreconstructedDeck
from content.opponents.models import OpponentProfile
from game.models import (
    CardZone, DeckSelection, GameState, MulliganDecision, PlayerRole, PlayerState,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateGameRequest(ApiModel):
    player_id: UUID
    decklist: DeckSelection
    opponent_profile_id: str = Field(min_length=1)


class DeckImportRequest(ApiModel):
    text: str


class MulliganCommandRequest(ApiModel):
    type: Literal["choose_mulligan"]
    decision: MulliganDecision


class SetupView(ApiModel):
    pending_player: PlayerRole | None
    decisions: dict[PlayerRole, MulliganDecision]


class CardView(ApiModel):
    instance_id: UUID
    definition_id: str
    title: str
    card_type: CardType


class TurnView(ApiModel):
    number: int
    active_player: PlayerRole
    phase: str


class PlayerSummary(ApiModel):
    player_id: UUID
    role: PlayerRole
    credits: int
    clicks_remaining: int
    identity: CardView
    deck_count: int


class OwnPlayerView(PlayerSummary):
    hand: list[CardView]


class OpponentView(PlayerSummary):
    hand_count: int


class InitialGameView(ApiModel):
    """Current state projection for one player; concealed cards stay hidden."""

    game_id: UUID
    viewer_player_id: UUID
    viewer_role: PlayerRole
    setup: SetupView
    turn: TurnView | None
    winner: PlayerRole | None
    you: OwnPlayerView
    opponent: OpponentView
    servers: list[str | int]

    @classmethod
    def from_game_state(
        cls,
        state: GameState,
        viewer_player_id: UUID,
        card_definitions: Mapping[str, CardDefinition],
    ) -> "InitialGameView":
        players = (state.corp, state.runner)
        you = next(
            (player for player in players if player.player_id == viewer_player_id),
            None,
        )
        if you is None:
            raise ValueError("Viewer is not a player in this game")
        opponent = state.runner if you is state.corp else state.corp

        def card_view(instance_id: UUID) -> CardView:
            instance = state.card_instances[instance_id]
            definition = card_definitions[instance.definition_id]
            return CardView(
                instance_id=instance.instance_id,
                definition_id=instance.definition_id,
                title=definition.title,
                card_type=definition.card_type,
            )

        def player_summary(player: PlayerState) -> PlayerSummary:
            identity = card_view(player.identity_card_instance_id)
            return PlayerSummary(
                player_id=player.player_id,
                role=player.role,
                credits=player.credits,
                clicks_remaining=player.clicks_remaining,
                identity=identity,
                deck_count=sum(
                    card.location.zone is CardZone.DECK
                    and card.location.player_id == player.player_id
                    for card in state.card_instances.values()
                ),
            )

        own_hand = [
            card_view(card.instance_id)
            for card in state.card_instances.values()
            if card.location.zone is CardZone.HAND
            and card.location.player_id == you.player_id
        ]
        opponent_hand_count = sum(
            card.location.zone is CardZone.HAND
            and card.location.player_id == opponent.player_id
            for card in state.card_instances.values()
        )
        return cls(
            game_id=state.game_id,
            viewer_player_id=viewer_player_id,
            viewer_role=you.role,
            setup=SetupView(**state.setup.model_dump()),
            turn=TurnView(
                number=state.turn.number,
                active_player=state.turn.active_player,
                phase=state.turn.phase.value,
            ) if state.turn is not None else None,
            winner=state.winner,
            you=OwnPlayerView(**player_summary(you).model_dump(), hand=own_hand),
            opponent=OpponentView(
                **player_summary(opponent).model_dump(),
                hand_count=opponent_hand_count,
            ),
            servers=[
                server.value if hasattr(server, "value") else server
                for server in state.servers
            ],
        )


class GameCreatedResponse(ApiModel):
    opponent_profile_id: str
    player_token: str
    reconnect_timeout_seconds: float
    initial_state: InitialGameView


class SessionSnapshot(ApiModel):
    type: Literal["state"] = "state"
    revision: int = 0
    state: InitialGameView


class OpponentProfileSummary(ApiModel):
    profile_id: str
    name: str

    @classmethod
    def from_profile(cls, profile: OpponentProfile) -> "OpponentProfileSummary":
        return cls(profile_id=profile.profile_id, name=profile.name)


class PreconstructedDeckResponse(ApiModel):
    deck_id: str
    name: str
    role: PlayerRole
    decklist: DeckSelection

    @classmethod
    def from_deck(cls, deck: PreconstructedDeck) -> "PreconstructedDeckResponse":
        return cls(**deck.model_dump())


class CardCatalogEntry(ApiModel):
    definition_id: str
    title: str
    side: CardSide
    card_type: CardType
    faction_code: str
    subtypes: tuple[str, ...]
    printed_text: str
    is_unique: bool
    play_cost: int | None
    install_cost: int | None
    rez_cost: int | None
    trash_cost: int | None
    advancement_requirement: int | None
    agenda_points: int | None
    strength: int | None
    memory_cost: int | None
    influence_cost: int | None
    deck_limit: int | None
    base_link: int | None
    minimum_deck_size: int | None
    influence_limit: int | None
    flavor_text: str | None
    illustrator: str | None

    @classmethod
    def from_definition(cls, definition: CardDefinition) -> "CardCatalogEntry":
        return cls(**definition.model_dump())
