"""HTTP API for creating an initial game against a selected AI profile."""

from pathlib import Path
from typing import Mapping

from fastapi import FastAPI, HTTPException, status

from application.game_setup import GameSetupError, create_ai_game
from content.cards.loader import load_nsg_card_definitions
from content.cards.models import CardDefinition
from content.decks.deck_importer import DeckImportError, parse_nrdb_deck
from content.decks.loader import load_preconstructed_decks
from content.decks.models import PreconstructedDeck
from content.opponents.loader import load_opponent_profiles
from content.opponents.models import OpponentProfile
from game.models import DeckSelection

from .schemas import (
    CreateGameRequest,
    CardCatalogEntry,
    DeckImportRequest,
    GameCreatedResponse,
    InitialGameView,
    OpponentProfileSummary,
    PreconstructedDeckResponse,
)


def create_app(
    card_definitions: Mapping[str, CardDefinition] | None = None,
    opponent_profiles: Mapping[str, OpponentProfile] | None = None,
    preconstructed_decks: Mapping[str, PreconstructedDeck] | None = None,
) -> FastAPI:
    """Create the API app, with optional content injection for embedding."""

    definitions = dict(card_definitions) if card_definitions is not None else None
    profiles = dict(opponent_profiles) if opponent_profiles is not None else None
    if definitions is None:
        project_root = Path(__file__).resolve().parents[2]
        definitions = load_nsg_card_definitions(
            project_root / "src/content/cards/system-gateway/cards.json"
        )
    if profiles is None:
        profiles = load_opponent_profiles(definitions)
    decks = (
        dict(preconstructed_decks)
        if preconstructed_decks is not None
        else load_preconstructed_decks(definitions)
    )

    api = FastAPI(title="Android: Netrunner API", version="0.1.0")
    api.state.card_definitions = definitions
    api.state.opponent_profiles = profiles
    api.state.preconstructed_decks = decks

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/opponent-profiles", response_model=list[OpponentProfileSummary])
    def list_opponent_profiles() -> list[OpponentProfileSummary]:
        return [
            OpponentProfileSummary.from_profile(profile)
            for profile in api.state.opponent_profiles.values()
        ]

    @api.get("/preconstructed-decks", response_model=list[PreconstructedDeckResponse])
    def list_preconstructed_decks() -> list[PreconstructedDeckResponse]:
        return [
            PreconstructedDeckResponse.from_deck(deck)
            for deck in api.state.preconstructed_decks.values()
        ]

    @api.get("/card-catalog", response_model=list[CardCatalogEntry])
    def list_card_catalog() -> list[CardCatalogEntry]:
        return [
            CardCatalogEntry.from_definition(definition)
            for definition in api.state.card_definitions.values()
        ]

    @api.post("/decks/import", response_model=DeckSelection)
    def import_deck(request: DeckImportRequest) -> DeckSelection:
        try:
            return parse_nrdb_deck(request.text, api.state.card_definitions)
        except DeckImportError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @api.post(
        "/games",
        response_model=GameCreatedResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_game(request: CreateGameRequest) -> GameCreatedResponse:
        profile = api.state.opponent_profiles.get(request.opponent_profile_id)
        if profile is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown opponent profile {request.opponent_profile_id!r}",
            )
        try:
            game_state = create_ai_game(
                player_id=request.player_id,
                decklist=request.decklist,
                opponent_profile=profile,
                card_definitions=api.state.card_definitions,
            )
            initial_state = InitialGameView.from_game_state(
                game_state,
                request.player_id,
                api.state.card_definitions,
            )
        except GameSetupError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return GameCreatedResponse(
            opponent_profile_id=profile.profile_id,
            initial_state=initial_state,
        )

    return api


app = create_app()
