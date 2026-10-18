"""Open supported paid windows without inferring hidden card identities.

Neither player has regular paid abilities in this slice. The Runner therefore
passes automatically; the Corp must explicitly decline rez/scoring opportunities
whenever public board state could permit them (CR 9.2.7).
"""

from typing import Literal

from .models import GameState, PlayerRole, PriorityWindow, TurnPhase


def open_paid_window(state: GameState,
                     run_continuation: Literal["movement", "breach"] | None = None) -> None:
    """Pause for Corp priority using only public installation and face status."""
    installed = [c for c in state.card_instances.values()
                 if c.installed and c.controller_id == state.corp.player_id]
    can_score = (state.run is None and state.turn.active_player is PlayerRole.CORP
                 and state.turn.phase in {TurnPhase.DRAW, TurnPhase.ACTION})
    if any(not c.face_up for c in installed) or (can_score and installed):
        state.window = PriorityWindow(kind="paid", player=PlayerRole.CORP,
                                      run_continuation=run_continuation)
