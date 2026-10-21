# CLI cleanup tracker

Status: complete; both CLI cleanup items are implemented.
Review date: 2026-10-21. Starting point: main at a92f2ad.

Preserve behavior and existing contracts, including the game-history additions in the latest merge. Tests are excluded from this review; review and refactor them separately. Game-engine changes, new features, dependencies, and a broader architecture redesign are outside this work.

Keep the card abilities data together in one JSON file. Do not split it as part of cleanup.

## Scope and order

Focus on `clients/cli/src/anr_cli/`. Start with generic mulligan submission and then split screen responsibilities and menu dispatch. Keep short setup-menu branches, terminal input adaptation, and WebSocket reconnect handling unless a specific simplification is demonstrated.

## Work items

- [x] **Use the generic command path for opening-hand choices.**
  - Locations: `clients/cli/src/anr_cli/game_ui.py` (`_root_choices`, `_activate`, constructor), `clients/cli/src/anr_cli/main.py` (`play_ai`).
  - Represent mulligan choices as ordinary command actions. Remove the separate opening-hand callback and its duplicated submission, error handling, state update, and invalidation logic.
  - Complete when keeping and mulliganing both use the same submission flow as other actions. Inspect the existing fallback for snapshots without `legal_actions` before deciding whether to retain it; removing compatibility is a separate behavior decision.
  - Completed in commit 85ee3dab14a2b3b48d536e98f14b3880cd75131f. Live flow: started API and CLI from the worktree; exercised mulligan and keep in separate terminal games. Retained compatibility for snapshots without legal_actions by submitting ordinary choose_mulligan actions. Net source reduction: 9 lines.

- [x] **Split the game screen and simplify menu dispatch.**
  - Location: `clients/cli/src/anr_cli/game_ui.py`, especially `GameScreen`, `_root_choices`, and `_activate`.
  - Extract action-menu construction and rendering into cohesive modules. Keep state, navigation, and terminal application wiring in `GameScreen`.
  - Use explicit handlers keyed by action type or menu-choice kind for the large branching chains. Centralize action availability checks and ordinary action-choice construction. Keep simple labels in a table and complex labels in named formatters.
  - Share repeated visible-card lookups and formatting where this makes callers simpler. Avoid a generic UI framework or one class per action.
  - Complete when inspection, selection, submission, responsive layout, scrolling, live updates, and history remain coherent and functional.
  - Completed in commit bb8d9fcd9db6323448d4cd1aaf941b0ad10d9e8c. Live validation: started API and CLI from the second worktree, kept the opening hand, submitted Gain 1 credit and observed the refreshed view, opened history, browsed the hand and Seamless Launch card details, and scrolled the action menu. Checked both 120x40 wide and 80x24 compact layouts; the WebSocket remained connected.
  - Existing suite only: unittest discovery ran 165 tests, all passing. No tests were added.
  - Measured diff: this item added 1,018 lines and removed 663 (net +355); no line savings claimed for the extraction. The mulligan item removed 9 net lines, for +346 net across the work. Explicit action formatters and menu handlers improve the boundaries at the cost of additional code; no framework or dependencies were added.

## Completion checks

- Implement each item in a dedicated Git worktree. Start both the API and CLI from that worktree and exercise the affected flow through the running application before marking it complete.
- Existing automated checks may supplement live validation. Leave test review and test refactoring for the separate pass.
- Record the commit, live flow exercised, and consequential tradeoffs beside completed items.
- Measure line savings from the actual diff; moving code between files alone is not a reduction. Prefer small helpers and explicit mappings over frameworks or unnecessary abstractions.
