# API cleanup tracker

Status: complete; production refactoring implemented.
Review date: 2026-10-21. Starting point: main at 50bb595.

Preserve behavior and existing contracts, including the game-history additions in the latest merge. Tests are excluded from this review; review and refactor them separately. Game-engine changes, new features, dependencies, and a broader architecture redesign are outside this work.

Keep the card abilities data together in one JSON file. Do not split it as part of cleanup.

## Scope and order

Focus on `src/api/`. Start with command conversion, catalog serialization, and broad imports; then separate projection responsibilities and share snapshot construction. Preserve concealed-information boundaries and HTTP/WebSocket response shapes.

## Work items

- [x] **Consolidate API command conversion.**
  - Location: `src/api/schemas.py` (`game_command_from_request`, `command_request_from_game`).
  - Replace mirrored type-check chains with one explicit request/domain type mapping and small conversion helpers. Keep selection descriptors as explicit special cases, and preserve tuple/list normalization.
  - Complete when all currently supported commands convert in both directions with unchanged fields, validation, defaults, and unsupported-type errors. Keep the existing validated request models; do not generate schemas dynamically just to reduce lines.
  - Completed in `28000dc`; CLI live flow created a game and submitted “keep opening hand”; net change: -36 lines. Selection descriptors remain explicit, and Pydantic request validation remains unchanged.

- [x] **Use an allowlist for card-catalog serialization.**
  - Location: `src/api/schemas.py` (`CardCatalogEntry.from_definition`).
  - Select the fields declared by `CardCatalogEntry` instead of maintaining a growing exclusion list of internal card fields.
  - Complete when catalog output is unchanged and adding an internal card-definition field cannot accidentally expose it or require another exclusion.
  - Completed in `2104124`; `/card-catalog` returned 77 entries with only declared fields, and the CLI used the catalog during game creation; net change: -1 line.

- [x] **Use module imports for broad command and schema imports.**
  - Locations: `src/api/schemas.py` (game commands), `src/api/app.py` (API schemas).
  - Prefer `from game import commands as cmd` and `from . import schemas` where most of a module's surface is used. Keep small, focused imports readable.
  - Complete when references are qualified consistently without circular imports or obscuring their source.
  - Completed in `6d4a6c3`; API startup and the CLI game-creation/command flow passed; net change: -38 lines.

- [x] **Separate API models, command adapters, and game projection.**
  - Location: `src/api/schemas.py`, especially `InitialGameView.from_game_state`.
  - Establish clear boundaries for command schemas/conversion, response models, and projection building. Extract card, player, installed-card, run, and decision projection helpers as useful.
  - Keep viewer visibility checks explicit and ensure the latest history projection is preserved. Avoid circular imports between models and builders.
  - Complete when HTTP and WebSocket response shapes remain unchanged and each viewer receives only the information they are entitled to see.
  - Completed in `6eb0b10`; live CLI setup, initial WebSocket snapshot, and command projection passed; net change: +39 lines. `InitialGameView.from_game_state` remains as a forwarding entry point for existing callers; API routes call the projection builder directly.

- [x] **Share session-snapshot construction.**
  - Location: `src/api/app.py` (`submit_command`, `game_events`).
  - Extract the repeated revision plus viewer-specific state projection into a small helper used by command responses, broadcasts, and initial socket delivery.
  - Complete when all paths use the same construction while preserving recipient identity, revision handling, and broadcast ordering. Keep the compact route module together unless further separation has a clear benefit.
  - Completed in `338466b`; CLI live flow exercised initial WebSocket delivery, command response, and broadcast; net change: +3 lines. The command lock and broadcast iteration order remain unchanged.

## Completion checks

- Implement each item in a dedicated Git worktree. Start both the API and CLI from that worktree and exercise the affected flow through the running application before marking it complete.
- Existing automated checks may supplement live validation. Leave test review and test refactoring for the separate pass.
- Record the commit, live flow exercised, and consequential tradeoffs beside completed items.
- Measure line savings from the actual diff; moving code between files alone is not a reduction. Prefer small helpers and explicit mappings over frameworks or unnecessary abstractions.
- Total `src/api/` change from `origin/main`: 326 insertions and 359 deletions, net -33 lines.
