# API cleanup tracker

Status: planned; no production refactoring has been implemented.
Review date: 2026-10-21. Starting point: main at a92f2ad.

Preserve behavior and existing contracts, including the game-history additions in the latest merge. Tests are excluded from this review; review and refactor them separately. Game-engine changes, new features, dependencies, and a broader architecture redesign are outside this work.

Keep the card abilities data together in one JSON file. Do not split it as part of cleanup.

## Scope and order

Focus on `src/api/`. Start with command conversion, catalog serialization, and broad imports; then separate projection responsibilities and share snapshot construction. Preserve concealed-information boundaries and HTTP/WebSocket response shapes.

## Work items

- [ ] **Consolidate API command conversion.**
  - Location: `src/api/schemas.py` (`game_command_from_request`, `command_request_from_game`).
  - Replace mirrored type-check chains with one explicit request/domain type mapping and small conversion helpers. Keep selection descriptors as explicit special cases, and preserve tuple/list normalization.
  - Complete when all currently supported commands convert in both directions with unchanged fields, validation, defaults, and unsupported-type errors. Keep the existing validated request models; do not generate schemas dynamically just to reduce lines.

- [ ] **Use an allowlist for card-catalog serialization.**
  - Location: `src/api/schemas.py` (`CardCatalogEntry.from_definition`).
  - Select the fields declared by `CardCatalogEntry` instead of maintaining a growing exclusion list of internal card fields.
  - Complete when catalog output is unchanged and adding an internal card-definition field cannot accidentally expose it or require another exclusion.

- [ ] **Use module imports for broad command and schema imports.**
  - Locations: `src/api/schemas.py` (game commands), `src/api/app.py` (API schemas).
  - Prefer `from game import commands as cmd` and `from . import schemas` where most of a module's surface is used. Keep small, focused imports readable.
  - Complete when references are qualified consistently without circular imports or obscuring their source.

- [ ] **Separate API models, command adapters, and game projection.**
  - Location: `src/api/schemas.py`, especially `InitialGameView.from_game_state`.
  - Establish clear boundaries for command schemas/conversion, response models, and projection building. Extract card, player, installed-card, run, and decision projection helpers as useful.
  - Keep viewer visibility checks explicit and ensure the latest history projection is preserved. Avoid circular imports between models and builders.
  - Complete when HTTP and WebSocket response shapes remain unchanged and each viewer receives only the information they are entitled to see.

- [ ] **Share session-snapshot construction.**
  - Location: `src/api/app.py` (`submit_command`, `game_events`).
  - Extract the repeated revision plus viewer-specific state projection into a small helper used by command responses, broadcasts, and initial socket delivery.
  - Complete when all paths use the same construction while preserving recipient identity, revision handling, and broadcast ordering. Keep the compact route module together unless further separation has a clear benefit.

## Completion checks

- Implement each item in a dedicated Git worktree. Start both the API and CLI from that worktree and exercise the affected flow through the running application before marking it complete.
- Existing automated checks may supplement live validation. Leave test review and test refactoring for the separate pass.
- Record the commit, live flow exercised, and consequential tradeoffs beside completed items.
- Measure line savings from the actual diff; moving code between files alone is not a reduction. Prefer small helpers and explicit mappings over frameworks or unnecessary abstractions.
