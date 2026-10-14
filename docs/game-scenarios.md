# Game-flow scenarios

These scenarios are design exercises for the backend and CLI. They move from a basic action to a run that opens nested choices. Card abilities named **Training Wall**, **Security Toll**, and **Paid in Full** are test fixtures with the behavior described here, not real card text.

## 1. Runner takes a basic action

**Purpose:** Establish the smallest useful command-and-state loop: validate an action, pay its cost, apply its effect, and return the next legal actions to the player whose decision is due.

**Starting point:** It is the Runner's turn. The Runner has 4 clicks and 5 credits, and is in the action phase.

| Step | Command or rule | State change |
| --- | --- | --- |
| 1 | Runner sends `GainCredit` | Validate that the Runner is active and has a click. |
| 2 | Resolve the basic action | Spend 1 click and gain 1 credit. |
| 3 | Finish the action | Publish the updated player view and resume the action phase at its next paid ability window. |

**Result:** The Runner has 3 clicks and 6 credits. No prompt or run exists. The Runner's updated view includes the legal actions available in the action window; the Corp's view says it is waiting on the Runner. The Runner can take another action, or end the action phase after spending all clicks. At turn setup, the Runner receives their allotted clicks, recurring credits refill, and the turn formally begins; there is no Runner draw phase. (Rules 5.1.3, 5.2.2, 5.2.7b, 5.7.1.)

**State this exercises:** Active player, turn phase, clicks, credits, action validation, and a revisioned player view. The client should use its current view to avoid sending an action when the player is not allowed to act. The server still validates every command against authoritative state. Keep the accepted command separate from internal facts such as `ClickSpent` and `CreditGained`; an outbound update is a projection of those facts.

## 2. A run ends at rezzed ice

**Purpose:** Keep a run alive across commands and priority windows, then resolve an encounter that ends it before access.

**Fixture:** A remote server has one unrezzed **Training Wall** (rez cost 2) with one subroutine: “End the run.” The Runner has 1 click and 4 credits; the Corp has at least 2 credits. The Runner has no icebreaker that can break the subroutine.

1. The Runner sends `StartRun(remote-1)`. The engine validates and spends 1 click, creates run state, records the attacked server, and enters the initiation phase.
2. The run advances to approach the outermost ice. In the approach window, the Corp may rez the approached ice. The Corp sends `RezCard(training-wall)` and pays 2 credits; the ice becomes rezzed.
3. The run enters the encounter phase. Initialize this encounter's subroutine statuses as unbroken. The Runner gets a paid ability window, then the Corp must resolve unbroken subroutines in order.
4. Training Wall's subroutine ends the run. The run moves immediately to its run-ends phase; it does not finish the encounter or breach the remote. Mark it unsuccessful when the run timing structure completes, clear the active run, and finish resolving the action that initiated it. With no clicks left, the Runner's action phase then ends.

**Result:** The Runner has 0 clicks and 4 credits. The Corp has 2 fewer credits, and Training Wall is rezzed. No card was accessed. (Rules 6.1.4, 6.4.3, 6.5.1, 6.8, 6.9.1–6.9.3.)

**State this exercises:** Run phase and position, the encountered ice instance, encounter-scoped subroutine statuses, priority window and whose priority it is, the nested `StartRun` action frame, and a terminal run result. A pass or rez is a command resolved inside the current window, not a new action-phase action.

## 3. HQ breach, agenda cost, and a triggered ability

**Purpose:** Exercise hidden information, candidate selection, prompts, a static cost modifier, and a triggered ability during nested resolution.

**Fixtures:** HQ has no ice or cards in its root. The Corp's hand contains a 2-point agenda, **Vault Ledger**, and other cards. For this scenario, Vault Ledger adds 1 credit to its own steal cost. The Runner has 4 clicks, 2 credits, and an installed **Paid in Full** training resource whose ability is “When you steal an agenda, gain 1 credit.”

1. The Runner sends `StartRun(HQ)`. With no ice to approach, the run advances to movement, reaches HQ, is declared successful, and begins a breach.
2. At the start of the breach, the random access limit is set to 1. The Runner chooses to access a random candidate from HQ. The server selects a card from HQ without disclosing other hand contents and records that chosen object as no longer a candidate for this breach.
3. The chosen card is revealed for access. If it is Vault Ledger, the Runner receives the mid-access opportunity; then the agenda must be stolen unless an additional steal cost is declined. The access view must disclose the accessed card to the Runner; the Corp may also see it because it was accessed from HQ.
4. Vault Ledger's additional cost is 1 credit. The Runner receives a prompt to pay or decline. If paid, resolve the steal instruction through any applicable interrupt window before moving Vault Ledger to the Runner's score area and adding 2 agenda points. Once the steal occurs, record an `AgendaStolen` fact; at the next applicable checkpoint, match it against active conditional abilities so Paid in Full becomes pending for a reaction window and can give the Runner 1 credit. If an interrupt prevents or changes the imminent instruction, record only the facts for what actually happens.
5. Finish the access, then the breach. The HQ random access limit has been reached, so no more cards from HQ can be chosen this breach. Finish the run and return to the Runner's action phase.

If the random card is not an agenda, follow its access rules instead; the scenario should not assume the draw from HQ selects Vault Ledger.

**State this exercises:** A server-side random selection that preserves hand secrecy, breach candidates and the fixed random access limit, card visibility during access, a suspendable access prompt, active static effects when calculating costs, a pending triggered-ability instance, and nested resolution frames such as `action → run → breach → access → reaction`. Prompt ownership and legal choices belong in server state; each player receives only their permitted view. (Rules 7.1.2, 7.1.4–7.1.6, 7.2, 7.3.4–7.3.5, 7.4, 9.1.2, 9.2.8, 9.6.)

## Design takeaways

- Treat commands as player intent. The engine checks legality against the current state and window before applying costs or effects.
- Let the CLI inspect the current player-visible state before sending a game command. Read-only client actions such as showing status, viewing the board or hand, or inspecting a card/server should not advance the game, consume a click, or change priority.
- Record facts after changes occur and use them at checkpoints to find triggered abilities. Check interrupts against an instruction's expected effects before it resolves; evaluate static abilities continuously against the current state.
- Keep durable game state (turn, zones, cards, run, scores) separate from transient resolution state (frames, priority windows, prompts, pending abilities).
- Record internal rule facts independently of client messages. Build each player's messages and snapshots from those facts and that player's visibility rules.
- Store enough context to resume after every choice. A run, breach, access, or ability can span multiple client commands.
- Keep card prose out of runtime rule evaluation. The named fixture abilities should be structured data or explicit handlers, as outlined in `docs/notes.md`.

## Client permissions and concealed information

- Give the client a maintained, player-specific view model rather than the internal game-state model. Update it from snapshots and live updates; omit hidden card identities and opponent-only details instead of sending them and relying on the UI to hide them. The player can inspect anything in their current view at any time, including while waiting, without issuing a game command.
- Every published player view after a command or game-flow step should report that player's decision state. Send the player who currently has the decision a list of their next legal actions; send the other player a `waiting_on_opponent` state so the client can say they are waiting on the opponent. Include the current window type when useful. The legal-action list is a player-specific API output, not just internal engine state.
- Here, the player who has the decision may be the inactive player in a priority window. Distinguish the Netrunner **active player** (whose turn it is) from the **decision owner** (the player currently allowed or required to act). In an action window, they are the same; in a priority window, the decision owner follows the priority rules. A player with a prompt likewise receives only the choices they can make.
- Do not send one player the opponent's legal-action list or an explicit “no legal action” result. Do not auto-pass by inspecting the opponent's hidden legal actions: keep the public priority-window sequence consistent, and let each player's own view show their choices. Only auto-resolve a window when the rules determine its outcome independently of hidden options.
