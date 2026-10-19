"""Pay supported Runner costs using credits available at the current timing point.

Temporary run funds are spent before pool credits: bad publicity, then credits
hosted on the initiating event. Event credits remain on their source card.
"""

from .models import GameState
from .setup import IllegalCommand


def event_credits(state: GameState) -> int:
    """Credits hosted on the event that created the current run, if any."""
    if state.run and state.run.source_event_id:
        return state.card_instances[state.run.source_event_id].counters.get("credit", 0)
    return 0


def runner_funds(state: GameState) -> int:
    """Return funds usable for the currently supported Runner credit costs."""
    return state.runner.credits + (state.run.bad_publicity_credits if state.run else 0) + event_credits(state)


def spend_runner_credits(state: GameState, amount: int) -> None:
    """Validate the full cost before spending temporary credits, then the pool."""
    if amount < 0 or amount > runner_funds(state):
        raise IllegalCommand("Not enough credits")
    if state.run:
        temporary = min(amount, state.run.bad_publicity_credits)
        state.run.bad_publicity_credits -= temporary
        amount -= temporary
        hosted = min(amount, event_credits(state))
        if hosted:
            state.card_instances[state.run.source_event_id].counters["credit"] -= hosted
            amount -= hosted
    state.runner.credits -= amount
