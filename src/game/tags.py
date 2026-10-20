"""Give a packet of tags and publish one occurrence for tag-taking reactions."""

from .models import PlayerRole, RuleFact


def give_tags(state, amount, definitions):
    from .reactions import emit_fact

    if amount <= 0 or state.winner is not None:
        return
    state.runner.tags += amount
    emit_fact(state, definitions, RuleFact(type="take_tags", player=PlayerRole.RUNNER), auto_single=True)
