# Android: Netrunner project guidance

## How to work

- Keep answers succinct.
- Always create a dedicated Git worktree before starting a new feature or change.
- Always start both the API and CLI from the worktree and exercise the affected flow through the running application before considering a change complete. Automated tests supplement this live check; they do not replace it.
- Work in small, visible steps. For substantial changes, briefly state your understanding and first step, then keep the user oriented about important progress and decisions.
- Make routine, low-risk choices. Ask when uncertainty could change product behavior, scope, or architecture. When the user is thinking through an idea, help reason about it before turning it into code.
- Keep scope deliberate. Prefer the simplest solution that meets the goal. Flag features, abstractions, or dependencies that add complexity without clear value; don't quietly expand the request.
- Preserve intent and coherence across the system, not only in nearby code. Add docstrings to substantial objects and files with several small components to capture their purpose and context. Use comments sparingly for non-obvious choices, and explain consequential tradeoffs.
- Maintain one growing end-to-end test for the core application flow and extend it as functionality is added. Don't add unit tests for every change; use them only when a specific behavior needs separate coverage.
- Do not add documentation files unless explicitly asked.
- Consult the rules reference under `rules/` when implementing game behavior. Document uncertain rules interpretations.

## Intended repository structure

```text
content/       # Shared content package
  cards/       # Card definitions and structured rule instructions
  decks/       # Deck importers and reusable deck lists
  formats/     # Versioned set membership, format legality, and ban lists
  opponents/   # Opponent profiles
src/
  game/          # Core game state models, commands, rule facts, views, and rules engine
  application/   # Coordinates game/session use cases and the engine
  api/           # HTTP/WebSocket transport and API schemas
clients/
  cli/           # CLI client; use the API rather than importing game internals
  react/         # Future React client
assets/          # Local-only assets; contents are ignored by Git
prototypes/      # Local-only experiments; contents are ignored by Git
docs/
rules/
```

- Keep the game engine independent of API and client code. Put authoritative game-state models and rule resolution in `src/game/`; keep API request/response schemas in `src/api/`.
- Keep `src/application/` as coordination for use cases such as creating/joining games and submitting commands. It should not implement game rules. Start with simple in-memory session storage; add persistence structure when needed.
- Add model and rule types incrementally as features need them; do not create the full game model or event catalog up front.
- Distinguish player commands, internal rule facts, and player-visible API updates. Do not assume the internal facts must be persisted as an event-sourced log.
- Keep static card definitions separate from in-game card instances. Card records belong under `content/cards/`; use structured instructions for execution and retain printed text for display. Do not parse printed prose at runtime. The exact data schema remains to be designed.
- Use one generic card-play command/API path that identifies the in-game card instance. The engine should resolve behavior from that instance's card definition; do not add an endpoint per card. Keep card-definition IDs distinct from per-copy game instance IDs.
- Keep reusable game content and format data tracked. Ignore local files in `assets/` and `prototypes/`, while retaining their `.gitkeep` placeholders.
