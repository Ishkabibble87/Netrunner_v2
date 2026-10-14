# Project guidance

- Use `docs/notes.md` for the project's current direction; treat it as a plan, not a fixed specification.
- Keep changes focused. Build the backend and CLI before the React client.
- Consult the rules reference under `rules/` when implementing game behavior. Document uncertain rules interpretations.

## Intended repository structure

```text
src/
  game/          # Core game state models, commands, rule facts, views, and rules engine
  application/   # Coordinates game/session use cases and the engine
  api/           # HTTP/WebSocket transport and API schemas
  content/
    cards/       # Card definitions and structured rule instructions
    formats/     # Versioned set membership, format legality, and ban lists
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
- Keep static card definitions separate from in-game card instances. Card records belong under `src/content/cards/`; use structured instructions for execution and retain printed text for display. Do not parse printed prose at runtime. The exact data schema remains to be designed.
- Use one generic card-play command/API path that identifies the in-game card instance. The engine should resolve behavior from that instance's card definition; do not add an endpoint per card. Keep card-definition IDs distinct from per-copy game instance IDs.
- Keep reusable game content and format data tracked. Ignore local files in `assets/` and `prototypes/`, while retaining their `.gitkeep` placeholders.


## Behavior

- **Keep answers succinct**, I will ignore long replies and ask you to repeat it using less techinical terms.
- Do not add new documentation files unless explicitly asked.
