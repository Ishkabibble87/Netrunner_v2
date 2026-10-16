# Basic game actions and CLI review

Reviewed: 2026-10-15. This records the current findings and a proposed implementation sequence. No gameplay changes have been made.

Implement one numbered step at a time, verify its checkpoint, and stop for review before starting the next step. The sequence can be adjusted after each review.

## Current position

- `src/game/models.py` already represents players, credits, clicks, tags, bad publicity, core damage, memory limits, card instances, counters, zones, servers, and turn phases. Keep these models and extend them as features require.
- Opening hands are already implemented: `deal_deck()` in `src/application/game_setup.py` creates card copies, shuffles each deck, and puts five cards into each player's hand. Setup also gives each player five credits. This opening deal does not need to be implemented again.
- Drawing during play is not implemented: the one-click draw action and the Corp's free mandatory draw each turn are still missing. Both players currently have zero clicks, with the Corp at the beginning of its draw phase. Turn setup remains pending.
- There is no command model or rules engine to advance the game or resolve actions.
- `src/api/schemas.py` provides an initial player-specific view that conceals the opponent's hand. It does not yet expose installed cards, decision ownership, legal actions, prompts, or run state.
- `src/api/app.py` authenticates WebSocket connections and sends an initial snapshot. Incoming messages are discarded; commands and subsequent game updates are not implemented.
- `clients/cli/src/anr_cli/session.py` maintains the connection but discards subsequent messages. Reconnect replaces `initial_state` without updating the existing game screen.
- `clients/cli/src/anr_cli/game_ui.py` provides inspection and exit menus. It keeps its opening state and infers waiting status from whose turn it is.
- Opponent profiles select decks only. There is no scripted playing behavior.

## Basic actions to support

These are the base costs before card modifiers. See rules 5.2.6 and 5.2.7 in the [local turn reference](../rules/Null_Signal_Games_Netrunner_Comprehensive_Rules_v26.03/05-turns.md).

| Side | Action | Base cost | CLI input |
| --- | --- | --- | --- |
| Both | Gain a credit | 1 click | None |
| Both | Draw a card during play | 1 click | None |
| Corp | Install agenda, asset, upgrade, or ice | 1 click; ice also costs 1 credit per other ice protecting the destination when its cost is paid | Card, existing server or new remote, installation trash choices |
| Runner | Install program, hardware, or resource | 1 click plus printed install cost | Card, any required host, program trash choices |
| Both | Play operation/event | 1 click plus play cost | Card, then required targets or choices |
| Corp | Advance an eligible installed card | 1 click + 1 credit | Card |
| Corp | Trash a Runner resource while the Runner is tagged | 1 click + 2 credits | Resource |
| Corp | Purge virus counters | 3 clicks | None |
| Runner | Run a server | 1 click | Server, then run decisions |
| Runner | Remove a tag | 1 click + 2 credits | None |

Rez, agenda scoring, paid abilities, and passing priority also need commands. They follow their permitted timing windows rather than being additional basic click actions. Scoring is available only in the appropriate windows on the Corp's turn.

## Shared requirements

- Put authoritative command validation and rule resolution in `src/game/`. Keep session coordination in `src/application/`, transport schemas in `src/api/`, and the CLI dependent on the API.
- Check the authenticated player, decision ownership, timing, costs, card ownership, and targets. Reject an invalid command without partially applying that command's costs or effects.
- Distinguish the active player from the player who currently has a decision. Track timing steps, priority, passes, and pending choices; add resolution context as effects need it.
- Add persistent run, encounter, breach, and access state when implementing runs. A run can span multiple commands.
- Use structured instructions or explicit handlers for card effects. Printed text remains for display. Keep one generic `PlayCard` path using the in-game instance ID; do not create endpoints per card.
- Calculate agenda score, used memory, and maximum hand size from state and applicable effects. Add a game outcome so completed games stop accepting gameplay commands.
- Publish each player's own visible board, decision state, and legal actions. Conceal unknown card identities and types where required, including unrezzed remote-root cards. Never expose the opponent's legal-action list.
- Maintain a session revision and process commands sequentially. Return clear rejections and refreshed views. Reconnect must restore the current view without accidentally replaying an action.
- Keep inspection local and available while waiting. Do not advance priority merely because the opponent has no hidden legal options.

## Implementation sequence and review checkpoints

All steps below are pending. Early engine steps can be reviewed through focused tests before the CLI action loop is connected.

| Step | Work | Review checkpoint |
| --- | --- | --- |
| 1 | Add the engine entry point, typed commands, and gain-credit action with a minimal decision view. | In a prepared action window, either side spends one click and gains one credit. Wrong player, wrong window, and insufficient clicks are rejected without changes. |
| 2 | Add turn-start timing, priority ownership, and passing. | Corp receives three clicks; Runner receives four. Priority passes correctly, and ordinary actions cannot occur inside a paid ability window. |
| 3 | Add drawing during play: the one-click draw action and the Corp's mandatory draw each turn. Preserve the existing opening-hand deal. | Both players still receive five opening cards. A basic draw spends one click; the Corp's mandatory draw is free and separate from the opening deal. Deck positions remain valid. Runner has no mandatory draw; an unsuccessful Corp draw ends the game correctly. |
| 4 | Add discard choices and turn completion. | The player selects exactly the required excess cards, discarded together. Corp discards facedown. After the final windows, the next turn starts correctly. |
| 5 | Connect commands and updated views through the API. | Authenticated commands reach the engine, accepted changes increase the revision, rejections are returned, and each connected player receives only their own view. |
| 6 | Connect the CLI action menu and live state. | Gain-credit, draw, pass, and discard work through the API. The board and menu refresh during inspection and after reconnect; selections use current instance IDs. |
| 7 | Add a minimal scripted-opponent driver. | Either human side can complete repeated turns against an opponent that passes, takes simple legal actions, and answers discard prompts through the same engine. |
| 8 | Add Corp installation. | Correct server restrictions, new remotes, outermost ice placement, ice costs, replacement trash choices, and concealed opponent views are demonstrated. |
| 9 | Add Runner installation. | Printed costs, available memory, program trash choices, hosting when supported, and uniqueness checkpoints are handled. The rig updates in the CLI. |
| 10 | Add generic operation/event play, starting with Hedge Fund and Sure Gamble. | The selected instance pays its costs, executes structured effects, and is trashed after resolution. Unsupported effects are explicitly unavailable. |
| 11 | Add advancement. | Only eligible installed cards can be advanced; one click and one credit produce one advancement counter. |
| 12 | Add rezzing. | Only eligible cards can be rezzed, their costs are paid, and timing restrictions are enforced. Ice rezzing is enabled with the later run windows. |
| 13 | Add agenda scoring and score-based outcomes. | Sufficient advancement and the correct scoring window are required. Scoring moves the agenda, removes advancement counters, resolves supported effects, and checks victory. |
| 14 | Add Runner tag removal. | One click and two credits remove one existing tag; the CLI offers it only when legal. |
| 15 | Add the Corp's tagged-resource trash action. | The Runner must be tagged, the target must be an installed Runner resource, and costs and trash effects resolve correctly. |
| 16 | Add virus purging. | Three clicks remove all virus counters hosted on cards, with any supported reactions resolved. |
| 17 | Add persistent run timing and run decisions. | A run spends one click and retains its server, timing, priority, and bad-publicity fund across commands. Ordinary actions stay blocked while it resolves. Access remains an explicit unfinished dependency until step 18. |
| 18 | Add breach and access for unprotected servers. | Server-specific access selection preserves secrecy; the CLI handles access order and costs where needed. Agendas are stolen and eligible cards can be trashed, then the run returns to the action loop. |
| 19 | Add ice encounters and supported icebreaker abilities. | Approach, rez, encounter, breaking, unbroken subroutines, jack-out opportunities, damage, and run-end behavior work across player decisions. |

For each step, verify the successful path, relevant rejection cases, and visibility rules. Once API and CLI support exist, include a manual CLI check. Extend the opponent's behavior as actions become available. Completing these steps still requires implementing the remaining selected cards' effects before claiming complete System Gateway support.

## Turn and installation details to preserve

- An ordinary end-turn command cannot skip unused clicks. The action phase normally ends after all clicks are spent, followed by discard and the remaining timing windows (rule 5.4.2).
- Corp mandatory draw happens after the opening paid ability window and turn-begin steps. Runner turn setup also has timing windows; allocating clicks alone does not complete setup.
- Corp agendas and assets belong in remote roots; upgrades may occupy central or remote roots. Ice normally installs outermost. Installing a new asset or agenda requires trashing any existing asset or agenda in that root.
- Runner program installation must allow the appropriate program trash choices to satisfy memory limits. Uniqueness is handled at checkpoints, not by universally prohibiting a second copy from being installed.
- Empty remotes cease to exist at the appropriate checkpoint, except while protected by the installation process. Preserve the project's current remote-position numbering convention and any resolution context when positions change.

Relevant references: [turns](../rules/Null_Signal_Games_Netrunner_Comprehensive_Rules_v26.03/05-turns.md), [card manipulation](../rules/Null_Signal_Games_Netrunner_Comprehensive_Rules_v26.03/08-card-manipulation.md), [abilities and priority](../rules/Null_Signal_Games_Netrunner_Comprehensive_Rules_v26.03/09-abilities.md), and [game-flow scenarios](game-scenarios.md).

## Relationship to the existing plan

Use `notes.md` for direction and this review for the proposed incremental checkpoints. Parts of `implementation-plan.md` describe earlier repository status: game creation now exists, the server owns opponent profiles, and local rules files are present even though ignored by Git. Its end-turn wording should be read with the click-spending restriction above. Human hosting/joining and React remain separate work from this action loop.
