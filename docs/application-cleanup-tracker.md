# Application cleanup tracker

Status: complete.
Review date: 2026-10-21. Starting point: main at 50bb595.

Preserve behavior and existing contracts, including the game-history additions in the latest merge. Tests are excluded from this review; review and refactor them separately. Game-engine changes, new features, dependencies, and a broader architecture redesign are outside this work.

Keep the card abilities data together in one JSON file. Do not split it as part of cleanup.

## Scope and order

Focus on `src/application/`. Extract the training-opponent policy and simplify broad command imports while preserving the compact session store and game-setup code. Keep authoritative game rules in the game layer.

## Work items

- [x] **Extract the training-opponent policy.**
  - Location: `src/application/sessions.py` (`advance_ai_setup`, `advance_ai_decisions`).
  - Move scripted opponent choices into a focused application module. Replace the nested command-selection ternary with readable selection steps, preserving the current priority order and run-time rez preference.
  - Complete when session lifecycle and authentication remain in the store, opponent policy uses authoritative engine commands, and AI behavior is unchanged.
  - Record: commit `5280ed4`; the live CLI flow kept the Runner hand, submitted a credit action, and observed the training Corp complete its turn through the API. The standard training game had no installed Corp card, so the run-time rez branch was not reached live; its priority check was preserved in the extracted code.
  - Tradeoff: the application files gained 39 net lines because the policy is now explicit and isolated; this cleanup favors readable decision steps over line reduction.

- [x] **Use a module import for game commands.**
  - Location: `src/application/sessions.py` and the extracted opponent-policy module.
  - Prefer `from game import commands as cmd` where many command types are used. Keep small, focused imports readable.
  - Complete when command references are consistently qualified without circular imports or changed behavior.
  - Record: commit `5280ed4`; commands are qualified through `cmd` in both application modules, and the existing 165-test suite and live API/CLI flow passed.

## Completion checks

- Implemented in dedicated Git worktree `codex/application-cleanup`. Started the API on port 8001 and CLI from that worktree; exercised the System Gateway Starter Runner against the training opponent. The API health check and game commands returned successfully, and the CLI left the session cleanly. The stock opponent does not install cards, so the live run-time rez path was unavailable in this flow.
- Existing test suite: `uv run --extra test python -m unittest discover -s tests` — 165 tests passed. No tests were added or refactored. `uv run ruff check .` also passed.
- Actual application diff: 97 insertions and 58 deletions, a net increase of 39 lines; moving code between files is not counted as a reduction.
