"""HTTP API for creating an initial game against a selected AI profile."""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Mapping
from uuid import UUID

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import JSONResponse

from application.game_setup import GameSetupError, create_ai_game
from application.sessions import (
    GameSession,
    SessionAccessDenied,
    SessionNotFound,
    SessionStore,
)
from content.cards.loader import load_nsg_card_definitions
from content.cards.models import CardDefinition
from content.decks.deck_importer import DeckImportError, parse_nrdb_deck
from content.decks.loader import load_preconstructed_decks
from content.decks.models import PreconstructedDeck
from content.opponents.loader import load_opponent_profiles
from content.opponents.models import OpponentProfile
from game.models import DeckSelection
from game.setup import IllegalCommand

from . import command_adapters, projections, schemas


def create_app(
    card_definitions: Mapping[str, CardDefinition] | None = None,
    opponent_profiles: Mapping[str, OpponentProfile] | None = None,
    preconstructed_decks: Mapping[str, PreconstructedDeck] | None = None,
    *,
    session_store: SessionStore | None = None,
    cleanup_interval_seconds: float = 10,
) -> FastAPI:
    """Create the API app, with optional content injection for embedding."""

    definitions = dict(card_definitions) if card_definitions is not None else None
    profiles = dict(opponent_profiles) if opponent_profiles is not None else None
    if definitions is None:
        project_root = Path(__file__).resolve().parents[2]
        definitions = load_nsg_card_definitions(
            project_root / "content/cards/system-gateway/cards.json"
        )
    if profiles is None:
        profiles = load_opponent_profiles(definitions)
    decks = (
        dict(preconstructed_decks)
        if preconstructed_decks is not None
        else load_preconstructed_decks(definitions)
    )

    if cleanup_interval_seconds <= 0:
        raise ValueError("The cleanup interval must be positive")
    sessions = session_store if session_store is not None else SessionStore()
    sockets: dict[UUID, WebSocket] = {}
    command_lock = asyncio.Lock()

    def session_snapshot(session: GameSession, viewer_id: UUID) -> schemas.SessionSnapshot:
        """Build the revisioned state snapshot visible to one session player."""
        return schemas.SessionSnapshot(
            revision=session.revision,
            state=projections.game_view_from_state(session.state, viewer_id, definitions),
        )

    async def cleanup_sessions() -> None:
        while True:
            await asyncio.sleep(cleanup_interval_seconds)
            sessions.remove_expired()

    @asynccontextmanager
    async def lifespan(api: FastAPI):
        cleanup = asyncio.create_task(cleanup_sessions())
        try:
            yield
        finally:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            sessions.clear()

    api = FastAPI(title="Android: Netrunner API", version="0.1.0", lifespan=lifespan)
    api.state.card_definitions = definitions
    api.state.opponent_profiles = profiles
    api.state.preconstructed_decks = decks
    api.state.sessions = sessions

    @api.exception_handler(SessionNotFound)
    async def missing_session(request: Request, error: SessionNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @api.exception_handler(SessionAccessDenied)
    async def denied_session(request: Request, error: SessionAccessDenied) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(error)})

    def player_token(authorization: str | None = Header(default=None)) -> str:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="A player session token is required")
        return token

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/opponent-profiles", response_model=list[schemas.OpponentProfileSummary])
    def list_opponent_profiles() -> list[schemas.OpponentProfileSummary]:
        return [
            schemas.OpponentProfileSummary.from_profile(profile)
            for profile in api.state.opponent_profiles.values()
        ]

    @api.get("/preconstructed-decks", response_model=list[schemas.PreconstructedDeckResponse])
    def list_preconstructed_decks() -> list[schemas.PreconstructedDeckResponse]:
        return [
            schemas.PreconstructedDeckResponse.from_deck(deck)
            for deck in api.state.preconstructed_decks.values()
        ]

    @api.get("/card-catalog", response_model=list[schemas.CardCatalogEntry])
    def list_card_catalog() -> list[schemas.CardCatalogEntry]:
        return [
            schemas.CardCatalogEntry.from_definition(definition)
            for definition in api.state.card_definitions.values()
        ]

    @api.post("/decks/import", response_model=DeckSelection)
    def import_deck(request: schemas.DeckImportRequest) -> DeckSelection:
        try:
            return parse_nrdb_deck(request.text, api.state.card_definitions)
        except DeckImportError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @api.post(
        "/games",
        response_model=schemas.GameCreatedResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_game(request: schemas.CreateGameRequest) -> schemas.GameCreatedResponse:
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
        except GameSetupError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        session = sessions.create(game_state, profile.profile_id, [request.player_id])
        initial_state = projections.game_view_from_state(
            session.state,
            request.player_id,
            api.state.card_definitions,
        )
        return schemas.GameCreatedResponse(
            opponent_profile_id=profile.profile_id,
            player_token=session.player_tokens[request.player_id],
            reconnect_timeout_seconds=sessions.reconnect_timeout_seconds,
            initial_state=initial_state,
        )

    @api.get("/games/{game_id}/state", response_model=schemas.InitialGameView)
    def game_snapshot(
        game_id: UUID,
        token: Annotated[str, Depends(player_token)],
    ) -> schemas.InitialGameView:
        session, player_id = sessions.get(game_id, token)
        return projections.game_view_from_state(session.state, player_id, definitions)

    @api.post("/games/{game_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
    async def leave_game(game_id: UUID, token: Annotated[str, Depends(player_token)]) -> None:
        connection_ids = sessions.leave(game_id, token)
        for connection_id in connection_ids:
            socket = sockets.get(connection_id)
            if socket is not None:
                with suppress(WebSocketDisconnect):
                    await socket.close(code=1000, reason="Player left game")

    @api.post("/games/{game_id}/commands", response_model=schemas.SessionSnapshot)
    async def submit_command(
        game_id: UUID,
        request: schemas.PlayerCommandRequest,
        token: Annotated[str, Depends(player_token)],
    ) -> schemas.SessionSnapshot:
        """Apply one authenticated command and publish viewer-specific snapshots."""
        # Keep commits and broadcasts in order when multiple clients submit.
        async with command_lock:
            try:
                session, player_id = sessions.submit(
                    game_id, token, command_adapters.game_command_from_request(request),
                    card_definitions=definitions,
                )
            except IllegalCommand as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            for connection_id, viewer_id in list(session.connections.items()):
                socket = sockets.get(connection_id)
                if socket is not None:
                    snapshot = session_snapshot(session, viewer_id)
                    with suppress(WebSocketDisconnect, RuntimeError, OSError):
                        await socket.send_json(snapshot.model_dump(mode="json"))
            return session_snapshot(session, player_id)

    @api.websocket("/games/{game_id}/events")
    async def game_events(websocket: WebSocket, game_id: UUID) -> None:
        try:
            token = player_token(websocket.headers.get("authorization"))
            sessions.get(game_id, token)
        except (HTTPException, SessionNotFound, SessionAccessDenied):
            await websocket.close(code=1008, reason="Game session unavailable")
            return
        connection_id = None
        try:
            await websocket.accept()
            # The session may have expired or been left while accepting the socket.
            session, player_id, connection_id = sessions.connect(game_id, token)
            sockets[connection_id] = websocket
            snapshot = session_snapshot(session, player_id)
            await websocket.send_json(snapshot.model_dump(mode="json"))
            # Uvicorn's websockets backend detects silent failures with protocol pings.
            # Decisions use HTTP; the socket delivers private state snapshots.
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except (SessionNotFound, SessionAccessDenied):
            await websocket.close(code=1008, reason="Game session unavailable")
        finally:
            if connection_id is not None:
                sockets.pop(connection_id, None)
                sessions.disconnect(game_id, connection_id)

    return api


app = create_app()
