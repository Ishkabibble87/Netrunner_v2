"""Record player history at resolution boundaries, independently of rule facts.

The command-local observer records small state changes before entering a nested
resolution and after it returns. This preserves intermediate outcomes that a
final-state diff would lose. Only committed working snapshots retain entries.
Viewer descriptions are captured at occurrence time; projections never resolve
old card references against today's visibility or deck positions.
"""

from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

from .models import CardZone, HistoryDetail, HistoryEntry, PlayerRole

_observer = ContextVar("history_observer", default=None)


def _snapshot(state):
    visible = {role: set(state.revealed_card_ids) for role in PlayerRole}
    if state.run:
        visible[PlayerRole.RUNNER].update(state.run.visible_card_ids)
    cards = {}
    for card in state.card_instances.values():
        location = card.location
        for player in (state.corp, state.runner):
            if (card.face_up or (card.owner_id == player.player_id
                    and location.zone not in {CardZone.DECK, CardZone.SET_ASIDE})):
                visible[player.role].add(card.instance_id)
        # Deck positions are deliberately absent: shuffles and reindexing do
        # not provide a correspondence between old and new hidden objects.
        place = location.zone.value
        if location.server:
            server = location.server
            name = f"Remote {server.server_id}" if isinstance(server.server_id, int) else server.server_id.value.upper()
            place = f"{name} {server.area.value}"
            if server.ice_distance_from_server is not None:
                place += f" {server.ice_distance_from_server + 1}"
        elif location.host_card_instance_id:
            place = "hosted in play"
        cards[card.instance_id] = {
            "definition_id": card.definition_id, "location": place,
            "rezzed": card.rezzed, "counters": dict(card.counters),
            "face_up": card.face_up,
        }
    return {
        "players": {p.role: {key: getattr(p, key) for key in
                    ("credits", "clicks_remaining", "tags", "bad_publicity", "core_damage")}
                    for p in (state.corp, state.runner)},
        "cards": cards, "visible": visible, "winner": state.winner,
        "run": ((state.run.run_id, state.run.phase, state.run.current_ice_id,
                 state.run.current_card_id, tuple(state.run.broken_subroutine_indices))
                if state.run else None),
    }


def append(state, kind, details, *, actor=None, parent_id=None, entry_id=None):
    """Append one occurrence with a monotonically increasing game sequence."""
    identity = entry_id or uuid4()
    if state.history_group_id is None:
        object.__setattr__(state, "history_group_id", identity)
    entry = HistoryEntry(
        entry_id=identity, sequence=len(state.history) + (_observer.get().offset if _observer.get() else 0) + 1,
        turn_number=state.turn.number if state.turn else None,
        turn_player=state.turn.active_player if state.turn else None,
        group_id=state.history_group_id,
        parent_id=parent_id if parent_id is not None else state.history_parent_id,
        run_id=state.run.run_id if state.run else None,
        actor=actor, kind=kind, details=details,
    )
    state.history.append(entry)
    return identity


def note(state, kind, text, *, actor=None, card_id=None, field=None, after=None):
    """Capture a source's name separately for each currently entitled viewer."""
    recorder = _observer.get()
    if recorder is None:
        return None
    snapshot = _snapshot(state)
    details = {}
    for role in PlayerRole:
        data = {"text": text, "field": field, "after": after}
        if card_id in snapshot["visible"][role]:
            definition = recorder.definitions.get(state.card_instances[card_id].definition_id)
            if definition:
                data.update(definition_id=definition.definition_id, title=definition.title)
        details[role] = HistoryDetail(**data)
    return append(state, kind, details, actor=actor)


class Recorder:
    """A command-local baseline shared by nested resolution boundaries."""

    def __init__(self, state, definitions):
        self.before = _snapshot(state)
        self.definitions = definitions
        self.offset = 0

    def flush(self, state):
        after = _snapshot(state)
        before, self.before = self.before, after
        for role, values in after["players"].items():
            for field, value in values.items():
                old = before["players"][role][field]
                if value != old:
                    detail = HistoryDetail(text=f"{role.value.title()}: {field.replace('_', ' ')} {old} → {value}",
                                           field=field, before=old, after=value)
                    append(state, "resource", {r: detail for r in PlayerRole}, actor=role)
        for instance_id, card in after["cards"].items():
            old = before["cards"].get(instance_id)
            if old is None:
                continue
            changes = []
            if old["location"] != card["location"]:
                changes.append(("movement", f"{old['location']} → {card['location']}", None, None, None))
            if old["rezzed"] != card["rezzed"]:
                changes.append(("rez", "Rezzed" if card["rezzed"] else "Derezzed", None, None, None))
            for counter in sorted(old["counters"].keys() | card["counters"].keys()):
                start, end = old["counters"].get(counter, 0), card["counters"].get(counter, 0)
                if start != end:
                    changes.append(("counter", f"{counter} counters {start} → {end}", counter, start, end))
            newly_visible = any(instance_id in after["visible"][r] - before["visible"][r] for r in PlayerRole)
            if newly_visible and not changes:
                changes.append(("reveal", "Card viewed", None, None, None))
            for kind, text, field, start, end in changes:
                details = {}
                for role in PlayerRole:
                    known = instance_id in before["visible"][role] or instance_id in after["visible"][role]
                    definition = self.definitions.get(card["definition_id"]) if known else None
                    # Private views leave no trace for the other viewer.
                    if kind == "reveal" and instance_id not in after["visible"][role] - before["visible"][role]:
                        continue
                    details[role] = HistoryDetail(
                        text=text, field=field, before=start, after=end,
                        definition_id=definition.definition_id if definition else None,
                        title=definition.title if definition else None,
                    )
                append(state, kind, details)
        if before["run"] != after["run"]:
            run = after["run"]
            text = f"Run: {run[1].replace('_', ' ')}" if run else "Run finished"
            identity = note(state, "run", text, card_id=run[2] if run else None)
            if identity and not run and before["run"]:
                state.history[-1].run_id = before["run"][0]
            if run and state.run.history_parent_id is None:
                state.run.history_parent_id = identity
            if run and before["run"] and run[0] == before["run"][0]:
                for index in set(run[4]) - set(before["run"][4]):
                    note(state, "subroutine", f"Break subroutine {index + 1}", card_id=run[2])
        if before["winner"] != after["winner"] and after["winner"]:
            note(state, "game_end", f"{after['winner'].value.title()} wins")


def boundary(function):
    """Observe a shared mutator without changing its rules or return value."""
    @wraps(function)
    def observed(state, *args, **kwargs):
        recorder = _observer.get()
        if recorder is None:
            return function(state, *args, **kwargs)
        recorder.flush(state)
        parent = state.history_parent_id
        if function.__name__ == "resolve_effect":
            instruction = args[1]
            source = args[2] if len(args) > 2 else kwargs.get("source")
            object.__setattr__(state, "history_parent_id", note(
                state, "effect", instruction.type.replace("_", " "),
                actor=args[0].role, card_id=source.instance_id if source else None,
                field="amount" if isinstance(getattr(instruction, "amount", None), int) else None,
                after=instruction.amount if isinstance(getattr(instruction, "amount", None), int) else None,
            ))
        elif function.__name__ == "deal_damage":
            note(state, "damage", f"Runner suffers {args[0]} {args[1]} damage",
                 actor=PlayerRole.RUNNER, field="damage", after=args[0])
        result = function(state, *args, **kwargs)
        recorder.flush(state)
        object.__setattr__(state, "history_parent_id", parent)
        return result
    return observed


def turn_step(state, step):
    """Separate automatic turn effects from the action that exhausted clicks."""
    recorder = _observer.get()
    if recorder is None:
        return
    if step != "start":
        recorder.flush(state)
    if step in {"start", "end", "before_action", "discard"}:
        object.__setattr__(state, "history_group_id", None)
        object.__setattr__(state, "history_parent_id", None)
        if step in {"start", "end"}:
            object.__setattr__(state, "history_parent_id", note(state, "turn", "Turn start" if step == "start" else "Turn end",
                                           actor=state.turn.active_player))
            if step == "start":
                state.turn.history_start_id = state.history_parent_id
    elif step in {"turn_begin", "mandatory_draw"} and state.turn.history_start_id:
        object.__setattr__(state, "history_group_id", state.turn.history_start_id)
        object.__setattr__(state, "history_parent_id", state.turn.history_start_id)
    if step == "start":
        recorder.flush(state)


def record_command(function):
    """Commit command history atomically with the engine's resulting snapshot."""
    @wraps(function)
    def recorded(state, player_id, command, card_definitions=None):
        # Existing entries are immutable by convention. Keep the committed
        # prefix outside the engine's deep copies and repeated validation;
        # only this command's new entries belong to its working transaction.
        working = state.model_copy(update={"history": []})
        recorder = Recorder(working, card_definitions or {})
        recorder.offset = len(state.history)
        token = _observer.set(recorder)
        try:
            role = next((p.role for p in (state.corp, state.runner) if p.player_id == player_id), None)
            independent = (not state.run and not state.memory_trash_pending
                           and (state.window is None or (state.window.kind == "paid"
                                and type(command).__name__ != "PassPriority")))
            if independent:
                object.__setattr__(working, "history_group_id", None)
                object.__setattr__(working, "history_parent_id", None)
            parent = (state.window.history_parent_id if state.window else None)
            parent = parent or (state.run.history_parent_id if state.run else None) or working.history_parent_id
            if independent:
                parent = None
            object.__setattr__(working, "history_parent_id", parent)
            name = function_command_label(command)
            identity = note(working, "command", name, actor=role,
                            card_id=getattr(command, "card_instance_id", None))
            if type(command).__name__ == "ChooseEffectOption" and state.window:
                from .choices import choice_options
                option = next((o for o in choice_options(state, card_definitions or {})
                               if o.option_id == command.option_id), None)
                if option and role in working.history[-1].details:
                    working.history[-1].details[role].text += f": {option.label}"
            object.__setattr__(working, "history_parent_id", identity)
            result = function(working, player_id, command, card_definitions)
            recorder.flush(result)
            object.__setattr__(result, "history", [*state.history, *result.history])
            return result
        finally:
            _observer.reset(token)
    return recorded


def function_command_label(command):
    """Describe intent without serializing private command targets."""
    import re
    text = re.sub(r"(?<!^)(?=[A-Z])", " ", type(command).__name__)
    if getattr(command, "decline", False):
        text = "Decline reaction"
    elif hasattr(command, "decision"):
        text += f": {command.decision.value}"
    server = getattr(command, "server_id", None)
    if server is not None:
        text += f": {server}"
    return text
