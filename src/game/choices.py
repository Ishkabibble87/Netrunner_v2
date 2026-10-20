"""Suspend effect resolution for Task 7's authored, affordable decision branches.

The existing reaction stack retains the parent continuation. Resolving a branch
uses the same instruction checkpoints, so tag reactions finish before its parent.
"""

from .commands import ChooseEffectOption
from .models import GameState, PlayerRole, PriorityWindow
from .payments import runner_funds, spend_runner_credits
from .setup import IllegalCommand


def choice_options(state: GameState):
    window = state.window
    funds = runner_funds(state) if window.player is PlayerRole.RUNNER else state.corp.credits
    return tuple(option for option in window.choice.options if option.credit_cost <= funds)


def open_choice(state, instruction, source, fact):
    state.suspended_windows.append(state.window)
    state.window = PriorityWindow(kind="choice", player=PlayerRole(instruction.player),
        choice=instruction, choice_source_id=source.instance_id, choice_fact=fact)


def resolve_choice(state, definitions, command: ChooseEffectOption):
    """Validate before spending, then restore the suspended instruction sequence."""
    from .reactions import resolve_instructions, settle_reactions

    option = next((o for o in choice_options(state) if o.option_id == command.option_id), None)
    if option is None:
        raise IllegalCommand("Choose an available effect option")
    window = state.window
    player = state.runner if window.player is PlayerRole.RUNNER else state.corp
    if window.player is PlayerRole.RUNNER:
        spend_runner_credits(state, option.credit_cost)
    else:
        player.credits -= option.credit_cost
    state.window = state.suspended_windows.pop()
    if option.instructions:
        resolve_instructions(state, definitions, player, state.card_instances[window.choice_source_id],
            option.instructions, option.option_id, option.label, fact=window.choice_fact)
    settle_reactions(state, definitions)
