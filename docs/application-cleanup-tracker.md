# Application cleanup tracker

Status: planned; no production refactoring has been implemented.
Review date: 2026-10-21. Starting point: main at a92f2ad.

Preserve behavior and existing contracts, including the game-history additions in the latest merge. Tests are excluded from this review; review and refactor them separately. Game-engine changes, new features, dependencies, and a broader architecture redesign are outside this work.

Keep the card abilities data together in one JSON file. Do not split it as part of cleanup.

## Scope and order

Focus on `src/application/`. Extract the training-opponent policy and simplify broad command imports while preserving the compact session store and game-setup code. Keep authoritative game rules in the game layer.

## Work items

- [ ] **Extract the training-opponent policy.**
  - Location: `src/application/sessions.py` (`advance_ai_setup`, `advance_ai_decisions`).
  - Move scripted opponent choices into a focused application module. Replace the nested command-selection ternary with readable selection steps, preserving the current priority order and run-time rez preference.
  - Complete when session lifecycle and authentication remain in the store, opponent policy uses authoritative engine commands, and AI behavior is unchanged.

- [ ] **Use a module import for game commands.**
  - Location: `src/application/sessions.py` and the extracted opponent-policy module.
  - Prefer `from game import commands as cmd` where many command types are used. Keep small, focused imports readable.
  - Complete when command references are consistently qualified without circular imports or changed behavior.

## Completion checks

- Implement each item in a dedicated Git worktree. Start both the API and CLI from that worktree and exercise the affected flow through the running application before marking it complete.
- Existing automated checks may supplement live validation. Leave test review and test refactoring for the separate pass.
- Record the commit, live flow exercised, and consequential tradeoffs beside completed items.
- Measure line savings from the actual diff; moving code between files alone is not a reduction. Prefer small helpers and explicit mappings over frameworks or unnecessary abstractions.
