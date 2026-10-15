"""HTTP API for creating an initial game against a selected AI profile."""

from pathlib import Path
from typing import Mapping

from fastapi import FastAPI, HTTPException, status

from application.game_setup import GameSetupError, create_ai_game
from content.cards.loader import load_nsg_card_definitions
from content.cards.models import CardDefinition
from content.opponents.loader import load_opponent_profiles
from content.opponents.models import OpponentProfile

from .schemas import (
    CreateGameRequest,
    GameCreatedResponse,
    InitialGameView,
    OpponentProfileSummary,
)


def create_app(
    card_definitions: Mapping[str, CardDefinition] | None = None,
    opponent_profiles: Mapping[str, OpponentProfile] | None = None,
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

    api = FastAPI(title="Android: Netrunner API", version="0.1.0")
    api.state.card_definitions = definitions
    api.state.opponent_profiles = profiles

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/opponent-profiles", response_model=list[OpponentProfileSummary])
    def list_opponent_profiles() -> list[OpponentProfileSummary]:
        return [
            OpponentProfileSummary.from_profile(profile)
            for profile in api.state.opponent_profiles.values()
        ]

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
