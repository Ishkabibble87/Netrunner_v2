# Project direction

Build a playable Android: Netrunner game, starting with the backend and game flow. A prototype exists elsewhere, but this project should stand on its own; bring over useful ideas as needed without depending on that prototype.

## Game and API

- The backend should own the game state and rules engine.
- The API should support playing a complete game.
- Clients send commands/actions to the server. The rules engine validates and resolves them, then the server publishes events or prompts so clients can stay up to date.
- A player can create a game, receive a session ID, and wait in a ready state.
- Another player or opponent can join using that ID. Once a second player joins, the game moves to in progress and no more players can join.

## Initial card pool and future set support

The initial playable card pool is **System Gateway only**. Do not implement every future set's card effects as part of the initial scope.

Design the rules engine and card model so later cards can be added without set-specific rewrites. The future compatibility target is the current Null Signal Games Standard pool, as of 2026-10-14:

- System Gateway
- Ashes Cycle: Downfall and Uprising
- Borealis Cycle: Midnight Sun and Parhelion
- Liberation Cycle: The Automata Initiative and Rebellion Without Rehearsal
- Elevation
- Vantage Point

System Update 2021 is not in this target Standard pool. Refresh the target when official releases or rotations change it.

Keep card definitions separate from printings, set membership, and format legality. Store format pools and ban lists as separate, versioned data, and retain card records when sets rotate out. Model abilities, timing windows, prompts, and effects generically; use explicit card handlers only for effects that cannot be represented by shared operations. Adding future card data should not require code keyed to a set or card title, though genuinely new mechanics may require new engine support.

## Initial model outline

Treat this as a planning map; refine it as game flow is implemented.

- **Game session:** ID, lifecycle/status, player seats and roles, and final outcome. Keep waiting-room/session coordination in `src/application/`.
- **Game state:** Authoritative state for a started game, in `src/game/`.
- **Player state:** Role, credits, clicks, tags, bad publicity, core damage, the Runner's maximum memory limit, and other changing player values. Keep remaining clicks here rather than duplicating them in turn state. Calculate used memory from installed programs; calculate agenda score from cards in the score area (Rule 1.17.1) and maximum hand size from core damage and applicable modifiers (Rules 5.5.3, 10.4.2b). The run-specific bad publicity fund belongs in run state (Rules 10.6.2–10.6.3).
- **Deck and identity:** The selected deck list and identity used to set up each player.
- **Card definition:** Static card data, including printed text and structured ability definitions.
- **Card instance:** A specific card in a game, referring to its definition and tracking owner/controller, zone/location, counters, face-up state, and host when applicable.
- **Zones and board:** Player zones plus the shared play area, including Corp servers and their installed cards/ice. HQ, R&D, and Archives always exist and use stable IDs. The rules describe remotes by their relative row location but assign no permanent number. **Implementation convention:** use one-based current positions, nearest-to-central first, and compact them when a remote ceases to exist at a checkpoint (Rules 4.6.8c/e, 8.5.8–8.5.9). Resolution context must preserve the previous server when an ability refers to “this server” after its source moves (Rule 4.6.6i). Runner hardware, programs, and resources are distinguished by card type in the play area rather than modeled as separate zones.
- **Turn state:** Turn number, active player, and turn phase/step.
- **Run state:** Attacked server, run phase/step, ice position/encounter progress, and breach/access progress. A run persists in game state across commands and player decisions until it ends.
- **Resolution state:** Current timing/resolution frames, nested priority windows, pending abilities, and any player prompt awaiting a choice. Keep this flexible; Netrunner timing is broader than a simple LIFO stack.
- **Player view:** A role-specific projection of game state that hides information the player is not allowed to see. The client maintains its current copy from snapshots and live updates; the player can inspect any information in that copy at any time, including while waiting on the opponent. Inspection reads the local view and does not ask the rules engine to resolve a game command.
- **Decision state and legal actions:** At each published step, the API should send the decision owner their next legal actions. The other player's view should say they are waiting on the opponent. The decision owner can differ from the turn's active player during priority windows, so model these separately; never include the opponent's legal-action list in a player's view.

### Ability and event model

- Abilities belong to card definitions; runtime card instances and current game state determine which abilities are active.
- Use a shared ability concept with specialized forms rather than forcing every ability into one shape:
  - **Static abilities** apply while their conditions are true. The rules engine checks relevant active static abilities when calculating costs, legality, values, and effects.
  - **Triggered abilities** become pending when their trigger condition occurs. The engine resolves them in the appropriate timing window and tracks each pending instance and its relevant context.
  - **Interrupt abilities** are available at the timing points and for the imminent instructions defined by the rules.
- Keep internal **game facts** (for example, `TurnBegan`, `RunInitiated`, `CardInstalled`, `AgendaStolen`) separate from outbound client messages. A fact records something that actually happened. At the next rule-defined checkpoint, match it against active conditional abilities and create the appropriate pending ability instances for reaction windows.
- Handle interrupts before an instruction resolves: represent the imminent instruction and its expected effects, open its interrupt window, and determine relevant interrupt options according to their timing and source-activity rules. Conditional interrupts are marked pending as the window opens; relevant paid interrupts may become available while the window remains open. Update expected effects as interrupts or replacement effects apply; emit facts only for the changes that actually occur when the instruction resolves.
- Static abilities are continuous effects, not abilities triggered by facts. Evaluate their conditions and effects against current game state; when a fact changes that state, subsequent legality, cost, and value calculations must reflect the updated static effects. (Rules 9.4, 9.6, 9.9.)
- Keep these internal facts and timing records separate from outbound client messages. Socket messages are player-specific views of state changes, events, and prompts.
- Card text is for display; rules execution should use structured ability definitions or explicit Python handlers, not depend on parsing prose at runtime.

## Provisional API surface

Use HTTP for game setup and snapshots, and a WebSocket for live commands and updates. Route and message names can change as the CLI drives the design.

### HTTP

- `POST /games` — Create a game and return its game/session ID. Include the creator's seat/deck and, when applicable, the selected scripted-opponent profile.
- `POST /games/{game_id}/join` — Join a waiting game with the second player's seat/deck. Starting the game when the second player joins follows the lifecycle above.
- `GET /games/{game_id}` — Get game lifecycle and seat/role summary.
- `GET /games/{game_id}/state` — Get a role-specific state snapshot for initial load or reconnect, including that player's decision state and legal actions when they may act; never return unrestricted internal state.
- `GET /opponent-profiles` — Optional if scripted profiles are managed by the API. If they remain local JSON files used by the CLI, this endpoint is unnecessary.
- `GET /health` — Basic service health for local development and CLI setup.

### WebSocket

- `WS /games/{game_id}/events` — Authenticate/associate the connected player, receive commands, and send that player's events, prompts, or refreshed view. Every published update reports either the recipient's legal actions or that they are waiting on the opponent.
- Client messages should represent intent, such as `StartRun`, `PlayCard`, `ChooseOption`, `TriggerAbility`, or `PassPriority`.
- Server messages should describe accepted results or required choices, such as `RunStarted`, `CardPlayed`, `PromptIssued`, or `ActionRejected`. Include a sequence/revision so clients can detect missed updates; reconnect behavior and event replay can be specified later.

Keep API request/response schemas separate from internal domain models where their needs differ, especially for hidden information and player-specific views.

## Clients

1. **CLI:** Build this first to develop and exercise the backend and game flow. When starting a game, let the user choose an AI opponent from the list of JSON profiles as the opponent. Separate game commands that ask the engine to change or resolve game state from read-only inspection commands, such as showing status, the board, or hand, or inspecting a card or server. Use the client's current player-specific state view as the source for inspection. These client interactions should help a player decide what to do next without changing game state or timing, and must show only information available in that player's view.
2. **Scripted opponent:** A JSON profile should describe an opponent's actions and timing, and be selectable when starting a game from the CLI.
3. **Agent-friendly CLI:** Provide a mode that presents game commands, read-only inspection options, current player-visible state, and legal actions in a format useful to an AI agent, for testing or play.
4. **React client:** This is the eventual player-facing client, after the backend and game flow are established.

## Suggested order

Start with the API, rules engine, and CLI. Add scripted and agent-friendly play, then build the React client.
