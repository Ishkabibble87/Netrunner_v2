"""Paid priority with active-player first and consecutive passes (CR 9.2.7).

Unsupported options pass automatically. Corp opportunities depend on public
installation/face status, never the identity or affordability of hidden cards.
Run continuations preserve the timing step suspended by each window.
"""

from .models import GameState, PlayerRole, PriorityWindow, TurnPhase


def has_window_options(state: GameState, role: PlayerRole, definitions) -> bool:
    """Check public potential, so passing cannot reveal hidden eligibility."""
    player = state.corp if role is PlayerRole.CORP else state.runner
    installed = [c for c in state.card_instances.values()
                 if c.installed and c.controller_id == player.player_id]
    if any(c.face_up and definitions.get(c.definition_id)
           and any(a.type != "break_self_subroutine" for a in definitions[c.definition_id].paid_abilities)
           for c in installed):
        return True
    if role is PlayerRole.RUNNER:
        run = state.run
        ice = state.card_instances.get(run.current_ice_id) if run and run.phase == "encounter" else None
        definition = definitions.get(ice.definition_id) if ice and ice.installed and ice.rezzed else None
        if (definition and any(a.type == "break_self_subroutine" and player.clicks_remaining >= a.click_cost
                               for a in definition.paid_abilities)
                and any(i not in run.broken_subroutine_indices for i in range(len(definition.subroutines or ())))):
            return True
        return False
    can_score = (state.run is None and state.turn.active_player is PlayerRole.CORP
                 and state.turn.phase in {TurnPhase.DRAW, TurnPhase.ACTION})
    return bool((can_score and installed) or
                (state.window.rez_allowed and any(not c.face_up for c in installed)))


def open_paid_window(state: GameState, run_continuation=None, definitions=None,
                     *, rez_allowed: bool = True) -> None:
    """Open a window, automatically passing players with no supported options."""
    state.window = PriorityWindow(history_parent_id=state.history_parent_id, kind="paid", player=state.turn.active_player,
                                  run_continuation=run_continuation, rez_allowed=rez_allowed)
    if not has_window_options(state, state.window.player, definitions or {}):
        pass_priority(state, definitions or {})


def pass_priority(state: GameState, definitions) -> str | None:
    """Exchange priority, or close after consecutive explicit/automatic passes."""
    window = state.window
    other = PlayerRole.RUNNER if window.player is PlayerRole.CORP else PlayerRole.CORP
    if window.opponent_passed or not has_window_options(state, other, definitions):
        continuation = window.run_continuation
        state.window = None
        return continuation
    window.player = other
    window.opponent_passed = True
    return None
