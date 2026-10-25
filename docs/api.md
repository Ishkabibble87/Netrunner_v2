# API guide

The HTTP API is served by FastAPI. The interactive reference is available at
`/docs`, and its machine-readable OpenAPI document is at `/openapi.json`. The
checked-in snapshot is [`openapi.json`](openapi.json); regenerate it from the
application's routes and Pydantic schemas with:

```powershell
python docs/generate_openapi.py
```

The WebSocket endpoint is described here because WebSockets are not represented
in OpenAPI.

## Run locally

From the repository root, start the API:

```powershell
uv run uvicorn api.app:app --reload
```

The default address is `http://127.0.0.1:8000`. `GET /health` should return
`{"status":"ok"}`. The API can also be used through the terminal client:

```powershell
uv run --project clients/cli anr
```

## HTTP endpoints

All paths below are relative to the API base URL. JSON request models reject
unknown fields. The complete field definitions and discriminated command
variants are in the OpenAPI document.

| Method and path | Purpose | Response |
| --- | --- | --- |
| `GET /health` | Liveness check | `{"status":"ok"}` |
| `GET /opponent-profiles` | List available scripted opponents | Array of profile IDs and names |
| `GET /preconstructed-decks` | List reusable deck selections | Array of deck IDs, names, roles, and decklists |
| `GET /card-catalog` | Read card definitions for display and deck building | Array of card catalog entries |
| `POST /decks/import` | Parse a NetrunnerDB deck export | A validated `DeckSelection` |
| `POST /games` | Start a game against a scripted opponent | `201 Created`; token, reconnect timeout, and initial state |
| `GET /games/{game_id}/state` | Fetch the caller's current state projection | `InitialGameView` |
| `POST /games/{game_id}/commands` | Submit one player command | Revisioned `SessionSnapshot` |
| `POST /games/{game_id}/leave` | Revoke the caller's token and leave | `204 No Content` |
| `WS /games/{game_id}/events` | Receive private state snapshots and updates | WebSocket messages containing `SessionSnapshot` |

Host/join multiplayer endpoints are not implemented. `POST /games` currently
creates a one-human game against the selected AI profile.

## Start and play a game

Use the catalogs to choose a deck and opponent. A deck selection contains the
identity definition ID and counts for main-deck card definition IDs:

```json
{
  "identity_definition_id": "<identity definition ID>",
  "card_counts": {
    "<card definition ID>": 3
  }
}
```

For a working request, copy `decklist` from a `GET /preconstructed-decks`
response (or submit an imported deck selection). Example PowerShell flow:

```powershell
$base = "http://127.0.0.1:8000"
$deck = (Invoke-RestMethod "$base/preconstructed-decks")[0]
$opponent = (Invoke-RestMethod "$base/opponent-profiles")[0]
$request = @{
    player_id = [guid]::NewGuid().ToString()
    decklist = $deck.decklist
    opponent_profile_id = $opponent.profile_id
} | ConvertTo-Json -Depth 20
$created = Invoke-RestMethod -Method Post -Uri "$base/games" `
    -ContentType "application/json" -Body $request
$gameId = $created.initial_state.game_id
$headers = @{ Authorization = "Bearer $($created.player_token)" }
```

The create response includes `initial_state` and a secret `player_token`. Keep
that token private and send it on every authenticated HTTP request:

```powershell
Invoke-RestMethod "$base/games/$gameId/state" -Headers $headers
```

`initial_state.legal_actions` lists the commands currently available to this
player. Submit a command using the exact discriminated shape shown there; for
example, when `choose_mulligan` is offered:

```powershell
$command = @{ type = "choose_mulligan"; decision = "keep" } |
    ConvertTo-Json
$snapshot = Invoke-RestMethod -Method Post `
    -Uri "$base/games/$gameId/commands" `
    -Headers $headers -ContentType "application/json" -Body $command
```

The server checks each command against the current game state. `legal_actions`
is guidance for the viewer, not authorization to skip validation. Card commands
refer to a physical copy using its `card_instance_id`; card definition IDs in
the catalog identify card types, not copies in a game.

The command response has this shape:

```json
{
  "type": "state",
  "revision": 1,
  "state": { "game_id": "..." }
}
```

Use the monotonically increasing `revision` to ignore stale WebSocket updates.
`GET /games/{game_id}/state` returns only the state projection, without the
revision wrapper.

## WebSocket updates

Connect to `ws://127.0.0.1:8000/games/{game_id}/events` (use `wss://` with
HTTPS) and include `Authorization: Bearer <player_token>` in the WebSocket
handshake. The server first sends the latest `SessionSnapshot`, then sends a
viewer-specific snapshot after each accepted command. Commands are submitted
over HTTP; the socket is for receiving state. A rejected or expired session
closes with code `1008`.

For example, with Python's `websockets` package:

```python
import json
from websockets.sync.client import connect

url = f"ws://127.0.0.1:8000/games/{game_id}/events"
with connect(
    url,
    additional_headers={"Authorization": f"Bearer {player_token}"},
) as socket:
    snapshot = json.loads(socket.recv())
```

The game view is private to the authenticated player: it includes that player's
hand and legal actions, while hiding unrevealed opponent cards. Connect soon
after creating a game. Disconnected games allow a 30-second reconnect window;
an unconnected game expires after 60 seconds. These sessions are process-local,
and the API clears them when it stops, so run one API worker for local play.

## Errors

Errors use JSON with a `detail` field. Request/schema errors and invalid deck or
opponent selections return `422`; missing or invalid bearer credentials return
`401` or `403`; unknown or expired games return `404`; and a command rejected by
the current game rules returns `409`. The WebSocket closes with `1008` when its
game session is unavailable.
