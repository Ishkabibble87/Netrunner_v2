"""Resolve modal effects, private targets, and complete branch payments.

The existing window stack retains the original ability and its controller while
another player chooses. Card selections describe eligible sets rather than
materializing every combination; submissions are revalidated on a private copy.
"""

from content.cards.models import EffectOption, SelectCards

from .commands import CardSelection, ChooseEffectOption
from .history import boundary
from .models import CardZone, GameState, PlayerRole, PriorityWindow, ZoneLocation
from .payments import runner_funds, spend_runner_credits
from .setup import IllegalCommand


def selection_candidates(state, instruction, definitions):
    """Find targets without revealing a private zone to the opposing player."""
    owner = state.corp if instruction.side == "corp" else state.runner
    return tuple(card.instance_id for card in state.card_instances.values()
        if ((instruction.side == "any" or card.owner_id == owner.player_id)
            and ((instruction.zone == "hand" and card.location.zone is CardZone.HAND
                  and card.location.player_id == owner.player_id)
                 or (instruction.zone == "deck" and card.location.zone is CardZone.DECK)
                 or (instruction.zone == "discard" and card.location.zone is CardZone.DISCARD_PILE)
                 or (instruction.zone == "hand_or_discard" and card.location.zone in {CardZone.HAND, CardZone.DISCARD_PILE})
                 or (instruction.zone == "set_aside" and card.location.zone is CardZone.SET_ASIDE)
                 or (instruction.zone == "installed" and card.installed))
            and (not instruction.card_types or definitions[card.definition_id].card_type in instruction.card_types)
            and definitions[card.definition_id].card_type not in instruction.exclude_types
            and (instruction.effect != "rez" or _can_rez(state, card.instance_id, instruction, definitions))
            and all(s in definitions[card.definition_id].subtypes for s in instruction.subtypes)
            and (not instruction.not_installed_this_turn or card.installed_turn != state.turn.number
                 or card.owner_id != (state.corp if instruction.player == "corp" else state.runner).player_id)))


def _can_rez(state, instance_id, instruction, definitions):
    from .installed_abilities import validate_rez

    try:
        validate_rez(state, state.corp, instance_id, definitions, during_effect=True,
                     ignore_costs=instruction.ignore_costs)
    except IllegalCommand:
        return False
    return True


def card_selection(state, definitions):
    window = state.window
    eligible = selection_candidates(state, window.selection, definitions)
    count = len(eligible) if window.selection.any_number else min(window.selection.count, len(eligible))
    return CardSelection(eligible, count, window.selection.optional,
                         window.selection.up_to or window.selection.any_number)


def can_pay_option(state, role, option):
    if (state.run and state.run.cannot_steal_or_trash
            and any(i.type == "trash_accessed" for i in option.instructions)):
        return False
    player = state.corp if role is PlayerRole.CORP else state.runner
    funds = runner_funds(state) if role is PlayerRole.RUNNER else player.credits
    hand_count = sum(c.location.zone is CardZone.HAND and c.location.player_id == player.player_id
                     for c in state.card_instances.values())
    return (funds >= option.credit_cost and player.clicks_remaining >= option.click_cost
            and hand_count >= option.trash_hand_cost)


def choice_options(state: GameState, definitions):
    """Offer payable branches whose required targets can be selected in full."""
    window = state.window
    options = tuple(option for option in window.choice.options
                    if option.option_id not in window.chosen_options
                    and can_pay_option(state, window.player, option)
                    and (option.required_selection is None
                         or len(selection_candidates(state, option.required_selection, definitions)) >= option.required_selection.count))
    if window.chosen_options:
        options += (EffectOption(option_id="finish", label="Finish resolving modes"),)
    return options


def open_choice(state, instruction, source, fact, player):
    parent = state.window.resolving if state.window and state.window.kind == "reaction" else None
    state.suspended_windows.append(state.window)
    state.window = PriorityWindow(history_parent_id=state.history_parent_id, kind="choice", player=PlayerRole(instruction.player),
        choice=instruction, choice_source_id=source.instance_id, choice_fact=fact,
        choice_effect_player=player.role,
        choice_ability_id=parent.reaction.ability_id if parent and parent.reaction.once_per_turn else None,
        choice_remaining=2 if instruction.repeat_if_tagged and state.runner.tags else 1)


def open_selection(state, instruction, source, fact, definitions):
    if instruction.effect == "install":
        from .effect_installation import open_install
        open_install(state, instruction, source, fact, definitions)
        return
    if instruction.effect == "swap_ice" and len(selection_candidates(state, instruction, definitions)) < 2:
        return  # A swap requires two cards, even when other effects resolve partially.
    if not selection_candidates(state, instruction, definitions):
        if instruction.effect in {"search", "shuffle"}:
            from .card_movement import shuffle_into_deck
            owner = state.corp if instruction.side == "corp" else state.runner
            shuffle_into_deck(state, owner.player_id)
        return  # CR 1.15.2e: resolve as much as possible if no targets exist.
    state.suspended_windows.append(state.window)
    state.window = PriorityWindow(history_parent_id=state.history_parent_id, kind="target", player=PlayerRole(instruction.player),
        selection=instruction, choice_source_id=source.instance_id if source else None, choice_fact=fact)


def _trash_hand(state, instance_ids, role):
    for instance_id in instance_ids:
        card = state.card_instances[instance_id]
        card.face_up = role is PlayerRole.RUNNER
        card.location = ZoneLocation(zone=CardZone.DISCARD_PILE, player_id=card.owner_id)


@boundary
def resolve_selection(state, definitions, command):
    """Revalidate the whole set before paying a cost or changing any target."""
    from .card_movement import trash_installed_card
    from .installation import remove_empty_remotes
    from .reactions import checkpoint, settle_reactions

    window = state.window
    descriptor = card_selection(state, definitions)
    selected = command.card_instance_ids
    if (len(set(selected)) != len(selected) or not set(selected) <= set(descriptor.eligible_card_instance_ids)
            or (len(selected) > descriptor.required_count if descriptor.up_to else
                (len(selected) != descriptor.required_count and not (descriptor.optional and not selected)))):
        raise IllegalCommand("Choose the required number of distinct eligible cards")
    state.window = state.suspended_windows.pop()
    if window.payment_option is not None:
        if not selected:
            return  # Selection was cancelled before any part of the cost was paid.
        resolve_choice(state, definitions, ChooseEffectOption(window.payment_option.option_id), payment_cards=selected)
        return
    instruction = window.selection
    owner = state.corp if instruction.side == "corp" else state.runner
    cards = [state.card_instances[i] for i in selected]
    if instruction.effect == "swap_ice" and cards:
        from .card_movement import swap_installed_ice
        swap_installed_ice(state, *cards)
    elif instruction.effect == "rez":
        from .installed_abilities import rez_card
        for card in cards:
            rez_card(state, state.corp, card.instance_id, definitions,
                     during_effect=True, ignore_costs=instruction.ignore_costs)
    elif instruction.effect == "shuffle":
        from .card_movement import shuffle_into_deck
        shuffle_into_deck(state, owner.player_id, cards)
    elif instruction.effect == "search":
        from .card_movement import shuffle_into_deck
        for card in cards:
            card.face_up = False
            card.location = ZoneLocation(zone=CardZone.SET_ASIDE)
        shuffle_into_deck(state, owner.player_id)
        state.revealed_card_ids = list(selected)
        if cards and instruction.install_if_successful and state.turn.occurrences.get("run_success:runner", 0):
            from .effect_installation import open_install
            install = SelectCards(player=instruction.player, side=instruction.side, zone="set_aside", effect="install", optional=True)
            open_install(state, install, state.card_instances.get(window.choice_source_id), window.choice_fact,
                         definitions, card_ids=list(selected), fallback_hand=True)
        else:
            for card in cards:
                card.location = ZoneLocation(zone=CardZone.HAND, player_id=owner.player_id)
    for instance_id in selected:
        card = state.card_instances[instance_id]
        if instruction.effect in {"shuffle", "search", "swap_ice", "rez"}:
            continue
        if instruction.effect == "to_hand":
            card.location = ZoneLocation(zone=CardZone.HAND, player_id=owner.player_id)
            card.face_up = None
        elif window.selection.effect == "place_advancement":
            # CR 1.18.2: counter placement does not emit an advance occurrence.
            card.counters["advancement"] = card.counters.get("advancement", 0) + window.selection.amount
        elif card.installed:
            trash_installed_card(state, card)
        else:
            _trash_hand(state, (instance_id,), window.player)
    if state.run is None:
        remove_empty_remotes(state)
    checkpoint(state, definitions)
    settle_reactions(state, definitions)


@boundary
def resolve_choice(state, definitions, command: ChooseEffectOption, *, payment_cards=None):
    """Resolve one mode or pay a complete cost, retaining the effect controller."""
    from .reactions import checkpoint, resolve_instructions, settle_reactions

    option = next((o for o in choice_options(state, definitions) if o.option_id == command.option_id), None)
    if option is None:
        raise IllegalCommand("Choose an available effect option")
    window = state.window
    if option.trash_hand_cost and payment_cards is None:
        instruction = SelectCards(player=window.player.value, side=window.player.value,
                                  zone="hand", count=option.trash_hand_cost, optional=True)
        state.suspended_windows.append(window)
        state.window = PriorityWindow(history_parent_id=state.history_parent_id, kind="target", player=window.player, selection=instruction,
                                      payment_option=option, choice_source_id=window.choice_source_id)
        return
    if option.trash_hand_cost:
        eligible = selection_candidates(state, SelectCards(player=window.player.value, side=window.player.value,
                                        zone="hand", count=option.trash_hand_cost), definitions)
        if (len(payment_cards) != option.trash_hand_cost or len(set(payment_cards)) != len(payment_cards)
                or not set(payment_cards) <= set(eligible)):
            raise IllegalCommand("Pay the entire hand-trash cost")
    payer = state.corp if window.player is PlayerRole.CORP else state.runner
    recipient = state.corp if window.choice_effect_player is PlayerRole.CORP else state.runner
    if window.player is PlayerRole.RUNNER:
        spend_runner_credits(state, option.credit_cost)
    else:
        payer.credits -= option.credit_cost
    payer.clicks_remaining -= option.click_cost
    _trash_hand(state, payment_cards or (), window.player)
    if window.choice_ability_id and option.instructions:
        state.card_instances[window.choice_source_id].ability_last_used_turn[window.choice_ability_id] = state.turn.number
    window.chosen_options.append(option.option_id)
    window.choice_remaining -= 1
    if option.option_id == "finish" or window.choice_remaining == 0:
        state.window = state.suspended_windows.pop()
    # Costs are paid in full before the cost-paid checkpoint (CR 1.16.1, 1.16.3).
    if option.instructions:
        resolve_instructions(state, definitions, recipient, state.card_instances[window.choice_source_id],
            option.instructions, option.option_id, option.label, fact=window.choice_fact, cost_paid=True)
    else:
        checkpoint(state, definitions)
    settle_reactions(state, definitions)
