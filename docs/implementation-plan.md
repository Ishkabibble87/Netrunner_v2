# CLI game work plan

## Start here

Build the game bootstrap and one complete action loop first. The repository already has the core state shapes and deck importer, but nothing yet turns a selected deck into a started game or lets a player submit an action. This work will expose gaps in setup, visibility, and command handling before card effects multiply them.

Target the System Gateway pool and a CLI game against a selected scripted-opponent profile. Keep the rules engine independent of the API and CLI. A profile can fill the opponent seat at creation; keep the session model able to support a second human joining later.

## Work sequence

### 1. Create a game from selected decks

- Add an application-level `GameSession` for the game ID, lifecycle, seats, and player identities.
- Add a game setup function that validates the selected identities and card definitions, creates uniquely identified card instances, shuffles each deck, deals opening hands, and initializes credits, clicks, and the first turn.
- Start from the tracked System Gateway starter decks. Keep the existing NetrunnerDB importer as another input path; deck import itself does not establish legality.
- Keep API setup schemas separate from `GameState`.

**Checkpoint:** the CLI can start a fresh game and retrieve each seat's own initial view.

### 2. Add the command, turn, and view loop

- Add typed commands and an engine entry point that validates each command against authoritative state.
- Add a player-specific `GameView` with visible cards, turn/window information, legal actions for the decision owner, and a waiting status for the other player.
- Implement turn setup and the first basic actions: gain credits, draw, install, play an operation/event, and end the turn. Keep click counts on `PlayerState`.
- Keep read-only inspection separate from commands that change game state.

**Checkpoint:** a player can take legal actions, see the updated view, and cannot act out of turn or spend unavailable clicks/credits.

### 3. Play a small set of cards

- Add structured card instructions for shared effects; do not execute printed text.
- Support costs, moving a played card to its destination, and simple credit effects. Use **Hedge Fund** and **Sure Gamble** as initial examples if their definitions are present in the selected catalog.
- Keep one generic `PlayCard` command keyed by card instance ID. The engine looks up its definition to resolve the effect.

**Checkpoint:** the same play path handles both sides, deducts costs, applies effects, and publishes the resulting view.

### 4. Expose the loop and drive it from the CLI

- HTTP: `POST /games`, `POST /games/{id}/join` for a human seat, `GET /games/{id}`, `GET /games/{id}/state`, and `GET /health`.
- WebSocket: `WS /games/{id}/events` for commands and player-specific updates. Keep commands generic; don’t add a route for each action or card.
- CLI: `new --deck-file … --side … --opponent …`, `join <game-id> --deck-file …`, read-only `status`/`board`/`hand`/`inspect`/`legal`, and action commands such as `gain-credit`, `draw`, `install`, `play`, and `end-turn`.
- Keep opponent profiles local JSON for the first CLI pass; add a profile endpoint only if the server later owns those profiles.

**Checkpoint:** the CLI uses the API rather than importing game internals, and inspection does not change the game.

### 5. Add runs before calling the game playable

Runs are a core Runner action and need their own persistent `RunState`, priority windows, prompts, and access flow. Add them after the basic action/card loop so they can be built against the command and view patterns above. Include a run against a central server, then ice encounters and card access. Keep resolution state separate from durable board state.

## Scope guardrails

- No React client in this phase.
- Don’t build a complete ability/event framework before a card needs it; add shared effect types incrementally.
- Keep card definitions, printings/set membership, and format legality separate. For the first pass, support and validate only the chosen System Gateway pool.
- Use `docs/game-scenarios.md` to guide run and prompt behavior. The current repository file inventory does not include rule-reference files under `rules/`.
