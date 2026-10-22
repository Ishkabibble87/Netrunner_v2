"""Build player actions and their labels from the current private game view."""

from collections.abc import Callable
from typing import Any

from .game_rendering import (
    MenuChoice,
    find_card,
    find_installed_card,
    host_label,
    server_label,
)

ActionFormatter = Callable[
    [dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]],
    list[MenuChoice],
]

def _choice(label: str, action: dict[str, Any]) -> list[MenuChoice]:
    return [MenuChoice(label, "action", dict(action))]

def _mulligan(
    _state: dict[str, Any],
    _catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    decision = action.get("decision")
    if decision == "keep":
        return _choice("Keep opening hand", action)
    if decision == "mulligan":
        return _choice("Mulligan · shuffle and draw five", action)
    return []

def _run_action(
    state: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    action_type = action["type"]
    run = state.get("run") or {}
    if action_type == "run_server":
        label = f"Run {server_label(action['server_id'])} · 1 click"
    elif action_type == "continue_run":
        label = (
            "Continue resolving subroutines"
            if run.get("phase") == "subroutine_choice"
            else "Continue inward"
        )
    elif action_type == "jack_out":
        label = "Jack out"
    elif action_type == "access_card":
        target = action["target"]
        opponent = state.get("opponent", {})
        known = list(run.get("accessed_cards", []))
        known.extend(opponent.get("discard_pile", []))
        known.extend(
            entry["card"]
            for entry in state.get("installed_cards", [])
            if entry.get("card")
        )
        card = find_card(known, target)
        if target == "hq":
            label = "Access a random card from HQ"
        elif target == "rd":
            label = "Access the top card of R&D"
        else:
            roots = [
                entry for entry in state.get("installed_cards", [])
                if entry["instance_id"] == target
            ]
            position = server_label(roots[0]["server_id"]) if roots else "Archives"
            root_cards = [
                entry for entry in state.get("installed_cards", [])
                if roots
                and entry["server_id"] == roots[0]["server_id"]
                and entry["area"] == "root"
            ]
            ordinal = next(
                (
                    index
                    for index, entry in enumerate(root_cards, 1)
                    if entry["instance_id"] == target
                ),
                1,
            )
            title = card["title"] if card else f"facedown card {ordinal}"
            label = f"Access {title} in {position}"
    else:
        card = run.get("current_card") or {}
        if action.get("trash"):
            cost = catalog.get(card.get("definition_id"), {}).get("trash_cost", "?")
            label = f"Trash {card.get('title', 'card')} · {cost} credits"
        elif card.get("card_type") == "agenda" and not run.get("cannot_steal_or_trash"):
            label = f"Steal {card['title']}"
        else:
            label = "Leave card and finish access"
    return _choice(label, action)

def _play_card(
    state: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    card = find_card(
        state.get("you", {}).get("hand", []),
        action["card_instance_id"],
    )
    if card is None:
        return []
    cost = catalog.get(card["definition_id"], {}).get("play_cost", "?")
    target = (
        f" !92 {server_label(action['server_id'])}"
        if action.get("server_id") is not None
        else ""
    )
    return _choice(
        f"Play {card['title']}{target} · {cost} credits",
        action,
    )

def _install_card(
    state: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    candidates = list(state.get("you", {}).get("hand", []))
    candidates.extend(state.get("selection_cards", []))
    card = find_card(candidates, action["card_instance_id"])
    if card is None:
        return []

    server_id = action.get("server_id")
    if server_id is None:
        target = "your rig"
    elif server_id == "new_remote":
        target = "new remote"
    else:
        target = server_label(server_id)
    host_id = action.get("host_card_instance_id")
    if host_id:
        target = host_label(state, host_id)

    trash_ids = set(action.get("trash_card_instance_ids", []))
    trash_titles = [
        entry["card"]["title"]
        for entry in state.get("installed_cards", [])
        if entry["instance_id"] in trash_ids and entry.get("card")
    ]
    replacement = " · trash " + ", ".join(trash_titles) if trash_titles else ""
    cost = card.get("effective_install_cost")
    if cost is None:
        cost = catalog.get(card["definition_id"], {}).get("install_cost")
    suffix = (
        f" · {cost} {'credit' if cost == 1 else 'credits'}"
        if server_id is None
        else ""
    )
    click = "" if state.get("effect_install") else " · 1 click"
    return _choice(
        f"Install {card['title']} in {target}{replacement}{click}{suffix}",
        action,
    )

def _installed_action(
    state: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    entry = find_installed_card(state, action["card_instance_id"])
    card = (entry or {}).get("card") or {}
    title = card.get("title", "card")
    if action["type"] == "rez_card":
        cost = card.get("effective_rez_cost")
        if cost is None:
            cost = catalog.get(card.get("definition_id"), {}).get("rez_cost", "?")
        label = f"Rez {title} · {cost} credits"
    else:
        ability_id = action["ability_id"]
        ability = card.get("ability_labels", {}).get(ability_id, ability_id)
        suffix = "" if ability_id in card.get("paid_ability_ids", []) else " · 1 click"
        if action.get("server_id") is not None:
            suffix += f" !92 {server_label(action['server_id'])}"
        targets = action.get("subroutine_indices", [])
        if targets:
            suffix += " · subroutines " + ", ".join(str(index + 1) for index in targets)
        label = f"{title}: {ability}{suffix}"
    return _choice(label, action)

def _effect_option(
    state: dict[str, Any],
    _catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    label = state.get("effect_options", {}).get(
        action["option_id"],
        action["option_id"],
    )
    return _choice(label, action)

def _trash_resource(
    state: dict[str, Any],
    _catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    entry = find_installed_card(state, action["card_instance_id"])
    card = (entry or {}).get("card") or {}
    return _choice(
        f"Trash {card.get('title', 'resource')} · 1 click, 2 credits",
        action,
    )

def _resolve_reaction(
    state: dict[str, Any],
    _catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    reaction = next(
        (
            item for item in state.get("reactions", [])
            if item["reaction_id"] == action.get("reaction_id")
        ),
        {},
    )
    verb = "Decline" if action.get("decline") else "Resolve"
    return _choice(f"{verb} {reaction.get('label', 'ability')}", action)

def _score_action(
    state: dict[str, Any],
    _catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    installed = [
        entry["card"]
        for entry in state.get("installed_cards", [])
        if entry.get("card")
    ]
    scored = state.get("you", {}).get("score_area", [])
    card = find_card(installed + scored, action["card_instance_id"]) or {}
    verb = "Advance" if action["type"] == "advance_card" else "Score"
    suffix = " · 1 credit" if action["type"] == "advance_card" else ""
    return _choice(f"{verb} {card.get('title', 'agenda')}{suffix}", action)

def _trash_programs(
    state: dict[str, Any],
    _catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    card_ids = set(action["card_instance_ids"])
    titles = [
        entry["card"]["title"]
        for entry in state.get("installed_cards", [])
        if entry["instance_id"] in card_ids and entry.get("card")
    ]
    return _choice("Trash " + ", ".join(titles) + " to free memory", action)

def _choose_effect_cards(
    _state: dict[str, Any],
    _catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    if "card_instance_ids" in action:
        return _choice("Decline installation", action)
    prefix = "Choose up to" if action.get("up_to") else "Choose"
    label = f"{prefix} {action['required_count']} cards"
    return [MenuChoice(label, "discard", action)]

def _discard_cards(
    _state: dict[str, Any],
    _catalog: dict[str, dict[str, Any]],
    action: dict[str, Any],
) -> list[MenuChoice]:
    label = f"Discard {action['required_count']} cards"
    return [MenuChoice(label, "discard", action)]

_SIMPLE_ACTION_LABELS = {
    "pass_priority": "Pass priority",
    "gain_credit": "Gain 1 credit",
    "draw_card": "Draw cards · 1 click",
    "remove_tag": "Remove 1 tag · 2 credits",
    "purge_virus_counters": "Purge virus counters · 3 clicks",
}

_ACTION_FORMATTERS: dict[str, ActionFormatter] = {
    "choose_mulligan": _mulligan,
    "run_server": _run_action,
    "continue_run": _run_action,
    "jack_out": _run_action,
    "access_card": _run_action,
    "finish_access": _run_action,
    "play_card": _play_card,
    "install_card": _install_card,
    "rez_card": _installed_action,
    "use_card_ability": _installed_action,
    "choose_effect_option": _effect_option,
    "trash_resource": _trash_resource,
    "resolve_reaction": _resolve_reaction,
    "advance_card": _score_action,
    "score_agenda": _score_action,
    "trash_programs": _trash_programs,
    "select_effect_cards": _choose_effect_cards,
    "discard_cards": _discard_cards,
}

def build_action_choices(
    state: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    *,
    submit_action_available: bool,
) -> list[MenuChoice]:
    """Format only server-authorized actions, preserving the legacy mulligan fallback."""
    if not submit_action_available:
        return []

    legal_actions = state.get("legal_actions")
    choices: list[MenuChoice] = []
    if isinstance(legal_actions, list):
        actions = (action for action in legal_actions if isinstance(action, dict))
    else:
        viewer_role = str(
            state.get("viewer_role", state.get("you", {}).get("role", ""))
        )
        if state.get("setup", {}).get("pending_player") != viewer_role:
            return []
        actions = iter(
            (
                {"type": "choose_mulligan", "decision": "keep"},
                {"type": "choose_mulligan", "decision": "mulligan"},
            )
        )

    for action in actions:
        action_type = action.get("type")
        formatter = _ACTION_FORMATTERS.get(action_type)
        if formatter is not None:
            choices.extend(formatter(state, catalog, action))
        elif action_type in _SIMPLE_ACTION_LABELS:
            choices.extend(_choice(_SIMPLE_ACTION_LABELS[action_type], action))
    return choices
